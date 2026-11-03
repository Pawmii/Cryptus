"""Merchant REST API v1 — authenticated with ``Authorization: Bearer ck_live_…``.

Test-mode keys (``ck_test_…``) create invoices that can only be paid with
testnet assets. Live keys only see live invoices.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Invoice
from ..services import audit, invoices as inv_svc
from ..services.infra import asset_view
from .deps import DB, ApiPrincipal, scope

router = APIRouter(prefix="/api/v1", tags=["merchant"])


class CreateInvoice(BaseModel):
    amount: str = Field(..., max_length=40, examples=["25.00"])
    currency: str = Field(..., max_length=160, examples=["USD", "ton:jetton:0:b113…"])
    asset: str | None = Field(None, description="Canonical asset id to quote immediately")
    allowed_assets: list[str] | None = None
    order_id: str | None = Field(None, max_length=128)
    description: str | None = Field(None, max_length=500)
    metadata: dict | None = None
    expires_in_minutes: int | None = None
    redirect_url: str | None = Field(None, max_length=2000)


def _err(exc: inv_svc.InvoiceError):
    raise HTTPException(exc.status, {"code": exc.code, "message": str(exc)})


def _own(db: Session, p: ApiPrincipal, invoice_id: str) -> Invoice:
    inv = db.get(Invoice, invoice_id)
    if inv is None or inv.mode != p.key.mode:
        raise HTTPException(404, {"code": "not_found", "message": "invoice not found"})
    return inv


@router.get("/assets")
def list_assets(p: ApiPrincipal = Depends(scope("invoices:read")), db: Session = DB):
    """Assets that can currently be used for payment in this key's mode."""
    return {"data": [asset_view(a) for a in inv_svc.available_assets(db, p.key.mode)]}


@router.post("/invoices", status_code=201)
def create(body: CreateInvoice, request: Request, idempotency_key: str | None = Header(None, max_length=128),
           p: ApiPrincipal = Depends(scope("invoices:write")), db: Session = DB):
    if body.metadata and len(str(body.metadata)) > 4000:
        raise HTTPException(400, {"code": "invalid_request", "message": "metadata too large"})
    if body.redirect_url and not body.redirect_url.startswith(("https://", "http://")):
        raise HTTPException(400, {"code": "invalid_request", "message": "redirect_url must be http(s)"})
    try:
        inv, created = inv_svc.create_invoice(db, inv_svc.NewInvoice(**body.model_dump()), mode=p.key.mode,
                                              api_key_id=p.key.id, idempotency_key=idempotency_key)
    except inv_svc.InvoiceError as exc:
        _err(exc)
    if created:
        audit.record(db, p.actor(request), "invoice.created", inv.id, number=inv.number, via="api")
    return inv_svc.merchant_view(db, inv)


@router.get("/invoices")
def list_(status: str | None = None, order_id: str | None = None, limit: int = Query(25, le=100),
          offset: int = 0, p: ApiPrincipal = Depends(scope("invoices:read")), db: Session = DB):
    stmt = select(Invoice).where(Invoice.mode == p.key.mode)
    if status:
        stmt = stmt.where(Invoice.status.in_(status.split(",")))
    if order_id:
        stmt = stmt.where(Invoice.order_id == order_id)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(Invoice.created_at.desc()).limit(limit).offset(offset)).scalars()
    return {"data": [inv_svc.merchant_view(db, i) for i in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/invoices/{invoice_id}")
def get(invoice_id: str, p: ApiPrincipal = Depends(scope("invoices:read")), db: Session = DB):
    return inv_svc.merchant_view(db, _own(db, p, invoice_id))


@router.post("/invoices/{invoice_id}/cancel")
def cancel(invoice_id: str, request: Request, p: ApiPrincipal = Depends(scope("invoices:write")),
           db: Session = DB):
    inv = _own(db, p, invoice_id)
    try:
        inv_svc.cancel(db, inv)
    except inv_svc.InvoiceError as exc:
        _err(exc)
    audit.record(db, p.actor(request), "invoice.cancelled", inv.id, via="api")
    return inv_svc.merchant_view(db, inv)
