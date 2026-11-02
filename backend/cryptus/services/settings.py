"""Operational settings stored in the database, with typed defaults."""
from __future__ import annotations

import copy

from sqlalchemy.orm import Session

from ..models import Setting

DEFAULTS: dict[str, dict] = {
    "setup": {"completed": False, "completed_at": None},
    "business": {
        "name": "", "website": "", "support_email": "", "statement_descriptor": "",
    },
    "invoice": {
        "default_expiry_minutes": 30,
        "late_payment_window_minutes": 1440,
        "underpay_tolerance_bps": 0,
        "default_currency": "USD",
        "amount_display_decimals": 8,
    },
    "compliance": {
        "terms_url": "", "privacy_url": "", "refund_policy_url": "",
        "checkout_notice": "",
        "require_terms_acceptance": False,
        "max_invoice_amount": None,  # in default currency; None = unlimited
        "jurisdiction_note": "",
    },
    "security": {"session_hours": 12, "require_mfa_for_admins": False},
    "treasury": {"sweeps_halted": False, "require_approval": True},
    "rates": {"provider": "coingecko", "cache_seconds": 60, "api_key_enc": None, "pro": False},
}


def get(db: Session, section: str) -> dict:
    row = db.get(Setting, section)
    base = copy.deepcopy(DEFAULTS.get(section, {}))
    if row and isinstance(row.value, dict):
        base.update(row.value)
    return base


class SettingsError(ValueError):
    pass


# Inclusive bounds for integer settings. A bad value here could lock every operator out
# (e.g. session_hours = 0), so they are enforced on write.
INT_BOUNDS: dict[tuple[str, str], tuple[int, int]] = {
    ("security", "session_hours"): (1, 720),
    ("invoice", "default_expiry_minutes"): (5, 10080),
    ("invoice", "late_payment_window_minutes"): (0, 43200),
    ("invoice", "underpay_tolerance_bps"): (0, 1000),
    ("invoice", "amount_display_decimals"): (2, 18),
    ("rates", "cache_seconds"): (10, 3600),
}
URL_KEYS = {("compliance", "terms_url"), ("compliance", "privacy_url"), ("compliance", "refund_policy_url"),
            ("business", "website")}
FREE_KEYS = {("setup", "completed_at"), ("rates", "api_key_enc"), ("compliance", "max_invoice_amount")}


def _validate(section: str, key: str, value):
    default = DEFAULTS[section][key]
    if (section, key) == ("compliance", "max_invoice_amount"):
        if value in (None, ""):
            return None
        try:
            v = float(value)
        except (TypeError, ValueError) as exc:
            raise SettingsError(f"{key} must be a number") from exc
        if v <= 0:
            raise SettingsError(f"{key} must be positive")
        return value
    if (section, key) in FREE_KEYS:
        return value
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise SettingsError(f"{key} must be true or false")
        return value
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise SettingsError(f"{key} must be a whole number")
        try:
            v = int(str(value))
        except ValueError as exc:
            raise SettingsError(f"{key} must be a whole number") from exc
        lo, hi = INT_BOUNDS.get((section, key), (0, 10**9))
        if not lo <= v <= hi:
            raise SettingsError(f"{key} must be between {lo} and {hi}")
        return v
    if isinstance(default, str):
        if not isinstance(value, str) or len(value) > 2000:
            raise SettingsError(f"{key} must be text (max 2000 characters)")
        v = value.strip()
        if (section, key) in URL_KEYS and v and not v.startswith(("https://", "http://")):
            raise SettingsError(f"{key} must start with https://")
        return v
    return value


def put(db: Session, section: str, values: dict) -> dict:
    allowed = DEFAULTS.get(section)
    if allowed is None:
        raise KeyError(section)
    current = get(db, section)
    for k, v in values.items():
        if k in allowed:
            current[k] = _validate(section, k, v)
    row = db.get(Setting, section)
    if row is None:
        db.add(Setting(key=section, value=current))
    else:
        row.value = current
    db.flush()
    return current


def setup_completed(db: Session) -> bool:
    return bool(get(db, "setup").get("completed"))
