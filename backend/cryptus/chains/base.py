"""Chain adapter contract.

Every network family implements the same interface so the payment monitor and
consolidation engine stay chain-agnostic. Adapters are stateless apart from a
scan cursor that the caller persists.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..core.networks import NetworkDef


class ChainError(RuntimeError):
    """Transient or configuration error talking to a chain backend."""


class NotConfigured(ChainError):
    pass


@dataclass
class NetConf:
    network: str
    backend: str
    url: str | None
    credentials: dict
    options: dict
    cursor: dict
    confirmations: int


@dataclass
class AssetInfo:
    id: str
    kind: str
    symbol: str
    decimals: int
    contract: str | None


@dataclass
class WatchItem:
    deposit_id: str
    address: str            # canonical comparison form
    display_address: str
    asset: AssetInfo
    meta: dict
    exclude_senders: set[str] = field(default_factory=set)  # e.g. our own gas station


@dataclass
class Observed:
    deposit_id: str
    txid: str
    output_index: int
    asset_id: str
    amount: int
    sender: str | None
    block_number: int | None
    block_hash: str | None
    confirmations: int
    detail: dict = field(default_factory=dict)


@dataclass
class TxState:
    found: bool
    confirmations: int = 0
    block_number: int | None = None
    block_hash: str | None = None
    failed: bool = False
    amount: int | None = None  # re-verified amount where the chain allows it


@dataclass
class Health:
    ok: bool
    height: int | None = None
    latency_ms: int | None = None
    detail: dict = field(default_factory=dict)
    error: str | None = None

    def as_dict(self) -> dict:
        return {"ok": self.ok, "height": self.height, "latency_ms": self.latency_ms,
                "detail": self.detail, "error": self.error}


@dataclass
class ScanResult:
    observed: list[Observed]
    cursor: dict
    head: int | None = None
    deposit_meta: dict[str, dict] = field(default_factory=dict)  # deposit_id -> meta updates


class ChainAdapter(ABC):
    family: str = ""
    backends: tuple[str, ...] = ()

    def __init__(self, network: NetworkDef, conf: NetConf, http: httpx.Client | None = None):
        if conf.backend not in self.backends:
            raise NotConfigured(f"backend {conf.backend!r} not supported for {network.key}")
        if not conf.url:
            raise NotConfigured(f"{network.key}: endpoint URL is not configured")
        self.network = network
        self.conf = conf
        self.http = http or httpx.Client(timeout=httpx.Timeout(20.0, connect=8.0),
                                         headers={"User-Agent": "cryptus-gateway/1.0"})

    # — observation —
    @abstractmethod
    def health(self) -> Health: ...

    def prepare_deposit(self, item: WatchItem) -> dict:
        """Chain-specific preparation when an address is allocated. Returns meta updates."""
        return {}

    @abstractmethod
    def scan(self, items: list[WatchItem]) -> ScanResult: ...

    @abstractmethod
    def refresh(self, txid: str, output_index: int, asset: AssetInfo, address: str) -> TxState: ...

    @abstractmethod
    def balance(self, address: str, asset: AssetInfo, meta: dict | None = None) -> int: ...

    # — settlement —
    @abstractmethod
    def broadcast(self, raw: str) -> str: ...

    # — helpers —
    def _timed(self, fn, *a, **kw) -> tuple[Any, int]:
        t = time.perf_counter()
        r = fn(*a, **kw)
        return r, int((time.perf_counter() - t) * 1000)


def chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]
