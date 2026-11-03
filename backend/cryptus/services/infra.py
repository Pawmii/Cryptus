"""Network, keychain, wallet and asset management (shared by the setup wizard and the console)."""
from __future__ import annotations

import json

from eth_utils import is_address, to_checksum_address
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..chains.base import ChainError
from ..chains.registry import DEFAULT_BACKEND, ADAPTERS, adapter_for, network_status
from ..core import derivation
from ..core.networks import NETWORKS, builtin_assets, get_network
from ..db import utcnow
from ..models import Asset, Keychain, NetworkConfig, NetworkWallet
from ..security import encrypt_secret
from . import audit


class InfraError(ValueError):
    pass


def seed_assets(db: Session) -> None:
    """Insert built-in assets that are missing (safe to run concurrently from several processes)."""
    from sqlalchemy.exc import IntegrityError

    for a in builtin_assets():
        row = db.get(Asset, a.id)
        if row is not None:
            if row.builtin and not row.coingecko_id and a.coingecko_id:
                row.coingecko_id = a.coingecko_id
            continue
        try:
            with db.begin_nested():
                db.add(Asset(id=a.id, network=a.network, kind=a.kind, symbol=a.symbol, name=a.name,
                             decimals=a.decimals, contract=a.contract, coingecko_id=a.coingecko_id, builtin=True,
                             enabled=True, verified_at=utcnow(), verification={"source": "built-in registry"}))
        except IntegrityError:
            pass
    db.flush()


# ── Address validation ────────────────────────────────────────────────────

def validate_address(network_key: str, address: str) -> str:
    net = get_network(network_key)
    a = address.strip()
    if net.family == "bitcoin":
        from embit import script
        from embit.networks import NETWORKS as BN
        try:
            spk = script.address_to_scriptpubkey(a)
        except Exception as exc:
            raise InfraError("not a valid Bitcoin address") from exc
        if spk.address(BN[net.btc_network]) != a:
            raise InfraError("address belongs to a different Bitcoin network")
        return a
    if net.family == "evm":
        if not is_address(a):
            raise InfraError("not a valid EVM address")
        body = a[2:]
        if body != body.lower() and body != body.upper() and a != to_checksum_address(a):
            raise InfraError("address checksum is invalid")
        return to_checksum_address(a)
    if net.family == "ton":
        from ..core.ton_wallet import parse_address
        try:
            parsed = parse_address(a)
        except Exception as exc:
            raise InfraError("not a valid TON address") from exc
        if parsed.is_test_only != net.ton_testnet and len(a) == 48:
            raise InfraError("address testnet flag does not match the network")
        return a
    raise InfraError("network is not implemented")


# ── Networks ──────────────────────────────────────────────────────────────

def network_rows(db: Session) -> list[dict]:
    out = []
    for key, net in NETWORKS.items():
        nc = db.get(NetworkConfig, key)
        nw = db.get(NetworkWallet, key)
        kc = db.get(Keychain, nw.keychain_id) if nw else None
        out.append({
            "key": key, "name": net.name, "family": net.family, "environment": net.environment,
            "implementation": net.status, "status": network_status(net, nc, nw),
            "native_symbol": net.native_symbol, "chain_id": net.chain_id,
            "backends": list(ADAPTERS[net.family].backends) if net.family in ADAPTERS else [],
            "backend": nc.backend if nc else DEFAULT_BACKEND.get(net.family),
            "endpoint_url": nc.endpoint_url if nc else None,
            "has_credentials": bool(nc and nc.credentials_enc),
            "enabled": bool(nc and nc.enabled),
            "confirmations": (nc.confirmations if nc and nc.confirmations else net.default_confirmations),
            "default_confirmations": net.default_confirmations,
            "options": nc.options if nc else {},
            "health": nc.health if nc else {}, "health_checked_at": nc.health_checked_at.isoformat()
            if nc and nc.health_checked_at else None,
            "wallet": wallet_view(db, nw, kc) if nw else None,
            "note": None if net.status == "implemented" else __import__("cryptus.chains.registry", fromlist=["x"]).PLANNED_NOTES.get(net.family),
        })
    return out


def configure_network(db: Session, actor: audit.Actor, key: str, *, backend: str | None, endpoint_url: str | None,
                      credentials: dict | None, confirmations: int | None, options: dict | None,
                      enabled: bool | None) -> NetworkConfig:
    net = get_network(key)
    if net.status != "implemented":
        raise InfraError(f"{net.name} is planned and cannot be configured yet")
    nc = db.get(NetworkConfig, key)
    if nc is None:
        nc = NetworkConfig(network=key, backend=backend or DEFAULT_BACKEND[net.family], options={}, scan_cursor={},
                           health={})
        db.add(nc)
    if backend:
        if backend not in ADAPTERS[net.family].backends:
            raise InfraError(f"unsupported backend {backend!r}")
        if backend != nc.backend:
            nc.scan_cursor = {}
        nc.backend = backend
    if endpoint_url is not None:
        u = endpoint_url.strip()
        if u and not u.startswith(("http://", "https://")):
            raise InfraError("endpoint must be an http(s) URL")
        if u != (nc.endpoint_url or ""):
            nc.scan_cursor = {}
        nc.endpoint_url = u or None
    if credentials is not None:
        clean = {k: v for k, v in credentials.items() if v}
        nc.credentials_enc = encrypt_secret(json.dumps(clean)) if clean else None
    if confirmations is not None:
        if confirmations < 1 or confirmations > 1000:
            raise InfraError("confirmations must be between 1 and 1000")
        if confirmations < net.default_confirmations:
            audit.record(db, actor, "network.low_confirmations", key, confirmations=confirmations,
                         recommended=net.default_confirmations)
        nc.confirmations = confirmations
    if options is not None:
        nc.options = {**(nc.options or {}), **options}
    if enabled is not None:
        if enabled and not nc.endpoint_url:
            raise InfraError("configure an endpoint before enabling the network")
        nc.enabled = enabled
    nc.health_checked_at = None
    db.flush()
    audit.record(db, actor, "network.configured", key, backend=nc.backend, endpoint=nc.endpoint_url,
                 enabled=nc.enabled, confirmations=nc.confirmations, credentials_changed=credentials is not None)
    return nc


def test_network(db: Session, key: str) -> dict:
    nc = db.get(NetworkConfig, key)
    if nc is None or not nc.endpoint_url:
        raise InfraError("network has no endpoint configured")
    try:
        h = adapter_for(db, key).health().as_dict()
    except ChainError as exc:
        h = {"ok": False, "error": str(exc)}
    nc.health, nc.health_checked_at = h, utcnow()
    return h


# ── Keychains ─────────────────────────────────────────────────────────────

def preview_addresses(kc: Keychain, network_key: str, n: int = 3) -> list[dict]:
    net = get_network(network_key)
    if kc.mode == "direct":  # every invoice uses the same wallet; only the memo differs
        d = derivation.ton_direct_wallet(kc.public_material, net)
        return [{"index": 0, "path": d.path, "address": d.address}]
    out = []
    for i in range(n):
        d = derivation.derive_deposit(net, kc.public_material, i)
        out.append({"index": i, "path": d.path, "address": d.address})
    return out


def create_keychain(db: Session, actor: audit.Actor, *, family: str, scope: str, public_material: str, mode: str,
                    label: str, master_fingerprint: str | None = None, account_path: str | None = None) -> Keychain:
    if mode not in ("watch_only", "managed", "direct"):
        raise InfraError("mode must be watch_only, managed or direct")
    net = NETWORKS.get(scope) if family != "evm" else None
    if family in ("bitcoin", "ton") and (net is None or net.family != family):
        raise InfraError("scope must be a network of the same family")
    if family == "evm" and scope != "evm":
        raise InfraError("EVM keychains use scope 'evm' and serve every EVM network")
    if mode == "direct" and family != "ton":
        raise InfraError("direct receiving (one wallet + memo) is available on TON only")
    material = public_material.strip()
    try:
        if mode == "direct":
            material = derivation.parse_ton_wallet(material, net).to_str(is_user_friendly=False)
            _check_direct_wallet(db, scope, material)
        else:
            derivation.validate_public_material(family, material, net)
    except derivation.DerivationError as exc:
        raise InfraError(str(exc)) from exc
    if family == "bitcoin":
        if not master_fingerprint or len(master_fingerprint) != 8:
            raise InfraError("Bitcoin keychains need the 8-hex-digit master fingerprint (for PSBT signing)")
        account_path = account_path or ("m/84'/0'/0'" if net.btc_network == "main" else "m/84'/1'/0'")
    if family == "ton" and mode != "direct":
        material = material.lower().removeprefix("0x")
    if db.execute(select(Keychain).where(Keychain.family == family, Keychain.public_material == material)).first():
        raise InfraError("this key material is already registered")
    kc = Keychain(family=family, scope=scope, label=label.strip() or f"{family} keychain", public_material=material,
                  mode=mode, master_fingerprint=(master_fingerprint or "").lower() or None, account_path=account_path)
    db.add(kc)
    db.flush()
    audit.record(db, actor, "keychain.created", kc.id, family=family, scope=scope, mode=mode, label=kc.label)
    return kc


def _check_direct_wallet(db: Session, network_key: str, raw_address: str) -> None:
    """Refuse addresses that are contracts rather than wallets (e.g. a jetton wallet pasted by mistake).

    Best effort: skipped when the network has no endpoint yet. A wallet that has never sent a transaction is
    still uninitialised; payments to it are credited because checkout uses the non-bounceable form."""
    from ..chains.base import NotConfigured
    from ..chains.ton import GetMethodFailed
    try:
        ad = adapter_for(db, network_key)
        if ad.account_state(raw_address)["state"] == "active":
            ad.seqno(raw_address)
    except GetMethodFailed as exc:
        raise InfraError("this address is a smart contract, not a wallet — enter the address shown on your "
                         "wallet's Receive screen") from exc
    except (NotConfigured, ChainError):
        pass


def verify_keychain(db: Session, actor: audit.Actor, kc: Keychain, confirmed_address: str) -> None:
    """Operator attests that the first derived address matches their own wallet software."""
    network_key = kc.scope if kc.family != "evm" else "eth"
    first = preview_addresses(kc, network_key, 1)[0]["address"]
    fam = kc.family
    if derivation.normalize_address(fam, confirmed_address) != derivation.normalize_address(fam, first):
        raise InfraError("address does not match index 0 of this keychain — check the key material")
    kc.verified_at = utcnow()
    audit.record(db, actor, "keychain.verified", kc.id, address=first)


def attach_wallet(db: Session, actor: audit.Actor, network_key: str, keychain_id: str) -> NetworkWallet:
    net = get_network(network_key)
    kc = db.get(Keychain, keychain_id)
    if kc is None:
        raise InfraError("keychain not found")
    if kc.family != net.family or (net.family != "evm" and kc.scope != network_key):
        raise InfraError("keychain does not belong to this network")
    nw = db.get(NetworkWallet, network_key)
    if nw is None:
        nw = NetworkWallet(network=network_key, keychain_id=kc.id, sweep_thresholds={}, max_fee={})
        db.add(nw)
    elif nw.keychain_id != kc.id:
        nw.keychain_id = kc.id
        nw.destination_confirmed_at = None
    if kc.mode == "direct":  # funds already land in the merchant's wallet; consolidation does not apply
        nw.destination, nw.destination_confirmed_at, nw.sweep_enabled = None, None, False
    db.flush()
    audit.record(db, actor, "wallet.attached", network_key, keychain=kc.id, mode=kc.mode)
    return nw


def set_destination(db: Session, actor: audit.Actor, network_key: str, address: str | None) -> NetworkWallet:
    nw = db.get(NetworkWallet, network_key)
    if nw is None:
        raise InfraError("attach a keychain first")
    kc = db.get(Keychain, nw.keychain_id)
    if address and kc.mode == "direct":
        raise InfraError("direct receiving pays your wallet itself — there is nothing to consolidate")
    if address:
        addr = validate_address(network_key, address)
        for i in range(0, min(kc.next_index, 2000)):
            if derivation.normalize_address(kc.family, derivation.derive_deposit(get_network(network_key),
                                            kc.public_material, i).address) == \
                    derivation.normalize_address(kc.family, addr):
                raise InfraError("destination cannot be one of CRYPTUS's own deposit addresses")
        nw.destination = addr
        nw.destination_confirmed_at, nw.destination_confirmed_by = utcnow(), actor.id
    else:
        nw.destination, nw.destination_confirmed_at, nw.sweep_enabled = None, None, False
    audit.record(db, actor, "wallet.destination_set", network_key, destination=nw.destination)
    return nw


def wallet_view(db: Session, nw: NetworkWallet, kc: Keychain | None) -> dict:
    from ..core.derivation import evm_gas_station, ton_gas_wallet
    net = NETWORKS[nw.network]
    gas = None
    if kc and kc.mode == "direct":
        pass  # no deposit contracts, so no gas wallet
    elif kc and net.family == "evm":
        gas = evm_gas_station(kc.public_material).address
    elif kc and net.family == "ton":
        gas = ton_gas_wallet(kc.public_material, net).address
    return {
        "network": nw.network, "keychain_id": nw.keychain_id,
        "keychain": keychain_view(kc) if kc else None,
        "destination": nw.destination,
        "destination_confirmed_at": nw.destination_confirmed_at.isoformat() if nw.destination_confirmed_at else None,
        "sweep_enabled": nw.sweep_enabled, "sweep_interval_minutes": nw.sweep_interval_minutes,
        "sweep_thresholds": nw.sweep_thresholds or {}, "max_fee": nw.max_fee or {},
        "gas_address": gas,
        "receiving_address": derivation.ton_direct_wallet(kc.public_material, net).address
        if kc and kc.mode == "direct" else None,
        "last_sweep_run_at": nw.last_sweep_run_at.isoformat() if nw.last_sweep_run_at else None,
    }


def keychain_view(kc: Keychain) -> dict:
    m = kc.public_material
    if kc.mode == "direct":  # stored raw; shown the way the merchant's wallet app shows it
        m = derivation.ton_direct_wallet(m, NETWORKS[kc.scope]).address
    return {"id": kc.id, "family": kc.family, "scope": kc.scope, "label": kc.label, "mode": kc.mode,
            "public_material": m, "public_material_short": m[:12] + "…" + m[-8:] if len(m) > 24 else m,
            "master_fingerprint": kc.master_fingerprint, "account_path": kc.account_path,
            "next_index": kc.next_index, "active": kc.active,
            "verified_at": kc.verified_at.isoformat() if kc.verified_at else None,
            "created_at": kc.created_at.isoformat()}


# ── Assets ────────────────────────────────────────────────────────────────

def add_token(db: Session, actor: audit.Actor, network_key: str, contract: str, *, symbol: str, name: str,
              decimals: int, coingecko_id: str | None) -> Asset:
    net = get_network(network_key)
    if net.family == "evm":
        if not is_address(contract):
            raise InfraError("invalid contract address")
        contract = to_checksum_address(contract)
        kind = "erc20"
    elif net.family == "ton":
        from ..core.ton_wallet import raw
        try:
            contract = raw(contract)
        except Exception as exc:
            raise InfraError("invalid jetton master address") from exc
        kind = "jetton"
    else:
        raise InfraError("tokens are not supported on this network")
    aid = f"{network_key}:{kind}:{contract}"
    if db.get(Asset, aid):
        raise InfraError("asset already exists")
    # On-chain verification: the contract must exist and its decimals must match.
    try:
        ad = adapter_for(db, network_key)
        info = ad.inspect_token(contract) if net.family == "evm" else ad.inspect_jetton(contract)
    except ChainError as exc:
        raise InfraError(f"on-chain verification failed: {exc}") from exc
    if net.family == "evm" and int(info["decimals"]) != decimals:
        raise InfraError(f"contract reports {info['decimals']} decimals, not {decimals}")
    if net.family == "ton":
        meta_dec = (info.get("metadata") or {}).get("decimals")
        if meta_dec is not None and int(meta_dec) != decimals:
            raise InfraError(f"jetton metadata reports {meta_dec} decimals, not {decimals}")
    a = Asset(id=aid, network=network_key, kind=kind, symbol=symbol.strip()[:16], name=name.strip()[:80],
              decimals=decimals, contract=contract, coingecko_id=coingecko_id or None, builtin=False, enabled=True,
              verified_at=utcnow(), verification={"source": "on-chain", **{k: v for k, v in info.items()
                                                                          if k != "metadata"}})
    db.add(a)
    db.flush()
    audit.record(db, actor, "asset.added", aid, symbol=a.symbol, decimals=decimals)
    return a


def asset_view(a: Asset) -> dict:
    net = NETWORKS[a.network]
    return {"id": a.id, "network": a.network, "network_name": net.name, "environment": net.environment,
            "kind": a.kind, "symbol": a.symbol, "name": a.name, "decimals": a.decimals, "contract": a.contract,
            "coingecko_id": a.coingecko_id, "fixed_usd_peg": a.fixed_usd_peg, "enabled": a.enabled,
            "builtin": a.builtin, "verified_at": a.verified_at.isoformat() if a.verified_at else None,
            "min_amount": a.min_amount}


def import_signer_keys(db: Session, actor: audit.Actor) -> list[Keychain]:
    """Register the signer's public keys as managed keychains (idempotent)."""
    from . import signer_client
    created = []
    for k in signer_client.keys():
        fam = k["family"]
        scope = "evm" if fam == "evm" else k["network"]
        mat = k["public_material"].lower() if fam == "ton" else k["public_material"]
        existing = db.execute(select(Keychain).where(Keychain.family == fam, Keychain.public_material == mat)
                              ).scalar_one_or_none()
        if existing:
            continue
        kc = create_keychain(db, actor, family=fam, scope=scope, public_material=mat, mode="managed",
                             label=f"Signer · {scope}", master_fingerprint=k.get("fingerprint"),
                             account_path=k.get("account_path") if fam == "bitcoin" else None)
        kc.signer_key_id = k.get("fingerprint")
        kc.verified_at = utcnow()  # material came authenticated from the signer itself
        audit.record(db, actor, "keychain.verified", kc.id, via="signer")
        created.append(kc)
    return created
