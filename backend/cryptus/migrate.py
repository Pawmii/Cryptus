from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from .config import get_settings

HERE = Path(__file__).resolve().parent / "migrations"


def alembic_config(url: str | None = None) -> Config:
    cfg = Config(str(HERE / "alembic.ini"))
    cfg.set_main_option("script_location", str(HERE))
    cfg.set_main_option("sqlalchemy.url", url or get_settings().database_url)
    return cfg


def upgrade(url: str | None = None) -> None:
    s = get_settings()
    if (url or s.database_url).startswith("sqlite"):
        s.data_dir.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(url), "head")
