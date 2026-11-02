"""Background worker: payment monitoring, expiry, webhooks, consolidation.

Only one worker may be active at a time; a database lease enforces this so a
second replica stays on standby and takes over if the first stops renewing.
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import time
from datetime import timedelta

from sqlalchemy import select

from .chains.base import ChainError
from .config import get_settings
from .db import session_scope, utcnow
from .models import NetworkConfig, Setting
from .services import consolidation, monitor, webhooks

log = logging.getLogger("cryptus.worker")
LEASE = timedelta(seconds=90)
_stop = False


def _on_signal(*_):
    global _stop
    _stop = True


def acquire_lease(holder: str) -> bool:
    """Take or renew the single-worker lease. Row-locked on PostgreSQL so two workers cannot both win."""
    from datetime import datetime

    from sqlalchemy.exc import IntegrityError

    try:
        with session_scope() as db:
            row = db.get(Setting, "worker_lease", with_for_update=True)
            now = utcnow()
            if row is None:
                db.add(Setting(key="worker_lease", value={"holder": holder, "until": (now + LEASE).isoformat()}))
                return True
            v = row.value or {}
            until = datetime.fromisoformat(v["until"]) if v.get("until") else now
            if v.get("holder") == holder or until < now:
                row.value = {"holder": holder, "until": (now + LEASE).isoformat()}
                return True
            return False
    except IntegrityError:
        return False  # another worker inserted the lease first


class LeaseLost(RuntimeError):
    pass


def tick(holder: str | None = None) -> None:
    def renew() -> None:
        if holder and not acquire_lease(holder):
            raise LeaseLost("worker lease taken over by another instance")

    with session_scope() as db:
        keys = [n.network for n in db.execute(select(NetworkConfig).where(NetworkConfig.enabled.is_(True))).scalars()]
    for key in keys:
        renew()
        try:
            with session_scope() as db:
                r = monitor.run_network(db, key)
                if r.get("watched") or r.get("error"):
                    log.info("%s: %s", key, r)
        except ChainError as exc:
            log.warning("%s: chain error: %s", key, exc)
        except Exception:
            log.exception("%s: monitor failure", key)
    renew()
    with session_scope() as db:
        monitor.expire_due(db)
    with session_scope() as db:
        webhooks.deliver_due(db)
    renew()
    try:
        with session_scope() as db:
            consolidation.run(db)
    except Exception:
        log.exception("consolidation failure")
    with session_scope() as db:
        housekeeping(db)


def housekeeping(db) -> None:
    """Bounded tables: expired sessions and old throttle records."""
    from datetime import timedelta

    from sqlalchemy import delete

    from .models import LoginAttempt, UserSession

    now = utcnow()
    db.execute(delete(UserSession).where(UserSession.expires_at < now - timedelta(days=1)))
    db.execute(delete(LoginAttempt).where(LoginAttempt.at < now - timedelta(days=1)))


def main() -> None:
    s = get_settings()
    logging.basicConfig(level=s.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s.effective_secret()
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    holder = f"{socket.gethostname()}:{os.getpid()}"
    log.info("worker %s starting (interval %.0fs)", holder, s.worker_interval)
    while not _stop:
        started = time.monotonic()
        if acquire_lease(holder):
            try:
                tick(holder)
            except LeaseLost as exc:
                log.warning("%s", exc)
        else:
            log.debug("standby: another worker holds the lease")
        time.sleep(max(1.0, s.worker_interval - (time.monotonic() - started)))
    log.info("worker stopped")


if __name__ == "__main__":
    main()
