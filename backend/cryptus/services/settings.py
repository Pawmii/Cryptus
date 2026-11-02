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


def put(db: Session, section: str, values: dict) -> dict:
    allowed = DEFAULTS.get(section)
    if allowed is None:
        raise KeyError(section)
    current = get(db, section)
    for k, v in values.items():
        if k in allowed:
            current[k] = v
    row = db.get(Setting, section)
    if row is None:
        db.add(Setting(key=section, value=current))
    else:
        row.value = current
    db.flush()
    return current


def setup_completed(db: Session) -> bool:
    return bool(get(db, "setup").get("completed"))
