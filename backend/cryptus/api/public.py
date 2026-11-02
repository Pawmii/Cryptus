"""Public checkout endpoints used by the hosted payment page.

Privacy: no cookies, no account, no personal data. The only identifier is the
unguessable invoice id in the URL.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Invoice
from ..services import invoices as inv_svc

router = APIRouter(prefix="/api/public", tags=["checkout"])


def _inv(db: Session, invoice_id: str) -> Invoice:
    inv = db.get(Invoice, invoice_id) if len(invoice_id) == 32 and invoice_id.isalnum() else None
    if inv is None:
        raise HTTPException(404, "invoice not found")
    return inv


@router.get("/invoices/{invoice_id}")
def get_invoice(invoice_id: str, db: Session = Depends(get_db)):
    return inv_svc.public_view(db, _inv(db, invoice_id))


class SelectIn(BaseModel):
    asset: str


@router.post("/invoices/{invoice_id}/select")
def select_asset(invoice_id: str, body: SelectIn, db: Session = Depends(get_db)):
    inv = _inv(db, invoice_id)
    try:
        inv_svc.quote(db, inv, body.asset)
    except inv_svc.InvoiceError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    return inv_svc.public_view(db, inv)
