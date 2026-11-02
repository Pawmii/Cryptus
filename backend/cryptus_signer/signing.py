"""Request validation and signing. Every check here is independent of the gateway.

Each ``sign_*`` function returns a ``Signed`` record including the asset and
amount that leaves our control, which feeds the rolling daily limits.
"""
from __future__ import annotations

from dataclasses import dataclass

from eth_account import Account
from eth_utils import to_checksum_address
from embit import script
from embit.finalizer import finalize_psbt
from embit.networks import NETWORKS as BTC_NETWORKS
from embit.psbt import PSBT

from cryptus.core import evm_abi, ton_wallet
from cryptus.core.derivation import evm_pubkey_to_address, normalize_address
from cryptus.core.networks import NetworkDef

from .keys import EVM_ACCOUNT_PATH, KeyRing, btc_account_path
from .policy import NetPolicy, PolicyViolation


@dataclass
class Signed:
    result: dict
    asset_id: str
    amount: int
    summary: dict


# ── Bitcoin ───────────────────────────────────────────────────────────────

def sign_btc_psbt(ring: KeyRing, net: NetworkDef, payload: dict, pol: NetPolicy | None) -> Signed:
    psbt = PSBT.from_string(payload["psbt"])
    fp = bytes.fromhex(ring.fingerprint)
    account = ring.root.derive(btc_account_path(net))
    acc_path = [int(x.rstrip("h"), 10) + (0x80000000 if x.endswith("h") else 0)
                for x in btc_account_path(net).split("/")[1:]]
    total_in = 0
    for i, inp in enumerate(psbt.inputs):
        if inp.witness_utxo is None:
            raise PolicyViolation(f"input {i} lacks witness_utxo")
        if inp.non_witness_utxo is not None:
            prev = inp.non_witness_utxo
            if prev.txid() != inp.txid or prev.vout[inp.vout].value != inp.witness_utxo.value:
                raise PolicyViolation(f"input {i}: non_witness_utxo does not match witness_utxo")
        ours = [d for d in inp.bip32_derivations.values() if d.fingerprint == fp]
        if len(ours) != 1:
            raise PolicyViolation(f"input {i} is not derived from this signer's key")
        path = ours[0].derivation
        if path[:3] != acc_path or len(path) != 5 or path[3] != 0:
            raise PolicyViolation(f"input {i} uses an unexpected derivation path")
        child = account.derive(path[3:])
        if inp.witness_utxo.script_pubkey.data != script.p2wpkh(child.key).data:
            raise PolicyViolation(f"input {i} script does not match its derivation")
        total_in += inp.witness_utxo.value
    total_out = 0
    for o in psbt.tx.vout:
        addr = o.script_pubkey.address(BTC_NETWORKS[net.btc_network])
        if pol:
            pol.require_destination(addr)
        total_out += o.value
    fee = total_in - total_out
    if fee <= 0:
        raise PolicyViolation("non-positive fee")
    vsize_est = 11 + 68 * len(psbt.inputs) + 43 * len(psbt.tx.vout)
    if pol and pol.max_fee is not None and fee > pol.max_fee:
        raise PolicyViolation(f"fee {fee} sat exceeds policy cap {pol.max_fee}")
    if pol and pol.max_feerate is not None and fee / vsize_est > pol.max_feerate * 1.1:
        raise PolicyViolation("feerate exceeds policy cap")
    if psbt.sign_with(ring.root) < len(psbt.inputs):
        raise PolicyViolation("could not sign every input")
    tx = finalize_psbt(psbt)
    if tx is None:
        raise PolicyViolation("finalization failed")
    return Signed({"raw": tx.serialize().hex(), "txid": tx.txid().hex()}, f"{net.key}:native", total_out,
                  {"inputs": len(psbt.inputs), "fee": fee, "outputs": len(psbt.tx.vout)})


# ── EVM ───────────────────────────────────────────────────────────────────

def _evm_addr(ring: KeyRing, role: str, index: int) -> tuple[bytes, str]:
    priv = ring.evm_private_key(role, index)
    path = f"{EVM_ACCOUNT_PATH}/{'0/' + str(index) if role == 'deposit' else '1/0'}"
    return priv, evm_pubkey_to_address(ring.root.derive(path).key.get_public_key())


def sign_evm(ring: KeyRing, net: NetworkDef, payload: dict, pol: NetPolicy | None) -> Signed:
    tx = dict(payload["tx"])
    intent = payload["intent"]
    role, index = payload["role"], int(payload.get("index", 0))
    if int(tx.get("chainId", -1)) != net.chain_id:
        raise PolicyViolation("chainId does not match the network")
    priv, from_addr = _evm_addr(ring, role, index)
    if tx.get("from", "").lower() != from_addr.lower():
        raise PolicyViolation("tx.from does not match the derived signing address")
    to = tx.get("to") or ""
    data = (tx.get("data") or "0x").lower()
    value = int(tx.get("value", 0))
    gas = int(tx["gas"])
    price = int(tx.get("maxFeePerGas") or tx.get("gasPrice") or 0)
    if price <= 0:
        raise PolicyViolation("missing fee parameters")
    kind = intent["type"]
    if kind == "sweep_native":
        if role != "deposit" or data not in ("0x", ""):
            raise PolicyViolation("native sweep must be a plain transfer from a deposit address")
        if pol:
            pol.require_destination(to)
        asset, amount = f"{net.key}:native", value
    elif kind == "sweep_token":
        if role != "deposit" or value != 0:
            raise PolicyViolation("token sweep must not carry native value")
        if to.lower() != intent["token"].lower():
            raise PolicyViolation("tx.to is not the declared token contract")
        if pol and pol.tokens is not None and to.lower() not in pol.tokens:
            raise PolicyViolation("token contract not permitted by policy")
        dest, amount = evm_abi.decode_transfer(data)
        if pol:
            pol.require_destination(dest)
        asset = f"{net.key}:erc20:{to_checksum_address(to)}"
    elif kind == "gas_topup":
        if role != "gas_station" or data not in ("0x", ""):
            raise PolicyViolation("gas top-ups must come from the gas station as plain transfers")
        _, recipient = _evm_addr(ring, "deposit", int(intent["recipient_index"]))
        if to.lower() != recipient.lower():
            raise PolicyViolation("gas top-up recipient is not one of this signer's deposit addresses")
        if pol and (pol.max_gas_topup is None or value > pol.max_gas_topup):
            raise PolicyViolation("gas top-up exceeds policy cap (max_gas_topup)")
        asset, amount = f"{net.key}:native", value
    else:
        raise PolicyViolation(f"unknown intent {kind!r}")
    fee = gas * price
    if pol and pol.max_fee is not None and fee > pol.max_fee:
        raise PolicyViolation(f"max fee {fee} exceeds policy cap {pol.max_fee}")
    clean = {"chainId": net.chain_id, "nonce": int(tx["nonce"]), "to": to_checksum_address(to), "value": value,
             "data": data if data != "0x" else b"", "gas": gas}
    if "maxFeePerGas" in tx:
        clean.update(maxFeePerGas=int(tx["maxFeePerGas"]), maxPriorityFeePerGas=int(tx["maxPriorityFeePerGas"]),
                     type=2)
    else:
        clean["gasPrice"] = int(tx["gasPrice"])
    signed = Account.from_key(priv).sign_transaction(clean)
    return Signed({"raw": "0x" + signed.raw_transaction.hex().removeprefix("0x"),
                   "txid": "0x" + signed.hash.hex().removeprefix("0x")}, asset, amount,
                  {"intent": kind, "from": from_addr, "to": clean["to"], "fee_cap": fee})


# ── TON ───────────────────────────────────────────────────────────────────

def sign_ton(ring: KeyRing, net: NetworkDef, payload: dict, pol: NetPolicy | None) -> Signed:
    intent = payload["intent"]
    role = payload["role"]
    sid = ring.ton_subwallet(role, payload.get("index"))
    seqno = int(payload["seqno"])
    kind = intent["type"]
    if kind == "sweep_native":
        if role != "deposit":
            raise PolicyViolation("native sweep must originate from a deposit wallet")
        dest = intent["destination"]
        if pol:
            pol.require_destination(dest)
        d = ton_wallet.parse_address(dest)
        msgs = [(ton_wallet.SEND_MODE_CARRY_ALL_BALANCE,
                 ton_wallet.internal_message(dest=d, value=0, bounce=d.is_bounceable))]
        asset, amount = f"{net.key}:native", 0  # whole balance; not known to the signer
    elif kind == "sweep_jetton":
        if role != "deposit":
            raise PolicyViolation("jetton sweep must originate from a deposit wallet")
        dest = intent["destination"]
        if pol:
            pol.require_destination(dest)
        attach = int(intent["attach"])
        if pol and (pol.max_jetton_attach is None or attach > pol.max_jetton_attach):
            raise PolicyViolation("attached GRAM exceeds policy cap (max_jetton_attach)")
        d = ton_wallet.parse_address(dest)
        body = ton_wallet.jetton_transfer_body(query_id=seqno, amount=int(intent["amount"]), destination=d,
                                               response_destination=d)
        jw = ton_wallet.parse_address(intent["jetton_wallet"])
        msgs = [(ton_wallet.SEND_MODE_PAY_FEES_SEPARATELY,
                 ton_wallet.internal_message(dest=jw, value=attach, bounce=True, body=body))]
        asset, amount = f"{net.key}:jetton:*", int(intent["amount"])
    elif kind == "gas_topup":
        if role != "gas_wallet":
            raise PolicyViolation("gas top-ups must come from the gas wallet")
        rid = ring.ton_subwallet("deposit", intent["recipient_index"])
        recipient = ton_wallet.wallet_address(ring.ton_public_key, rid)
        value = int(intent["amount"])
        if pol and (pol.max_gas_topup is None or value > pol.max_gas_topup):
            raise PolicyViolation("gas top-up exceeds policy cap (max_gas_topup)")
        msgs = [(ton_wallet.SEND_MODE_PAY_FEES_SEPARATELY,
                 ton_wallet.internal_message(dest=recipient, value=value, bounce=False))]
        asset, amount = f"{net.key}:native", value
    else:
        raise PolicyViolation(f"unknown intent {kind!r}")
    cell, boc = ton_wallet.build_external(public_key=ring.ton_public_key, subwallet_id=sid, seqno=seqno,
                                          messages=msgs, sign=ring.ton_sign)
    src = ton_wallet.wallet_address(ring.ton_public_key, sid)
    return Signed({"boc": boc, "msg_hash": cell.hash.hex()}, asset, amount,
                  {"intent": kind, "from": normalize_address("ton", src.to_str())})


SIGNERS = {"btc_psbt": ("bitcoin", sign_btc_psbt), "evm_tx": ("evm", sign_evm), "ton_transfer": ("ton", sign_ton)}
