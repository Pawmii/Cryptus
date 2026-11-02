"""EVM adapter (Ethereum, BNB Smart Chain, Polygon, Arbitrum, Base and testnets).

Detection
  ERC-20   ``eth_getLogs`` for ``Transfer(from, to, value)`` restricted to the
           configured token contracts *and* our deposit addresses as ``to``.
  Native   Full-block scan for top-level transactions to deposit addresses,
           with receipt status verification. Native value delivered by
           contracts (internal transactions) is caught by balance
           reconciliation in the monitor.

Reorgs: every payment stores the block hash it was seen in; ``refresh``
re-fetches the receipt and compares. A missing receipt or a different
canonical block marks the payment orphaned/re-included accordingly.
"""
from __future__ import annotations

import httpx
from eth_utils import to_checksum_address

from ..core import evm_abi
from .base import AssetInfo, ChainAdapter, ChainError, Health, Observed, ScanResult, TxState, WatchItem, chunks


class EvmAdapter(ChainAdapter):
    family = "evm"
    backends = ("jsonrpc",)

    def __init__(self, network, conf, http=None):
        super().__init__(network, conf, http)
        self.url = conf.url
        self.blocks_per_tick = int(conf.options.get("blocks_per_tick", 120))
        self.log_range = int(conf.options.get("max_log_range", 1000))
        self.reorg_rescan = int(conf.options.get("reorg_rescan", min(conf.confirmations, 6)))
        self._id = 0

    # ── JSON-RPC ───────────────────────────────────────────────────────
    def _post(self, payload):
        headers = {}
        if tok := self.conf.credentials.get("bearer"):
            headers["Authorization"] = f"Bearer {tok}"
        try:
            r = self.http.post(self.url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise ChainError(f"rpc unreachable: {exc}") from exc
        if r.status_code >= 400:
            raise ChainError(f"rpc HTTP {r.status_code}: {r.text[:200]}")
        return r.json()

    def call(self, method: str, *params):
        self._id += 1
        body = self._post({"jsonrpc": "2.0", "id": self._id, "method": method, "params": list(params)})
        if "error" in body and body["error"]:
            raise ChainError(f"{method}: {body['error'].get('message')}")
        return body.get("result")

    def batch(self, calls: list[tuple[str, list]]) -> list:
        if not calls:
            return []
        payload = []
        for m, p in calls:
            self._id += 1
            payload.append({"jsonrpc": "2.0", "id": self._id, "method": m, "params": p})
        body = self._post(payload)
        if not isinstance(body, list):
            raise ChainError("rpc does not support batch requests")
        by_id = {x["id"]: x for x in body}
        out = []
        for req in payload:
            res = by_id.get(req["id"])
            if res is None or res.get("error"):
                raise ChainError(f"{req['method']}: {(res or {}).get('error')}")
            out.append(res["result"])
        return out

    def head(self) -> int:
        return int(self.call("eth_blockNumber"), 16)

    # ── Health ─────────────────────────────────────────────────────────
    def health(self) -> Health:
        try:
            cid, ms = self._timed(self.call, "eth_chainId")
            cid = int(cid, 16)
            if cid != self.network.chain_id:
                return Health(False, error=f"endpoint is chain {cid}, expected {self.network.chain_id}")
            h = self.head()
            syncing = self.call("eth_syncing")
            return Health(not syncing, h, ms, {"chain_id": cid}, "node is syncing" if syncing else None)
        except ChainError as exc:
            return Health(False, error=str(exc))

    # ── Scan ───────────────────────────────────────────────────────────
    def scan(self, items: list[WatchItem]) -> ScanResult:
        head = self.head()
        cursor = self.conf.cursor.get("block")
        if cursor is None:
            start = max(0, head - max(self.conf.confirmations * 2, 32))
            end = min(head, start + self.blocks_per_tick - 1)
        else:
            cursor = int(cursor)
            if cursor >= head:
                return ScanResult([], {"block": head}, head)
            end = min(head, cursor + self.blocks_per_tick)
            # Re-read the most recent blocks every pass: a short reorg can put a payment into a replacement
            # block below the cursor. Upserts are idempotent, so overlap is harmless.
            start = max(0, cursor + 1 - self.reorg_rescan)
        observed: list[Observed] = []
        native = {i.address: i for i in items if i.asset.kind == "native"}
        tokens: dict[str, list[WatchItem]] = {}
        for i in items:
            if i.asset.kind == "erc20":
                tokens.setdefault(i.asset.contract.lower(), []).append(i)
        if tokens:
            observed += self._scan_tokens(tokens, start, end, head)
        if native:
            observed += self._scan_native(native, start, end, head)
        return ScanResult(observed, {"block": end}, head)

    def _scan_tokens(self, tokens: dict[str, list[WatchItem]], start: int, end: int, head: int) -> list[Observed]:
        out = []
        by_key = {(c, i.address): i for c, lst in tokens.items() for i in lst}
        all_items = [i for lst in tokens.values() for i in lst]
        contracts = list(tokens)
        for frm in range(start, end + 1, self.log_range):
            to = min(end, frm + self.log_range - 1)
            for grp in chunks(sorted({i.address for i in all_items}), 64):
                logs = self.call("eth_getLogs", {
                    "fromBlock": hex(frm), "toBlock": hex(to), "address": contracts,
                    "topics": [evm_abi.TOPIC_TRANSFER, None, [evm_abi.address_topic(a) for a in grp]],
                }) or []
                for lg in logs:
                    if lg.get("removed") or len(lg.get("topics", [])) != 3:
                        continue
                    contract = lg["address"].lower()
                    recipient = evm_abi.topic_to_address(lg["topics"][2]).lower()
                    item = by_key.get((contract, recipient))
                    if item is None:
                        continue
                    bn = int(lg["blockNumber"], 16)
                    out.append(Observed(item.deposit_id, lg["transactionHash"], int(lg["logIndex"], 16),
                                        item.asset.id, evm_abi.decode_uint(lg["data"]),
                                        evm_abi.topic_to_address(lg["topics"][1]), bn, lg["blockHash"],
                                        head - bn + 1, {"contract": to_checksum_address(contract)}))
        return out

    def _scan_native(self, native: dict[str, WatchItem], start: int, end: int, head: int) -> list[Observed]:
        out = []
        for grp in chunks(list(range(start, end + 1)), 25):
            blocks = self.batch([("eth_getBlockByNumber", [hex(b), True]) for b in grp])
            hits = []
            for blk in blocks:
                if blk is None:
                    raise ChainError("node returned no block inside the requested range")
                for tx in blk["transactions"]:
                    to = (tx.get("to") or "").lower()
                    item = native.get(to)
                    if item is None or int(tx["value"], 16) == 0:
                        continue
                    if tx["from"].lower() in item.exclude_senders:
                        continue
                    hits.append((item, tx, blk))
            if hits:
                receipts = self.batch([("eth_getTransactionReceipt", [tx["hash"]]) for _, tx, _ in hits])
                for (item, tx, blk), rc in zip(hits, receipts):
                    if rc is None or int(rc["status"], 16) != 1:
                        continue
                    bn = int(blk["number"], 16)
                    out.append(Observed(item.deposit_id, tx["hash"], -1, item.asset.id, int(tx["value"], 16),
                                        to_checksum_address(tx["from"]), bn, blk["hash"], head - bn + 1, {}))
        return out

    def refresh(self, txid: str, output_index: int, asset: AssetInfo, address: str) -> TxState:
        if txid.startswith("balance:"):
            return TxState(found=True, confirmations=10**6)  # reconciliation entries are confirmed by construction
        rc = self.call("eth_getTransactionReceipt", txid)
        if rc is None or rc.get("blockNumber") is None:
            return TxState(found=False)
        bn = int(rc["blockNumber"], 16)
        conf = self.head() - bn + 1
        if int(rc["status"], 16) != 1:
            return TxState(True, conf, bn, rc["blockHash"], failed=True)
        amount = None
        if asset.kind == "erc20":
            for lg in rc.get("logs", []):
                if int(lg["logIndex"], 16) == output_index and lg["address"].lower() == asset.contract.lower() \
                        and len(lg["topics"]) == 3 and lg["topics"][0] == evm_abi.TOPIC_TRANSFER \
                        and evm_abi.topic_to_address(lg["topics"][2]).lower() == address:
                    amount = evm_abi.decode_uint(lg["data"])
            if amount is None:
                return TxState(True, conf, bn, rc["blockHash"], failed=True)
        else:
            tx = self.call("eth_getTransactionByHash", txid)
            amount = int(tx["value"], 16) if tx and (tx.get("to") or "").lower() == address else None
        return TxState(True, conf, bn, rc["blockHash"], amount=amount)

    def receipt_state(self, txid: str) -> TxState:
        """Execution status of an arbitrary transaction (used for our own sweeps and top-ups)."""
        rc = self.call("eth_getTransactionReceipt", txid)
        if rc is None or rc.get("blockNumber") is None:
            return TxState(found=False)
        bn = int(rc["blockNumber"], 16)
        return TxState(True, self.head() - bn + 1, bn, rc["blockHash"], failed=int(rc["status"], 16) != 1)

    def tx_known(self, txid: str) -> bool:
        """True if the node has the transaction (mempool or mined)."""
        return self.call("eth_getTransactionByHash", txid) is not None

    # ── Balances ───────────────────────────────────────────────────────
    def balance(self, address: str, asset: AssetInfo, meta: dict | None = None, block: str | int = "latest") -> int:
        tag = hex(block) if isinstance(block, int) else block
        if asset.kind == "native":
            return int(self.call("eth_getBalance", to_checksum_address(address), tag), 16)
        res = self.call("eth_call", {"to": asset.contract, "data": evm_abi.encode_balance_of(address)}, tag)
        return evm_abi.decode_uint(res)

    # ── Token verification ─────────────────────────────────────────────
    def inspect_token(self, contract: str) -> dict:
        code = self.call("eth_getCode", to_checksum_address(contract), "latest")
        if not code or code == "0x":
            raise ChainError("no contract code at this address on this chain")
        decimals = evm_abi.decode_uint(self.call("eth_call", {"to": contract, "data": "0x" + evm_abi.SEL_DECIMALS}, "latest"))
        symbol = evm_abi.decode_string(self.call("eth_call", {"to": contract, "data": "0x" + evm_abi.SEL_SYMBOL}, "latest"))
        return {"decimals": decimals, "symbol": symbol, "code_size": (len(code) - 2) // 2}

    # ── Settlement ─────────────────────────────────────────────────────
    def nonce(self, address: str) -> int:
        return int(self.call("eth_getTransactionCount", to_checksum_address(address), "pending"), 16)

    def fee_params(self) -> dict:
        """EIP-1559 fees where available, legacy gasPrice otherwise."""
        block = self.call("eth_getBlockByNumber", "latest", False)
        base = block.get("baseFeePerGas")
        if base is None or self.conf.options.get("legacy_gas"):
            return {"gasPrice": int(self.call("eth_gasPrice"), 16)}
        try:
            tip = int(self.call("eth_maxPriorityFeePerGas"), 16)
        except ChainError:
            tip = 1_500_000_000
        base = int(base, 16)
        return {"maxFeePerGas": base * 2 + tip, "maxPriorityFeePerGas": tip}

    def estimate_gas(self, tx: dict) -> int:
        return int(self.call("eth_estimateGas", {k: (hex(v) if isinstance(v, int) else v) for k, v in tx.items()}), 16)

    def simulate(self, tx: dict) -> None:
        """eth_call the exact transaction; raises if it would revert."""
        self.call("eth_call", {k: (hex(v) if isinstance(v, int) else v) for k, v in tx.items()
                               if k in ("from", "to", "value", "data", "gas")}, "latest")

    def broadcast(self, raw: str) -> str:
        return self.call("eth_sendRawTransaction", raw if raw.startswith("0x") else "0x" + raw)
