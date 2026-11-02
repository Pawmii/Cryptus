"""Tamper-evident audit trail.

Each entry stores the SHA-256 of the previous entry's hash and its own
canonical content. Any edit or deletion in the middle of the log breaks the
chain, which ``verify_chain`` detects.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import is_postgres, utcnow
from ..models import AuditEntry

GENESIS = "0" * 64


@dataclass
class Actor:
    type: str  # user | api | system | setup
    id: str | None = None
    label: str | None = None
    ip: str | None = None


SYSTEM = Actor("system", None, "cryptus-worker")


def _digest(prev: str, e: AuditEntry) -> str:
    body = json.dumps({
        "prev": prev, "at": e.at.isoformat(), "actor_type": e.actor_type, "actor_id": e.actor_id,
        "action": e.action, "target": e.target, "details": e.details, "ip": e.ip,
    }, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def record(db: Session, actor: Actor, action: str, target: str | None = None, **details) -> AuditEntry:
    if is_postgres(db):
        # Serialise appenders so the chain stays linear.
        db.execute(select(AuditEntry.id).order_by(AuditEntry.id.desc()).limit(1).with_for_update())
    last = db.execute(select(AuditEntry).order_by(AuditEntry.id.desc()).limit(1)).scalar_one_or_none()
    prev = last.hash if last else GENESIS
    e = AuditEntry(at=utcnow().replace(microsecond=0), actor_type=actor.type, actor_id=actor.id,
                   actor_label=actor.label, action=action, target=target,
                   details=json.loads(json.dumps(details, default=str)), ip=actor.ip, prev_hash=prev, hash="")
    e.hash = _digest(prev, e)
    db.add(e)
    db.flush()
    return e


def verify_chain(db: Session) -> tuple[bool, int | None]:
    prev = GENESIS
    for e in db.execute(select(AuditEntry).order_by(AuditEntry.id)).scalars():
        if e.prev_hash != prev or _digest(prev, e) != e.hash:
            return False, e.id
        prev = e.hash
    return True, None
