"""Event log and signed webhook delivery with retries.

Signature scheme (verify on your server):

    Cryptus-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, f"{t}.{raw_body}")>

Reject requests whose timestamp is more than five minutes old, and compare
signatures in constant time. Deliveries are at-least-once; de-duplicate on
the ``id`` field of the payload (the event id).
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import time
from datetime import timedelta
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import utcnow
from ..models import Event, WebhookDelivery, WebhookEndpoint
from ..security import decrypt_secret

log = logging.getLogger("cryptus.webhooks")

EVENT_TYPES = [
    "invoice.created", "invoice.processing", "invoice.paid", "invoice.underpaid", "invoice.expired",
    "invoice.cancelled", "invoice.reverted", "invoice.needs_review",
    "payment.detected", "payment.confirmed", "payment.orphaned",
    "sweep.broadcast", "sweep.confirmed", "sweep.failed",
]
BACKOFF = [30, 120, 600, 1800, 3600, 3 * 3600, 6 * 3600, 12 * 3600, 24 * 3600]


def sign(secret: str, body: bytes, ts: int | None = None) -> str:
    t = ts or int(time.time())
    mac = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def verify(secret: str, body: bytes, header: str, tolerance: int = 300) -> bool:
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        t = int(parts["t"])
    except (ValueError, KeyError):
        return False
    if abs(time.time() - t) > tolerance:
        return False
    expected = sign(secret, body, t).split("v1=")[1]
    return hmac.compare_digest(expected, parts.get("v1", ""))


def emit(db: Session, type_: str, data: dict, *, invoice_id: str | None = None, mode: str = "live") -> Event:
    ev = Event(type=type_, invoice_id=invoice_id, mode=mode, payload=data)
    db.add(ev)
    db.flush()
    endpoints = db.execute(select(WebhookEndpoint).where(WebhookEndpoint.active.is_(True),
                                                         WebhookEndpoint.mode == mode)).scalars()
    for ep in endpoints:
        if not ep.events or type_ in ep.events:
            db.add(WebhookDelivery(endpoint_id=ep.id, event_id=ev.id, status="pending", next_attempt_at=utcnow()))
    return ev


def envelope(ev: Event) -> dict:
    return {"id": ev.id, "type": ev.type, "created_at": ev.created_at.isoformat(), "mode": ev.mode,
            "data": ev.payload}


class UnsafeTarget(ValueError):
    pass


def check_target(url: str) -> None:
    """Refuse webhook targets on private networks (SSRF) unless explicitly allowed."""
    u = urlparse(url)
    if u.scheme not in ("https", "http"):
        raise UnsafeTarget("webhook URL must use http(s)")
    s = get_settings()
    if s.is_production and u.scheme != "https" and not s.webhook_allow_private_targets:
        raise UnsafeTarget("webhook URL must use https in production")
    if s.webhook_allow_private_targets:
        return
    host = u.hostname or ""
    try:
        infos = socket.getaddrinfo(host, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeTarget(f"cannot resolve {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast \
                or ip.is_unspecified:
            raise UnsafeTarget(f"{host} resolves to a non-public address ({ip})")


def deliver_one(db: Session, d: WebhookDelivery, client: httpx.Client) -> None:
    ep = db.get(WebhookEndpoint, d.endpoint_id)
    if ep is None or not ep.active:
        d.status = "failed"
        d.last_error = "endpoint disabled"
        return
    body = json.dumps(envelope(d.event), separators=(",", ":"), default=str).encode()
    secret = decrypt_secret(ep.secret_enc) or ""
    headers = {"Content-Type": "application/json", "User-Agent": "Cryptus-Webhooks/1.0",
               "Cryptus-Event": d.event.type, "Cryptus-Delivery": d.id, "Cryptus-Signature": sign(secret, body)}
    d.attempts += 1
    try:
        check_target(ep.url)
        r = client.post(ep.url, content=body, headers=headers)
        d.last_status_code = r.status_code
        if 200 <= r.status_code < 300:
            d.status, d.delivered_at, d.last_error = "succeeded", utcnow(), None
            return
        d.last_error = f"HTTP {r.status_code}: {r.text[:300]}"
    except (httpx.HTTPError, UnsafeTarget) as exc:
        d.last_error = str(exc)[:500]
    if d.attempts > len(BACKOFF):
        d.status = "dead"
    else:
        d.status = "pending"
        d.next_attempt_at = utcnow() + timedelta(seconds=BACKOFF[d.attempts - 1])


def deliver_due(db: Session, limit: int = 50) -> int:
    due = db.execute(select(WebhookDelivery).where(WebhookDelivery.status == "pending",
                                                   WebhookDelivery.next_attempt_at <= utcnow())
                     .order_by(WebhookDelivery.next_attempt_at).limit(limit)).scalars().all()
    if not due:
        return 0
    with httpx.Client(timeout=10, follow_redirects=False) as client:
        for d in due:
            deliver_one(db, d, client)
            db.commit()
    return len(due)
