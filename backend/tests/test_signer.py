import json

import pytest
from eth_account import Account

from cryptus.chains.base import NetConf
from cryptus.chains.bitcoin import BitcoinAdapter
from cryptus.core import derivation, evm_abi
from cryptus.core.networks import NETWORKS
from cryptus_signer import keystore
from cryptus_signer.policy import Policy, PolicyViolation
from cryptus_signer.signing import sign_btc_psbt, sign_evm, sign_ton

DEST_BTC = "bc1qnjg0jd8228aq7egyzacy8cys3knf9xvrerkf9g"
DEST_EVM = "0x000000000000000000000000000000000000dEaD"
USDT = "0xdAC17F958D2ee523a2206206994597C13D831ec7"


@pytest.fixture()
def policy(tmp_path, ring):
    ton_dest = derivation.ton_gas_wallet(ring.ton_public_key.hex(), NETWORKS["ton"]).address
    p = tmp_path / "policy.json"
    p.write_text(json.dumps({"networks": {
        "btc": {"allowlist": [DEST_BTC], "max_fee": "0.001"},
        "eth": {"allowlist": [DEST_EVM], "max_fee": "0.05", "max_gas_topup": "0.01", "tokens": [USDT]},
        "ton": {"allowlist": [ton_dest], "max_gas_topup": "0.2", "max_jetton_attach": "0.1"},
    }}))
    return Policy(p)


def test_keystore_roundtrip(tmp_path, ton_mnemonic):
    from tests.conftest import ABANDON
    path = tmp_path / "ks.json"
    keystore.create(path, keystore.Secrets(ABANDON, "", ton_mnemonic), passphrase="a very long passphrase!")
    assert keystore.unlock(path, "a very long passphrase!").bip39 == ABANDON
    with pytest.raises(ValueError):
        keystore.unlock(path, "wrong passphrase entirely")
    with pytest.raises(FileExistsError):
        keystore.create(path, keystore.Secrets(ABANDON, "", ton_mnemonic), passphrase="a very long passphrase!")


def _btc_psbt(ring, dest=DEST_BTC, value=150_000):
    net = NETWORKS["btc"]
    xpub = ring.btc_account_xpub(net)
    ad = BitcoinAdapter(net, NetConf("btc", "esplora", "http://unused", {}, {}, {}, 2))
    ad.raw_transaction = lambda txid: None
    a0 = derivation.btc_address(xpub, net, 0).address
    utxos = [{"txid": "11" * 32, "vout": 0, "value": value, "address": a0}]
    return ad.build_sweep_psbt(utxos=utxos, index_by_address={a0: 0}, xpub=xpub, master_fingerprint=ring.fingerprint,
                               account_path="m/84h/0h/0h", destination=dest, feerate=5)


def test_btc_psbt_signed_within_policy(ring, policy):
    psbt, amount, fee = _btc_psbt(ring)
    s = sign_btc_psbt(ring, NETWORKS["btc"], {"psbt": psbt}, policy.for_network("btc"))
    assert s.amount == amount and s.summary["fee"] == fee
    assert len(s.result["raw"]) > 100


def test_btc_psbt_to_unlisted_destination_refused(ring, policy):
    psbt, _, _ = _btc_psbt(ring, dest="bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu")
    with pytest.raises(PolicyViolation, match="allowlist"):
        sign_btc_psbt(ring, NETWORKS["btc"], {"psbt": psbt}, policy.for_network("btc"))


def _evm_req(ring, **over):
    src = derivation.evm_address(ring.evm_account_xpub(), 4).address
    tx = {"from": src, "to": USDT, "value": 0, "data": evm_abi.encode_transfer(DEST_EVM, 10**6), "gas": 80_000,
          "chainId": 1, "nonce": 0, "maxFeePerGas": 50 * 10**9, "maxPriorityFeePerGas": 10**9}
    tx.update(over.pop("tx", {}))
    req = {"tx": tx, "role": "deposit", "index": 4,
           "intent": {"type": "sweep_token", "token": USDT, "destination": DEST_EVM, "amount": 10**6}}
    req.update(over)
    return req


def test_evm_token_sweep_signed_by_deposit_key(ring, policy):
    s = sign_evm(ring, NETWORKS["eth"], _evm_req(ring), policy.for_network("eth"))
    assert Account.recover_transaction(s.result["raw"]) == derivation.evm_address(ring.evm_account_xpub(), 4).address


@pytest.mark.parametrize("over,msg", [
    ({"tx": {"data": evm_abi.encode_transfer("0x1111111111111111111111111111111111111111", 1)}}, "allowlist"),
    ({"tx": {"chainId": 56}}, "chainId"),
    ({"index": 5}, "derived signing address"),
    ({"tx": {"gas": 10_000_000}}, "max fee"),
    ({"tx": {"value": 1}}, "native value"),
])
def test_evm_policy_violations(ring, policy, over, msg):
    with pytest.raises(PolicyViolation, match=msg):
        sign_evm(ring, NETWORKS["eth"], _evm_req(ring, **over), policy.for_network("eth"))


def test_evm_gas_topup_only_to_own_deposit(ring, policy):
    xpub = ring.evm_account_xpub()
    gas = derivation.evm_gas_station(xpub).address
    good = derivation.evm_address(xpub, 7).address
    req = {"tx": {"from": gas, "to": good, "value": 10**15, "data": "0x", "gas": 21000, "chainId": 1, "nonce": 0,
                  "gasPrice": 10**9}, "role": "gas_station", "index": 0,
           "intent": {"type": "gas_topup", "recipient_index": 7, "amount": 10**15}}
    sign_evm(ring, NETWORKS["eth"], req, policy.for_network("eth"))
    req["tx"]["to"] = DEST_EVM
    with pytest.raises(PolicyViolation, match="recipient"):
        sign_evm(ring, NETWORKS["eth"], req, policy.for_network("eth"))


def test_ton_signing_and_limits(ring, policy):
    pol = policy.for_network("ton")
    dest = derivation.ton_gas_wallet(ring.ton_public_key.hex(), NETWORKS["ton"]).address
    s = sign_ton(ring, NETWORKS["ton"], {"role": "deposit", "index": 2, "seqno": 0,
                                         "intent": {"type": "sweep_native", "destination": dest}}, pol)
    assert s.result["boc"] and len(s.result["msg_hash"]) == 64
    with pytest.raises(PolicyViolation, match="cap"):
        sign_ton(ring, NETWORKS["ton"], {"role": "gas_wallet", "seqno": 3,
                                         "intent": {"type": "gas_topup", "recipient_index": 2,
                                                    "amount": 10**9}}, pol)


def test_halt_file_stops_everything(policy):
    (policy.path.parent / "HALT").write_text("")
    with pytest.raises(PolicyViolation, match="halted"):
        policy.for_network("eth")


def test_signer_exports_btc_xpubs_matching_derivation(ring):
    for key in ("btc", "btc-testnet"):
        net = NETWORKS[key]
        xpub = ring.btc_account_xpub(net)
        assert xpub[:4] == ("zpub" if key == "btc" else "vpub")
        derivation.btc_address(xpub, net, 0)  # accepted by the gateway for that network
