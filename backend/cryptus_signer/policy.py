"""Signer policy: what this signer is willing to sign, independent of the gateway.

Example ``policy.json``::

    {
      "halted": false,
      "networks": {
        "btc":  {"allowlist": ["bc1q…"], "max_fee": "0.0005", "max_feerate_sat_vb": 300,
                 "daily_limits": {"btc:native": "500000000"}},
        "eth":  {"allowlist": ["0x…"], "max_fee": "0.01", "max_gas_topup": "0.004",
                 "tokens": ["0xdAC17F958D2ee523a2206206994597C13D831ec7"],
                 "daily_limits": {"eth:erc20:0xdAC17F958D2ee523a2206206994597C13D831ec7": "100000000000"}},
        "ton":  {"allowlist": ["UQ…"], "max_gas_topup": "0.2", "max_jetton_attach": "0.1"}
      }
    }

``daily_limits`` are in atomic units per canonical asset id over a rolling 24h window.
A network absent from the policy cannot be signed for at all. Creating a file
named ``HALT`` next to the policy stops all signing immediately.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from cryptus.core.derivation import normalize_address
from cryptus.core.money import to_atomic
from cryptus.core.networks import NETWORKS


class PolicyViolation(Exception):
    pass


@dataclass
class NetPolicy:
    network: str
    allowlist: set[str]
    allowlist_display: list[str]
    max_fee: int | None
    max_feerate: float | None
    max_gas_topup: int | None
    max_jetton_attach: int | None
    tokens: set[str] | None
    daily_limits: dict[str, int] = field(default_factory=dict)

    def require_destination(self, addr: str) -> None:
        fam = NETWORKS[self.network].family
        try:
            n = normalize_address(fam, addr)
        except Exception as exc:
            raise PolicyViolation(f"malformed destination {addr!r}") from exc
        if n not in self.allowlist:
            raise PolicyViolation(f"destination {addr} is not on the signer allowlist for {self.network}")


class Policy:
    def __init__(self, path: Path):
        self.path = path
        self.reload()

    def reload(self) -> None:
        raw = json.loads(self.path.read_text())
        self._halted = bool(raw.get("halted"))
        self.networks: dict[str, NetPolicy] = {}
        for key, p in (raw.get("networks") or {}).items():
            net = NETWORKS.get(key)
            if not net or net.status != "implemented":
                raise ValueError(f"policy references unknown or unsupported network {key!r}")
            fam = net.family
            dec = net.native_decimals
            allow = list(p.get("allowlist") or [])
            self.networks[key] = NetPolicy(
                network=key,
                allowlist={normalize_address(fam, a) for a in allow},
                allowlist_display=allow,
                max_fee=to_atomic(p["max_fee"], dec) if p.get("max_fee") else None,
                max_feerate=float(p["max_feerate_sat_vb"]) if p.get("max_feerate_sat_vb") else None,
                max_gas_topup=to_atomic(p["max_gas_topup"], dec) if p.get("max_gas_topup") else None,
                max_jetton_attach=to_atomic(p["max_jetton_attach"], dec) if p.get("max_jetton_attach") else None,
                tokens={t.lower() for t in p["tokens"]} if p.get("tokens") is not None else None,
                daily_limits={k: int(v) for k, v in (p.get("daily_limits") or {}).items()},
            )

    @property
    def halted(self) -> bool:
        return self._halted or (self.path.parent / "HALT").exists()

    def for_network(self, key: str) -> NetPolicy:
        if self.halted:
            raise PolicyViolation("signer is halted")
        p = self.networks.get(key)
        if p is None:
            raise PolicyViolation(f"signing for {key} is not permitted by policy")
        return p

    def summary(self) -> dict:
        return {"halted": self.halted,
                "allowlist": {k: v.allowlist_display for k, v in self.networks.items()},
                "networks": sorted(self.networks)}
