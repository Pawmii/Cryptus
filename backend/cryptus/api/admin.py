"""Console (admin) API. Session cookie + CSRF; role-gated."""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..chains.base import ChainError
from ..core.money import fmt_atomic
from ..core.networks import NETWORKS
from ..db import get_db, utcnow
from ..models import (ApiKey, Asset, AuditEntry, DepositAddress, Event, Invoice, Keychain, NetworkWallet, Payment,
                      Sweep, User, UserSession, WebhookDelivery, WebhookEndpoint)
from ..security import (decrypt_secret, encrypt_secret, hash_password, new_api_key, new_webhook_secret,
                        password_problems, token_digest)
from ..services import audit, consolidation, infra, invoices as inv_svc, settings as settings_svc, signer_client
from ..services.webhooks import EVENT_TYPES, UnsafeTarget, check_target, emit, envelope
from .auth import user_view
from .deps import Principal, current, require

router = APIRouter(prefix="/api/admin", tags=["console"])


def _404(what: str = "not found"):
    raise HTTPException(404, what)


def _bad(exc: Exception):
    raise HTTPException(400, str(exc))


# ── Dashboard ─────────────────────────────────────────────────────────────

@router.get("/overview")
def overview(days: int = Query(30, ge=7, le=365), p: Principal = Depends(current), db: Session = Depends(get_db)):
    now = utcnow()
    since = now - timedelta(days=days)
    currency = settings_svc.get(db, "invoice")["default_currency"]
    rows = db.execute(select(Invoice).where(Invoice.created_at >= since, Invoice.mode == "live")).scalars().all()
    by_day: dict[str, dict] = {}
    for i in range(days):
        d = (since + timedelta(days=i + 1)).date().isoformat()
        by_day[d] = {"date": d, "volume": Decimal(0), "paid": 0, "created": 0}
    status_counts: dict[str, int] = defaultdict(int)
    volume = Decimal(0)
    for inv in rows:
        status_counts[inv.status] += 1
        day = inv.created_at.date().isoformat()
        if day in by_day:
            by_day[day]["created"] += 1
        if inv.status == "paid" and inv.paid_at:
            pd = inv.paid_at.date().isoformat()
            if pd in by_day:
                by_day[pd]["paid"] += 1
                if inv.price_currency == currency:
                    by_day[pd]["volume"] += Decimal(inv.price_amount)
            if inv.price_currency == currency:
                volume += Decimal(inv.price_amount)
    closed = status_counts["paid"] + status_counts["expired"] + status_counts["underpaid"]
    received: dict[str, dict] = {}
    q = select(Payment).where(Payment.status == "confirmed", Payment.first_seen_at >= since)
    for pmt in db.execute(q).scalars():
        a = db.get(Asset, pmt.asset_id)
        if not a or NETWORKS[a.network].environment != "mainnet":
            continue
        r = received.setdefault(a.id, {"asset": a.id, "symbol": a.symbol, "network": NETWORKS[a.network].name,
                                       "atomic": 0, "decimals": a.decimals, "count": 0})
        r["atomic"] += int(pmt.amount)
        r["count"] += 1
    recent = db.execute(select(Invoice).order_by(Invoice.created_at.desc()).limit(8)).scalars().all()
    return {
        "currency": currency, "days": days,
        "kpis": {
            "volume": str(volume), "paid": status_counts["paid"], "created": len(rows),
            "conversion": round(status_counts["paid"] / closed, 4) if closed else None,
            "open": db.execute(select(func.count()).select_from(Invoice).where(
                Invoice.status.in_(inv_svc.OPEN))).scalar_one(),
            "needs_review": db.execute(select(func.count()).select_from(Invoice).where(
                Invoice.needs_review.is_(True))).scalar_one(),
            "sweeps_pending": db.execute(select(func.count()).select_from(Sweep).where(
                Sweep.status.in_(("awaiting_approval", "awaiting_external_signature")))).scalar_one(),
        },
        "series": [{**v, "volume": str(v["volume"])} for v in by_day.values()],
        "status_counts": status_counts,
        "received": [{**r, "amount": fmt_atomic(r["atomic"], r["decimals"]), "atomic": str(r["atomic"])}
                     for r in received.values()],
        "networks": [{k: n[k] for k in ("key", "name", "environment", "status", "health")}
                     for n in infra.network_rows(db) if n["implementation"] == "implemented"],
        "recent": [_inv_row(db, i) for i in recent],
        "treasury": settings_svc.get(db, "treasury"),
    }


# ── Invoices ──────────────────────────────────────────────────────────────

def _inv_row(db: Session, i: Invoice) -> dict:
    dep = db.get(DepositAddress, i.active_deposit_id) if i.active_deposit_id else None
    a = db.get(Asset, dep.asset_id) if dep else None
    return {"id": i.id, "number": i.number, "status": i.status, "mode": i.mode,
            "price": {"amount": i.price_amount, "currency": i.price_currency},
            "asset": a.symbol if a else None, "network": NETWORKS[a.network].name if a else None,
            "amount_due": fmt_atomic(dep.amount_due, a.decimals) if dep and a else None,
            "order_id": i.order_id, "description": i.description,
            "flags": {"overpaid": i.overpaid, "paid_late": i.paid_late, "needs_review": i.needs_review},
            "created_at": i.created_at.isoformat(), "expires_at": i.expires_at.isoformat(),
            "paid_at": i.paid_at.isoformat() if i.paid_at else None}


@router.get("/invoices")
def list_invoices(status: str | None = None, q: str | None = None, mode: str | None = None,
                  review: bool = False, limit: int = Query(50, le=200), offset: int = 0,
                  p: Principal = Depends(current), db: Session = Depends(get_db)):
    stmt = select(Invoice)
    if status:
        stmt = stmt.where(Invoice.status.in_(status.split(",")))
    if mode:
        stmt = stmt.where(Invoice.mode == mode)
    if review:
        stmt = stmt.where(Invoice.needs_review.is_(True))
    if q:
        like = f"%{q.strip()}%"
        sub = select(DepositAddress.invoice_id).where(DepositAddress.display_address.ilike(like))
        sub2 = select(Payment.invoice_id).where(Payment.txid.ilike(like))
        stmt = stmt.where(or_(Invoice.number.ilike(like), Invoice.order_id.ilike(like), Invoice.id == q.strip(),
                              Invoice.id.in_(sub), Invoice.id.in_(sub2)))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(Invoice.created_at.desc()).limit(limit).offset(offset)).scalars().all()
    return {"total": total, "items": [_inv_row(db, i) for i in rows]}


@router.get("/invoices/export.csv")
def export_invoices(p: Principal = Depends(require("operator")), db: Session = Depends(get_db)):
    def gen():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["number", "status", "mode", "price", "currency", "asset", "network", "amount_due",
                    "amount_confirmed", "order_id", "created_at", "paid_at", "overpaid", "paid_late"])
        yield buf.getvalue()
        for i in db.execute(select(Invoice).order_by(Invoice.created_at)).scalars():
            buf.seek(0)
            buf.truncate()
            t = inv_svc.totals(db, i)
            dep = db.get(DepositAddress, i.active_deposit_id) if i.active_deposit_id else None
            a = db.get(Asset, dep.asset_id) if dep else None
            w.writerow([i.number, i.status, i.mode, i.price_amount, i.price_currency, a.symbol if a else "",
                        a.network if a else "", fmt_atomic(dep.amount_due, a.decimals) if dep else "",
                        fmt_atomic(t.get(dep.id, {}).get("confirmed", 0), a.decimals) if dep else "",
                        i.order_id or "", i.created_at.isoformat(), i.paid_at.isoformat() if i.paid_at else "",
                        i.overpaid, i.paid_late])
            yield buf.getvalue()
    audit.record(db, audit.Actor("user", p.user.id, p.user.email), "invoices.exported")
    db.commit()
    return StreamingResponse(gen(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=cryptus-invoices.csv"})


@router.get("/invoices/{invoice_id}")
def get_invoice(invoice_id: str, p: Principal = Depends(current), db: Session = Depends(get_db)):
    inv = db.get(Invoice, invoice_id) or _404()
    view = inv_svc.merchant_view(db, inv)
    view["events"] = [{"id": e.id, "type": e.type, "created_at": e.created_at.isoformat()} for e in db.execute(
        select(Event).where(Event.invoice_id == inv.id).order_by(Event.created_at)).scalars()]
    view["deposit_details"] = [{"id": d.id, "keychain_id": d.keychain_id, "index": d.derivation_index,
                                "path": d.derivation_path, "prepared": d.prepared,
                                "monitor_until": d.monitor_until.isoformat(),
                                "swept_at": d.swept_at.isoformat() if d.swept_at else None, "meta": d.meta}
                               for d in inv.deposits]
    return view


class InvoiceIn(BaseModel):
    amount: str = Field(max_length=40)
    currency: str = Field(max_length=160)
    asset: str | None = None
    allowed_assets: list[str] | None = None
    order_id: str | None = Field(None, max_length=128)
    description: str | None = Field(None, max_length=500)
    expires_in_minutes: int | None = None
    mode: str = "live"


@router.post("/invoices")
def create_invoice(body: InvoiceIn, request: Request, p: Principal = Depends(require("operator")),
                   db: Session = Depends(get_db)):
    if body.mode not in ("live", "test"):
        _bad(ValueError("mode must be live or test"))
    try:
        inv, _ = inv_svc.create_invoice(db, inv_svc.NewInvoice(
            amount=body.amount, currency=body.currency, asset=body.asset, allowed_assets=body.allowed_assets,
            order_id=body.order_id, description=body.description, expires_in_minutes=body.expires_in_minutes),
            mode=body.mode, created_by=p.user.id)
    except inv_svc.InvoiceError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    audit.record(db, p.actor(request), "invoice.created", inv.id, number=inv.number)
    return inv_svc.merchant_view(db, inv)


@router.post("/invoices/{invoice_id}/cancel")
def cancel_invoice(invoice_id: str, request: Request, p: Principal = Depends(require("operator")),
                   db: Session = Depends(get_db)):
    inv = db.get(Invoice, invoice_id) or _404()
    try:
        inv_svc.cancel(db, inv)
    except inv_svc.InvoiceError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    audit.record(db, p.actor(request), "invoice.cancelled", inv.id)
    return inv_svc.merchant_view(db, inv)


class ResolveIn(BaseModel):
    note: str = Field(min_length=3, max_length=500)


@router.post("/invoices/{invoice_id}/resolve")
def resolve_review(invoice_id: str, body: ResolveIn, request: Request, p: Principal = Depends(require("admin")),
                   db: Session = Depends(get_db)):
    inv = db.get(Invoice, invoice_id) or _404()
    inv.needs_review = False
    audit.record(db, p.actor(request), "invoice.review_resolved", inv.id, note=body.note)
    return inv_svc.merchant_view(db, inv)


@router.get("/payments")
def list_payments(network: str | None = None, status: str | None = None, limit: int = Query(50, le=200),
                  offset: int = 0, p: Principal = Depends(current), db: Session = Depends(get_db)):
    stmt = select(Payment)
    if network:
        stmt = stmt.where(Payment.network == network)
    if status:
        stmt = stmt.where(Payment.status == status)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    out = []
    for pm in db.execute(stmt.order_by(Payment.first_seen_at.desc()).limit(limit).offset(offset)).scalars():
        a = db.get(Asset, pm.asset_id)
        inv = db.get(Invoice, pm.invoice_id)
        net = NETWORKS[pm.network]
        out.append({"id": pm.id, "txid": pm.txid, "network": pm.network, "network_name": net.name,
                    "symbol": a.symbol if a else "?", "amount": fmt_atomic(pm.amount, a.decimals) if a else pm.amount,
                    "status": pm.status, "confirmations": pm.confirmations,
                    "required": inv_svc.required_confirmations(db, pm.network), "source": pm.source,
                    "invoice_id": pm.invoice_id, "invoice_number": inv.number if inv else None,
                    "first_seen_at": pm.first_seen_at.isoformat(),
                    "explorer_url": net.explorer_tx.format(txid=pm.txid) if not pm.txid.startswith("balance:") else None})
    return {"total": total, "items": out}


# ── Networks ──────────────────────────────────────────────────────────────

@router.get("/networks")
def networks(p: Principal = Depends(current), db: Session = Depends(get_db)):
    return infra.network_rows(db)


class NetworkIn(BaseModel):
    backend: str | None = None
    endpoint_url: str | None = None
    credentials: dict[str, str] | None = None
    confirmations: int | None = None
    options: dict | None = None
    enabled: bool | None = None


@router.put("/networks/{key}")
def update_network(key: str, body: NetworkIn, request: Request, p: Principal = Depends(require("admin")),
                   db: Session = Depends(get_db)):
    if key not in NETWORKS:
        _404()
    try:
        infra.configure_network(db, p.actor(request), key, **body.model_dump())
    except infra.InfraError as exc:
        _bad(exc)
    return next(n for n in infra.network_rows(db) if n["key"] == key)


@router.post("/networks/{key}/test")
def test_network(key: str, p: Principal = Depends(require("admin")), db: Session = Depends(get_db)):
    try:
        return infra.test_network(db, key)
    except infra.InfraError as exc:
        _bad(exc)


# ── Keychains & wallets ───────────────────────────────────────────────────

@router.get("/keychains")
def keychains(p: Principal = Depends(current), db: Session = Depends(get_db)):
    out = []
    for kc in db.execute(select(Keychain).order_by(Keychain.created_at)).scalars():
        v = infra.keychain_view(kc)
        net_key = kc.scope if kc.family != "evm" else "eth"
        v["preview"] = infra.preview_addresses(kc, net_key, 3)
        v["networks"] = [w.network for w in db.execute(select(NetworkWallet).where(
            NetworkWallet.keychain_id == kc.id)).scalars()]
        out.append(v)
    return {"items": out, "signer": {"configured": signer_client.configured()}}


class KeychainIn(BaseModel):
    family: str
    scope: str
    public_material: str = Field(max_length=512)
    label: str = Field("", max_length=120)
    master_fingerprint: str | None = None
    account_path: str | None = None


@router.post("/keychains")
def create_keychain(body: KeychainIn, request: Request, p: Principal = Depends(require("admin")),
                    db: Session = Depends(get_db)):
    try:
        kc = infra.create_keychain(db, p.actor(request), mode="watch_only", **body.model_dump())
    except infra.InfraError as exc:
        _bad(exc)
    v = infra.keychain_view(kc)
    v["preview"] = infra.preview_addresses(kc, kc.scope if kc.family != "evm" else "eth", 3)
    return v


@router.post("/keychains/import-signer")
def import_signer(request: Request, p: Principal = Depends(require("admin")), db: Session = Depends(get_db)):
    try:
        created = infra.import_signer_keys(db, p.actor(request))
    except signer_client.SignerError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"created": [infra.keychain_view(k) for k in created]}


class VerifyIn(BaseModel):
    address: str


@router.post("/keychains/{kid}/verify")
def verify_keychain(kid: str, body: VerifyIn, request: Request, p: Principal = Depends(require("admin")),
                    db: Session = Depends(get_db)):
    kc = db.get(Keychain, kid) or _404()
    try:
        infra.verify_keychain(db, p.actor(request), kc, body.address)
    except infra.InfraError as exc:
        _bad(exc)
    return infra.keychain_view(kc)


@router.post("/keychains/{kid}/deactivate")
def deactivate_keychain(kid: str, request: Request, p: Principal = Depends(require("owner")),
                        db: Session = Depends(get_db)):
    kc = db.get(Keychain, kid) or _404()
    kc.active = False
    audit.record(db, p.actor(request), "keychain.deactivated", kc.id)
    return infra.keychain_view(kc)


class WalletIn(BaseModel):
    keychain_id: str | None = None
    destination: str | None = None
    set_destination: bool = False
    sweep_enabled: bool | None = None
    sweep_interval_minutes: int | None = Field(None, ge=10, le=10080)
    sweep_thresholds: dict[str, str] | None = None
    max_fee: dict[str, str] | None = None


@router.put("/wallets/{network}")
def update_wallet(network: str, body: WalletIn, request: Request, p: Principal = Depends(require("admin")),
                  db: Session = Depends(get_db)):
    actor = p.actor(request)
    try:
        if body.keychain_id:
            infra.attach_wallet(db, actor, network, body.keychain_id)
        if body.set_destination:
            infra.set_destination(db, actor, network, body.destination)
        nw = db.get(NetworkWallet, network)
        if nw is None:
            raise infra.InfraError("attach a keychain first")
        if body.sweep_enabled is not None:
            if body.sweep_enabled and not nw.destination_confirmed_at:
                raise infra.InfraError("set and confirm a consolidation destination before enabling sweeps")
            nw.sweep_enabled = body.sweep_enabled
        if body.sweep_interval_minutes:
            nw.sweep_interval_minutes = body.sweep_interval_minutes
        if body.sweep_thresholds is not None:
            for v in body.sweep_thresholds.values():
                if Decimal(v) < 0:
                    raise infra.InfraError("thresholds must be non-negative")
            nw.sweep_thresholds = body.sweep_thresholds
        if body.max_fee is not None:
            nw.max_fee = body.max_fee
        audit.record(db, actor, "wallet.updated", network, sweep_enabled=nw.sweep_enabled,
                     thresholds=nw.sweep_thresholds, max_fee=nw.max_fee)
    except (infra.InfraError, ArithmeticError) as exc:
        _bad(exc)
    kc = db.get(Keychain, nw.keychain_id)
    return infra.wallet_view(db, nw, kc)


# ── Assets ────────────────────────────────────────────────────────────────

@router.get("/assets")
def assets(p: Principal = Depends(current), db: Session = Depends(get_db)):
    return [infra.asset_view(a) for a in db.execute(select(Asset).order_by(Asset.network, Asset.kind)).scalars()]


class TokenIn(BaseModel):
    network: str
    contract: str
    symbol: str = Field(max_length=16)
    name: str = Field(max_length=80)
    decimals: int = Field(ge=0, le=36)
    coingecko_id: str | None = None


@router.post("/assets")
def add_asset(body: TokenIn, request: Request, p: Principal = Depends(require("admin")), db: Session = Depends(get_db)):
    try:
        a = infra.add_token(db, p.actor(request), body.network, body.contract, symbol=body.symbol, name=body.name,
                            decimals=body.decimals, coingecko_id=body.coingecko_id)
    except infra.InfraError as exc:
        _bad(exc)
    return infra.asset_view(a)


class AssetPatch(BaseModel):
    enabled: bool | None = None
    coingecko_id: str | None = None
    fixed_usd_peg: bool | None = None
    min_amount: str | None = None


@router.patch("/assets/{asset_id:path}")
def patch_asset(asset_id: str, body: AssetPatch, request: Request, p: Principal = Depends(require("admin")),
                db: Session = Depends(get_db)):
    a = db.get(Asset, asset_id) or _404()
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(a, k, v if v != "" else None)
    audit.record(db, p.actor(request), "asset.updated", a.id, **body.model_dump(exclude_unset=True))
    return infra.asset_view(a)


# ── Consolidation ─────────────────────────────────────────────────────────

@router.get("/sweeps")
def sweeps(status: str | None = None, p: Principal = Depends(current), db: Session = Depends(get_db)):
    stmt = select(Sweep)
    if status:
        stmt = stmt.where(Sweep.status.in_(status.split(",")))
    rows = db.execute(stmt.order_by(Sweep.created_at.desc()).limit(200)).scalars()
    return {"items": [consolidation.sweep_view(db, s) for s in rows], "treasury": settings_svc.get(db, "treasury")}


@router.post("/sweeps/plan/{network}")
def plan_sweeps(network: str, request: Request, p: Principal = Depends(require("admin")), db: Session = Depends(get_db)):
    try:
        created = consolidation.plan(db, network, force=True)
    except (ChainError, consolidation.SweepError, signer_client.SignerError) as exc:
        raise HTTPException(409, str(exc)) from exc
    audit.record(db, p.actor(request), "sweep.plan_requested", network, created=len(created))
    for s in created:
        consolidation.advance(db, s)
    return {"created": [consolidation.sweep_view(db, s) for s in created]}


@router.post("/sweeps/{sid}/approve")
def approve_sweep(sid: str, request: Request, p: Principal = Depends(require("admin")), db: Session = Depends(get_db)):
    s = db.get(Sweep, sid) or _404()
    try:
        consolidation.approve(db, s, p.actor(request))
    except consolidation.SweepError as exc:
        raise HTTPException(409, str(exc)) from exc
    return consolidation.sweep_view(db, s)


@router.post("/sweeps/{sid}/cancel")
def cancel_sweep(sid: str, request: Request, p: Principal = Depends(require("admin")), db: Session = Depends(get_db)):
    s = db.get(Sweep, sid) or _404()
    try:
        consolidation.cancel(db, s, p.actor(request))
    except consolidation.SweepError as exc:
        raise HTTPException(409, str(exc)) from exc
    return consolidation.sweep_view(db, s)


class SignedIn(BaseModel):
    signed: str = Field(min_length=10, max_length=200_000)


@router.post("/sweeps/{sid}/submit")
def submit_sweep(sid: str, body: SignedIn, request: Request, p: Principal = Depends(require("admin")),
                 db: Session = Depends(get_db)):
    s = db.get(Sweep, sid) or _404()
    try:
        consolidation.submit_external(db, s, body.signed, p.actor(request))
    except (consolidation.SweepError, ChainError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return consolidation.sweep_view(db, s)


class HaltIn(BaseModel):
    halted: bool
    reason: str = Field("", max_length=300)


@router.post("/treasury/halt")
def treasury_halt(body: HaltIn, request: Request, p: Principal = Depends(require("admin")), db: Session = Depends(get_db)):
    if not body.halted and p.user.role != "owner":
        raise HTTPException(403, "only the owner can lift the emergency stop")
    settings_svc.put(db, "treasury", {"sweeps_halted": body.halted})
    audit.record(db, p.actor(request), "treasury.halt" if body.halted else "treasury.resume", None, reason=body.reason)
    return settings_svc.get(db, "treasury")


@router.get("/signer")
def signer_status(p: Principal = Depends(require("admin"))):
    if not signer_client.configured():
        return {"configured": False}
    try:
        return {"configured": True, **signer_client.health(), "policy": signer_client.policy()}
    except signer_client.SignerError as exc:
        return {"configured": True, "ok": False, "error": str(exc)}


# ── Webhooks ──────────────────────────────────────────────────────────────

def _ep_view(db: Session, ep: WebhookEndpoint) -> dict:
    stats = dict(db.execute(select(WebhookDelivery.status, func.count()).where(
        WebhookDelivery.endpoint_id == ep.id).group_by(WebhookDelivery.status)).all())
    return {"id": ep.id, "url": ep.url, "description": ep.description, "events": ep.events, "mode": ep.mode,
            "active": ep.active, "created_at": ep.created_at.isoformat(), "stats": stats}


@router.get("/webhooks")
def webhooks(p: Principal = Depends(current), db: Session = Depends(get_db)):
    return {"items": [_ep_view(db, e) for e in db.execute(select(WebhookEndpoint).order_by(
        WebhookEndpoint.created_at)).scalars()], "event_types": EVENT_TYPES}


class WebhookIn(BaseModel):
    url: str = Field(max_length=2000)
    description: str | None = Field(None, max_length=200)
    events: list[str] = []
    mode: str = "live"
    active: bool = True


@router.post("/webhooks")
def create_webhook(body: WebhookIn, request: Request, p: Principal = Depends(require("admin")),
                   db: Session = Depends(get_db)):
    try:
        check_target(body.url)
    except UnsafeTarget as exc:
        _bad(exc)
    if any(e not in EVENT_TYPES for e in body.events):
        _bad(ValueError("unknown event type"))
    secret = new_webhook_secret()
    ep = WebhookEndpoint(url=body.url, description=body.description, events=body.events, mode=body.mode,
                         active=body.active, secret_enc=encrypt_secret(secret))
    db.add(ep)
    db.flush()
    audit.record(db, p.actor(request), "webhook.created", ep.id, url=ep.url)
    return {**_ep_view(db, ep), "secret": secret}


@router.put("/webhooks/{wid}")
def update_webhook(wid: str, body: WebhookIn, request: Request, p: Principal = Depends(require("admin")),
                   db: Session = Depends(get_db)):
    ep = db.get(WebhookEndpoint, wid) or _404()
    try:
        check_target(body.url)
    except UnsafeTarget as exc:
        _bad(exc)
    ep.url, ep.description, ep.events, ep.mode, ep.active = body.url, body.description, body.events, body.mode, body.active
    audit.record(db, p.actor(request), "webhook.updated", ep.id)
    return _ep_view(db, ep)


@router.post("/webhooks/{wid}/rotate-secret")
def rotate_webhook(wid: str, request: Request, p: Principal = Depends(require("admin")), db: Session = Depends(get_db)):
    ep = db.get(WebhookEndpoint, wid) or _404()
    secret = new_webhook_secret()
    ep.secret_enc = encrypt_secret(secret)
    audit.record(db, p.actor(request), "webhook.secret_rotated", ep.id)
    return {"secret": secret}


@router.delete("/webhooks/{wid}")
def delete_webhook(wid: str, request: Request, p: Principal = Depends(require("admin")), db: Session = Depends(get_db)):
    ep = db.get(WebhookEndpoint, wid) or _404()
    db.delete(ep)
    audit.record(db, p.actor(request), "webhook.deleted", wid)
    return {"ok": True}


@router.post("/webhooks/{wid}/test")
def test_webhook(wid: str, request: Request, p: Principal = Depends(require("admin")), db: Session = Depends(get_db)):
    ep = db.get(WebhookEndpoint, wid) or _404()
    ev = Event(type="webhook.test", mode=ep.mode, payload={"message": "Test event from CRYPTUS"})
    db.add(ev)
    db.flush()
    d = WebhookDelivery(endpoint_id=ep.id, event_id=ev.id, status="pending", next_attempt_at=utcnow())
    db.add(d)
    db.flush()
    import httpx
    from ..services.webhooks import deliver_one
    with httpx.Client(timeout=10, follow_redirects=False) as c:
        deliver_one(db, d, c)
    return {"status": d.status, "status_code": d.last_status_code, "error": d.last_error}


@router.get("/webhooks/{wid}/deliveries")
def deliveries(wid: str, p: Principal = Depends(current), db: Session = Depends(get_db)):
    rows = db.execute(select(WebhookDelivery).where(WebhookDelivery.endpoint_id == wid)
                      .order_by(WebhookDelivery.created_at.desc()).limit(100)).scalars()
    return [{"id": d.id, "event_type": d.event.type, "event_id": d.event_id, "status": d.status,
             "attempts": d.attempts, "last_status_code": d.last_status_code, "last_error": d.last_error,
             "created_at": d.created_at.isoformat(), "next_attempt_at": d.next_attempt_at.isoformat(),
             "payload": envelope(d.event)} for d in rows]


@router.post("/webhooks/deliveries/{did}/retry")
def retry_delivery(did: str, request: Request, p: Principal = Depends(require("admin")), db: Session = Depends(get_db)):
    d = db.get(WebhookDelivery, did) or _404()
    d.status, d.next_attempt_at = "pending", utcnow()
    audit.record(db, p.actor(request), "webhook.redelivery", did)
    return {"ok": True}


# ── API keys ──────────────────────────────────────────────────────────────

API_SCOPES = ["invoices:read", "invoices:write"]


def _key_view(k: ApiKey) -> dict:
    return {"id": k.id, "name": k.name, "prefix": k.prefix, "scopes": k.scopes, "mode": k.mode,
            "created_at": k.created_at.isoformat(), "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "revoked_at": k.revoked_at.isoformat() if k.revoked_at else None}


@router.get("/api-keys")
def api_keys(p: Principal = Depends(require("admin")), db: Session = Depends(get_db)):
    return {"items": [_key_view(k) for k in db.execute(select(ApiKey).order_by(ApiKey.created_at.desc())).scalars()],
            "scopes": API_SCOPES}


class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    mode: str = "live"
    scopes: list[str] = API_SCOPES


@router.post("/api-keys")
def create_api_key(body: ApiKeyIn, request: Request, p: Principal = Depends(require("admin")),
                   db: Session = Depends(get_db)):
    if body.mode not in ("live", "test") or any(s not in API_SCOPES for s in body.scopes):
        _bad(ValueError("invalid mode or scope"))
    raw, prefix = new_api_key(body.mode)
    k = ApiKey(name=body.name, prefix=prefix, key_hash=token_digest(raw), scopes=body.scopes, mode=body.mode,
               created_by=p.user.id)
    db.add(k)
    db.flush()
    audit.record(db, p.actor(request), "api_key.created", k.id, name=k.name, mode=k.mode, scopes=k.scopes)
    return {**_key_view(k), "key": raw}


@router.post("/api-keys/{kid}/revoke")
def revoke_api_key(kid: str, request: Request, p: Principal = Depends(require("admin")), db: Session = Depends(get_db)):
    k = db.get(ApiKey, kid) or _404()
    k.revoked_at = k.revoked_at or utcnow()
    audit.record(db, p.actor(request), "api_key.revoked", k.id)
    return _key_view(k)


# ── Team ──────────────────────────────────────────────────────────────────

@router.get("/users")
def users(p: Principal = Depends(require("admin")), db: Session = Depends(get_db)):
    return [user_view(u) for u in db.execute(select(User).order_by(User.created_at)).scalars()]


class UserIn(BaseModel):
    email: str = Field(max_length=254)
    name: str = Field(max_length=120)
    role: str
    password: str = Field(max_length=1024)


@router.post("/users")
def create_user(body: UserIn, request: Request, p: Principal = Depends(require("owner")), db: Session = Depends(get_db)):
    if body.role not in ("admin", "operator", "viewer"):
        _bad(ValueError("role must be admin, operator or viewer"))
    email = body.email.strip().lower()
    if "@" not in email:
        _bad(ValueError("invalid email"))
    if db.execute(select(User).where(User.email == email)).first():
        _bad(ValueError("a user with this email exists"))
    if probs := password_problems(body.password):
        _bad(ValueError("password " + "; ".join(probs)))
    u = User(email=email, name=body.name.strip(), role=body.role, password_hash=hash_password(body.password))
    db.add(u)
    db.flush()
    audit.record(db, p.actor(request), "user.created", u.id, email=email, role=u.role)
    return user_view(u)


class UserPatch(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    reset_mfa: bool = False


@router.patch("/users/{uid}")
def patch_user(uid: str, body: UserPatch, request: Request, p: Principal = Depends(require("owner")),
               db: Session = Depends(get_db)):
    u = db.get(User, uid) or _404()
    if u.id == p.user.id:
        _bad(ValueError("you cannot modify your own role or status"))
    if body.role:
        if body.role not in ("admin", "operator", "viewer", "owner"):
            _bad(ValueError("invalid role"))
        u.role = body.role
    if body.is_active is not None:
        u.is_active = body.is_active
        if not body.is_active:
            db.execute(UserSession.__table__.delete().where(UserSession.user_id == u.id))
    if body.reset_mfa:
        u.totp_enabled, u.totp_secret_enc = False, None
    audit.record(db, p.actor(request), "user.updated", u.id, **body.model_dump(exclude_unset=True))
    return user_view(u)


# ── Audit & settings ──────────────────────────────────────────────────────

@router.get("/audit")
def audit_log(action: str | None = None, limit: int = Query(100, le=500), offset: int = 0,
              p: Principal = Depends(require("admin")), db: Session = Depends(get_db)):
    stmt = select(AuditEntry)
    if action:
        stmt = stmt.where(AuditEntry.action.ilike(f"{action}%"))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(AuditEntry.id.desc()).limit(limit).offset(offset)).scalars()
    return {"total": total, "items": [{"id": e.id, "at": e.at.isoformat(), "actor_type": e.actor_type,
                                       "actor": e.actor_label, "action": e.action, "target": e.target,
                                       "details": e.details, "ip": e.ip, "hash": e.hash} for e in rows]}


@router.get("/audit/verify")
def audit_verify(p: Principal = Depends(require("admin")), db: Session = Depends(get_db)):
    ok, broken = audit.verify_chain(db)
    return {"ok": ok, "broken_at": broken}


SETTINGS_SECTIONS = {"business": "admin", "invoice": "admin", "compliance": "admin", "security": "owner",
                     "rates": "admin"}


@router.get("/settings")
def get_settings_(p: Principal = Depends(current), db: Session = Depends(get_db)):
    out = {k: settings_svc.get(db, k) for k in SETTINGS_SECTIONS}
    out["rates"] = {k: v for k, v in out["rates"].items() if k != "api_key_enc"} | {
        "has_api_key": bool(out["rates"].get("api_key_enc"))}
    return out


@router.put("/settings/{section}")
def put_settings(section: str, body: dict, request: Request, p: Principal = Depends(current),
                 db: Session = Depends(get_db)):
    need = SETTINGS_SECTIONS.get(section) or _404()
    if {"viewer": 0, "operator": 1, "admin": 2, "owner": 3}[p.user.role] < {"admin": 2, "owner": 3}[need]:
        raise HTTPException(403, f"requires {need} role")
    if section == "rates" and "api_key" in body:
        key = body.pop("api_key")
        body["api_key_enc"] = encrypt_secret(key) if key else None
    if section == "invoice":
        cur = body.get("default_currency")
        if cur is not None and not __import__("cryptus.services.rates", fromlist=["x"]).is_fiat(cur):
            _bad(ValueError("unsupported fiat currency"))
    try:
        res = settings_svc.put(db, section, body)
    except KeyError:
        _404()
    audit.record(db, p.actor(request), "settings.updated", section,
                 keys=sorted(k for k in body if k != "api_key_enc"))
    return res
