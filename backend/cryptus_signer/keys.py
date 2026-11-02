"""Key derivation inside the signer. Mirrors cryptus.core.derivation exactly."""
from __future__ import annotations

from functools import cached_property

import nacl.signing
from embit import bip32, bip39
from embit.networks import NETWORKS as BTC_NETWORKS
from pytoniq_core.crypto.keys import mnemonic_is_valid, mnemonic_to_private_key

from cryptus.core.derivation import EVM_DEPOSIT_CHAIN, EVM_GAS_CHAIN, TON_DEFAULT_SUBWALLET, ton_subwallet_id
from cryptus.core.networks import NetworkDef

from .keystore import Secrets


def btc_account_path(net: NetworkDef) -> str:
    return "m/84h/0h/0h" if net.btc_network == "main" else "m/84h/1h/0h"


EVM_ACCOUNT_PATH = "m/44h/60h/0h"


class KeyRing:
    def __init__(self, s: Secrets):
        if not bip39.mnemonic_is_valid(s.bip39):
            raise ValueError("invalid BIP39 mnemonic in keystore")
        if not mnemonic_is_valid(s.ton_mnemonic):
            raise ValueError("invalid TON mnemonic in keystore")
        self._root = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(s.bip39, s.bip39_passphrase))
        pub, priv = mnemonic_to_private_key(s.ton_mnemonic)
        self._ton_pub, self._ton_sk = pub, nacl.signing.SigningKey(priv[:32])

    @cached_property
    def fingerprint(self) -> str:
        return self._root.my_fingerprint.hex()

    @property
    def root(self) -> bip32.HDKey:
        return self._root

    # Bitcoin
    def btc_account_xpub(self, net: NetworkDef) -> str:
        acc = self._root.derive(btc_account_path(net)).to_public()
        ver = BTC_NETWORKS[net.btc_network]["zpub" if net.btc_network == "main" else "vpub"]
        return acc.to_string(version=ver)

    # EVM
    def evm_account_xpub(self) -> str:
        return self._root.derive(EVM_ACCOUNT_PATH).to_public().to_string(version=BTC_NETWORKS["main"]["xpub"])

    def evm_private_key(self, role: str, index: int) -> bytes:
        if role == "deposit":
            path = f"{EVM_ACCOUNT_PATH}/{EVM_DEPOSIT_CHAIN}/{index}"
        elif role == "gas_station":
            path = f"{EVM_ACCOUNT_PATH}/{EVM_GAS_CHAIN}/0"
        else:
            raise ValueError("unknown EVM key role")
        return self._root.derive(path).key.secret

    # TON
    @property
    def ton_public_key(self) -> bytes:
        return self._ton_pub

    def ton_sign(self, digest: bytes) -> bytes:
        return self._ton_sk.sign(digest).signature

    @staticmethod
    def ton_subwallet(role: str, index: int | None) -> int:
        if role == "deposit":
            return ton_subwallet_id(int(index))
        if role == "gas_wallet":
            return TON_DEFAULT_SUBWALLET
        raise ValueError("unknown TON key role")
