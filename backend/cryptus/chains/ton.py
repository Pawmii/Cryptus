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

import httpx
from pytoniq_core import Address, Cell, begin_cell

from ..core import ton_wallet
from .base import AssetInfo, ChainAdapter, ChainError, Health, Observed, ScanResult, TxState, WatchItem

MAINNET_GLOBAL_ID = -239
TESTNET_GLOBAL_ID = -3


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


def _stack_cell(entry) -> Cell:
    """Parse a cell/slice stack entry from either Toncenter v2 or v3 response formats."""
    if isinstance(entry, dict):  # v3: {"type": "cell"|"slice", "value": b64}
        return Cell.one_from_boc(base64.b64decode(entry["value"]))
    kind, val = entry[0], entry[1]
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
        try:
            r = self.http.request(method, self.base + path, headers=self.headers, **kw)
        except httpx.HTTPError as exc:
            raise ChainError(f"toncenter unreachable: {exc}") from exc
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
            raise ChainError(f"get-method {method} exited with {res.get('exit_code')}")
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
        out: list[Observed] = []
        meta: dict[str, dict] = {}
        for item in items:
            if item.asset.kind == "native":
                out += self._scan_native(item, head)
            else:
                m = dict(item.meta)
                if not m.get("jetton_wallet"):
                    m.update(self.prepare_deposit(item))
                    meta[item.deposit_id] = m
                obs = self._scan_jetton(item, m["jetton_wallet"], head)
                if obs and not m.get("jetton_wallet_verified"):
                    ok = self.verify_jetton_wallet(m["jetton_wallet"], item.address, item.asset.contract)
                    if ok is False:
                        for o in obs:
                            o.detail["rejected"] = "jetton wallet owner/master mismatch"
                    elif ok:
                        m["jetton_wallet_verified"] = True
                        meta[item.deposit_id] = m
                out += obs
        return ScanResult(out, {"mc_seqno": head}, head, meta)

    def _scan_native(self, item: WatchItem, head: int) -> list[Observed]:
        out = []
        for tx in self._txs(item.address):
            msg = tx.get("in_msg") or {}
            src = norm(msg.get("source"))
            if not src or msg.get("bounced"):
                continue  # external message or a bounce of our own transfer
            if src in item.exclude_senders:
                continue  # gas top-up from our own gas wallet
            value = int(msg.get("value") or 0)
            if value <= 0:
                continue
            d = tx.get("description", {})
            if d.get("bounce") or (d.get("credit_ph") or {}).get("credit") in ("0", 0):
                continue  # value was bounced back to the sender
            seq = tx.get("mc_block_seqno")
            out.append(Observed(item.deposit_id, b64_to_hex(tx["hash"]), 0, item.asset.id, value, src,
                                seq, str(tx.get("lt")), head - int(seq) + 1 if seq else 0,
                                {"lt": tx.get("lt"), "comment": self._comment(msg)}))
        return out

    def _scan_jetton(self, item: WatchItem, jetton_wallet: str, head: int) -> list[Observed]:
        out = []
        for tx in self._txs(jetton_wallet):
            msg = tx.get("in_msg") or {}
            body_b64 = (msg.get("message_content") or {}).get("body")
            if not body_b64 or not msg.get("source") or msg.get("bounced"):
                continue
            try:
                parsed = ton_wallet.parse_jetton_internal_transfer(Cell.one_from_boc(base64.b64decode(body_b64)))
            except Exception:
                continue
            if parsed is None:
                continue
            if norm(tx.get("account")) != norm(jetton_wallet):
                continue
            d = tx.get("description", {})
            if d.get("aborted") or not (d.get("compute_ph") or {}).get("success") \
                    or (d.get("action") or {}).get("success") is False:
                continue  # transfer failed; no tokens were credited
            if parsed["from"] and norm(parsed["from"]) in item.exclude_senders:
                continue
            seq = tx.get("mc_block_seqno")
            out.append(Observed(item.deposit_id, b64_to_hex(tx["hash"]), 0, item.asset.id, int(parsed["amount"]),
                                parsed["from"], seq, str(tx.get("lt")), head - int(seq) + 1 if seq else 0,
                                {"jetton_wallet": jetton_wallet, "query_id": parsed["query_id"]}))
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
