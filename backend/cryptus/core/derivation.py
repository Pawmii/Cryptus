"""Deterministic, watch-only address derivation.

Every deposit address CRYPTUS hands out is derived from *public* key material
supplied by the merchant (or exported by the isolated signer). The gateway can
therefore operate without ever holding a private key.

Derivation layout (all paths relative to the account-level public key):

    Bitcoin   BIP84  m/84'/coin'/account'   deposits: 0/i          (P2WPKH)
    EVM       BIP44  m/44'/60'/account'     deposits: 0/i          gas station: 1/0
    TON       one ed25519 key, wallet v4r2  deposits: subwallet_id = TON_SUBWALLET_BASE + i
                                            gas wallet: default subwallet 698983191 (+workchain)
    TON       direct mode                   deposits: the merchant's own wallet + a unique memo per invoice
"""
from __future__ import annotations

from dataclasses import dataclass

from embit import bip32, script
from embit.networks import NETWORKS as BTC_NETWORKS
from eth_hash.auto import keccak
from eth_utils import to_checksum_address

from .networks import NetworkDef
from . import ton_wallet


class DerivationError(ValueError):
    pass


EVM_DEPOSIT_CHAIN = 0
EVM_GAS_CHAIN = 1
TON_SUBWALLET_BASE = 0x40000000
TON_DEFAULT_SUBWALLET = 698983191


@dataclass(frozen=True)
class Derived:
    address: str
    path: str  # human readable, relative to the keychain root
    meta: dict


# ── Bitcoin ────────────────────────────────────────────────────────────────

def parse_btc_xpub(xpub: str, network: NetworkDef) -> bip32.HDKey:
    try:
        key = bip32.HDKey.from_string(xpub.strip())
    except Exception as exc:
        raise DerivationError("not a valid extended public key") from exc
    if key.is_private:
        raise DerivationError("a private extended key was supplied — provide the PUBLIC key (xpub/zpub/vpub) only")
    if key.depth != 3:
        raise DerivationError(f"expected an account-level key (depth 3, e.g. m/84'/0'/0'), got depth {key.depth}")
    is_test_key = xpub[:4] in ("tpub", "upub", "vpub")
    want_test = network.btc_network != "main"
    if is_test_key != want_test:
        raise DerivationError("extended key network does not match the selected Bitcoin network")
    return key


def btc_address(xpub: str, network: NetworkDef, index: int, chain: int = 0) -> Derived:
    key = parse_btc_xpub(xpub, network)
    child = key.derive([chain, index])
    addr = script.p2wpkh(child.key).address(BTC_NETWORKS[network.btc_network or "main"])
    return Derived(addr, f"{chain}/{index}", {"pubkey": child.key.sec().hex()})


# ── EVM ────────────────────────────────────────────────────────────────────

def parse_evm_xpub(xpub: str) -> bip32.HDKey:
    try:
        key = bip32.HDKey.from_string(xpub.strip())
    except Exception as exc:
        raise DerivationError("not a valid extended public key") from exc
    if key.is_private:
        raise DerivationError("a private extended key was supplied — provide the PUBLIC key (xpub) only")
    if key.depth != 3:
        raise DerivationError("expected an account-level key at m/44'/60'/0' (depth 3)")
    return key


def evm_pubkey_to_address(pub) -> str:
    from embit.ec import PublicKey

    p = PublicKey.parse(pub.sec())
    p.compressed = False
    return to_checksum_address("0x" + keccak(p.sec()[1:])[-20:].hex())


def evm_address(xpub: str, index: int, chain: int = EVM_DEPOSIT_CHAIN) -> Derived:
    key = parse_evm_xpub(xpub).derive([chain, index])
    return Derived(evm_pubkey_to_address(key.key), f"{chain}/{index}", {})


def evm_gas_station(xpub: str) -> Derived:
    return evm_address(xpub, 0, chain=EVM_GAS_CHAIN)


# ── TON ────────────────────────────────────────────────────────────────────

def parse_ton_pubkey(pubkey_hex: str) -> bytes:
    try:
        raw = bytes.fromhex(pubkey_hex.strip().removeprefix("0x"))
    except ValueError as exc:
        raise DerivationError("TON public key must be 64 hex characters") from exc
    if len(raw) != 32:
        raise DerivationError("TON public key must be exactly 32 bytes (ed25519)")
    return raw


def ton_subwallet_id(index: int) -> int:
    sid = TON_SUBWALLET_BASE + index
    if not 0 <= sid < 2**32:
        raise DerivationError("TON deposit index out of range")
    return sid


def ton_address(pubkey_hex: str, network: NetworkDef, index: int) -> Derived:
    pub = parse_ton_pubkey(pubkey_hex)
    sid = ton_subwallet_id(index)
    addr = ton_wallet.wallet_address(pub, sid)
    return Derived(
        ton_wallet.friendly(addr, bounceable=False, testnet=network.ton_testnet),
        f"subwallet:{sid}",
        {"subwallet_id": sid, "raw": addr.to_str(is_user_friendly=False)},
    )


def ton_gas_wallet(pubkey_hex: str, network: NetworkDef) -> Derived:
    pub = parse_ton_pubkey(pubkey_hex)
    addr = ton_wallet.wallet_address(pub, TON_DEFAULT_SUBWALLET)
    return Derived(
        ton_wallet.friendly(addr, bounceable=False, testnet=network.ton_testnet),
        f"subwallet:{TON_DEFAULT_SUBWALLET}",
        {"subwallet_id": TON_DEFAULT_SUBWALLET, "raw": addr.to_str(is_user_friendly=False)},
    )


def parse_ton_wallet(address: str, network: NetworkDef):
    """Validate a merchant wallet address for direct receiving (basechain, matching testnet flag)."""
    try:
        addr = ton_wallet.parse_address(address)
    except Exception as exc:
        raise DerivationError("not a valid TON address") from exc
    if addr.wc != 0:
        raise DerivationError("use a basechain (workchain 0) wallet address")
    if len(address.strip()) == 48 and addr.is_test_only != network.ton_testnet:
        raise DerivationError("address testnet flag does not match the network")
    return addr


def ton_direct_wallet(address: str, network: NetworkDef) -> Derived:
    addr = parse_ton_wallet(address, network)
    return Derived(ton_wallet.friendly(addr, bounceable=False, testnet=network.ton_testnet), "memo",
                   {"raw": addr.to_str(is_user_friendly=False), "direct": True})


# ── Dispatcher ─────────────────────────────────────────────────────────────

def derive_deposit(network: NetworkDef, public_material: str, index: int) -> Derived:
    if index < 0:
        raise DerivationError("index must be non-negative")
    if network.family == "bitcoin":
        return btc_address(public_material, network, index)
    if network.family == "evm":
        return evm_address(public_material, index)
    if network.family == "ton":
        return ton_address(public_material, network, index)
    raise DerivationError(f"network family {network.family!r} is not implemented")


def validate_public_material(family: str, material: str, network: NetworkDef | None = None) -> None:
    if family == "bitcoin":
        if network is None:
            raise DerivationError("bitcoin keychains need a network")
        parse_btc_xpub(material, network)
    elif family == "evm":
        parse_evm_xpub(material)
    elif family == "ton":
        parse_ton_pubkey(material)
    else:
        raise DerivationError(f"family {family!r} is not implemented")


def btc_key_origin(xpub: str) -> dict:
    key = bip32.HDKey.from_string(xpub.strip())
    return {"parent_fingerprint": key.parent_fingerprint.hex(), "depth": key.depth}


def normalize_address(family: str, address: str) -> str:
    """Canonical comparison form for addresses of a family."""
    if family == "evm":
        return address.lower()
    if family == "ton":
        return ton_wallet.parse_address(address).to_str(is_user_friendly=False)
    return address.strip()
