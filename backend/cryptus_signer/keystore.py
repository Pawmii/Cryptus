"""Encrypted keystore.

File format (JSON):
    {"version": 1, "kek": "passphrase" | "aws-kms",
     "kdf": {"name": "scrypt", "n": 2**17, "r": 8, "p": 1, "salt": b64},   # passphrase only
     "wrapped_key": b64,                                                 # aws-kms only
     "nonce": b64, "ciphertext": b64}

The plaintext is a JSON object with the BIP39 mnemonic (Bitcoin + EVM) and the
TON mnemonic. It is AES-256-GCM encrypted under a key-encryption key that is
either derived from an operator passphrase (scrypt) or a data key wrapped by
AWS KMS (envelope encryption). Secrets are only ever held in process memory.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

AAD = b"cryptus-signer-keystore-v1"


@dataclass
class Secrets:
    bip39: str
    bip39_passphrase: str
    ton_mnemonic: list[str]


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _scrypt(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(passphrase.encode())


def create(path: Path, secrets: Secrets, *, passphrase: str | None = None, kms_key_id: str | None = None) -> None:
    if path.exists():
        raise FileExistsError(f"{path} already exists — refusing to overwrite a keystore")
    plaintext = json.dumps({"bip39": secrets.bip39, "bip39_passphrase": secrets.bip39_passphrase,
                            "ton_mnemonic": secrets.ton_mnemonic}).encode()
    doc: dict = {"version": 1}
    if kms_key_id:
        import boto3  # optional dependency: pip install cryptus[kms]

        dk = boto3.client("kms").generate_data_key(KeyId=kms_key_id, KeySpec="AES_256")
        kek, doc["kek"], doc["wrapped_key"], doc["kms_key_id"] = dk["Plaintext"], "aws-kms", _b64(dk["CiphertextBlob"]), kms_key_id
    else:
        if not passphrase or len(passphrase) < 16:
            raise ValueError("keystore passphrase must be at least 16 characters")
        salt = os.urandom(16)
        n, r, p = 2**17, 8, 1
        kek = _scrypt(passphrase, salt, n, r, p)
        doc["kek"], doc["kdf"] = "passphrase", {"name": "scrypt", "n": n, "r": r, "p": p, "salt": _b64(salt)}
    nonce = os.urandom(12)
    doc["nonce"], doc["ciphertext"] = _b64(nonce), _b64(AESGCM(kek).encrypt(nonce, plaintext, AAD))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(doc, f, indent=2)


def unlock(path: Path, passphrase: str | None = None) -> Secrets:
    doc = json.loads(path.read_text())
    if doc.get("version") != 1:
        raise ValueError("unsupported keystore version")
    if doc["kek"] == "aws-kms":
        import boto3

        kek = boto3.client("kms").decrypt(CiphertextBlob=base64.b64decode(doc["wrapped_key"]))["Plaintext"]
    else:
        if not passphrase:
            raise ValueError("keystore passphrase required")
        k = doc["kdf"]
        kek = _scrypt(passphrase, base64.b64decode(k["salt"]), k["n"], k["r"], k["p"])
    try:
        pt = AESGCM(kek).decrypt(base64.b64decode(doc["nonce"]), base64.b64decode(doc["ciphertext"]), AAD)
    except Exception as exc:
        raise ValueError("cannot decrypt keystore (wrong passphrase or corrupted file)") from exc
    d = json.loads(pt)
    return Secrets(d["bip39"], d.get("bip39_passphrase", ""), d["ton_mnemonic"])
