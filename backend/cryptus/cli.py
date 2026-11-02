"""cryptus — gateway command line.

    cryptus migrate                 apply database migrations
    cryptus serve [--host --port]   run the web application
    cryptus worker                  run the background worker
    cryptus setup-token             print the one-time installation token
    cryptus reset-password EMAIL    set a new password for a user (from a trusted shell)
    cryptus audit-verify            verify the audit hash chain
"""
from __future__ import annotations

import argparse
import getpass
import sys


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="cryptus")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    s = sub.add_parser("serve")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--workers", type=int, default=2)
    sub.add_parser("worker")
    sub.add_parser("setup-token")
    r = sub.add_parser("reset-password")
    r.add_argument("email")
    sub.add_parser("audit-verify")
    a = p.parse_args(argv)

    if a.cmd == "migrate":
        from .migrate import upgrade
        upgrade()
        print("database is up to date")
    elif a.cmd == "serve":
        import uvicorn
        # Forwarded headers are evaluated by the app against CRYPTUS_TRUSTED_PROXIES, never blindly here.
        uvicorn.run("cryptus.app:app", host=a.host, port=a.port, workers=a.workers, proxy_headers=False,
                    access_log=False)
    elif a.cmd == "worker":
        from .worker import main as wmain
        wmain()
    elif a.cmd == "setup-token":
        from .api.setup import setup_token
        print(setup_token())
    elif a.cmd == "reset-password":
        from sqlalchemy import delete, select
        from .db import session_scope
        from .models import User, UserSession
        from .security import hash_password, password_problems
        pw = getpass.getpass("New password: ")
        if probs := password_problems(pw):
            sys.exit("password " + "; ".join(probs))
        with session_scope() as db:
            u = db.execute(select(User).where(User.email == a.email.lower())).scalar_one_or_none()
            if not u:
                sys.exit("no such user")
            u.password_hash, u.failed_logins, u.locked_until = hash_password(pw), 0, None
            db.execute(delete(UserSession).where(UserSession.user_id == u.id))
            from .services import audit
            audit.record(db, audit.Actor("system", None, "cli"), "auth.password_reset_cli", u.id)
        print("password updated; all sessions revoked")
    elif a.cmd == "audit-verify":
        from .db import session_scope
        from .services.audit import verify_chain
        with session_scope() as db:
            ok, at = verify_chain(db)
        print("audit chain intact" if ok else f"audit chain BROKEN at entry {at}")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
