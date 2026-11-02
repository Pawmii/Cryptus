"""Bitcoin adapter.

Backends
  bitcoin_core  Bitcoin Core JSON-RPC with a dedicated *watch-only descriptor
                wallet* (no private keys). Each deposit address is imported as
                an ``addr()`` descriptor at allocation time. Reorgs are handled
                through ``listsinceblock``'s ``removed`` set and per-tx
                re-verification with ``gettransaction``.
  esplora       Esplora REST (self-hosted electrs/mempool, or a public instance).

Consolidation builds a BIP174 PSBT that carries full BIP32 derivation info, so
it can be signed by the CRYPTUS signer *or* by any hardware/offline wallet
(Sparrow, Coldcard, Trezor, Ledger…) in watch-only mode.
"""
from __future__ import annotations

import json
import time
from decimal import Decimal

import httpx
from embit import bip32, script
from embit.networks import NETWORKS as BTC_NETWORKS
from embit.psbt import PSBT, DerivationPath
from embit.transaction import Transaction, TransactionInput, TransactionOutput

from .base import AssetInfo, ChainAdapter, ChainError, Health, Observed, ScanResult, TxState, WatchItem

GENESIS = {
    "main": {"000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f"},
    "test": {
        "000000000933ea01ad0ee984209779baaec3ced90fa3f408719526f8d77f4943",  # testnet3
        "00000000da84f2bafbbc53dee25a72ae507ff4914b867c565be350b0da8bf043",  # testnet4
        "00000008819873e925422c1ff0f99f7cc9bbb232af63a077a480a3633bee1ef6",  # signet
    },
}
CORE_CHAINS = {"main": {"main"}, "test": {"test", "testnet4", "signet"}}

SATS = Decimal(100_000_000)
DUST_P2WPKH = 294


def btc_to_sats(v) -> int:
    d = Decimal(str(v)) * SATS
    if d != d.to_integral_value():
        raise ChainError(f"non-integral satoshi amount {v}")
    return int(d)


def estimate_vsize(n_inputs: int, n_outputs: int = 1) -> int:
    # P2WPKH inputs (68 vB each incl. witness discount); outputs sized for P2TR/P2WSH (43 vB) — conservative.
    return 11 + 68 * n_inputs + 43 * n_outputs


class _CoreRPC:
    def __init__(self, http: httpx.Client, url: str, user: str | None, password: str | None, wallet: str):
        self.http, self.url, self.wallet = http, url.rstrip("/"), wallet
        self.auth = (user, password) if user else None

    def call(self, method: str, *params, wallet: bool = False):
        url = f"{self.url}/wallet/{self.wallet}" if wallet else self.url
        try:
            r = self.http.post(url, auth=self.auth, json={"jsonrpc": "1.0", "id": "cryptus", "method": method,
                                                          "params": list(params)})
        except httpx.HTTPError as exc:
            raise ChainError(f"bitcoind unreachable: {exc}") from exc
        if r.status_code == 401:
            raise ChainError("bitcoind rejected credentials")
        try:
            body = json.loads(r.text, parse_float=Decimal)
        except ValueError as exc:
            raise ChainError(f"bitcoind returned HTTP {r.status_code}") from exc
        if body.get("error"):
            err = body["error"]
            raise ChainError(f"bitcoind {method}: {err.get('message')} ({err.get('code')})")
        return body["result"]


class BitcoinAdapter(ChainAdapter):
    family = "bitcoin"
    backends = ("bitcoin_core", "esplora")

    def __init__(self, network, conf, http=None):
        super().__init__(network, conf, http)
        self.btc_net = BTC_NETWORKS[network.btc_network or "main"]
        if conf.backend == "bitcoin_core":
            self.rpc = _CoreRPC(self.http, conf.url, conf.credentials.get("username"),
                                conf.credentials.get("password"), conf.options.get("wallet", "cryptus-watch"))
        else:
            self.base = conf.url.rstrip("/")

    # ── Esplora plumbing ───────────────────────────────────────────────
    def _get(self, path: str, *, text: bool = False):
        try:
            r = self.http.get(self.base + path)
        except httpx.HTTPError as exc:
            raise ChainError(f"esplora unreachable: {exc}") from exc
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise ChainError(f"esplora {path}: HTTP {r.status_code}")
        return r.text if text else r.json()

    # ── Health ─────────────────────────────────────────────────────────
    def health(self) -> Health:
        try:
            if self.conf.backend == "bitcoin_core":
                info, ms = self._timed(self.rpc.call, "getblockchaininfo")
                if info["chain"] not in CORE_CHAINS["main" if self.network.btc_network == "main" else "test"]:
                    return Health(False, error=f"node is on chain {info['chain']!r}, expected {self.network.key}")
                self._ensure_wallet()
                return Health(not info.get("initialblockdownload", False), info["blocks"], ms,
                              {"chain": info["chain"], "headers": info["headers"],
                               "verification_progress": float(info["verificationprogress"]),
                               "pruned": info.get("pruned", False)},
                              None if not info.get("initialblockdownload") else "node is in initial block download")
            height, ms = self._timed(self._get, "/blocks/tip/height", text=True)
            genesis = self._get("/block-height/0", text=True)
            if genesis not in GENESIS["main" if self.network.btc_network == "main" else "test"]:
                return Health(False, error="esplora instance serves a different Bitcoin network")
            return Health(True, int(height), ms, {"genesis": genesis})
        except ChainError as exc:
            return Health(False, error=str(exc))

    def _ensure_wallet(self) -> None:
        name = self.rpc.wallet
        loaded = self.rpc.call("listwallets")
        if name in loaded:
            return
        try:
            self.rpc.call("loadwallet", name, True)
        except ChainError as exc:
            if "not found" not in str(exc).lower() and "does not exist" not in str(exc).lower() and "-18" not in str(exc):
                raise
            # watch-only, blank, descriptor wallet; never holds private keys
            self.rpc.call("createwallet", name, True, True, "", False, True, True)

    # ── Observation ────────────────────────────────────────────────────
    def prepare_deposit(self, item: WatchItem) -> dict:
        if self.conf.backend != "bitcoin_core":
            return {}
        self._ensure_wallet()
        desc = self.rpc.call("getdescriptorinfo", f"addr({item.display_address})")["descriptor"]
        # Rescan from shortly before the address was handed out, so a payment made while the node was
        # unreachable (and the import deferred) is still found. Two hours covers block-timestamp skew.
        created = int(item.meta.get("created_ts") or time.time())
        res = self.rpc.call("importdescriptors", [{"desc": desc, "timestamp": max(0, created - 7200),
                                                   "label": item.deposit_id}], wallet=True)
        if not res or not res[0].get("success"):
            raise ChainError(f"importdescriptors failed: {res}")
        return {"core_imported": True}

    def scan(self, items: list[WatchItem]) -> ScanResult:
        if self.conf.backend == "bitcoin_core":
            return self._scan_core(items)
        return self._scan_esplora(items)

    def _scan_core(self, items: list[WatchItem]) -> ScanResult:
        by_addr = {i.address: i for i in items}
        meta: dict[str, dict] = {}
        for i in items:  # self-heal addresses that were allocated while the node was unreachable
            if not i.meta.get("core_imported"):
                meta[i.deposit_id] = self.prepare_deposit(i)
        cursor = self.conf.cursor.get("lastblock", "")
        res = self.rpc.call("listsinceblock", cursor, 1, True, True, wallet=True)
        head = self.rpc.call("getblockcount")
        out: list[Observed] = []
        for tx in res.get("transactions", []):
            if tx.get("category") != "receive":
                continue
            item = by_addr.get(tx.get("address"))
            if item is None:
                continue
            out.append(Observed(item.deposit_id, tx["txid"], int(tx["vout"]), item.asset.id, btc_to_sats(tx["amount"]),
                                None, tx.get("blockheight"), tx.get("blockhash"), max(int(tx["confirmations"]), 0),
                                {"replaceable": tx.get("bip125-replaceable")}))
        return ScanResult(out, {"lastblock": res["lastblock"]}, head, meta)

    def _scan_esplora(self, items: list[WatchItem]) -> ScanResult:
        head = int(self._get("/blocks/tip/height", text=True))
        out: list[Observed] = []
        for item in items:
            txs = self._get(f"/address/{item.display_address}/txs") or []
            for tx in txs:
                st = tx.get("status", {})
                conf = head - st["block_height"] + 1 if st.get("confirmed") else 0
                for n, vout in enumerate(tx.get("vout", [])):
                    if vout.get("scriptpubkey_address") == item.display_address:
                        out.append(Observed(item.deposit_id, tx["txid"], n, item.asset.id, int(vout["value"]), None,
                                            st.get("block_height"), st.get("block_hash"), conf, {}))
        return ScanResult(out, {"height": head}, head)

    def refresh(self, txid: str, output_index: int, asset: AssetInfo, address: str) -> TxState:
        if self.conf.backend == "bitcoin_core":
            try:
                tx = self.rpc.call("gettransaction", txid, True, wallet=True)
            except ChainError as exc:
                if "Invalid or non-wallet transaction id" in str(exc):
                    return TxState(found=False)
                raise
            c = int(tx["confirmations"])
            if c < 0:  # conflicted: double-spent / replaced
                return TxState(found=False, failed=True)
            amount = None
            for d in tx.get("details", []):
                if d.get("vout") == output_index and d.get("category") == "receive":
                    amount = btc_to_sats(d["amount"])
            return TxState(True, c, tx.get("blockheight"), tx.get("blockhash"), amount=amount)
        status = self._get(f"/tx/{txid}/status")
        if status is None:
            return TxState(found=False)
        tx = self._get(f"/tx/{txid}")
        amount = None
        if tx and output_index < len(tx.get("vout", [])):
            v = tx["vout"][output_index]
            if v.get("scriptpubkey_address") == address:
                amount = int(v["value"])
        if not status.get("confirmed"):
            return TxState(True, 0, amount=amount)
        head = int(self._get("/blocks/tip/height", text=True))
        return TxState(True, head - status["block_height"] + 1, status["block_height"], status["block_hash"],
                       amount=amount)

    # ── UTXOs & balances (read from the chain, never from our database) ──
    def utxos(self, addresses: list[str], min_conf: int) -> list[dict]:
        if self.conf.backend == "bitcoin_core":
            rows = self.rpc.call("listunspent", min_conf, 9_999_999, addresses, True, wallet=True)
            return [{"txid": u["txid"], "vout": u["vout"], "value": btc_to_sats(u["amount"]),
                     "address": u["address"], "confirmations": u["confirmations"]} for u in rows]
        head = int(self._get("/blocks/tip/height", text=True))
        out = []
        for a in addresses:
            for u in self._get(f"/address/{a}/utxo") or []:
                st = u.get("status", {})
                conf = head - st["block_height"] + 1 if st.get("confirmed") else 0
                if conf >= min_conf:
                    out.append({"txid": u["txid"], "vout": u["vout"], "value": int(u["value"]), "address": a,
                                "confirmations": conf})
        return out

    def balance(self, address: str, asset: AssetInfo, meta: dict | None = None) -> int:
        return sum(u["value"] for u in self.utxos([address], 0))

    def raw_transaction(self, txid: str) -> str | None:
        if self.conf.backend == "bitcoin_core":
            try:
                return self.rpc.call("gettransaction", txid, True, wallet=True)["hex"]
            except ChainError:
                return None
        return self._get(f"/tx/{txid}/hex", text=True)

    def tx_known(self, txid: str) -> bool:
        """True if the transaction is in the mempool or the chain."""
        if self.conf.backend == "bitcoin_core":
            try:
                self.rpc.call("getmempoolentry", txid)
                return True
            except ChainError:
                return self.raw_transaction(txid) is not None
        return self._get(f"/tx/{txid}/status") is not None

    def feerate_sat_vb(self, target_blocks: int = 6) -> float:
        if self.conf.backend == "bitcoin_core":
            r = self.rpc.call("estimatesmartfee", target_blocks)
            if "feerate" not in r:
                raise ChainError("bitcoind has no fee estimate yet")
            return float(Decimal(r["feerate"]) * SATS / 1000)
        est = self._get("/fee-estimates") or {}
        for t in (str(target_blocks), "6", "3", "2", "1"):
            if t in est:
                return float(est[t])
        raise ChainError("esplora returned no fee estimates")

    def broadcast(self, raw: str) -> str:
        if self.conf.backend == "bitcoin_core":
            return self.rpc.call("sendrawtransaction", raw)
        try:
            r = self.http.post(self.base + "/tx", content=raw)
        except httpx.HTTPError as exc:
            raise ChainError(f"esplora unreachable: {exc}") from exc
        if r.status_code >= 400:
            raise ChainError(f"broadcast rejected: {r.text[:300]}")
        return r.text.strip()

    # ── Consolidation ──────────────────────────────────────────────────
    def build_sweep_psbt(self, *, utxos: list[dict], index_by_address: dict[str, int], xpub: str,
                         master_fingerprint: str, account_path: str, destination: str,
                         feerate: float) -> tuple[str, int, int]:
        """Return (psbt base64, amount sent, fee) — one output, everything to ``destination``."""
        if not utxos:
            raise ChainError("no spendable outputs")
        account = bip32.HDKey.from_string(xpub)
        origin = bip32.parse_path(account_path)
        fp = bytes.fromhex(master_fingerprint)
        total = sum(u["value"] for u in utxos)
        fee = int(estimate_vsize(len(utxos)) * feerate + 0.999)
        amount = total - fee
        if amount <= DUST_P2WPKH:
            raise ChainError("value after fees would be dust")
        dest_spk = script.address_to_scriptpubkey(destination)
        vin = [TransactionInput(bytes.fromhex(u["txid"]), u["vout"], sequence=0xFFFFFFFD) for u in utxos]
        tx = Transaction(vin=vin, vout=[TransactionOutput(amount, dest_spk)])
        psbt = PSBT(tx)
        for i, u in enumerate(utxos):
            idx = index_by_address[u["address"]]
            child = account.derive([0, idx])
            spk = script.p2wpkh(child.key)
            psbt.inputs[i].witness_utxo = TransactionOutput(u["value"], spk)
            raw = self.raw_transaction(u["txid"])
            if raw:
                psbt.inputs[i].non_witness_utxo = Transaction.from_string(raw)
            psbt.inputs[i].bip32_derivations[child.key] = DerivationPath(fp, origin + [0, idx])
        return psbt.to_string(), amount, fee
