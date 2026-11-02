"""Minimal, audited-by-reading ABI helpers for the ERC-20 subset CRYPTUS uses."""
from __future__ import annotations

from eth_utils import to_checksum_address

SEL_TRANSFER = "a9059cbb"      # transfer(address,uint256)
SEL_BALANCE_OF = "70a08231"    # balanceOf(address)
SEL_DECIMALS = "313ce567"      # decimals()
SEL_SYMBOL = "95d89b41"        # symbol()
TOPIC_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def _word_address(addr: str) -> str:
    a = addr.lower().removeprefix("0x")
    if len(a) != 40:
        raise ValueError("invalid EVM address")
    return a.rjust(64, "0")


def _word_uint(v: int) -> str:
    if v < 0 or v >= 2**256:
        raise ValueError("uint256 out of range")
    return format(v, "064x")


def encode_transfer(to: str, amount: int) -> str:
    return "0x" + SEL_TRANSFER + _word_address(to) + _word_uint(amount)


def decode_transfer(data: str) -> tuple[str, int]:
    d = data.lower().removeprefix("0x")
    if len(d) != 8 + 128 or d[:8] != SEL_TRANSFER:
        raise ValueError("calldata is not a plain ERC-20 transfer(address,uint256)")
    if d[8:32] != "0" * 24:
        raise ValueError("dirty address padding")
    return to_checksum_address("0x" + d[32:72]), int(d[72:136], 16)


def encode_balance_of(owner: str) -> str:
    return "0x" + SEL_BALANCE_OF + _word_address(owner)


def address_topic(addr: str) -> str:
    return "0x" + _word_address(addr)


def topic_to_address(topic: str) -> str:
    return to_checksum_address("0x" + topic[-40:])


def decode_uint(hexdata: str) -> int:
    d = hexdata.removeprefix("0x")
    return int(d[:64] or "0", 16)


def decode_string(hexdata: str) -> str:
    d = bytes.fromhex(hexdata.removeprefix("0x"))
    if len(d) == 32:  # some legacy tokens return bytes32
        return d.rstrip(b"\0").decode("utf-8", "replace")
    if len(d) < 64:
        return ""
    off = int.from_bytes(d[0:32], "big")
    ln = int.from_bytes(d[off:off + 32], "big")
    return d[off + 32: off + 32 + ln].decode("utf-8", "replace")


def decode_raw_transaction(raw: str) -> dict:
    """Decode a signed EIP-1559/EIP-2930 or legacy (EIP-155) transaction into to/value/data/chain_id."""
    import rlp
    from eth_account.typed_transactions import TypedTransaction
    from hexbytes import HexBytes

    b = bytes.fromhex(raw.removeprefix("0x"))
    if not b:
        raise ValueError("empty transaction")
    if b[0] <= 0x7F:  # typed envelope
        d = TypedTransaction.from_bytes(HexBytes(b)).as_dict()
        to = d.get("to")
        return {"to": to_checksum_address(to) if to else None, "value": int(d.get("value", 0)),
                "data": "0x" + bytes(d.get("data") or b"").hex(), "chain_id": int(d["chainId"])}
    fields = rlp.decode(b)
    if len(fields) != 9:
        raise ValueError("unsupported legacy transaction encoding")
    v = int.from_bytes(fields[6], "big")
    if v < 35:
        raise ValueError("legacy transaction without EIP-155 replay protection")
    to = fields[3]
    return {"to": to_checksum_address("0x" + to.hex()) if to else None, "value": int.from_bytes(fields[4], "big"),
            "data": "0x" + fields[5].hex(), "chain_id": (v - 35) // 2}
