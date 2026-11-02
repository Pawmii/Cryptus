"""Invoice lifecycle: creation, quoting, address allocation and settlement state.

State machine

    pending ──payment seen──▶ processing ──confirmed ≥ due──▶ paid
       │                          │
       └──expiry, nothing seen──▶ expired        expiry, partial confirmed ──▶ underpaid
    pending/processing ──operator──▶ cancelled

Payments arriving after expiry (within the late-payment window) still settle
the invoice and set ``paid_late``. Excess value sets ``overpaid``. Anything
ambiguous (payments to several quotes, payments to cancelled invoices) sets
``needs_review`` — CRYPTUS never silently discards customer funds.
"""
from __future__ import annotations

import hashlib
import json
import logging
import segno
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_CEILING, Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..chains.base import AssetInfo, ChainError, WatchItem
from ..chains.registry import adapter_for
from ..core.derivation import derive_deposit, normalize_address
from ..core.money import AmountError, fmt_atomic, to_atomic
from ..core.networks import NETWORKS, get_network
from ..db import utcnow
from ..models import Asset, DepositAddress, Invoice, InvoiceSequence, Keychain, NetworkConfig, NetworkWallet, Payment
from . import rates as rates_svc
from . import settings as settings_svc
from .webhooks import emit

log = logging.getLogger("cryptus.invoices")

OPEN = ("pending", "processing")
FINAL = ("paid", "expired", "underpaid", "cancelled")
MAX_QUOTES_PER_INVOICE = 8


class InvoiceError(ValueError):
    def __init__(self, message: str, code: str = "invalid_request", status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


# ── Availability ──────────────────────────────────────────────────────────

def available_assets(db: Session, mode: str) -> list[Asset]:
    """Assets that can be quoted right now: enabled, verified, with an enabled network and a verified wallet."""
    env = "mainnet" if mode == "live" else "testnet"
    out = []
    for a in db.execute(select(Asset).where(Asset.enabled.is_(True)).order_by(Asset.network, Asset.kind)).scalars():
        net = NETWORKS.get(a.network)
        if not net or net.status != "implemented" or net.environment != env or not a.verified_at:
            continue
        nc = db.get(NetworkConfig, a.network)
        nw = db.get(NetworkWallet, a.network)
        if not nc or not nc.enabled or not nc.endpoint_url or not nw:
            continue
        kc = db.get(Keychain, nw.keychain_id)
        if not kc or not kc.active or not kc.verified_at:
            continue
        out.append(a)
    return out


# ── Numbering ─────────────────────────────────────────────────────────────

def next_number(db: Session) -> str:
    year = utcnow().year
    res = db.execute(update(InvoiceSequence).where(InvoiceSequence.year == year)
                     .values(last=InvoiceSequence.last + 1).returning(InvoiceSequence.last)).scalar_one_or_none()
    if res is None:
        db.add(InvoiceSequence(year=year, last=1))
        db.flush()
        res = 1
    return f"CR-{year}-{res:06d}"


# ── Creation ──────────────────────────────────────────────────────────────

@dataclass
class NewInvoice:
    amount: str
    currency: str  # fiat code or canonical asset id
    asset: str | None = None  # canonical asset id to quote immediately
    allowed_assets: list[str] | None = None
    order_id: str | None = None
    description: str | None = None
    metadata: dict | None = None
    expires_in_minutes: int | None = None
    redirect_url: str | None = None


def request_fingerprint(req: NewInvoice) -> str:
    return hashlib.sha256(json.dumps(req.__dict__, sort_keys=True, default=str).encode()).hexdigest()


def create_invoice(db: Session, req: NewInvoice, *, mode: str, api_key_id: str | None = None,
                   created_by: str | None = None, idempotency_key: str | None = None) -> tuple[Invoice, bool]:
    """Returns (invoice, created). Replays with the same idempotency key return the original."""
    fp = request_fingerprint(req)
    if idempotency_key:
        prev = db.execute(select(Invoice).where(Invoice.api_key_id == api_key_id,
                                                Invoice.idempotency_key == idempotency_key)).scalar_one_or_none()
        if prev:
            if prev.request_hash != fp:
                raise InvoiceError("idempotency key reused with a different request", "idempotency_conflict", 409)
            return prev, False

    cfg = settings_svc.get(db, "invoice")
    compliance = settings_svc.get(db, "compliance")
    available = {a.id: a for a in available_assets(db, mode)}
    if not available:
        raise InvoiceError("no payment assets are operational for this mode", "no_assets", 409)

    currency = req.currency.strip()
    fiat = rates_svc.is_fiat(currency)
    if fiat:
        currency = currency.upper()
        try:
            price = Decimal(req.amount)
        except Exception as exc:
            raise InvoiceError("amount is not a number") from exc
        if price <= 0 or price != price.quantize(Decimal("0.01")):
            raise InvoiceError("fiat amount must be positive with at most two decimals")
        cap = compliance.get("max_invoice_amount")
        if cap and currency == cfg["default_currency"] and price > Decimal(str(cap)):
            raise InvoiceError("amount exceeds the merchant's configured invoice limit", "amount_limit")
    else:
        if currency not in available:
            raise InvoiceError(f"asset {currency} is not available", "asset_unavailable")
        try:
            to_atomic(req.amount, available[currency].decimals)
        except AmountError as exc:
            raise InvoiceError(str(exc)) from exc
        if Decimal(req.amount) <= 0:
            raise InvoiceError("amount must be positive")

    allowed = req.allowed_assets or ([currency] if not fiat else list(available))
    for a in allowed:
        if a not in available:
            raise InvoiceError(f"asset {a} is not available", "asset_unavailable")
        if not fiat and a != currency:
            raise InvoiceError("crypto-priced invoices can only be paid in the pricing asset")
    if req.asset and req.asset not in allowed:
        raise InvoiceError("asset is not in allowed_assets")

    expiry = req.expires_in_minutes or int(cfg["default_expiry_minutes"])
    if not 5 <= expiry <= 7 * 24 * 60:
        raise InvoiceError("expires_in_minutes must be between 5 and 10080")

    inv = Invoice(number=next_number(db), mode=mode, status="pending", price_amount=str(Decimal(req.amount)),
                  price_currency=currency, allowed_assets=allowed, order_id=req.order_id,
                  description=req.description, metadata_=req.metadata or {}, redirect_url=req.redirect_url,
                  api_key_id=api_key_id, created_by=created_by, idempotency_key=idempotency_key,
                  request_hash=fp, expires_at=utcnow() + timedelta(minutes=expiry))
    db.add(inv)
    db.flush()
    chosen = req.asset or (allowed[0] if len(allowed) == 1 else None)
    if chosen:
        quote(db, inv, chosen)
    emit(db, "invoice.created", {"invoice": merchant_view(db, inv)}, invoice_id=inv.id, mode=mode)
    return inv, True


# ── Quoting & allocation ──────────────────────────────────────────────────

def _amount_due(db: Session, inv: Invoice, asset: Asset) -> tuple[int, str | None, str | None]:
    if not rates_svc.is_fiat(inv.price_currency):
        return to_atomic(inv.price_amount, asset.decimals), None, None
    rate, source = rates_svc.rate(db, asset, inv.price_currency)
    places = min(asset.decimals, int(settings_svc.get(db, "invoice")["amount_display_decimals"]))
    human = (Decimal(inv.price_amount) / rate).quantize(Decimal(1).scaleb(-places), rounding=ROUND_CEILING)
    return to_atomic(human, asset.decimals), str(rate), source


def allocate_address(db: Session, network_key: str) -> tuple[Keychain, int, str, str, dict]:
    net = get_network(network_key)
    nw = db.get(NetworkWallet, network_key)
    if nw is None:
        raise InvoiceError(f"no wallet attached to {net.name}", "wallet_missing", 409)
    kc = db.get(Keychain, nw.keychain_id)
    if not kc or not kc.verified_at or not kc.active:
        raise InvoiceError("the wallet for this network has not been verified by an operator", "wallet_unverified", 409)
    # Atomic counter bump — safe under concurrency on PostgreSQL and SQLite.
    idx = db.execute(update(Keychain).where(Keychain.id == kc.id).values(next_index=Keychain.next_index + 1)
                     .returning(Keychain.next_index)).scalar_one() - 1
    d = derive_deposit(net, kc.public_material, idx)
    return kc, idx, d.path, d.address, d.meta


def quote(db: Session, inv: Invoice, asset_id: str) -> DepositAddress:
    if inv.status not in OPEN:
        raise InvoiceError("invoice is no longer open", "invoice_closed", 409)
    if asset_id not in inv.allowed_assets:
        raise InvoiceError("asset not allowed for this invoice")
    if inv.active_deposit_id:
        cur = db.get(DepositAddress, inv.active_deposit_id)
        if cur and cur.asset_id == asset_id:
            return cur
        if db.execute(select(Payment.id).where(Payment.deposit_id == inv.active_deposit_id).limit(1)).first():
            raise InvoiceError("a payment was already detected; the asset can no longer be changed", "payment_seen", 409)
    if len(inv.deposits) >= MAX_QUOTES_PER_INVOICE:
        raise InvoiceError("too many payment method changes for this invoice", "quote_limit", 429)
    asset = db.get(Asset, asset_id)
    if asset is None or asset.id not in {a.id for a in available_assets(db, inv.mode)}:
        raise InvoiceError("asset is not available", "asset_unavailable", 409)
    try:
        due, rate, source = _amount_due(db, inv, asset)
    except rates_svc.RateUnavailable as exc:
        raise InvoiceError(str(exc), "rate_unavailable", 503) from exc
    if asset.min_amount and due < to_atomic(asset.min_amount, asset.decimals):
        raise InvoiceError(f"amount is below the minimum for {asset.symbol}", "amount_too_small")
    kc, idx, path, display, meta = allocate_address(db, asset.network)
    net = get_network(asset.network)
    late = int(settings_svc.get(db, "invoice")["late_payment_window_minutes"])
    dep = DepositAddress(keychain_id=kc.id, derivation_index=idx, derivation_path=path, network=asset.network,
                         asset_id=asset.id, address=normalize_address(net.family, display), display_address=display,
                         invoice_id=inv.id, amount_due=str(due), rate=rate, rate_source=source, meta=meta,
                         monitor_until=inv.expires_at + timedelta(minutes=late))
    db.add(dep)
    db.flush()
    inv.active_deposit_id = dep.id
    _prepare(db, dep, asset)
    return dep


def _prepare(db: Session, dep: DepositAddress, asset: Asset) -> None:
    """Best-effort chain preparation; the monitor retries anything that fails here."""
    try:
        ad = adapter_for(db, dep.network)
        upd = ad.prepare_deposit(WatchItem(dep.id, dep.address, dep.display_address, asset_info(asset), dep.meta))
        if upd:
            dep.meta = {**dep.meta, **upd}
        dep.prepared = True
    except ChainError as exc:
        log.warning("deposit %s preparation deferred: %s", dep.id, exc)


def asset_info(a: Asset) -> AssetInfo:
    return AssetInfo(a.id, a.kind, a.symbol, a.decimals, a.contract)


def cancel(db: Session, inv: Invoice) -> None:
    if inv.status not in OPEN:
        raise InvoiceError("only open invoices can be cancelled", "invoice_closed", 409)
    inv.status = "cancelled"
    inv.cancelled_at = utcnow()
    emit(db, "invoice.cancelled", {"invoice": merchant_view(db, inv)}, invoice_id=inv.id, mode=inv.mode)


# ── Settlement state ──────────────────────────────────────────────────────

def required_confirmations(db: Session, network_key: str) -> int:
    nc = db.get(NetworkConfig, network_key)
    return (nc.confirmations if nc and nc.confirmations else None) or NETWORKS[network_key].default_confirmations


def totals(db: Session, inv: Invoice) -> dict[str, dict]:
    """Per-deposit sums of seen and confirmed value."""
    out: dict[str, dict] = {}
    for d in inv.deposits:
        out[d.id] = {"due": int(d.amount_due), "seen": 0, "confirmed": 0}
    for p in db.execute(select(Payment).where(Payment.invoice_id == inv.id)).scalars():
        if p.status in ("orphaned", "rejected") or p.deposit_id not in out:
            continue
        out[p.deposit_id]["seen"] += int(p.amount)
        if p.status == "confirmed":
            out[p.deposit_id]["confirmed"] += int(p.amount)
    return out


def recompute(db: Session, inv: Invoice) -> str | None:
    """Re-derive invoice status from payments. Returns the new status if it changed."""
    old = inv.status
    tol = int(settings_svc.get(db, "invoice")["underpay_tolerance_bps"])
    t = totals(db, inv)
    now = utcnow()

    def covered(v: int, due: int) -> bool:
        return v * 10_000 >= due * (10_000 - tol)

    paid_dep = next((k for k, v in t.items() if covered(v["confirmed"], v["due"])), None)
    seen_any = any(v["seen"] > 0 for v in t.values())
    seen_full = any(covered(v["seen"], v["due"]) for v in t.values())
    partial_deps = [k for k, v in t.items() if v["seen"] > 0]

    if len(partial_deps) > 1 and not inv.needs_review:
        inv.needs_review = True
        emit(db, "invoice.needs_review", {"invoice": merchant_view(db, inv), "reason": "payments to multiple quotes"},
             invoice_id=inv.id, mode=inv.mode)

    if inv.status == "cancelled":
        if seen_any and not inv.needs_review:
            inv.needs_review = True
            emit(db, "invoice.needs_review", {"invoice": merchant_view(db, inv), "reason": "payment to cancelled invoice"},
                 invoice_id=inv.id, mode=inv.mode)
        return None

    if paid_dep:
        new = "paid"
        v = t[paid_dep]
        inv.overpaid = v["confirmed"] > v["due"]
        if inv.paid_at is None:
            inv.paid_at = now
            first_seen = db.execute(select(Payment.first_seen_at).where(Payment.deposit_id == paid_dep)
                                    .order_by(Payment.first_seen_at)).scalars().first()
            inv.paid_late = bool(first_seen and first_seen > inv.expires_at)
        if inv.active_deposit_id != paid_dep:
            inv.active_deposit_id = paid_dep
    elif seen_any and (now <= inv.expires_at or seen_full):
        new = "processing"
        inv.detected_at = inv.detected_at or now
    elif now > inv.expires_at:
        new = "underpaid" if any(v["confirmed"] > 0 for v in t.values()) else (
            "processing" if seen_any else "expired")
    else:
        new = "pending"

    if old == "paid" and new != "paid":
        inv.paid_at = None
        emit(db, "invoice.reverted", {"invoice": merchant_view(db, inv), "previous_status": old},
             invoice_id=inv.id, mode=inv.mode)
    if new != old:
        inv.status = new
        if new in ("processing", "paid", "expired", "underpaid"):
            emit(db, f"invoice.{new}", {"invoice": merchant_view(db, inv)}, invoice_id=inv.id, mode=inv.mode)
        return new
    return None


# ── Views ─────────────────────────────────────────────────────────────────

def _deposit_view(db: Session, d: DepositAddress, t: dict | None) -> dict:
    a = db.get(Asset, d.asset_id)
    net = NETWORKS[d.network]
    tt = (t or {}).get(d.id, {"seen": 0, "confirmed": 0})
    return {
        "id": d.id, "asset": d.asset_id, "symbol": a.symbol, "asset_name": a.name, "asset_kind": a.kind,
        "contract": a.contract, "decimals": a.decimals,
        "network": d.network, "network_name": net.name, "environment": net.environment,
        "address": d.display_address,
        "amount_due": fmt_atomic(d.amount_due, a.decimals), "amount_due_atomic": d.amount_due,
        "amount_seen": fmt_atomic(tt["seen"], a.decimals), "amount_confirmed": fmt_atomic(tt["confirmed"], a.decimals),
        "rate": d.rate, "rate_source": d.rate_source,
        "confirmations_required": required_confirmations(db, d.network),
        "payment_uri": payment_uri(d, a),
        "explorer_url": net.explorer_address.format(address=d.display_address) if net.explorer_address else None,
    }


def payment_uri(d: DepositAddress, a: Asset) -> str | None:
    amt = fmt_atomic(d.amount_due, a.decimals)
    net = NETWORKS[d.network]
    if net.family == "bitcoin":
        return f"bitcoin:{d.display_address}?amount={amt}"
    if net.family == "ton":
        if a.kind == "native":
            return f"ton://transfer/{d.display_address}?amount={d.amount_due}"
        from ..core.ton_wallet import friendly, parse_address
        jetton = friendly(parse_address(a.contract), bounceable=True, testnet=net.ton_testnet)
        return f"ton://transfer/{d.display_address}?jetton={jetton}&amount={d.amount_due}"
    if net.family == "evm":
        if a.kind == "native":
            return f"ethereum:{d.display_address}@{net.chain_id}?value={d.amount_due}"
        return f"ethereum:{a.contract}@{net.chain_id}/transfer?address={d.display_address}&uint256={d.amount_due}"
    return None


def qr_svg(data: str) -> str:
    q = segno.make(data, error="m")
    return q.svg_inline(scale=6, dark="#09090B", light="#F4F4F5", border=2)


def _payments_view(db: Session, inv: Invoice) -> list[dict]:
    out = []
    for p in db.execute(select(Payment).where(Payment.invoice_id == inv.id).order_by(Payment.first_seen_at)).scalars():
        a = db.get(Asset, p.asset_id)
        net = NETWORKS[p.network]
        out.append({
            "id": p.id, "txid": p.txid, "network": p.network, "asset": p.asset_id, "symbol": a.symbol if a else "?",
            "amount": fmt_atomic(p.amount, a.decimals) if a else p.amount, "status": p.status,
            "confirmations": p.confirmations, "required": required_confirmations(db, p.network),
            "sender": p.sender, "source": p.source, "first_seen_at": p.first_seen_at.isoformat(),
            "confirmed_at": p.confirmed_at.isoformat() if p.confirmed_at else None,
            "explorer_url": net.explorer_tx.format(txid=p.txid) if net.explorer_tx and not p.txid.startswith("balance:") else None,
        })
    return out


def merchant_view(db: Session, inv: Invoice) -> dict:
    t = totals(db, inv) if inv.deposits else {}
    active = db.get(DepositAddress, inv.active_deposit_id) if inv.active_deposit_id else None
    from ..config import get_settings
    return {
        "id": inv.id, "number": inv.number, "mode": inv.mode, "status": inv.status,
        "price": {"amount": inv.price_amount, "currency": inv.price_currency},
        "allowed_assets": inv.allowed_assets, "order_id": inv.order_id, "description": inv.description,
        "metadata": inv.metadata_, "redirect_url": inv.redirect_url,
        "flags": {"overpaid": inv.overpaid, "paid_late": inv.paid_late, "needs_review": inv.needs_review},
        "payment": _deposit_view(db, active, t) if active else None,
        "quotes": [_deposit_view(db, d, t) for d in inv.deposits],
        "payments": _payments_view(db, inv),
        "checkout_url": f"{get_settings().public_url.rstrip('/')}/pay/{inv.id}",
        "created_at": inv.created_at.isoformat(), "expires_at": inv.expires_at.isoformat(),
        "paid_at": inv.paid_at.isoformat() if inv.paid_at else None,
    }


def public_view(db: Session, inv: Invoice) -> dict:
    """What the payer's browser sees — no merchant metadata, no internal ids."""
    t = totals(db, inv) if inv.deposits else {}
    active = db.get(DepositAddress, inv.active_deposit_id) if inv.active_deposit_id else None
    business = settings_svc.get(db, "business")
    compliance = settings_svc.get(db, "compliance")
    options = []
    for aid in inv.allowed_assets:
        a = db.get(Asset, aid)
        if a:
            net = NETWORKS[a.network]
            options.append({"asset": a.id, "symbol": a.symbol, "name": a.name, "kind": a.kind,
                            "network": a.network, "network_name": net.name, "environment": net.environment})
    pv = None
    if active:
        pv = _deposit_view(db, active, t)
        pv["qr_svg"] = qr_svg(pv["payment_uri"] or pv["address"])
    return {
        "id": inv.id, "number": inv.number, "status": inv.status, "mode": inv.mode,
        "price": {"amount": inv.price_amount, "currency": inv.price_currency},
        "description": inv.description, "expires_at": inv.expires_at.isoformat(),
        "server_time": utcnow().isoformat(), "redirect_url": inv.redirect_url if inv.status == "paid" else None,
        "flags": {"overpaid": inv.overpaid, "paid_late": inv.paid_late},
        "merchant": {"name": business.get("name") or "Merchant", "website": business.get("website"),
                     "support_email": business.get("support_email")},
        "compliance": {k: compliance.get(k) for k in ("terms_url", "privacy_url", "refund_policy_url",
                                                      "checkout_notice", "require_terms_acceptance")},
        "options": options, "payment": pv,
        "payments": [{k: p[k] for k in ("txid", "amount", "symbol", "status", "confirmations", "required", "explorer_url")}
                     for p in _payments_view(db, inv)],
    }
