"""Signer HTTP service.

Bind it to a private interface only (the gateway reaches it over an internal
network). Authentication: HMAC-SHA256 over ``<timestamp>.<body>`` with a shared
token; timestamps older than 60s and reused request ids are rejected.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from cryptus.core.networks import NETWORKS

from .keys import KeyRing, btc_account_path
from .policy import Policy, PolicyViolation
from .signing import SIGNERS


class AuditLog:
    """Append-only, hash-chained JSONL log of every signing decision."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.prev = "0" * 64
        self.seen: set[str] = set()
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    e = json.loads(line)
                    self.prev = e["hash"]
                    self.seen.add(e.get("request_id", ""))

    def append(self, entry: dict) -> None:
        with self.lock:
            entry = {**entry, "at": datetime.now(timezone.utc).isoformat(), "prev": self.prev}
            entry["hash"] = hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()
            with self.path.open("a") as f:
                f.write(json.dumps(entry, sort_keys=True) + "\n")
                f.flush()
                os.fsync(f.fileno())
            self.prev = entry["hash"]
            if entry.get("request_id"):
                self.seen.add(entry["request_id"])

    def spent_24h(self, asset_id: str) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        total = 0
        if not self.path.exists():
            return 0
        for line in self.path.read_text().splitlines():
            e = json.loads(line)
            if e.get("decision") == "signed" and e.get("asset") == asset_id and \
                    datetime.fromisoformat(e["at"]) > cutoff:
                total += int(e.get("amount", 0))
        return total


def create_app(ring: KeyRing, policy: Policy, token: str, data_dir: Path) -> FastAPI:
    if len(token) < 32:
        raise ValueError("signer token must be at least 32 characters")
    app = FastAPI(title="CRYPTUS Signer", docs_url=None, redoc_url=None, openapi_url=None)
    audit = AuditLog(data_dir / "signer-audit.jsonl")
    sign_lock = threading.Lock()

    async def _auth(request: Request) -> bytes:
        body = await request.body()
        ts = request.headers.get("X-Cryptus-Timestamp", "")
        sig = request.headers.get("X-Cryptus-Signature", "")
        if not ts.isdigit() or abs(time.time() - int(ts)) > 60:
            raise HTTPException(401, "stale or missing timestamp")
        expected = hmac.new(token.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            raise HTTPException(401, "bad signature")
        return body

    @app.get("/v1/health")
    async def health(request: Request):
        await _auth(request)
        return {"ok": True, "halted": policy.halted, "fingerprint": ring.fingerprint,
                "networks": sorted(policy.networks)}

    @app.get("/v1/policy")
    async def get_policy(request: Request):
        await _auth(request)
        return policy.summary()

    @app.get("/v1/keys")
    async def keys(request: Request):
        await _auth(request)
        out = []
        for key, net in NETWORKS.items():
            if net.status != "implemented":
                continue
            if net.family == "bitcoin":
                out.append({"network": key, "family": "bitcoin", "public_material": ring.btc_account_xpub(net),
                            "fingerprint": ring.fingerprint, "account_path": btc_account_path(net).replace("h", "'")})
            elif net.family == "ton":
                out.append({"network": key, "family": "ton", "public_material": ring.ton_public_key.hex(),
                            "fingerprint": None, "account_path": "ed25519 / wallet v4r2"})
        out.append({"network": "evm", "family": "evm", "public_material": ring.evm_account_xpub(),
                    "fingerprint": ring.fingerprint, "account_path": "m/44'/60'/0'"})
        return {"keys": out}

    @app.post("/v1/sign")
    async def sign(request: Request):
        body = await _auth(request)
        try:
            req = json.loads(body)
            rid, network, kind, payload = req["request_id"], req["network"], req["kind"], req["payload"]
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, "malformed request") from exc
        net = NETWORKS.get(network)
        if net is None or kind not in SIGNERS or SIGNERS[kind][0] != net.family:
            raise HTTPException(400, "unsupported network/kind combination")
        with sign_lock:
            if rid in audit.seen:
                raise HTTPException(409, "request id already used")
            try:
                pol = policy.for_network(network)
                signed = SIGNERS[kind][1](ring, net, payload, pol)
                limit = pol.daily_limits.get(signed.asset_id)
                if limit is not None and audit.spent_24h(signed.asset_id) + signed.amount > limit:
                    raise PolicyViolation(f"rolling 24h limit for {signed.asset_id} would be exceeded")
            except PolicyViolation as exc:
                audit.append({"request_id": rid, "network": network, "kind": kind, "decision": "refused",
                              "reason": str(exc)})
                return JSONResponse({"detail": f"policy: {exc}"}, status_code=403)
            except (KeyError, ValueError, TypeError) as exc:
                audit.append({"request_id": rid, "network": network, "kind": kind, "decision": "refused",
                              "reason": f"invalid payload: {exc}"})
                return JSONResponse({"detail": f"invalid payload: {exc}"}, status_code=400)
            audit.append({"request_id": rid, "network": network, "kind": kind, "decision": "signed",
                          "asset": signed.asset_id, "amount": str(signed.amount), "summary": signed.summary,
                          "txid": signed.result.get("txid") or signed.result.get("msg_hash")})
        return signed.result

    return app
