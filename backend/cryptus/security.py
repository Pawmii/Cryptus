from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from functools import lru_cache

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .config import get_settings

_ph = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2)
# A constant hash used to keep login timing uniform for unknown accounts.
_DUMMY_HASH = _ph.hash(secrets.token_hex(16))

MIN_PASSWORD_LENGTH = 12


def _derive(purpose: bytes, length: int = 32) -> bytes:
    secret = get_settings().effective_secret().encode()
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=b"cryptus/v1", info=purpose).derive(secret)


@lru_cache
def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(_derive(b"secrets-at-rest")))


@lru_cache
def _mac_key() -> bytes:
    return _derive(b"token-mac")


def reset_key_cache() -> None:
    _fernet.cache_clear()
    _mac_key.cache_clear()


# ── Secrets at rest ───────────────────────────────────────────────────────

def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("stored secret cannot be decrypted — was CRYPTUS_SECRET_KEY changed?") from exc


# ── Passwords ─────────────────────────────────────────────────────────────

def password_problems(password: str) -> list[str]:
    problems = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(set(password)) < 6:
        problems.append("is too repetitive")
    if password.lower() in {"cryptuscryptus", "password1234", "123456789012"}:
        problems.append("is too common")
    return problems


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(stored: str | None, password: str) -> bool:
    try:
        return _ph.verify(stored or _DUMMY_HASH, password) and stored is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored: str) -> bool:
    return _ph.check_needs_rehash(stored)


# ── Opaque tokens ─────────────────────────────────────────────────────────

def token_digest(token: str) -> str:
    """Keyed digest so a leaked database cannot be used to forge lookups."""
    return hmac.new(_mac_key(), token.encode(), hashlib.sha256).hexdigest()


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def new_api_key(mode: str) -> tuple[str, str]:
    """Return (full key, display prefix). Format: ck_live_<prefix>_<secret>."""
    prefix = secrets.token_hex(4)
    key = f"ck_{mode}_{prefix}_{secrets.token_urlsafe(32)}"
    return key, f"ck_{mode}_{prefix}"


def new_webhook_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def constant_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


# ── TOTP ──────────────────────────────────────────────────────────────────

def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="CRYPTUS")


def totp_step(secret: str, code: str) -> int | None:
    """Return the time step a code is valid for (±1 step of drift), or None. Callers reject reused steps."""
    import time

    code = code.strip().replace(" ", "")
    if not (code.isdigit() and len(code) == 6):
        return None
    totp = pyotp.TOTP(secret)
    now = int(time.time())
    for drift in (0, -1, 1):
        t = now + drift * totp.interval
        if hmac.compare_digest(totp.at(t), code):
            return t // totp.interval
    return None


def verify_totp(secret: str, code: str) -> bool:
    return totp_step(secret, code) is not None
