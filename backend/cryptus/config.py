from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process configuration. Operational settings (networks, wallets, policies)
    live in the database and are managed from the admin console; only
    bootstrap concerns and secrets belong here."""

    model_config = SettingsConfigDict(env_prefix="CRYPTUS_", env_file=".env", extra="ignore")

    env: str = Field("production", description="production | development | test")
    database_url: str = "sqlite:///./data/cryptus.db"
    data_dir: Path = Path("./data")

    # Master secret: signs sessions and derives the key that encrypts secrets
    # stored in the database (RPC credentials, webhook secrets, TOTP seeds).
    secret_key: str = ""

    public_url: str = "http://localhost:8000"
    trusted_proxies: list[str] = ["127.0.0.1", "::1"]
    cookie_secure: bool = True

    # Isolated signer (managed signing mode). Never co-locate with the web tier in production.
    signer_url: str | None = None
    signer_token: str | None = None
    signer_timeout: float = 20.0

    # Install wizard guard. If unset, a one-time token is generated and written to data_dir/setup-token.
    setup_token: str | None = None

    webhook_allow_private_targets: bool = False
    frontend_dist: Path | None = None
    worker_interval: float = 10.0
    log_level: str = "INFO"

    @field_validator("secret_key")
    @classmethod
    def _secret_strength(cls, v: str) -> str:
        if v and len(v) < 32:
            raise ValueError("CRYPTUS_SECRET_KEY must be at least 32 characters")
        return v

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    def effective_secret(self) -> str:
        if self.secret_key:
            return self.secret_key
        if self.is_production:
            raise RuntimeError("CRYPTUS_SECRET_KEY is required in production")
        # Development convenience: persist a random key so restarts keep sessions valid.
        p = self.data_dir / "dev-secret-key"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text(secrets.token_urlsafe(48))
        return p.read_text().strip()


@lru_cache
def get_settings() -> Settings:
    return Settings()
