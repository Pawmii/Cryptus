"""TON adapter (native GRAM and Jettons) over the Toncenter HTTP API.

Native GRAM
  Deposit wallets are v4r2 contracts derived per invoice (see core.ton_wallet).
  They stay *uninitialised* until consolidation. Incoming value is accepted
  only when the receiving transaction credited the account and produced no
  bounce, and the message was not itself a bounce.

Jettons (e.g. USDT Jetton)
  For every deposit owner we ask the *verified* jetton master for the jetton
  wallet address (``get_wallet_address``). Because that address is derived
  from the master's wallet code and the owner, a successful
  ``internal_transfer`` executed by that contract can only originate from a
  genuine jetton wallet of the same master. We additionally:
    * parse the ``internal_transfer`` body (op 0x178d4519) for amount/sender,
    * require compute and action phases to have succeeded and the tx not to be aborted,
    * after the jetton wallet is deployed, call ``get_wallet_data`` and check
      that owner == deposit wallet and master == configured master.

Finality: transactions are taken from the indexer once included in a
masterchain block; confirmations = masterchain head − tx mc seqno + 1.
"""
from __future__ import annotations

import base64
import time

import httpx
from pytoniq_core import Address, Cell, begin_cell

from ..core import ton_wallet
from .base import AssetInfo, ChainAdapter, ChainError, Health, Observed, ScanResult, TxState, WatchItem

MAINNET_GLOBAL_ID = -239
TESTNET_GLOBAL_ID = -3

DIRECT_PAGE = 100          # transactions per indexer request when following a merchant wallet
DIRECT_MAX_PAGES = 10      # per account and pass; a backlog continues on the next pass
DIRECT_CURSOR_LAG = 60     # seconds: the cursor never passes recent transactions, so a deposit created while a
                           # pass is running still sees payments made right after it was issued
MEMO_CLOCK_SKEW = 120      # seconds a payment may predate its deposit's creation timestamp


class GetMethodFailed(ChainError):
    """The contract ran but the get-method failed (missing method, not a wallet, ...)."""


def norm(addr: str | None) -> str | None:
    if not addr:
        return None
    try:
        return Address(addr).to_str(is_user_friendly=False)
    except Exception:
        return None


def b64_to_hex(h: str) -> str:
    try:
        return base64.b64decode(h).hex()
    except Exception:
        return h


def _body(tx: dict) -> Cell:
    b64 = ((tx.get("in_msg") or {}).get("message_content") or {}).get("body")
    return Cell.one_from_boc(base64.b64decode(b64)) if b64 else Cell.empty()


def _stack_cell(entry) -> Cell:
    """Parse a cell/slice stack entry from either Toncenter v2 or v3 response formats."""
    if isinstance(entry, dict):  # v3: {"type": "cell"|"slice", "value": b64}
        return Cell.one_from_boc(base64.b64decode(entry["value"]))
    val = entry[1]
    b = val.get("bytes") if isinstance(val, dict) else val
    return Cell.one_from_boc(base64.b64decode(b))


class TonAdapter(ChainAdapter):
    family = "ton"
    backends = ("toncenter",)

    def __init__(self, network, conf, http=None):
        super().__init__(network, conf, http)
        self.base = conf.url.rstrip("/")
        self.headers = {"X-API-Key": k} if (k := conf.credentials.get("api_key")) else {}

    def _req(self, method: str, path: str, **kw):
        for attempt in range(4):
            try:
                r = self.http.request(method, self.base + path, headers=self.headers, **kw)
            except httpx.HTTPError as exc:
                raise ChainError(f"toncenter unreachable: {exc}") from exc
            if r.status_code != 429:
                break
            time.sleep(1.1 * (attempt + 1))  # keyless toncenter allows ~1 rps
        if r.status_code == 429:
            raise ChainError("toncenter rate limit reached — configure an API key")
        if r.status_code >= 400:
            raise ChainError(f"toncenter {path}: HTTP {r.status_code} {r.text[:200]}")
        return r.json()

    def v3(self, path: str, **params):
        return self._req("GET", "/api/v3" + path, params=params)

    def run_get(self, address: str, method: str, stack: list) -> list:
        body = self._req("POST", "/api/v2/runGetMethod", json={"address": address, "method": method, "stack": stack})
        res = body.get("result", body)
        if res.get("exit_code", 0) not in (0, 1):
            raise GetMethodFailed(f"get-method {method} exited with {res.get('exit_code')}")
        return res.get("stack", [])

    def mc_head(self) -> int:
        info = self.v3("/masterchainInfo")
        return int(info["last"]["seqno"])

    # ── Health ─────────────────────────────────────────────────────────
    def health(self) -> Health:
        try:
            info, ms = self._timed(self.v3, "/masterchainInfo")
            last = info["last"]
            gid = last.get("global_id")
            want = TESTNET_GLOBAL_ID if self.network.ton_testnet else MAINNET_GLOBAL_ID
            if gid is not None and int(gid) != want:
                return Health(False, error=f"endpoint serves global_id {gid}, expected {want}")
            return Health(True, int(last["seqno"]), ms, {"global_id": gid, "api_key": bool(self.headers)})
        except (ChainError, KeyError) as exc:
            return Health(False, error=str(exc))

    # ── Jetton wallet resolution & verification ────────────────────────
    def jetton_wallet_of(self, master_raw: str, owner_raw: str) -> str:
        owner_cell = begin_cell().store_address(Address(owner_raw)).end_cell()
        stack = self.run_get(master_raw, "get_wallet_address",
                             [["tvm.Slice", base64.b64encode(owner_cell.to_boc()).decode()]])
        if not stack:
            raise ChainError("get_wallet_address returned an empty stack")
        addr = _stack_cell(stack[0]).begin_parse().load_address()
        return addr.to_str(is_user_friendly=False)

    def verify_jetton_wallet(self, jetton_wallet: str, owner_raw: str, master_raw: str) -> bool | None:
        """True if verified, False if mismatch, None if the wallet is not deployed yet."""
        info = self._req("GET", "/api/v2/getAddressInformation", params={"address": jetton_wallet})
        if info.get("result", {}).get("state") != "active":
            return None
        stack = self.run_get(jetton_wallet, "get_wallet_data", [])
        if len(stack) < 3:
            return False
        owner = _stack_cell(stack[1]).begin_parse().load_address().to_str(is_user_friendly=False)
        master = _stack_cell(stack[2]).begin_parse().load_address().to_str(is_user_friendly=False)
        return owner == owner_raw and master == master_raw

    def inspect_jetton(self, master: str) -> dict:
        stack = self.run_get(master, "get_jetton_data", [])
        if len(stack) < 5:
            raise ChainError("not a TEP-74 jetton master (get_jetton_data failed)")
        info = self.v3("/jetton/masters", address=master, limit=1)
        meta = (info.get("jetton_masters") or [{}])[0].get("jetton_content", {}) if isinstance(info, dict) else {}
        return {"metadata": meta}

    def prepare_deposit(self, item: WatchItem) -> dict:
        if item.asset.kind != "jetton":
            return {}
        jw = self.jetton_wallet_of(item.asset.contract, item.address)
        return {"jetton_wallet": jw, "jetton_master": item.asset.contract}

    # ── Scan ───────────────────────────────────────────────────────────
    def _txs(self, account: str, limit: int = 40) -> list[dict]:
        return self.v3("/transactions", account=account, limit=limit, sort="desc").get("transactions", [])

    def scan(self, items: list[WatchItem]) -> ScanResult:
        head = self.mc_head()
        started = time.time()
        out: list[Observed] = []
        meta: dict[str, dict] = {}
        accounts = dict((self.conf.cursor or {}).get("accounts") or {})
        direct: dict[str, list[WatchItem]] = {}  # account to follow -> deposits identified by memo
        for item in items:
            m = dict(item.meta)
            if item.asset.kind != "native" and not m.get("jetton_wallet"):
                m.update(self.prepare_deposit(item))
                meta[item.deposit_id] = m
            if m.get("memo"):
                acct = item.address if item.asset.kind == "native" else m["jetton_wallet"]
                direct.setdefault(acct, []).append(WatchItem(item.deposit_id, item.address, item.display_address,
                                                             item.asset, m, item.exclude_senders))
                continue
            if item.asset.kind == "native":
                out += self._scan_native(item, head)
                continue
            obs = self._scan_jetton(item, m["jetton_wallet"], head)
            out += self._verify_jetton_obs([WatchItem(item.deposit_id, item.address, item.display_address,
                                                      item.asset, m)], obs, meta)
        for acct, group in direct.items():
            obs = self._scan_direct(acct, group, head, accounts, started)
            out += obs if group[0].asset.kind == "native" else self._verify_jetton_obs(group, obs, meta)
        return ScanResult(out, {"mc_seqno": head, "accounts": accounts}, head, meta)

    def _verify_jetton_obs(self, group: list[WatchItem], obs: list[Observed],
                           meta: dict[str, dict]) -> list[Observed]:
        """Before crediting, prove the receiving jetton wallet belongs to the owner and the verified master."""
        first = group[0]
        if not obs or first.meta.get("jetton_wallet_verified"):
            return obs
        ok = self.verify_jetton_wallet(first.meta["jetton_wallet"], first.address, first.asset.contract)
        if ok is False:
            for o in obs:
                o.detail["rejected"] = "jetton wallet owner/master mismatch"
        elif ok:
            for it in group:
                meta[it.deposit_id] = {**it.meta, **meta.get(it.deposit_id, {}), "jetton_wallet_verified": True}
        return obs

    @staticmethod
    def _native_credit(tx: dict) -> tuple[int, str] | None:
        """(credited value, sender) of an incoming internal transfer that stayed with the account."""
        msg = tx.get("in_msg") or {}
        src = norm(msg.get("source"))
        if not src or msg.get("bounced"):
            return None  # external message or a bounce of our own transfer
        value = int(msg.get("value") or 0)
        if value <= 0:
            return None
        d = tx.get("description", {})
        bounce = d.get("bounce")
        if isinstance(bounce, dict) and bounce.get("type") == "ok":
            return None  # value was bounced back to the sender (a failed bounce leaves it with us)
        credited = int((d.get("credit_ph") or {}).get("credit") or 0)
        if credited <= 0:
            return None
        return min(value, credited), src

    @staticmethod
    def _jetton_credit(tx: dict, jetton_wallet: str) -> dict | None:
        """Parsed ``internal_transfer`` that this jetton wallet executed successfully."""
        msg = tx.get("in_msg") or {}
        body_b64 = (msg.get("message_content") or {}).get("body")
        if not body_b64 or not msg.get("source") or msg.get("bounced"):
            return None
        try:
            parsed = ton_wallet.parse_jetton_internal_transfer(Cell.one_from_boc(base64.b64decode(body_b64)))
        except Exception:
            return None
        if parsed is None or norm(tx.get("account")) != norm(jetton_wallet):
            return None
        d = tx.get("description", {})
        if d.get("aborted") or not (d.get("compute_ph") or {}).get("success") \
                or (d.get("action") or {}).get("success") is False:
            return None  # transfer failed; no tokens were credited
        return parsed

    @staticmethod
    def _observed(item: WatchItem, tx: dict, amount: int, sender: str | None, head: int, detail: dict) -> Observed:
        seq = tx.get("mc_block_seqno")
        return Observed(item.deposit_id, b64_to_hex(tx["hash"]), 0, item.asset.id, amount, sender,
                        seq, str(tx.get("lt")), head - int(seq) + 1 if seq else 0, detail)

    def _scan_native(self, item: WatchItem, head: int) -> list[Observed]:
        out = []
        for tx in self._txs(item.address):
            got = self._native_credit(tx)
            if not got or got[1] in item.exclude_senders:
                continue  # nothing credited, or a gas top-up from our own gas wallet
            out.append(self._observed(item, tx, got[0], got[1], head,
                                      {"lt": tx.get("lt"), "comment": self._comment(tx.get("in_msg") or {})}))
        return out

    def _scan_jetton(self, item: WatchItem, jetton_wallet: str, head: int) -> list[Observed]:
        out = []
        for tx in self._txs(jetton_wallet):
            parsed = self._jetton_credit(tx, jetton_wallet)
            if parsed is None or (parsed["from"] and norm(parsed["from"]) in item.exclude_senders):
                continue
            out.append(self._observed(item, tx, int(parsed["amount"]), parsed["from"], head,
                                      {"jetton_wallet": jetton_wallet, "query_id": parsed["query_id"]}))
        return out

    def _scan_direct(self, account: str, group: list[WatchItem], head: int, accounts: dict[str, str],
                     started: float) -> list[Observed]:
        """Follow one merchant-owned account (wallet or its jetton wallet) and attribute transfers by memo.

        Every invoice shares this account, so it is read in logical-time order from a persisted cursor rather
        than as "the latest N transactions": nothing is skipped however busy the wallet is. Transfers without
        a known memo are the merchant's own business and are ignored (the value is already in their wallet)."""
        by_memo = {ton_wallet.normalize_memo(it.meta["memo"]): it for it in group}
        since = min(int(it.meta.get("created_ts") or 0) for it in group) - MEMO_CLOCK_SKEW
        params: dict = {"account": account, "sort": "asc", "limit": DIRECT_PAGE, "start_utime": max(0, since)}
        if accounts.get(account):
            params["start_lt"] = int(accounts[account]) + 1
        native = group[0].asset.kind == "native"
        out: list[Observed] = []
        advancing = True
        for _ in range(DIRECT_MAX_PAGES):
            txs = self.v3("/transactions", **params).get("transactions", [])
            for tx in txs:
                if advancing and tx.get("mc_block_seqno") and int(tx.get("now") or 0) < started - DIRECT_CURSOR_LAG:
                    accounts[account] = str(tx["lt"])
                else:
                    advancing = False  # re-read everything from here on during the next pass
                if native:
                    got = self._native_credit(tx)
                    if not got:
                        continue
                    amount, sender = got
                    text = ton_wallet.comment_of(_body(tx)) or self._comment(tx.get("in_msg") or {})
                else:
                    parsed = self._jetton_credit(tx, account)
                    if not parsed:
                        continue
                    amount, sender, text = int(parsed["amount"]), parsed["from"], parsed["comment"]
                memo = ton_wallet.normalize_memo(text)
                item = by_memo.get(memo) if memo else None
                if item is None or int(tx.get("now") or 0) < int(item.meta.get("created_ts") or 0) - MEMO_CLOCK_SKEW:
                    continue  # not one of our open deposits (or sent before the memo was issued)
                detail = {"lt": tx.get("lt"), "memo": item.meta["memo"]}
                if not native:
                    detail.update(jetton_wallet=account, query_id=parsed["query_id"])
                out.append(self._observed(item, tx, amount, sender, head, detail))
            if len(txs) < DIRECT_PAGE:
                break
            params["start_lt"] = int(txs[-1]["lt"]) + 1
        return out

    @staticmethod
    def _comment(msg: dict) -> str | None:
        dec = (msg.get("message_content") or {}).get("decoded") or {}
        return dec.get("comment") if isinstance(dec, dict) else None

    def refresh(self, txid: str, output_index: int, asset: AssetInfo, address: str) -> TxState:
        h = base64.b64encode(bytes.fromhex(txid)).decode() if len(txid) == 64 else txid
        res = self.v3("/transactions", hash=h, limit=1).get("transactions", [])
        if not res:
            return TxState(found=False)
        seq = res[0].get("mc_block_seqno")
        if not seq:
            return TxState(True, 0)
        return TxState(True, self.mc_head() - int(seq) + 1, int(seq), str(res[0].get("lt")))

    # ── Balances & state ───────────────────────────────────────────────
    def account_state(self, address: str) -> dict:
        info = self._req("GET", "/api/v2/getAddressInformation", params={"address": address}).get("result", {})
        return {"state": info.get("state"), "balance": int(info.get("balance") or 0)}

    def seqno(self, address: str) -> int:
        st = self.account_state(address)
        if st["state"] != "active":
            return 0
        stack = self.run_get(address, "seqno", [])
        v = stack[0][1] if isinstance(stack[0], list) else stack[0]["value"]
        return int(v, 16) if isinstance(v, str) and v.startswith("0x") else int(v)

    def balance(self, address: str, asset: AssetInfo, meta: dict | None = None) -> int:
        if asset.kind == "native":
            return self.account_state(address)["balance"]
        jw = (meta or {}).get("jetton_wallet") or self.jetton_wallet_of(asset.contract, address)
        if self.account_state(jw)["state"] != "active":
            return 0
        stack = self.run_get(jw, "get_wallet_data", [])
        v = stack[0][1] if isinstance(stack[0], list) else stack[0]["value"]
        return int(v, 16) if isinstance(v, str) and v.startswith("0x") else int(v)

    def broadcast(self, raw: str) -> str:
        body = self._req("POST", "/api/v2/sendBocReturnHash", json={"boc": raw})
        res = body.get("result", {})
        return b64_to_hex(res.get("hash")) if isinstance(res, dict) and res.get("hash") else ton_wallet.boc_b64_to_cell(raw).hash.hex()

    def tx_by_message(self, msg_hash_hex: str) -> dict | None:
        h = base64.b64encode(bytes.fromhex(msg_hash_hex)).decode()
        res = self.v3("/transactionsByMessage", msg_hash=h, direction="in", limit=1)
        txs = res.get("transactions", [])
        return txs[0] if txs else None
