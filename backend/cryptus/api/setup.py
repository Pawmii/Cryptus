"""First-run installation wizard API.

Until an owner account exists, the wizard is protected by a one-time setup
token printed to the server log and written to ``<data_dir>/setup-token``.
This prevents anyone who reaches a fresh, internet-exposed instance from
claiming it.
"""
from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .. import __version__
from ..config import get_settings
from ..db import get_db, utcnow
from ..models import User
from ..security import constant_eq, hash_password, password_problems
from ..services import audit, infra, settings as settings_svc, signer_client
from ..services.rates import is_fiat
from .auth import start_session, user_view

router = APIRouter(prefix="/api/setup", tags=["setup"])
log = logging.getLogger("cryptus.setup")


def setup_token() -> str:
    s = get_settings()
    if s.setup_token:
        return s.setup_token
    p = s.data_dir / "setup-token"
    if not p.exists():
        s.data_dir.mkdir(parents=True, exist_ok=True)
        p.write_text(secrets.token_urlsafe(18))
    return p.read_text().strip()


def ensure_setup_token(db: Session) -> None:
    if db.execute(select(func.count()).select_from(User)).scalar_one() == 0:
        log.warning("CRYPTUS is not set up yet. Setup token: %s  (also in %s/setup-token)", setup_token(),
                    get_settings().data_dir)


def _has_owner(db: Session) -> bool:
    return db.execute(select(func.count()).select_from(User)).scalar_one() > 0


@router.get("/status")
def status(db: Session = Depends(get_db)):
    s = get_settings()
    try:
        db.execute(text("SELECT 1"))
        db_ok, dialect = True, db.get_bind().dialect.name
    except Exception:  # pragma: no cover
        db_ok, dialect = False, None
    signer = {"configured": signer_client.configured()}
    if signer["configured"]:
        try:
            h = signer_client.health()
            signer.update(ok=True, halted=h.get("halted"), networks=h.get("networks"))
        except signer_client.SignerError as exc:
            signer.update(ok=False, error=str(exc))
    return {
        "version": __version__, "has_owner": _has_owner(db), "completed": settings_svc.setup_completed(db),
        "checks": {
            "database": {"ok": db_ok, "dialect": dialect,
                         "note": "SQLite is fine for evaluation; use PostgreSQL in production" if dialect == "sqlite" else None},
            "secret_key": {"ok": bool(s.secret_key), "note": None if s.secret_key else
                           "CRYPTUS_SECRET_KEY is not set (development key in use)"},
            "public_url": {"ok": s.public_url.startswith("https://"), "value": s.public_url},
            "environment": {"ok": True, "value": s.env},
            "signer": signer,
        },
    }


class TokenIn(BaseModel):
    token: str = Field(max_length=200)


@router.post("/verify-token")
def verify_token(body: TokenIn, db: Session = Depends(get_db)):
    if _has_owner(db):
        raise HTTPException(409, "setup has already been completed")
    if not constant_eq(body.token.strip(), setup_token()):
        raise HTTPException(403, "invalid setup token")
    return {"ok": True}


class OwnerIn(BaseModel):
    email: str = Field(max_length=254)
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(max_length=1024)


class BusinessIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    website: str = Field("", max_length=300)
    support_email: str = Field("", max_length=254)
    default_currency: str = "USD"
    default_expiry_minutes: int = Field(30, ge=5, le=10080)


class CompleteIn(BaseModel):
    token: str
    owner: OwnerIn
    business: BusinessIn


@router.post("/initialize")
def initialize(body: CompleteIn, request: Request, response: Response, db: Session = Depends(get_db)):
    if _has_owner(db):
        raise HTTPException(409, "setup has already been completed")
    if not constant_eq(body.token.strip(), setup_token()):
        raise HTTPException(403, "invalid setup token")
    email = body.owner.email.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(400, "invalid email address")
    if probs := password_problems(body.owner.password):
        raise HTTPException(400, "password " + "; ".join(probs))
    if not is_fiat(body.business.default_currency):
        raise HTTPException(400, "unsupported currency")
    owner = User(email=email, name=body.owner.name.strip(), role="owner",
                 password_hash=hash_password(body.owner.password))
    db.add(owner)
    db.flush()
    settings_svc.put(db, "business", {"name": body.business.name.strip(), "website": body.business.website.strip(),
                                      "support_email": body.business.support_email.strip()})
    settings_svc.put(db, "invoice", {"default_currency": body.business.default_currency.upper(),
                                     "default_expiry_minutes": body.business.default_expiry_minutes})
    infra.seed_assets(db)
    settings_svc.put(db, "setup", {"completed": True, "completed_at": utcnow().isoformat()})
    from .deps import client_ip
    audit.record(db, audit.Actor("setup", owner.id, email, client_ip(request)), "setup.completed", owner.id,
                 business=body.business.name)
    s = start_session(db, response, request, owner, mfa_pending=False)
    owner.last_login_at = utcnow()
    p = get_settings().data_dir / "setup-token"
    if p.exists():
        p.unlink()
    return {"user": user_view(owner), "csrf_token": s.csrf_token}
