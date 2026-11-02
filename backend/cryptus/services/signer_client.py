"""Client for the isolated CRYPTUS signer (managed signing mode).

Requests are authenticated with an HMAC over timestamp + body and carry a
unique request id; the signer rejects replays, enforces its own policy and
keeps its own audit log. The gateway can only *ask* — it cannot force a
signature.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid

import httpx

from ..config import get_settings


class SignerError(RuntimeError):
    pass


class SignerUnavailable(SignerError):
    pass


def _headers(token: str, body: bytes) -> dict:
    ts = str(int(time.time()))
    mac = hmac.new(token.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    return {"Content-Type": "application/json", "X-Cryptus-Timestamp": ts, "X-Cryptus-Signature": mac}


def configured() -> bool:
    s = get_settings()
    return bool(s.signer_url and s.signer_token)


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    s = get_settings()
    if not configured():
        raise SignerUnavailable("managed signing is not configured (CRYPTUS_SIGNER_URL / CRYPTUS_SIGNER_TOKEN)")
    body = json.dumps(payload or {}, separators=(",", ":"), sort_keys=True).encode()
    try:
        r = httpx.request(method, s.signer_url.rstrip("/") + path, content=body if method == "POST" else None,
                          headers=_headers(s.signer_token, body if method == "POST" else b""),
                          timeout=s.signer_timeout)
    except httpx.HTTPError as exc:
        raise SignerUnavailable(f"signer unreachable: {exc}") from exc
    try:
        data = r.json()
    except ValueError:
        data = {"error": r.text[:300]}
    if r.status_code >= 400:
        raise SignerError(data.get("detail") or data.get("error") or f"signer HTTP {r.status_code}")
    return data


def health() -> dict:
    return _request("GET", "/v1/health")


def keys() -> list[dict]:
    return _request("GET", "/v1/keys")["keys"]


def policy() -> dict:
    return _request("GET", "/v1/policy")


def sign(network: str, kind: str, payload: dict, request_id: str | None = None) -> dict:
    return _request("POST", "/v1/sign", {"request_id": request_id or uuid.uuid4().hex, "network": network,
                                         "kind": kind, "payload": payload})
