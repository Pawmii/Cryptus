from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, UTCDateTime, utcnow

# Amounts are stored as decimal integer strings in atomic units. This is exact
# for uint256 on every database CRYPTUS supports.
Atomic = String(80)


def _uuid() -> str:
    return uuid.uuid4().hex


def TS(nullable: bool = True, **kw):  # noqa: N802
    return mapped_column(UTCDateTime(), nullable=nullable, **kw)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | list | str | int | bool | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = TS(False, default=utcnow, onupdate=utcnow)


# ── Identity ──────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16))  # owner | admin | operator | viewer
    totp_secret_enc: Mapped[str | None] = mapped_column(Text)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = TS()
    last_login_at: Mapped[datetime | None] = TS()
    created_at: Mapped[datetime] = TS(False, default=utcnow)


class UserSession(Base):
    __tablename__ = "user_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 of the cookie token
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    mfa_pending: Mapped[bool] = mapped_column(Boolean, default=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = TS(False, default=utcnow)
    last_seen_at: Mapped[datetime] = TS(False, default=utcnow)
    expires_at: Mapped[datetime] = TS(False)
    user: Mapped[User] = relationship()


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bucket: Mapped[str] = mapped_column(String(160), index=True)
    at: Mapped[datetime] = TS(False, default=utcnow)


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120))
    prefix: Mapped[str] = mapped_column(String(24), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    mode: Mapped[str] = mapped_column(String(8))  # live | test
    created_by: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = TS(False, default=utcnow)
    last_used_at: Mapped[datetime | None] = TS()
    revoked_at: Mapped[datetime | None] = TS()


# ── Infrastructure configuration ──────────────────────────────────────────

class NetworkConfig(Base):
    __tablename__ = "network_configs"
    network: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    backend: Mapped[str] = mapped_column(String(32))
    endpoint_url: Mapped[str | None] = mapped_column(Text)
    credentials_enc: Mapped[str | None] = mapped_column(Text)
    confirmations: Mapped[int | None] = mapped_column(Integer)
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    scan_cursor: Mapped[dict] = mapped_column(JSON, default=dict)
    health: Mapped[dict] = mapped_column(JSON, default=dict)
    health_checked_at: Mapped[datetime | None] = TS()
    updated_at: Mapped[datetime] = TS(False, default=utcnow, onupdate=utcnow)


class Keychain(Base):
    """A source of deterministic deposit addresses (public material only)."""
    __tablename__ = "keychains"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    family: Mapped[str] = mapped_column(String(16))
    scope: Mapped[str] = mapped_column(String(32))  # bitcoin: network key; evm: "evm"; ton: network key
    label: Mapped[str] = mapped_column(String(120))
    public_material: Mapped[str] = mapped_column(Text)  # xpub / zpub / ed25519 pubkey hex / direct: wallet address
    mode: Mapped[str] = mapped_column(String(16))  # watch_only | managed | direct (one wallet, memo per deposit)
    signer_key_id: Mapped[str | None] = mapped_column(String(64))
    master_fingerprint: Mapped[str | None] = mapped_column(String(8))
    account_path: Mapped[str | None] = mapped_column(String(64))
    next_index: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    verified_at: Mapped[datetime | None] = TS()
    created_at: Mapped[datetime] = TS(False, default=utcnow)
    __table_args__ = (UniqueConstraint("family", "public_material", name="uq_keychain_material"),)


class NetworkWallet(Base):
    __tablename__ = "network_wallets"
    network: Mapped[str] = mapped_column(String(32), primary_key=True)
    keychain_id: Mapped[str] = mapped_column(ForeignKey("keychains.id"))
    destination: Mapped[str | None] = mapped_column(String(128))
    destination_confirmed_by: Mapped[str | None] = mapped_column(String(32))
    destination_confirmed_at: Mapped[datetime | None] = TS()
    sweep_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    sweep_interval_minutes: Mapped[int] = mapped_column(Integer, default=360)
    sweep_thresholds: Mapped[dict] = mapped_column(JSON, default=dict)  # asset_id -> min human amount
    max_fee: Mapped[dict] = mapped_column(JSON, default=dict)  # e.g. {"native": "0.002"} human units
    last_sweep_run_at: Mapped[datetime | None] = TS()
    keychain: Mapped[Keychain] = relationship()


class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    network: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(80))
    decimals: Mapped[int] = mapped_column(Integer)
    contract: Mapped[str | None] = mapped_column(String(128))
    coingecko_id: Mapped[str | None] = mapped_column(String(80))
    fixed_usd_peg: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_at: Mapped[datetime | None] = TS()
    verification: Mapped[dict] = mapped_column(JSON, default=dict)
    min_amount: Mapped[str | None] = mapped_column(String(40))


# ── Commerce ──────────────────────────────────────────────────────────────

class InvoiceSequence(Base):
    __tablename__ = "invoice_sequences"
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last: Mapped[int] = mapped_column(Integer, default=0)


class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    number: Mapped[str] = mapped_column(String(24), unique=True)
    mode: Mapped[str] = mapped_column(String(8), default="live")  # live | test (test = testnet assets only)
    status: Mapped[str] = mapped_column(String(16), index=True)
    price_amount: Mapped[str] = mapped_column(String(40))
    price_currency: Mapped[str] = mapped_column(String(160))  # ISO-4217 code or canonical asset id
    allowed_assets: Mapped[list] = mapped_column(JSON, default=list)
    order_id: Mapped[str | None] = mapped_column(String(128), index=True)
    description: Mapped[str | None] = mapped_column(String(500))
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    redirect_url: Mapped[str | None] = mapped_column(Text)
    active_deposit_id: Mapped[str | None] = mapped_column(String(32))
    overpaid: Mapped[bool] = mapped_column(Boolean, default=False)
    paid_late: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    api_key_id: Mapped[str | None] = mapped_column(String(32))
    created_by: Mapped[str | None] = mapped_column(String(32))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = TS(False, default=utcnow, index=True)
    expires_at: Mapped[datetime] = TS(False)
    detected_at: Mapped[datetime | None] = TS()
    paid_at: Mapped[datetime | None] = TS()
    cancelled_at: Mapped[datetime | None] = TS()
    deposits: Mapped[list["DepositAddress"]] = relationship(back_populates="invoice", order_by="DepositAddress.created_at")
    __table_args__ = (UniqueConstraint("api_key_id", "idempotency_key", name="uq_invoice_idempotency"),)


class DepositAddress(Base):
    __tablename__ = "deposit_addresses"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    keychain_id: Mapped[str] = mapped_column(ForeignKey("keychains.id"))
    derivation_index: Mapped[int] = mapped_column(Integer)
    derivation_path: Mapped[str] = mapped_column(String(64))
    network: Mapped[str] = mapped_column(String(32))
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    address: Mapped[str] = mapped_column(String(128))  # canonical comparison form
    display_address: Mapped[str] = mapped_column(String(128))
    memo: Mapped[str] = mapped_column(String(64), default="", server_default="")  # direct mode: required comment
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), index=True)
    amount_due: Mapped[str] = mapped_column(Atomic)
    rate: Mapped[str | None] = mapped_column(String(64))  # price currency per 1 asset unit at quote time
    rate_source: Mapped[str | None] = mapped_column(String(64))
    prepared: Mapped[bool] = mapped_column(Boolean, default=False)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    monitor_until: Mapped[datetime] = TS(False)
    swept_at: Mapped[datetime | None] = TS()
    created_at: Mapped[datetime] = TS(False, default=utcnow)
    invoice: Mapped[Invoice] = relationship(back_populates="deposits")
    __table_args__ = (
        UniqueConstraint("keychain_id", "derivation_index", name="uq_deposit_derivation"),
        UniqueConstraint("network", "address", "memo", name="uq_deposit_address"),
        Index("ix_deposit_monitor", "network", "monitor_until"),
    )


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    network: Mapped[str] = mapped_column(String(32))
    asset_id: Mapped[str] = mapped_column(String(160))
    txid: Mapped[str] = mapped_column(String(128))
    output_index: Mapped[int] = mapped_column(BigInteger)
    deposit_id: Mapped[str] = mapped_column(ForeignKey("deposit_addresses.id"), index=True)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), index=True)
    amount: Mapped[str] = mapped_column(Atomic)
    sender: Mapped[str | None] = mapped_column(String(128))
    block_number: Mapped[int | None] = mapped_column(BigInteger)
    block_hash: Mapped[str | None] = mapped_column(String(128))
    confirmations: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16))  # pending | confirmed | orphaned | rejected
    source: Mapped[str] = mapped_column(String(24), default="scan")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    first_seen_at: Mapped[datetime] = TS(False, default=utcnow)
    confirmed_at: Mapped[datetime | None] = TS()
    updated_at: Mapped[datetime] = TS(False, default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("network", "txid", "output_index", "asset_id", name="uq_payment_output"),)


# ── Integrations ──────────────────────────────────────────────────────────

class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    url: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(String(200))
    secret_enc: Mapped[str] = mapped_column(Text)
    events: Mapped[list] = mapped_column(JSON, default=list)  # empty = all
    mode: Mapped[str] = mapped_column(String(8), default="live")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = TS(False, default=utcnow)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    type: Mapped[str] = mapped_column(String(48), index=True)
    mode: Mapped[str] = mapped_column(String(8), default="live")
    invoice_id: Mapped[str | None] = mapped_column(String(32), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = TS(False, default=utcnow, index=True)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    endpoint_id: Mapped[str] = mapped_column(ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"))
    status: Mapped[str] = mapped_column(String(12), index=True)  # pending | succeeded | failed | dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = TS(False, default=utcnow, index=True)
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = TS(False, default=utcnow)
    delivered_at: Mapped[datetime | None] = TS()
    event: Mapped[Event] = relationship()


# ── Treasury operations ───────────────────────────────────────────────────

class Sweep(Base):
    __tablename__ = "sweeps"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    network: Mapped[str] = mapped_column(String(32), index=True)
    asset_id: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(16))  # sweep | gas_topup
    deposit_ids: Mapped[list] = mapped_column(JSON, default=list)
    source_address: Mapped[str | None] = mapped_column(String(128))
    destination: Mapped[str] = mapped_column(String(128))
    amount: Mapped[str] = mapped_column(Atomic)
    fee_estimate: Mapped[str | None] = mapped_column(Atomic)
    status: Mapped[str] = mapped_column(String(24), index=True)
    # planned | awaiting_approval | awaiting_external_signature | signing | broadcast | confirmed | failed | cancelled
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    approved_by: Mapped[str | None] = mapped_column(String(32))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    txid: Mapped[str | None] = mapped_column(String(128))
    artifact: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    depends_on: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = TS(False, default=utcnow)
    updated_at: Mapped[datetime] = TS(False, default=utcnow, onupdate=utcnow)
    next_attempt_at: Mapped[datetime | None] = TS()


class AuditEntry(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = TS(False, default=utcnow, index=True)
    actor_type: Mapped[str] = mapped_column(String(12))  # user | api | system | setup
    actor_id: Mapped[str | None] = mapped_column(String(64))
    actor_label: Mapped[str | None] = mapped_column(String(254))
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str | None] = mapped_column(String(200))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
