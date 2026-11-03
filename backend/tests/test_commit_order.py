"""Every request commits its transaction *before* the response leaves the server.

With FastAPI's default ("request") scope, a yield-dependency's teardown — our commit — runs after the
response is sent. A client could then receive an invoice or a session that a failing commit never stored,
and an immediate follow-up request could miss it. Found on a real server (TestClient hides the race).
"""
import socket
import threading
import time

import httpx
import uvicorn

from cryptus.db import get_db


def _walk(dep):
    yield dep
    for d in dep.dependencies:
        yield from _walk(d)


def _routes(routes):
    for r in routes:
        inner = getattr(r, "original_router", None)  # FastAPI ≥ 0.16x keeps included routers nested
        yield from _routes(inner.routes) if inner is not None else [r]


def test_every_route_commits_before_responding(client):
    from fastapi.routing import APIRoute

    checked = 0
    for route in _routes(client.app.routes):
        if not isinstance(route, APIRoute):
            continue
        for d in _walk(route.dependant):
            if d.call is get_db:
                assert d.scope == "function", f"{route.path}: get_db must use Depends(get_db, scope='function')"
                checked += 1
    assert checked > 40


def test_follow_up_request_sees_the_session(client, db_url):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(client.app, host="127.0.0.1", port=port, log_level="warning",
                                           lifespan="off"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        with httpx.Client(base_url=f"http://127.0.0.1:{port}") as h:
            r = h.post("/api/setup/initialize", json={
                "token": "setup-token-for-tests",
                "owner": {"email": "owner@example.com", "name": "Owner", "password": "correct horse battery staple"},
                "business": {"name": "Acme"}})
            assert r.status_code == 200, r.text
            assert h.get("/api/auth/me").status_code == 200  # immediately, no delay
            for _ in range(5):
                h.post("/api/auth/logout", headers={"X-CSRF-Token": r.json()["csrf_token"]})
                r = h.post("/api/auth/login", json={"email": "owner@example.com",
                                                    "password": "correct horse battery staple"})
                assert r.status_code == 200 and h.get("/api/auth/me").status_code == 200
    finally:
        server.should_exit = True
        t.join(5)
