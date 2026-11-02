from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db, utcnow
from ..models import ApiKey, User, UserSession
from ..security import constant_eq, token_digest
from ..services.audit import Actor

SESSION_COOKIE = "cryptus_session"
CSRF_HEADER = "X-CSRF-Token"
ROLES = {"viewer": 0, "operator": 1, "admin": 2, "owner": 3}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _is_trusted(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for entry in get_settings().trusted_proxies:
        try:
            if addr in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


def client_ip(request: Request) -> str:
    """Client address, honouring X-Forwarded-For only when it was set by a trusted proxy."""
    peer = request.client.host if request.client else "0.0.0.0"
    xff = request.headers.get("x-forwarded-for")
    if xff and _is_trusted(peer):
        for hop in reversed([h.strip() for h in xff.split(",")]):
            try:
                ipaddress.ip_address(hop)
            except ValueError:
                continue
            if not _is_trusted(hop):
                return hop
    return peer


@dataclass
class Principal:
    user: User
    session: UserSession

    def actor(self, request: Request) -> Actor:
        return Actor("user", self.user.id, self.user.email, client_ip(request))


def _session(request: Request, db: Session, allow_mfa_pending: bool = False) -> Principal:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, "authentication required")
    s = db.get(UserSession, token_digest(token))
    now = utcnow()
    if s is None or s.expires_at < now:
        raise HTTPException(401, "session expired")
    if s.mfa_pending and not allow_mfa_pending:
        raise HTTPException(401, "two-factor verification required")
    if not s.user.is_active:
        raise HTTPException(403, "account disabled")
    if request.method not in SAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not sent or not constant_eq(sent, s.csrf_token):
            raise HTTPException(403, "CSRF token missing or invalid")
    if now - s.last_seen_at > timedelta(minutes=1):
        s.last_seen_at = now
    return Principal(s.user, s)


def current(request: Request, db: Session = Depends(get_db)) -> Principal:
    return _session(request, db)


def current_mfa_pending(request: Request, db: Session = Depends(get_db)) -> Principal:
    return _session(request, db, allow_mfa_pending=True)


def require(role: str):
    def dep(p: Principal = Depends(current)) -> Principal:
        if ROLES.get(p.user.role, -1) < ROLES[role]:
            raise HTTPException(403, f"requires {role} role")
        return p
    return dep


@dataclass
class ApiPrincipal:
    key: ApiKey

    def actor(self, request: Request) -> Actor:
        return Actor("api", self.key.id, self.key.name, client_ip(request))


def api_key(request: Request, authorization: str | None = Header(None), db: Session = Depends(get_db)) -> ApiPrincipal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer API key", headers={"WWW-Authenticate": "Bearer"})
    raw = authorization[7:].strip()
    k = db.execute(select(ApiKey).where(ApiKey.key_hash == token_digest(raw))).scalar_one_or_none()
    if k is None or k.revoked_at is not None:
        raise HTTPException(401, "invalid API key", headers={"WWW-Authenticate": "Bearer"})
    now = utcnow()
    if not k.last_used_at or now - k.last_used_at > timedelta(minutes=1):
        k.last_used_at = now
    return ApiPrincipal(k)


def scope(name: str):
    def dep(p: ApiPrincipal = Depends(api_key)) -> ApiPrincipal:
        if name not in (p.key.scopes or []):
            raise HTTPException(403, f"API key lacks scope {name}")
        return p
    return dep
