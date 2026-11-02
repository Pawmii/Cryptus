"""cryptus-signer command line.

    cryptus-signer init      --keystore PATH [--kms-key-id ARN]     create a new encrypted keystore
    cryptus-signer import    --keystore PATH                        import existing mnemonics
    cryptus-signer pubkeys   --keystore PATH                        print public key material for the gateway
    cryptus-signer serve     --keystore PATH --policy PATH          run the signing service
    cryptus-signer offline sign-psbt|sign-evm|sign-ton              sign a sweep exported from the console
                                                                     (watch-only mode, air-gapped machine)

Secrets are read from the terminal or from files named by environment
variables (CRYPTUS_SIGNER_PASSPHRASE_FILE, CRYPTUS_SIGNER_TOKEN_FILE) so they
never appear in process arguments or the general environment.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from embit import bip39
from pytoniq_core.crypto.keys import mnemonic_new

from cryptus.core.networks import NETWORKS

from . import keystore
from .keys import KeyRing, btc_account_path


def _secret_from_file(env: str) -> str | None:
    p = os.environ.get(env)
    return Path(p).read_text().strip() if p else None


def _passphrase(confirm: bool = False) -> str:
    v = _secret_from_file("CRYPTUS_SIGNER_PASSPHRASE_FILE")
    if v:
        return v
    p = getpass.getpass("Keystore passphrase: ")
    if confirm and getpass.getpass("Repeat passphrase: ") != p:
        sys.exit("passphrases do not match")
    return p


def _ring(path: Path) -> KeyRing:
    doc = json.loads(path.read_text())
    return KeyRing(keystore.unlock(path, None if doc.get("kek") == "aws-kms" else _passphrase()))


def cmd_init(a) -> None:
    import secrets as _s

    words = bip39.mnemonic_from_bytes(_s.token_bytes(32))
    ton = mnemonic_new(24)
    keystore.create(Path(a.keystore), keystore.Secrets(words, "", ton),
                    passphrase=None if a.kms_key_id else _passphrase(confirm=True), kms_key_id=a.kms_key_id)
    print("Keystore created:", a.keystore)
    print("\nWRITE DOWN BOTH RECOVERY PHRASES NOW AND STORE THEM OFFLINE. They are shown once.\n")
    print("BIP39 (Bitcoin + EVM):\n  " + words)
    print("\nTON (24 words, TON-native scheme):\n  " + " ".join(ton))


def cmd_import(a) -> None:
    words = getpass.getpass("BIP39 mnemonic: ").strip()
    bpass = getpass.getpass("BIP39 passphrase (empty for none): ")
    ton = getpass.getpass("TON mnemonic (24 words): ").split()
    KeyRing(keystore.Secrets(words, bpass, ton))  # validates
    keystore.create(Path(a.keystore), keystore.Secrets(words, bpass, ton),
                    passphrase=None if a.kms_key_id else _passphrase(confirm=True), kms_key_id=a.kms_key_id)
    print("Keystore created:", a.keystore)


def cmd_pubkeys(a) -> None:
    ring = _ring(Path(a.keystore))
    out = {"fingerprint": ring.fingerprint, "evm": {"xpub": ring.evm_account_xpub(), "path": "m/44'/60'/0'"},
           "ton": {"public_key": ring.ton_public_key.hex(), "wallet": "v4r2"}}
    for k, n in NETWORKS.items():
        if n.family == "bitcoin":
            out[k] = {"xpub": ring.btc_account_xpub(n), "path": btc_account_path(n).replace("h", "'")}
    print(json.dumps(out, indent=2))


def cmd_serve(a) -> None:
    import uvicorn

    from .policy import Policy
    from .server import create_app

    token = _secret_from_file("CRYPTUS_SIGNER_TOKEN_FILE") or os.environ.get("CRYPTUS_SIGNER_TOKEN")
    if not token:
        sys.exit("signer token missing (CRYPTUS_SIGNER_TOKEN_FILE)")
    ring = _ring(Path(a.keystore))
    data = Path(a.data_dir)
    data.mkdir(parents=True, exist_ok=True)
    app = create_app(ring, Policy(Path(a.policy)), token, data)
    uvicorn.run(app, host=a.host, port=a.port, log_level="info", access_log=False)


def _load_sweep(path: str) -> dict:
    d = json.loads(Path(path).read_text())
    return d.get("artifact", d) | {"network": d.get("network")}


def cmd_offline(a) -> None:
    from .signing import sign_btc_psbt, sign_evm, sign_ton

    ring = _ring(Path(a.keystore))
    sw = _load_sweep(a.file)
    net = NETWORKS[sw["network"]]
    print(f"Network: {net.name}")
    if a.action == "sign-psbt":
        res = sign_btc_psbt(ring, net, {"psbt": sw["psbt"]}, None)
    elif a.action == "sign-evm":
        tx = dict(sw["tx"])
        tx["nonce"] = a.nonce if a.nonce is not None else int(input("Nonce for source address: "))
        res = sign_evm(ring, net, {"tx": tx, "role": sw["role"], "index": sw.get("index", 0),
                                   "intent": sw["intent"]}, None)
    else:
        seqno = a.seqno if a.seqno is not None else int(input("Current seqno of source wallet (0 if undeployed): "))
        res = sign_ton(ring, net, {"role": sw["role"], "index": sw.get("index"), "seqno": seqno,
                                   "intent": sw["intent"]}, None)
    print("Summary:", json.dumps(res.summary, default=str))
    if input("Type SIGN to output the signed transaction: ").strip() != "SIGN":
        sys.exit("aborted")
    print(res.result.get("raw") or res.result.get("boc"))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="cryptus-signer")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("init", "import"):
        s = sub.add_parser(name)
        s.add_argument("--keystore", required=True)
        s.add_argument("--kms-key-id")
    s = sub.add_parser("pubkeys")
    s.add_argument("--keystore", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--keystore", required=True)
    s.add_argument("--policy", required=True)
    s.add_argument("--data-dir", default="./signer-data")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8700)
    o = sub.add_parser("offline")
    o.add_argument("action", choices=["sign-psbt", "sign-evm", "sign-ton"])
    o.add_argument("--keystore", required=True)
    o.add_argument("--file", required=True, help="sweep JSON exported from the console")
    o.add_argument("--nonce", type=int)
    o.add_argument("--seqno", type=int)
    a = p.parse_args(argv)
    {"init": cmd_init, "import": cmd_import, "pubkeys": cmd_pubkeys, "serve": cmd_serve,
     "offline": cmd_offline}[a.cmd](a)


if __name__ == "__main__":
    main()
