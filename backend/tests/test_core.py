from decimal import Decimal

import pytest

from cryptus.core import derivation, evm_abi, ton_wallet
from cryptus.core.money import AmountError, fmt_atomic, to_atomic
from cryptus.core.networks import NETWORKS, TON_USDT_MASTER_RAW, parse_asset_id

# BIP84 reference vector (mnemonic "abandon … about")
ZPUB = "zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1ADqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs"


def test_bip84_reference_vector():
    assert derivation.btc_address(ZPUB, NETWORKS["btc"], 0).address == "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"
    assert derivation.btc_address(ZPUB, NETWORKS["btc"], 1).address == "bc1qnjg0jd8228aq7egyzacy8cys3knf9xvrerkf9g"


def test_btc_rejects_private_and_wrong_network_keys(ring):
    with pytest.raises(derivation.DerivationError):
        derivation.parse_btc_xpub(ZPUB, NETWORKS["btc-testnet"])
    xprv = ring.root.derive("m/84h/0h/0h").to_string()
    with pytest.raises(derivation.DerivationError):
        derivation.parse_btc_xpub(xprv, NETWORKS["btc"])


def test_evm_reference_vector(ring):
    xpub = ring.evm_account_xpub()
    assert derivation.evm_address(xpub, 0).address == "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"
    a1 = derivation.evm_address(xpub, 1).address
    gas = derivation.evm_gas_station(xpub).address
    assert len({a1, gas, "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"}) == 3


def test_ton_wallet_code_and_usdt_master():
    assert ton_wallet.wallet_code().hash.hex() == ton_wallet.WALLET_V4R2_CODE_HASH
    assert ton_wallet.raw("EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs") == TON_USDT_MASTER_RAW


def test_ton_unique_deposit_addresses(ring):
    pub = ring.ton_public_key.hex()
    addrs = {derivation.ton_address(pub, NETWORKS["ton"], i).address for i in range(50)}
    assert len(addrs) == 50
    a = derivation.ton_address(pub, NETWORKS["ton"], 0).address
    assert a.startswith("UQ")  # non-bounceable: required for undeployed wallets
    assert derivation.ton_address(pub, NETWORKS["ton-testnet"], 0).address.startswith("0Q")
    assert derivation.normalize_address("ton", a) == derivation.ton_address(pub, NETWORKS["ton"], 0).meta["raw"]


def test_ton_external_message_is_signed_by_key(ring):
    import nacl.signing
    pub = ring.ton_public_key
    dest = ton_wallet.wallet_address(pub, derivation.TON_DEFAULT_SUBWALLET)
    msg = ton_wallet.internal_message(dest=dest, value=1, bounce=False)
    cell, boc = ton_wallet.build_external(public_key=pub, subwallet_id=derivation.ton_subwallet_id(3), seqno=0,
                                          messages=[(128, msg)], sign=ring.ton_sign)
    body = ton_wallet.boc_b64_to_cell(boc)
    assert body.hash == cell.hash
    payload = ton_wallet.signing_payload(derivation.ton_subwallet_id(3), 0, 0, [(128, msg)])
    nacl.signing.VerifyKey(pub).verify(payload.hash, ring.ton_sign(payload.hash))


def test_money_exactness():
    assert to_atomic("25.00", 6) == 25_000_000
    assert to_atomic("0.1", 18) == 10**17
    assert fmt_atomic(1, 18) == "0.000000000000000001"
    assert fmt_atomic(25_000_000, 6) == "25"
    with pytest.raises(AmountError):
        to_atomic("0.0000001", 6)
    assert to_atomic(Decimal("0.0000001"), 6, round_up=True) == 1


def test_asset_ids_are_canonical():
    ton = NETWORKS["ton"]
    assert ton.native_symbol == "GRAM" and ton.native.id == "ton:native"
    usdt = ton.tokens[0]
    assert usdt.id == f"ton:jetton:{TON_USDT_MASTER_RAW}"
    assert parse_asset_id(usdt.id) == ("ton", "jetton", TON_USDT_MASTER_RAW)
    # Same ticker on different networks never collides
    ids = [a.id for n in NETWORKS.values() for a in n.assets]
    assert len(ids) == len(set(ids))


def test_erc20_transfer_roundtrip():
    to = "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"
    data = evm_abi.encode_transfer(to, 123)
    assert evm_abi.decode_transfer(data) == (to, 123)
    with pytest.raises(ValueError):
        evm_abi.decode_transfer(data + "00")


def test_planned_networks_are_not_implemented():
    for k in ("ltc", "doge", "tron", "solana"):
        assert NETWORKS[k].status == "planned"
