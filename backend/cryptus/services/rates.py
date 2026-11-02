"""Exchange rates for fiat-priced invoices.

Rates are fetched from CoinGecko (optionally with a demo/pro key) and cached
briefly. Each deposit records the rate and source used, so accounting can
always be reproduced. If no rate is available the invoice cannot be quoted —
CRYPTUS never invents a price.
"""
from __future__ import annotations

import json
import threading
import time
from decimal import Decimal

import httpx
from sqlalchemy.orm import Session

from ..models import Asset
from ..security import decrypt_secret
from . import settings as settings_svc

FIAT = {"USD", "EUR", "GBP", "CHF", "JPY", "CAD", "AUD", "CNY", "INR", "BRL", "TRY", "PLN", "SEK", "NOK",
        "DKK", "CZK", "HUF", "AED", "SGD", "HKD", "KRW", "MXN", "ZAR", "UAH", "KZT", "RUB"}


class RateUnavailable(RuntimeError):
    pass


_cache: dict[tuple[str, str], tuple[float, Decimal]] = {}
_lock = threading.Lock()


def is_fiat(code: str) -> bool:
    return code.upper() in FIAT


def _fetch(ids: list[str], vs: str, api_key: str | None, pro: bool) -> dict:
    base = "https://pro-api.coingecko.com/api/v3" if pro else "https://api.coingecko.com/api/v3"
    headers = {"accept": "application/json"}
    if api_key:
        headers["x-cg-pro-api-key" if pro else "x-cg-demo-api-key"] = api_key
    try:
        r = httpx.get(f"{base}/simple/price", params={"ids": ",".join(ids), "vs_currencies": vs.lower(),
                                                       "precision": "full"}, headers=headers, timeout=10)
        r.raise_for_status()
        return json.loads(r.text, parse_float=Decimal)
    except (httpx.HTTPError, ValueError) as exc:
        raise RateUnavailable(f"rate provider unavailable: {exc}") from exc


def rate(db: Session, asset: Asset, fiat: str) -> tuple[Decimal, str]:
    """Price of one whole unit of ``asset`` in ``fiat``. Returns (rate, source)."""
    fiat = fiat.upper()
    if asset.fixed_usd_peg and fiat == "USD":
        return Decimal(1), "fixed-peg"
    if not asset.coingecko_id:
        raise RateUnavailable(f"{asset.symbol} on {asset.network} has no price source configured")
    cfg = settings_svc.get(db, "rates")
    ttl = int(cfg.get("cache_seconds", 60))
    key = (asset.coingecko_id, fiat)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1], "coingecko"
    api_key = decrypt_secret(cfg.get("api_key_enc")) if cfg.get("api_key_enc") else None
    data = _fetch([asset.coingecko_id], fiat, api_key, bool(cfg.get("pro")))
    try:
        value = Decimal(str(data[asset.coingecko_id][fiat.lower()]))
    except KeyError as exc:
        raise RateUnavailable(f"no {fiat} price for {asset.coingecko_id}") from exc
    if value <= 0:
        raise RateUnavailable("provider returned a non-positive price")
    with _lock:
        _cache[key] = (now, value)
    return value, "coingecko"
