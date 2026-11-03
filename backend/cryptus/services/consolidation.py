"""Optional consolidation of deposit funds into the merchant's primary wallets.

Safety rules (enforced here *and* independently by the signer):

  * Never sweep unless the network wallet has a destination that an operator
    explicitly confirmed, and — in managed mode — that the signer's own
    allowlist contains.
  * Never move funds because the database says so: every plan and every
    execution re-reads balances / UTXOs from the chain.
  * Every sweep has a deterministic idempotency key; duplicates are refused
    by a unique constraint.
  * Fees are estimated up front and checked against per-network caps.
  * EVM transactions are simulated with ``eth_call`` before signing.
  * A global emergency stop halts planning, approval, signing and broadcast.
  * Watch-only keychains never sign: sweeps wait for an externally signed
    transaction (PSBT for Bitcoin; unsigned transaction JSON for EVM/TON).
"""
from __future__ import annotations

import base64
import logging
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..chains.base import ChainError
from ..chains.bitcoin import BitcoinAdapter
from ..chains.evm import EvmAdapter
from ..chains.registry import adapter_for
from ..chains.ton import TonAdapter
from ..core import evm_abi, ton_wallet
from ..core.derivation import evm_gas_station, normalize_address, ton_gas_wallet, ton_subwallet_id, \
    TON_DEFAULT_SUBWALLET
from ..core.money import fmt_atomic, to_atomic
from ..core.networks import NETWORKS
from ..db import utcnow
from ..models import Asset, DepositAddress, Invoice, Keychain, NetworkWallet, Payment, Sweep
from . import audit, settings as settings_svc, signer_client
from .invoices import asset_info, required_confirmations
from .webhooks import emit

log = logging.getLogger("cryptus.sweeps")

ACTIVE = ("planned", "awaiting_approval", "approved", "awaiting_external_signature", "signing", "broadcast")
EVM_GAS_NATIVE = 21_000
EVM_GAS_TOKEN_FALLBACK = 100_000
TON_JETTON_ATTACH = 50_000_000     # 0.05 GRAM forwarded to the jetton wallet for the transfer
TON_JETTON_MIN_GAS = 100_000_000   # deposit must hold ≥ 0.1 GRAM before a jetton sweep (deploy + transfer)
TON_NATIVE_MIN = 20_000_000        # do not sweep dust below 0.02 GRAM
MAX_ATTEMPTS = 3


class SweepError(RuntimeError):
    pass


def halted(db: Session) -> bool:
    return bool(settings_svc.get(db, "treasury").get("sweeps_halted"))


def _fee_cap(db: Session, nw: NetworkWallet, net) -> int | None:
    cap = (nw.max_fee or {}).get("native")
    return to_atomic(cap, net.native_decimals) if cap else None


def destination_ok(db: Session, nw: NetworkWallet, kc: Keychain) -> tuple[bool, str]:
    if not nw.destination:
        return False, "no consolidation destination configured"
    if not nw.destination_confirmed_at:
        return False, "destination has not been confirmed by an operator"
    if kc.mode == "managed":
        try:
            allow = signer_client.policy().get("allowlist", {}).get(nw.network, [])
        except signer_client.SignerError as exc:
            return False, f"cannot verify destination against signer policy: {exc}"
        fam = NETWORKS[nw.network].family
        if normalize_address(fam, nw.destination) not in {normalize_address(fam, a) for a in allow}:
            return False, "destination is not on the signer's allowlist"
    return True, ""


def _eligible_deposits(db: Session, network: str) -> list[DepositAddress]:
    """Deposits holding confirmed, not-yet-swept value whose invoice is settled (or past its late window).

    Only deposits with a confirmed payment newer than their last sweep qualify, so unpaid addresses are never
    polled again and value that arrives after a sweep is still collected."""
    now = utcnow()
    busy = set()
    for s in db.execute(select(Sweep).where(Sweep.network == network, Sweep.status.in_(ACTIVE))).scalars():
        busy.update(s.deposit_ids or [])
    last_paid = (select(Payment.deposit_id, func.max(Payment.confirmed_at).label("at"))
                 .where(Payment.network == network, Payment.status == "confirmed")
                 .group_by(Payment.deposit_id).subquery())
    q = (select(DepositAddress, Invoice, last_paid.c.at)
         .join(Invoice, Invoice.id == DepositAddress.invoice_id)
         .join(last_paid, last_paid.c.deposit_id == DepositAddress.id)
         .where(DepositAddress.network == network, DepositAddress.memo == ""))  # memo deposits = merchant's wallet
    out = []
    for d, inv, paid_at in db.execute(q).all():
        if d.id in busy or (d.swept_at and paid_at and paid_at <= d.swept_at):
            continue
        if inv.status == "paid" or (inv.status in ("expired", "underpaid", "cancelled") and d.monitor_until < now):
            out.append(d)
    return out


class _Batch:
    """Collects sweep specs while the chain is queried; rows (and audit entries, which take the audit-chain
    lock) are written only at the end so no lock is held across network calls."""

    def __init__(self, db: Session):
        self.db = db
        self.specs: list[dict] = []

    def add(self, **kw) -> str | None:
        key = kw["idempotency_key"]
        if any(x["idempotency_key"] == key for x in self.specs):
            return key
        if self.db.execute(select(Sweep.id).where(Sweep.idempotency_key == key)).first():
            return key  # already planned earlier; dependants still link to it
        self.specs.append(kw)
        return key

    def commit(self) -> list[Sweep]:
        out: list[Sweep] = []
        for kw in self.specs:
            dep_key = kw.pop("depends_on_key", None)
            dep_id = None
            if dep_key:
                dep_id = self.db.execute(select(Sweep.id).where(Sweep.idempotency_key == dep_key)).scalar_one_or_none()
            s = Sweep(status="planned", depends_on=dep_id, **kw)
            self.db.add(s)
            self.db.flush()
            audit.record(self.db, audit.SYSTEM, "sweep.planned", s.id, network=s.network, asset=s.asset_id,
                         amount=s.amount, kind=s.kind, destination=s.destination)
            out.append(s)
        return out


# ── Planning ──────────────────────────────────────────────────────────────

def plan(db: Session, network: str, *, force: bool = False) -> list[Sweep]:
    if halted(db):
        return []
    nw = db.get(NetworkWallet, network)
    if not nw or (not nw.sweep_enabled and not force):
        return []
    if not force and nw.last_sweep_run_at and utcnow() - nw.last_sweep_run_at < timedelta(minutes=nw.sweep_interval_minutes):
        return []
    kc = db.get(Keychain, nw.keychain_id)
    if kc.mode == "direct":
        return []  # payments already land in the merchant's own wallet
    ok, why = destination_ok(db, nw, kc)
    if not ok:
        log.info("%s: sweep skipped — %s", network, why)
        return []
    nw.last_sweep_run_at = utcnow()
    deps = _eligible_deposits(db, network)
    if not deps:
        return []
    adapter = adapter_for(db, network)
    batch = _Batch(db)
    fam = NETWORKS[network].family
    if fam == "bitcoin":
        _plan_btc(db, batch, adapter, nw, kc, deps)
    elif fam == "evm":
        _plan_evm(db, batch, adapter, nw, kc, deps)
    elif fam == "ton":
        _plan_ton(db, batch, adapter, nw, kc, deps)
    return batch.commit()


def _threshold(nw: NetworkWallet, asset: Asset) -> int:
    t = (nw.sweep_thresholds or {}).get(asset.id)
    return to_atomic(t, asset.decimals) if t else 1


def _plan_btc(db, batch: _Batch, adapter: BitcoinAdapter, nw, kc, deps) -> None:
    net = NETWORKS[nw.network]
    asset = db.get(Asset, f"{nw.network}:native")
    idx = {d.display_address: d.derivation_index for d in deps}
    by_addr = {d.display_address: d for d in deps}
    utxos = adapter.utxos(list(idx), required_confirmations(db, nw.network))
    if not utxos or sum(u["value"] for u in utxos) < _threshold(nw, asset):
        return
    feerate = adapter.feerate_sat_vb(int(nw.max_fee.get("target_blocks", 6)) if nw.max_fee else 6)
    if not kc.master_fingerprint or not kc.account_path:
        raise SweepError("keychain lacks master fingerprint / account path required for PSBT signing")
    psbt, amount, fee = adapter.build_sweep_psbt(utxos=utxos, index_by_address=idx, xpub=kc.public_material,
                                                 master_fingerprint=kc.master_fingerprint,
                                                 account_path=kc.account_path, destination=nw.destination,
                                                 feerate=feerate)
    cap = _fee_cap(db, nw, net)
    if cap is not None and fee > cap:
        log.warning("%s: sweep fee %s exceeds cap %s", nw.network, fee, cap)
        return
    used = sorted({by_addr[u["address"]].id for u in utxos})
    key = f"{nw.network}:btc:" + ",".join(sorted(f"{u['txid']}:{u['vout']}" for u in utxos))
    batch.add(network=nw.network, asset_id=asset.id, kind="sweep", deposit_ids=used, destination=nw.destination,
              amount=str(amount), fee_estimate=str(fee), idempotency_key=key,
              artifact={"psbt": psbt, "feerate_sat_vb": feerate, "inputs": len(utxos)})


def _plan_evm(db, batch: _Batch, adapter: EvmAdapter, nw, kc, deps) -> None:
    net = NETWORKS[nw.network]
    fees = adapter.fee_params()
    price = fees.get("maxFeePerGas") or fees["gasPrice"]
    cap = _fee_cap(db, nw, net)
    native_asset = db.get(Asset, f"{nw.network}:native")
    gas_station = evm_gas_station(kc.public_material).address
    for d in deps:
        asset = db.get(Asset, d.asset_id)
        if asset.kind == "native":
            bal = adapter.balance(d.address, asset_info(asset))
            cost = EVM_GAS_NATIVE * price
            amount = bal - cost
            if amount <= 0 or bal < _threshold(nw, asset) or (cap is not None and cost > cap):
                continue
            tx = {"from": d.display_address, "to": nw.destination, "value": amount, "data": "0x",
                  "gas": EVM_GAS_NATIVE, "chainId": net.chain_id, **fees}
            batch.add(network=nw.network, asset_id=asset.id, kind="sweep", deposit_ids=[d.id],
                      source_address=d.display_address, destination=nw.destination, amount=str(amount),
                      fee_estimate=str(cost), idempotency_key=f"{nw.network}:native:{d.id}:{bal}",
                      artifact={"tx": tx, "role": "deposit", "index": d.derivation_index,
                                "intent": {"type": "sweep_native", "destination": nw.destination, "amount": amount}})
            continue
        bal = adapter.balance(d.address, asset_info(asset))
        if bal <= 0 or bal < _threshold(nw, asset):
            continue
        data = evm_abi.encode_transfer(nw.destination, bal)
        try:
            gas = int(adapter.estimate_gas({"from": d.display_address, "to": asset.contract, "data": data}) * 1.25)
        except ChainError:
            gas = EVM_GAS_TOKEN_FALLBACK
        cost = gas * price
        if cap is not None and cost > cap:
            continue
        have = adapter.balance(d.address, asset_info(native_asset))
        topup_key = None
        if have < cost:
            need = cost - have
            topup_key = batch.add(
                network=nw.network, asset_id=native_asset.id, kind="gas_topup", deposit_ids=[d.id],
                source_address=gas_station, destination=d.display_address, amount=str(need),
                fee_estimate=str(EVM_GAS_NATIVE * price), idempotency_key=f"{nw.network}:gas:{d.id}:{bal}:{have}",
                artifact={"tx": {"from": gas_station, "to": d.display_address, "value": need, "data": "0x",
                                 "gas": EVM_GAS_NATIVE, "chainId": net.chain_id, **fees},
                          "role": "gas_station", "index": 0,
                          "intent": {"type": "gas_topup", "recipient_index": d.derivation_index, "amount": need}})
        batch.add(network=nw.network, asset_id=asset.id, kind="sweep", deposit_ids=[d.id],
                  source_address=d.display_address, destination=nw.destination, amount=str(bal),
                  fee_estimate=str(cost), idempotency_key=f"{nw.network}:token:{d.id}:{bal}", depends_on_key=topup_key,
                  artifact={"tx": {"from": d.display_address, "to": asset.contract, "value": 0, "data": data,
                                   "gas": gas, "chainId": net.chain_id},
                            "role": "deposit", "index": d.derivation_index,
                            "intent": {"type": "sweep_token", "token": asset.contract,
                                       "destination": nw.destination, "amount": bal}})


def _plan_ton(db, batch: _Batch, adapter: TonAdapter, nw, kc, deps) -> None:
    net = NETWORKS[nw.network]
    gas = ton_gas_wallet(kc.public_material, net)
    for d in deps:
        asset = db.get(Asset, d.asset_id)
        st = adapter.account_state(d.display_address)
        if asset.kind == "native":
            if st["balance"] < max(TON_NATIVE_MIN, _threshold(nw, asset)):
                continue
            batch.add(network=nw.network, asset_id=asset.id, kind="sweep", deposit_ids=[d.id],
                      source_address=d.display_address, destination=nw.destination, amount=str(st["balance"]),
                      fee_estimate="10000000", idempotency_key=f"{nw.network}:native:{d.id}:{st['balance']}",
                      artifact={"role": "deposit", "index": d.derivation_index,
                                "subwallet_id": ton_subwallet_id(d.derivation_index),
                                "intent": {"type": "sweep_native", "destination": nw.destination}})
            continue
        bal = adapter.balance(d.display_address, asset_info(asset), d.meta)
        if bal <= 0 or bal < _threshold(nw, asset):
            continue
        jw = (d.meta or {}).get("jetton_wallet")
        if not jw or not (d.meta or {}).get("jetton_wallet_verified"):
            log.warning("TON deposit %s: jetton wallet not verified — not sweeping", d.id)
            continue
        topup_key = None
        if st["balance"] < TON_JETTON_MIN_GAS:
            need = TON_JETTON_MIN_GAS - st["balance"]
            topup_key = batch.add(
                network=nw.network, asset_id=f"{nw.network}:native", kind="gas_topup", deposit_ids=[d.id],
                source_address=gas.address, destination=d.display_address, amount=str(need), fee_estimate="5000000",
                idempotency_key=f"{nw.network}:gas:{d.id}:{bal}:{st['balance']}",
                artifact={"role": "gas_wallet", "subwallet_id": TON_DEFAULT_SUBWALLET,
                          "intent": {"type": "gas_topup", "recipient_index": d.derivation_index, "amount": need}})
        batch.add(network=nw.network, asset_id=asset.id, kind="sweep", deposit_ids=[d.id],
                  source_address=d.display_address, destination=nw.destination, amount=str(bal),
                  fee_estimate=str(TON_JETTON_ATTACH), idempotency_key=f"{nw.network}:jetton:{d.id}:{bal}",
                  depends_on_key=topup_key,
                  artifact={"role": "deposit", "index": d.derivation_index,
                            "subwallet_id": ton_subwallet_id(d.derivation_index),
                            "intent": {"type": "sweep_jetton", "jetton_wallet": jw, "destination": nw.destination,
                                       "amount": bal, "attach": TON_JETTON_ATTACH}})


# ── Execution ─────────────────────────────────────────────────────────────

def approve(db: Session, s: Sweep, actor: audit.Actor) -> None:
    if halted(db):
        raise SweepError("treasury emergency stop is active")
    if s.status not in ("planned", "awaiting_approval"):
        raise SweepError(f"sweep is {s.status}")
    s.status, s.approved_by = "approved", actor.id
    audit.record(db, actor, "sweep.approved", s.id, amount=s.amount, destination=s.destination)


def cancel(db: Session, s: Sweep, actor: audit.Actor, reason: str = "cancelled by operator") -> None:
    if s.status not in ("planned", "awaiting_approval", "approved", "awaiting_external_signature"):
        raise SweepError(f"sweep is {s.status} and cannot be cancelled")
    s.status, s.error = "cancelled", reason
    audit.record(db, actor, "sweep.cancelled", s.id, reason=reason)


def _verify_still_valid(db: Session, s: Sweep) -> None:
    """Re-read the chain right before signing; refuse if reality diverged from the plan."""
    adapter = adapter_for(db, s.network)
    asset = db.get(Asset, s.asset_id)
    if s.kind == "gas_topup":
        return
    if NETWORKS[s.network].family == "bitcoin":
        from embit.psbt import PSBT
        psbt = PSBT.from_string(s.artifact["psbt"])
        outs = adapter.utxos([d.display_address for d in (db.get(DepositAddress, i) for i in s.deposit_ids)], 0)
        live = {(u["txid"], u["vout"]) for u in outs}
        for inp in psbt.inputs:
            if (inp.txid.hex(), inp.vout) not in live:
                raise SweepError("an input of this sweep is no longer unspent")
        return
    dep = db.get(DepositAddress, s.deposit_ids[0])
    bal = adapter.balance(dep.address if NETWORKS[s.network].family == "evm" else dep.display_address,
                          asset_info(asset), dep.meta)
    if bal < int(s.amount) and not (NETWORKS[s.network].family == "ton" and asset.kind == "native"):
        raise SweepError(f"on-chain balance {bal} is below the planned amount {s.amount}")


def advance(db: Session, s: Sweep) -> None:
    """Move one sweep forward by at most one step."""
    if halted(db) and s.status in ("planned", "approved", "signing", "awaiting_approval"):
        return
    kc = db.get(Keychain, db.get(NetworkWallet, s.network).keychain_id)
    if s.status == "planned":
        s.status = "awaiting_approval" if settings_svc.get(db, "treasury")["require_approval"] else "approved"
        return
    if s.status == "approved":
        if s.depends_on:
            dep = db.get(Sweep, s.depends_on)
            if dep and dep.status != "confirmed":
                if dep.status in ("failed", "cancelled"):
                    s.status, s.error = "cancelled", "gas top-up did not complete"
                return
        try:
            _verify_still_valid(db, s)
        except (SweepError, ChainError) as exc:
            s.status, s.error = "failed", str(exc)
            audit.record(db, audit.SYSTEM, "sweep.invalidated", s.id, error=str(exc))
            return
        if kc.mode != "managed":
            s.status = "awaiting_external_signature"
            s.artifact = {**s.artifact, "instructions": external_instructions(s)}
            return
        _sign_and_broadcast(db, s)
        return
    if s.status == "broadcast":
        _track(db, s)


def _landed_txid(adapter, s: Sweep) -> str | None:
    """If a previous attempt was broadcast but its response was lost, return that transaction's id."""
    prev = (s.artifact or {}).get("last_attempt") or {}
    fam = NETWORKS[s.network].family
    try:
        if fam == "ton":
            if not prev.get("msg_hash"):
                return None
            tx = adapter.tx_by_message(prev["msg_hash"])
            return base64.b64decode(tx["hash"]).hex() if tx else None
        if not prev.get("txid"):
            return None
        if fam == "evm":
            return prev["txid"] if adapter.tx_known(prev["txid"]) else None
        return prev["txid"] if adapter.tx_known(prev["txid"]) else None
    except ChainError:
        return None


def _refresh_evm_fees(db: Session, adapter: EvmAdapter, s: Sweep, tx: dict) -> dict:
    """Re-price an EVM transaction at signing time and re-check the fee cap; recompute native sweep value."""
    nw = db.get(NetworkWallet, s.network)
    net = NETWORKS[s.network]
    fees = adapter.fee_params()
    for k in ("maxFeePerGas", "maxPriorityFeePerGas", "gasPrice"):
        tx.pop(k, None)
    tx.update(fees)
    price = fees.get("maxFeePerGas") or fees["gasPrice"]
    cost = int(tx["gas"]) * price
    cap = _fee_cap(db, nw, net)
    if cap is not None and cost > cap:
        raise SweepError(f"current network fee {cost} exceeds the configured cap {cap}; will retry later")
    intent = dict(s.artifact["intent"])
    if intent["type"] == "sweep_native":
        bal = adapter.balance(tx["from"], asset_info(db.get(Asset, s.asset_id)))
        value = bal - cost
        if value <= 0:
            raise SweepError("balance no longer covers the network fee")
        tx["value"] = value
        intent["amount"] = value
        s.amount = str(value)
    s.fee_estimate = str(cost)
    s.artifact = {**s.artifact, "intent": intent}
    return tx


def _sign_and_broadcast(db: Session, s: Sweep) -> None:
    adapter = adapter_for(db, s.network)
    fam = NETWORKS[s.network].family
    landed = _landed_txid(adapter, s)
    if landed:
        s.txid, s.status = landed, "broadcast"
        audit.record(db, audit.SYSTEM, "sweep.broadcast_recovered", s.id, txid=landed)
        return
    s.status = "signing"
    s.attempts += 1
    db.flush()
    try:
        if fam == "bitcoin":
            res = signer_client.sign(s.network, "btc_psbt", {"psbt": s.artifact["psbt"]}, f"{s.id}:{s.attempts}")
            raw = res["raw"]
        elif fam == "evm":
            tx = _refresh_evm_fees(db, adapter, s, dict(s.artifact["tx"]))
            tx["nonce"] = adapter.nonce(tx["from"])
            adapter.simulate(tx)
            res = signer_client.sign(s.network, "evm_tx", {"tx": tx, "role": s.artifact["role"],
                                                           "index": s.artifact["index"],
                                                           "intent": s.artifact["intent"]}, f"{s.id}:{s.attempts}")
            raw = res["raw"]
        else:
            seqno = adapter.seqno(s.source_address)
            res = signer_client.sign(s.network, "ton_transfer", {"role": s.artifact["role"],
                                                                 "index": s.artifact.get("index"),
                                                                 "seqno": seqno, "intent": s.artifact["intent"]},
                                     f"{s.id}:{s.attempts}")
            raw = res["boc"]
            s.artifact = {**s.artifact, "msg_hash": res["msg_hash"]}
        # Remember what we are about to broadcast, so a lost response is recognised on the next attempt.
        s.artifact = {**s.artifact, "last_attempt": {"txid": res.get("txid"), "msg_hash": res.get("msg_hash")}}
        db.flush()
        txid = adapter.broadcast(raw)
        s.txid = res.get("txid") or txid
        s.status = "broadcast"
        audit.record(db, audit.SYSTEM, "sweep.broadcast", s.id, txid=s.txid)
        emit(db, "sweep.broadcast", sweep_view(db, s))
    except (signer_client.SignerError, ChainError, SweepError, KeyError) as exc:
        s.error = str(exc)[:500]
        s.status = "failed" if s.attempts >= MAX_ATTEMPTS else "approved"
        s.next_attempt_at = utcnow() + timedelta(minutes=5 * s.attempts)
        audit.record(db, audit.SYSTEM, "sweep.sign_failed", s.id, error=s.error, attempt=s.attempts)
        if s.status == "failed":
            emit(db, "sweep.failed", sweep_view(db, s))


def submit_external(db: Session, s: Sweep, signed: str, actor: audit.Actor) -> None:
    """Broadcast a transaction signed outside CRYPTUS (watch-only mode)."""
    if halted(db):
        raise SweepError("treasury emergency stop is active")
    if s.status != "awaiting_external_signature":
        raise SweepError("sweep is not waiting for an external signature")
    adapter = adapter_for(db, s.network)
    fam = NETWORKS[s.network].family
    signed = signed.strip()
    if fam == "bitcoin":
        from embit.psbt import PSBT
        from embit.transaction import Transaction
        try:
            if signed.startswith("cHNid"):  # base64 PSBT
                psbt = PSBT.from_string(signed)
                from embit.finalizer import finalize_psbt
                tx = finalize_psbt(psbt)
                if tx is None:
                    raise SweepError("PSBT is not fully signed")
            else:
                tx = Transaction.from_string(signed)
        except SweepError:
            raise
        except Exception as exc:
            raise SweepError(f"could not parse signed transaction: {exc}") from exc
        planned = PSBT.from_string(s.artifact["psbt"])
        if [o.script_pubkey.data for o in tx.vout] != [o.script_pubkey.data for o in planned.tx.vout] or \
                [o.value for o in tx.vout] != [o.value for o in planned.tx.vout]:
            raise SweepError("signed transaction outputs differ from the planned sweep")
        if sorted((i.txid, i.vout) for i in tx.vin) != sorted((i.txid, i.vout) for i in planned.tx.vin):
            raise SweepError("signed transaction spends different inputs than the planned sweep")
        raw = tx.serialize().hex()
    elif fam == "ton":
        cell = ton_wallet.boc_b64_to_cell(signed)
        s.artifact = {**s.artifact, "msg_hash": cell.hash.hex()}
        raw = signed
    else:
        raw = signed
        from eth_account import Account
        try:
            decoded = Account.recover_transaction(raw)
        except Exception as exc:
            raise SweepError(f"could not decode signed transaction: {exc}") from exc
        if decoded.lower() != s.artifact["tx"]["from"].lower():
            raise SweepError("transaction is not signed by the deposit/gas address of this sweep")
        try:
            got = evm_abi.decode_raw_transaction(raw)
        except ValueError as exc:
            raise SweepError(str(exc)) from exc
        plan_tx = s.artifact["tx"]
        if got["chain_id"] != NETWORKS[s.network].chain_id:
            raise SweepError("signed transaction targets a different chain")
        if (got["to"] or "").lower() != plan_tx["to"].lower():
            raise SweepError("signed transaction pays a different recipient than the planned sweep")
        if got["data"].lower().removeprefix("0x") != (plan_tx.get("data") or "0x").lower().removeprefix("0x"):
            raise SweepError("signed transaction calldata differs from the planned sweep")
        if s.artifact["intent"]["type"] == "sweep_native":
            if got["value"] > int(plan_tx["value"]) or got["value"] <= 0:
                raise SweepError("signed transaction moves more value than planned")
        elif got["value"] != int(plan_tx.get("value", 0)):
            raise SweepError("signed transaction value differs from the planned sweep")
    s.txid = adapter.broadcast(raw)
    s.status = "broadcast"
    audit.record(db, actor, "sweep.external_broadcast", s.id, txid=s.txid)
    emit(db, "sweep.broadcast", sweep_view(db, s))


def _track(db: Session, s: Sweep) -> None:
    adapter = adapter_for(db, s.network)
    fam = NETWORKS[s.network].family
    asset = db.get(Asset, s.asset_id)
    required = required_confirmations(db, s.network)
    if fam == "ton":
        tx = adapter.tx_by_message(s.artifact.get("msg_hash", "")) if s.artifact.get("msg_hash") else None
        if tx is None:
            if utcnow() - s.updated_at > timedelta(minutes=15):
                s.status, s.error = "failed", "external message was not executed (expired)"
                emit(db, "sweep.failed", sweep_view(db, s))
            return
        d = tx.get("description", {})
        if d.get("aborted") and (d.get("compute_ph") or {}).get("success") is False:
            s.status, s.error = "failed", "wallet rejected the message"
            emit(db, "sweep.failed", sweep_view(db, s))
            return
        s.txid = base64.b64decode(tx["hash"]).hex() if tx.get("hash") else s.txid
        ok = True
    else:
        if fam == "evm":
            st = adapter.receipt_state(s.txid)
        else:
            st = adapter.refresh(s.txid, -1, asset_info(asset), (s.destination or "").lower())
        if not st.found:
            if utcnow() - s.updated_at > timedelta(hours=3):
                s.status, s.error = "failed", "transaction not found on chain"
                emit(db, "sweep.failed", sweep_view(db, s))
            return
        if st.failed:
            s.status, s.error = "failed", "transaction reverted"
            emit(db, "sweep.failed", sweep_view(db, s))
            return
        ok = st.confirmations >= required
    if ok:
        s.status = "confirmed"
        if s.kind == "sweep":
            for dep_id in s.deposit_ids:
                dep = db.get(DepositAddress, dep_id)
                if dep:
                    dep.swept_at = utcnow()
        audit.record(db, audit.SYSTEM, "sweep.confirmed", s.id, txid=s.txid)
        emit(db, "sweep.confirmed", sweep_view(db, s))


def run(db: Session) -> None:
    from ..models import NetworkConfig
    for nc in db.execute(select(NetworkConfig).where(NetworkConfig.enabled.is_(True))).scalars().all():
        try:
            plan(db, nc.network)
            db.commit()
        except (ChainError, SweepError, signer_client.SignerError) as exc:
            db.rollback()
            log.warning("%s: sweep planning failed: %s", nc.network, exc)
    now = utcnow()
    for s in db.execute(select(Sweep).where(Sweep.status.in_(("planned", "approved", "broadcast")))).scalars().all():
        if s.next_attempt_at and s.next_attempt_at > now:
            continue
        try:
            advance(db, s)
            db.commit()
        except (ChainError, SweepError) as exc:
            db.rollback()
            log.warning("sweep %s: %s", s.id, exc)


def external_instructions(s: Sweep) -> str:
    fam = NETWORKS[s.network].family
    if fam == "bitcoin":
        return ("Load the PSBT into your hardware or offline wallet (Sparrow, Coldcard, Trezor, Ledger, or "
                "`cryptus-signer offline sign-psbt`), verify the single output pays your consolidation address, "
                "sign, and paste the signed PSBT or raw transaction back here.")
    if fam == "evm":
        return ("Sign the transaction below with the key for the shown derivation path (for example "
                "`cryptus-signer offline sign-evm`). Set the nonce for the source address, then paste the raw "
                "signed transaction (0x…) back here.")
    return ("Sign with `cryptus-signer offline sign-ton` using your TON key; the tool uses the subwallet id shown. "
            "Paste the resulting external message BOC (base64) back here.")


def sweep_view(db: Session, s: Sweep) -> dict:
    asset = db.get(Asset, s.asset_id)
    net = NETWORKS[s.network]
    dec = asset.decimals if asset else 0
    return {
        "id": s.id, "network": s.network, "network_name": net.name, "asset": s.asset_id,
        "symbol": asset.symbol if asset else "?", "kind": s.kind, "status": s.status,
        "amount": fmt_atomic(s.amount, dec), "fee_estimate": fmt_atomic(s.fee_estimate or 0, net.native_decimals),
        "fee_symbol": net.native_symbol, "source": s.source_address, "destination": s.destination,
        "deposits": len(s.deposit_ids or []), "txid": s.txid, "error": s.error, "attempts": s.attempts,
        "depends_on": s.depends_on, "artifact": s.artifact, "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
        "explorer_url": net.explorer_tx.format(txid=s.txid) if s.txid and net.explorer_tx else None,
    }
