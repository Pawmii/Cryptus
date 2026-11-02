"""Adapter registry and the operational status model shown in the console.

Status vocabulary (never collapse these — an unconfigured or planned network
must never look operational):

    operational      implemented, configured, enabled, wallet attached, last health check OK
    degraded         implemented, configured and enabled, but the backend is unhealthy
    not_configured   implemented but missing endpoint and/or wallet
    disabled         implemented and configured, switched off by an operator
    planned          adapter reserved for a future release; cannot be enabled
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..core.networks import NETWORKS, NetworkDef
from ..models import NetworkConfig, NetworkWallet
from ..security import decrypt_secret
from .base import ChainAdapter, NetConf, NotConfigured
from .bitcoin import BitcoinAdapter
from .evm import EvmAdapter
from .ton import TonAdapter

ADAPTERS: dict[str, type[ChainAdapter]] = {"bitcoin": BitcoinAdapter, "evm": EvmAdapter, "ton": TonAdapter}

DEFAULT_BACKEND = {"bitcoin": "esplora", "evm": "jsonrpc", "ton": "toncenter"}

PLANNED_NOTES = {
    "litecoin": "UTXO adapter shares the Bitcoin code path; pending MWEB-aware address handling.",
    "dogecoin": "UTXO adapter planned on the Bitcoin code path (legacy P2PKH, no SegWit).",
    "tron": "TRC-20 needs per-invoice account activation (1 TRX) and energy/bandwidth management.",
    "solana": "Requires associated token accounts per invoice and rent-exempt funding.",
}


def conf_of(nc: NetworkConfig, network: NetworkDef) -> NetConf:
    import json

    creds = json.loads(decrypt_secret(nc.credentials_enc) or "{}")
    return NetConf(network=nc.network, backend=nc.backend, url=nc.endpoint_url, credentials=creds,
                   options=nc.options or {}, cursor=nc.scan_cursor or {},
                   confirmations=nc.confirmations or network.default_confirmations)


def adapter_for(db: Session, network_key: str) -> ChainAdapter:
    net = NETWORKS[network_key]
    if net.status != "implemented":
        raise NotConfigured(f"{net.name} is planned and not available in this release")
    nc = db.get(NetworkConfig, network_key)
    if nc is None or not nc.endpoint_url:
        raise NotConfigured(f"{net.name} has no endpoint configured")
    return ADAPTERS[net.family](net, conf_of(nc, net))


def build_adapter(net: NetworkDef, conf: NetConf) -> ChainAdapter:
    if net.status != "implemented":
        raise NotConfigured(f"{net.name} is planned")
    return ADAPTERS[net.family](net, conf)


def network_status(net: NetworkDef, nc: NetworkConfig | None, wallet: NetworkWallet | None) -> str:
    if net.status == "planned":
        return "planned"
    if nc is None or not nc.endpoint_url or wallet is None:
        return "not_configured"
    if not nc.enabled:
        return "disabled"
    if not (nc.health or {}).get("ok"):
        return "degraded"
    return "operational"
