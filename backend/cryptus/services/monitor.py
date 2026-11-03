"""Blockchain payment monitor.

One pass per network:
  1. scan       — adapter finds transfers to watched deposit addresses
  2. upsert     — idempotent on (network, txid, output index, asset)
  3. refresh    — re-verify every unsettled or recently confirmed payment
                  against the chain (confirmations, canonical block, amount);
                  payments that disappear are marked orphaned (reorg / RBF)
  4. reconcile  — EVM native: compare on-chain balance at the confirmation
                  depth with attributed payments to catch internal transfers
  5. recompute  — derive invoice states and emit events
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..chains.base import ChainError, Observed, WatchItem
from ..chains.evm import EvmAdapter
from ..chains.registry import adapter_for
from ..core.derivation import evm_gas_station, normalize_address, ton_gas_wallet
from ..core.money import fmt_atomic
from ..core.networks import NETWORKS
from ..db import utcnow
from ..models import Asset, DepositAddress, Invoice, Keychain, NetworkConfig, NetworkWallet, Payment
from .invoices import asset_info, merchant_view, recompute, required_confirmations
from .webhooks import emit

log = logging.getLogger("cryptus.monitor")

HEALTH_EVERY = timedelta(minutes=2)
REORG_WATCH_FACTOR = 3  # keep re-verifying confirmed payments until conf ≥ required × factor


def _own_senders(db: Session, network_key: str) -> set[str]:
    """Addresses whose transfers to deposits are internal (gas top-ups), never customer payments."""
    nw = db.get(NetworkWallet, network_key)
    if not nw:
        return set()
    kc = db.get(Keychain, nw.keychain_id)
    net = NETWORKS[network_key]
    if kc.mode == "direct":
        return set()  # no gas wallet: deposits are the merchant's own wallet
    try:
        if net.family == "evm":
            return {evm_gas_station(kc.public_material).address.lower()}
        if net.family == "ton":
            return {normalize_address("ton", ton_gas_wallet(kc.public_material, net).address)}
    except Exception:  # pragma: no cover - malformed material is rejected at creation
        return set()
    return set()


def watch_items(db: Session, network_key: str) -> list[WatchItem]:
    now = utcnow()
    own = _own_senders(db, network_key)
    deps = db.execute(select(DepositAddress).where(DepositAddress.network == network_key,
                                                   DepositAddress.monitor_until > now)).scalars().all()
    items = []
    for d in deps:
        a = db.get(Asset, d.asset_id)
        meta = {**(d.meta or {}), "memo": d.memo} if d.memo else (d.meta or {})
        items.append(WatchItem(d.id, d.address, d.display_address, asset_info(a), meta, own))
    return items


def _upsert(db: Session, o: Observed, network_key: str, required: int) -> tuple[Payment, bool]:
    p = db.execute(select(Payment).where(Payment.network == network_key, Payment.txid == o.txid,
                                         Payment.output_index == o.output_index,
                                         Payment.asset_id == o.asset_id)).scalar_one_or_none()
    rejected = o.detail.get("rejected")
    if p is None:
        dep = db.get(DepositAddress, o.deposit_id)
        status = "rejected" if rejected else ("confirmed" if o.confirmations >= required else "pending")
        p = Payment(network=network_key, asset_id=o.asset_id, txid=o.txid, output_index=o.output_index,
                    deposit_id=dep.id, invoice_id=dep.invoice_id, amount=str(o.amount), sender=o.sender,
                    block_number=o.block_number, block_hash=o.block_hash, confirmations=o.confirmations,
                    status=status, detail=o.detail, confirmed_at=utcnow() if status == "confirmed" else None)
        db.add(p)
        db.flush()
        return p, True
    if p.status in ("pending", "confirmed"):
        p.confirmations = max(p.confirmations, o.confirmations) if o.block_hash == p.block_hash else o.confirmations
        p.block_number, p.block_hash = o.block_number, o.block_hash
    elif p.status == "orphaned" and not rejected:
        # Re-included in the canonical chain after a reorg (or re-broadcast): bring it back.
        p.status = "confirmed" if o.confirmations >= required else "pending"
        p.confirmations, p.block_number, p.block_hash = o.confirmations, o.block_number, o.block_hash
        p.confirmed_at = utcnow() if p.status == "confirmed" else None
        p.detail = {**(p.detail or {}), "reincluded_at": utcnow().isoformat()}
        return p, True
    return p, False


def _emit_payment(db: Session, p: Payment, type_: str) -> None:
    inv = db.get(Invoice, p.invoice_id)
    a = db.get(Asset, p.asset_id)
    emit(db, type_, {"payment": {"id": p.id, "txid": p.txid, "network": p.network, "asset": p.asset_id,
                                 "amount": fmt_atomic(p.amount, a.decimals), "confirmations": p.confirmations,
                                 "status": p.status}, "invoice": merchant_view(db, inv)},
         invoice_id=inv.id, mode=inv.mode)


def _refresh(db: Session, adapter, network_key: str, required: int) -> set[str]:
    touched: set[str] = set()
    rows = db.execute(select(Payment).where(Payment.network == network_key,
                                            Payment.status.in_(("pending", "confirmed")),
                                            Payment.confirmations < required * REORG_WATCH_FACTOR)).scalars().all()
    for p in rows:
        a = db.get(Asset, p.asset_id)
        dep = db.get(DepositAddress, p.deposit_id)
        try:
            st = adapter.refresh(p.txid, p.output_index, asset_info(a), dep.address)
        except ChainError as exc:
            log.warning("%s refresh %s failed: %s", network_key, p.txid, exc)
            continue
        before = p.status
        if not st.found:
            # Unconfirmed tx evicted / replaced, or block reorganised away.
            age = utcnow() - p.first_seen_at
            if st.failed or p.block_hash or age > timedelta(hours=6):
                p.status = "orphaned"
        elif st.failed or (st.amount is not None and st.amount != int(p.amount)):
            p.status = "rejected"
            p.detail = {**p.detail, "rejected": "chain re-verification failed"}
        else:
            p.confirmations = st.confirmations
            p.block_number, p.block_hash = st.block_number, st.block_hash or p.block_hash
            p.status = "confirmed" if st.confirmations >= required else "pending"
            if p.status == "confirmed" and not p.confirmed_at:
                p.confirmed_at = utcnow()
        if p.status != before:
            touched.add(p.invoice_id)
            if p.status == "confirmed":
                _emit_payment(db, p, "payment.confirmed")
            elif p.status in ("orphaned", "rejected"):
                _emit_payment(db, p, "payment.orphaned")
        elif p.status == "pending":
            touched.add(p.invoice_id)
    return touched


def _reconcile_evm_native(db: Session, adapter: EvmAdapter, network_key: str, required: int, head: int,
                          scanned_to: int | None) -> set[str]:
    touched: set[str] = set()
    safe_block = head - required + 1
    # Only reconcile blocks the transaction scan has already covered; otherwise a payment the scan has not
    # reached yet would be credited here *and* again when the scan finds it.
    if safe_block <= 0 or scanned_to is None or scanned_to < safe_block:
        return touched
    deps = db.execute(select(DepositAddress).join(Invoice, Invoice.id == DepositAddress.invoice_id).where(
        DepositAddress.network == network_key, DepositAddress.asset_id == f"{network_key}:native",
        DepositAddress.monitor_until > utcnow(), DepositAddress.swept_at.is_(None),
        Invoice.status.in_(("pending", "processing", "expired", "underpaid")))).scalars().all()
    for d in deps:
        pays = db.execute(select(Payment).where(Payment.deposit_id == d.id,
                                                Payment.status.in_(("pending", "confirmed")))).scalars().all()
        if any(p.status == "pending" for p in pays):
            continue  # let explicit detection settle first
        try:
            bal = adapter.balance(d.address, asset_info(db.get(Asset, d.asset_id)), block=safe_block)
        except ChainError:
            continue
        delta = bal - sum(int(p.amount) for p in pays)
        if delta > 0:
            p = Payment(network=network_key, asset_id=d.asset_id, txid=f"balance:{d.address}:{safe_block}",
                        output_index=0, deposit_id=d.id, invoice_id=d.invoice_id, amount=str(delta),
                        block_number=safe_block, confirmations=required, status="confirmed",
                        source="balance_reconciliation", confirmed_at=utcnow(),
                        detail={"note": "value received without a top-level transfer (e.g. contract wallet)"})
            db.add(p)
            db.flush()
            _emit_payment(db, p, "payment.confirmed")
            touched.add(d.invoice_id)
    return touched


def check_health(db: Session, nc: NetworkConfig, force: bool = False) -> dict:
    if not force and nc.health_checked_at and utcnow() - nc.health_checked_at < HEALTH_EVERY:
        return nc.health
    try:
        h = adapter_for(db, nc.network).health().as_dict()
    except ChainError as exc:
        h = {"ok": False, "error": str(exc)}
    nc.health, nc.health_checked_at = h, utcnow()
    return h


def run_network(db: Session, network_key: str) -> dict:
    nc = db.get(NetworkConfig, network_key)
    if not nc or not nc.enabled or not nc.endpoint_url:
        return {"skipped": "not enabled"}
    health = check_health(db, nc)
    if not health.get("ok"):
        return {"skipped": "unhealthy", "error": health.get("error")}
    adapter = adapter_for(db, network_key)
    required = required_confirmations(db, network_key)
    items = watch_items(db, network_key)
    touched: set[str] = set()
    head = None
    if items:
        res = adapter.scan(items)
        head = res.head
        nc.scan_cursor = res.cursor
        for dep_id, meta in res.deposit_meta.items():
            dep = db.get(DepositAddress, dep_id)
            dep.meta, dep.prepared = {**(dep.meta or {}), **meta}, True
        for o in res.observed:
            p, new = _upsert(db, o, network_key, required)
            touched.add(p.invoice_id)
            if new and p.status != "rejected":
                _emit_payment(db, p, "payment.detected")
                if p.status == "confirmed":
                    _emit_payment(db, p, "payment.confirmed")
    else:
        # Nothing to watch — keep the cursor near the tip so a new invoice does not trigger a long backfill.
        if NETWORKS[network_key].family == "evm":
            nc.scan_cursor = {"block": max(0, adapter.head() - 1)}
        elif NETWORKS[network_key].family == "bitcoin" and nc.backend == "bitcoin_core":
            pass
    touched |= _refresh(db, adapter, network_key, required)
    if isinstance(adapter, EvmAdapter) and head is not None:
        touched |= _reconcile_evm_native(db, adapter, network_key, required, head,
                                         (nc.scan_cursor or {}).get("block"))
    for inv_id in touched:
        inv = db.get(Invoice, inv_id)
        if inv:
            recompute(db, inv)
    return {"watched": len(items), "touched": len(touched), "head": head}


def expire_due(db: Session) -> int:
    n = 0
    for inv in db.execute(select(Invoice).where(Invoice.status.in_(("pending", "processing")),
                                                Invoice.expires_at < utcnow())).scalars():
        if recompute(db, inv):
            n += 1
    return n
