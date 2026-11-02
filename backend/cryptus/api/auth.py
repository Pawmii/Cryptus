from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db, utcnow
from ..models import LoginAttempt, User, UserSession
from ..security import (decrypt_secret, encrypt_secret, hash_password, needs_rehash, new_session_token,
                        new_totp_secret, password_problems, token_digest, totp_uri, verify_password, verify_totp)
from ..services import audit, settings as settings_svc
from .deps import SESSION_COOKIE, Principal, client_ip, current, current_mfa_pending

router = APIRouter(prefix="/api/auth", tags=["auth"])

WINDOW = timedelta(minutes=15)
MAX_PER_BUCKET = 10
LOCK_AFTER = 8
LOCK_FOR = timedelta(minutes=15)


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=1024)


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=10)


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(max_length=1024)


def user_view(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "role": u.role, "totp_enabled": u.totp_enabled,
            "is_active": u.is_active, "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "created_at": u.created_at.isoformat()}


def _throttle(db: Session, *buckets: str) -> None:
    since = utcnow() - WINDOW
    db.execute(delete(LoginAttempt).where(LoginAttempt.at < since))
    for b in buckets:
        n = db.execute(select(func.count()).select_from(LoginAttempt)
                       .where(LoginAttempt.bucket == b, LoginAttempt.at >= since)).scalar_one()
        if n >= MAX_PER_BUCKET:
            raise HTTPException(429, "too many attempts — try again later")


def start_session(db: Session, response: Response, request: Request, user: User, mfa_pending: bool) -> UserSession:
    token = new_session_token()
    hours = int(settings_svc.get(db, "security")["session_hours"])
    s = UserSession(id=token_digest(token), user_id=user.id, csrf_token=secrets.token_urlsafe(24),
                    mfa_pending=mfa_pending, ip=client_ip(request),
                    user_agent=(request.headers.get("user-agent") or "")[:255],
                    expires_at=utcnow() + (timedelta(minutes=5) if mfa_pending else timedelta(hours=hours)))
    db.add(s)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=get_settings().cookie_secure,
                        samesite="strict", max_age=hours * 3600, path="/")
    return s


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = client_ip(request)
    email = body.email.strip().lower()
    _throttle(db, f"ip:{ip}", f"acct:{email}")
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    now = utcnow()
    ok = verify_password(user.password_hash if user else None, body.password)
    if user and user.locked_until and user.locked_until > now:
        ok = False
    if not ok or not user or not user.is_active:
        db.add(LoginAttempt(bucket=f"ip:{ip}"))
        db.add(LoginAttempt(bucket=f"acct:{email}"))
        if user:
            user.failed_logins += 1
            if user.failed_logins >= LOCK_AFTER:
                user.locked_until, user.failed_logins = now + LOCK_FOR, 0
            audit.record(db, audit.Actor("user", user.id, email, ip), "auth.login_failed", user.id)
        db.commit()
        raise HTTPException(401, "invalid email or password")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.failed_logins, user.locked_until = 0, None
    s = start_session(db, response, request, user, mfa_pending=user.totp_enabled)
    if not user.totp_enabled:
        user.last_login_at = now
        audit.record(db, audit.Actor("user", user.id, email, ip), "auth.login", user.id)
    return {"mfa_required": user.totp_enabled, "csrf_token": s.csrf_token,
            "user": None if user.totp_enabled else user_view(user)}


@router.post("/mfa")
def mfa(body: CodeIn, request: Request, p: Principal = Depends(current_mfa_pending), db: Session = Depends(get_db)):
    if not p.session.mfa_pending:
        return {"ok": True, "user": user_view(p.user), "csrf_token": p.session.csrf_token}
    ip = client_ip(request)
    _throttle(db, f"mfa:{p.user.id}")
    if not verify_totp(decrypt_secret(p.user.totp_secret_enc) or "", body.code):
        db.add(LoginAttempt(bucket=f"mfa:{p.user.id}"))
        db.commit()
        raise HTTPException(401, "invalid code")
    hours = int(settings_svc.get(db, "security")["session_hours"])
    p.session.mfa_pending = False
    p.session.expires_at = utcnow() + timedelta(hours=hours)
    p.user.last_login_at = utcnow()
    audit.record(db, audit.Actor("user", p.user.id, p.user.email, ip), "auth.login", p.user.id, mfa=True)
    return {"ok": True, "user": user_view(p.user), "csrf_token": p.session.csrf_token}


@router.post("/logout")
def logout(response: Response, p: Principal = Depends(current), db: Session = Depends(get_db)):
    db.delete(p.session)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(p: Principal = Depends(current), db: Session = Depends(get_db)):
    sec = settings_svc.get(db, "security")
    return {"user": user_view(p.user), "csrf_token": p.session.csrf_token,
            "mfa_enforced": bool(sec.get("require_mfa_for_admins")) and p.user.role in ("admin", "owner")
            and not p.user.totp_enabled}


@router.post("/password")
def change_password(body: PasswordChangeIn, request: Request, p: Principal = Depends(current),
                    db: Session = Depends(get_db)):
    if not verify_password(p.user.password_hash, body.current_password):
        raise HTTPException(400, "current password is incorrect")
    if probs := password_problems(body.new_password):
        raise HTTPException(400, "password " + "; ".join(probs))
    p.user.password_hash = hash_password(body.new_password)
    db.execute(delete(UserSession).where(UserSession.user_id == p.user.id, UserSession.id != p.session.id))
    audit.record(db, p.actor(request), "auth.password_changed", p.user.id)
    return {"ok": True}


@router.post("/totp/begin")
def totp_begin(p: Principal = Depends(current), db: Session = Depends(get_db)):
    if p.user.totp_enabled:
        raise HTTPException(400, "two-factor authentication is already enabled")
    secret = new_totp_secret()
    p.user.totp_secret_enc = encrypt_secret(secret)
    import segno
    uri = totp_uri(secret, p.user.email)
    return {"secret": secret, "uri": uri,
            "qr_svg": segno.make(uri, error="m").svg_inline(scale=5, dark="#09090B", light="#F4F4F5", border=2)}


@router.post("/totp/confirm")
def totp_confirm(body: CodeIn, request: Request, p: Principal = Depends(current), db: Session = Depends(get_db)):
    secret = decrypt_secret(p.user.totp_secret_enc)
    if not secret or not verify_totp(secret, body.code):
        raise HTTPException(400, "invalid code")
    p.user.totp_enabled = True
    audit.record(db, p.actor(request), "auth.totp_enabled", p.user.id)
    return {"ok": True}


@router.post("/totp/disable")
def totp_disable(body: CodeIn, request: Request, p: Principal = Depends(current), db: Session = Depends(get_db)):
    secret = decrypt_secret(p.user.totp_secret_enc)
    if not p.user.totp_enabled or not secret or not verify_totp(secret, body.code):
        raise HTTPException(400, "invalid code")
    p.user.totp_enabled, p.user.totp_secret_enc = False, None
    audit.record(db, p.actor(request), "auth.totp_disabled", p.user.id)
    return {"ok": True}


@router.get("/sessions")
def sessions(p: Principal = Depends(current), db: Session = Depends(get_db)):
    rows = db.execute(select(UserSession).where(UserSession.user_id == p.user.id, UserSession.expires_at > utcnow())
                      .order_by(UserSession.last_seen_at.desc())).scalars()
    return [{"id": s.id[:12], "current": s.id == p.session.id, "ip": s.ip, "user_agent": s.user_agent,
             "created_at": s.created_at.isoformat(), "last_seen_at": s.last_seen_at.isoformat()} for s in rows]


@router.post("/sessions/revoke-others")
def revoke_others(request: Request, p: Principal = Depends(current), db: Session = Depends(get_db)):
    db.execute(delete(UserSession).where(UserSession.user_id == p.user.id, UserSession.id != p.session.id))
    audit.record(db, p.actor(request), "auth.sessions_revoked", p.user.id)
    return {"ok": True}
