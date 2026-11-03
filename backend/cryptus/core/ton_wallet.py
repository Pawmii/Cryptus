"""TON wallet v4r2 primitives: address computation and message construction.

Design: one ed25519 key controls an unbounded family of v4r2 wallets, one per
``subwallet_id``. A wallet's address is the hash of its StateInit
(code + data), and the data cell contains the subwallet id, so different ids
yield different, independent contract addresses — all spendable by the same
key. This gives CRYPTUS a unique deposit address per invoice while the
merchant (or the isolated signer) only needs a single key.

Deposit wallets are *not deployed* until consolidation. Funds sent to an
uninitialised address with ``bounce=false`` are credited to the account and
the first outgoing external message carries the StateInit that deploys the
wallet. Payment pages therefore always display the non-bounceable form
(``UQ…`` / ``0Q…``) — wallets will refuse or bounce transfers to a bounceable
address that has no code yet.
"""
from __future__ import annotations

import time
from base64 import b64decode, b64encode

from pytoniq_core import Address, Builder, Cell, ExternalMsgInfo, MessageAny, StateInit, WalletMessage, begin_cell
from pytoniq_core.tlb.transaction import InternalMsgInfo
from pytoniq_core import CurrencyCollection

WALLET_V4R2_CODE_HEX = (
    "b5ee9c72410214010002d4000114ff00f4a413f4bcf2c80b010201200203020148040504f8f28308d71820d31fd31fd31f02f823bbf264"
    "ed44d0d31fd31fd3fff404d15143baf2a15151baf2a205f901541064f910f2a3f80024a4c8cb1f5240cb1f5230cbff5210f400c9ed54f8"
    "0f01d30721c0009f6c519320d74a96d307d402fb00e830e021c001e30021c002e30001c0039130e30d03a4c8cb1f12cb1fcbff10111213"
    "02e6d001d0d3032171b0925f04e022d749c120925f04e002d31f218210706c7567bd22821064737472bdb0925f05e003fa403020fa4401"
    "c8ca07cbffc9d0ed44d0810140d721f404305c810108f40a6fa131b3925f07e005d33fc8258210706c7567ba923830e30d038210647374"
    "72ba925f06e30d06070201200809007801fa00f40430f8276f2230500aa121bef2e0508210706c7567831eb17080185004cb0526cf1658"
    "fa0219f400cb6917cb1f5260cb3f20c98040fb0006008a5004810108f45930ed44d0810140d720c801cf16f400c9ed540172b08e238210"
    "64737472831eb17080185005cb055003cf1623fa0213cb6acb1fcb3fc98040fb00925f03e20201200a0b0059bd242b6f6a2684080a06b9"
    "0fa0218470d4080847a4937d29910ce6903e9ff9837812801b7810148987159f31840201580c0d0011b8c97ed44d0d70b1f8003db29dfb"
    "513420405035c87d010c00b23281f2fff274006040423d029be84c600201200e0f0019adce76a26840206b90eb85ffc00019af1df6a268"
    "40106b90eb858fc0006ed207fa00d4d422f90005c8ca0715cbffc9d077748018c8cb05cb0222cf165005fa0214cb6b12ccccc973fb00c8"
    "4014810108f451f2a7020070810108d718fa00d33fc8542047810108f451f2a782106e6f746570748018c8cb05cb025006cf165004fa02"
    "14cb6a12cb1fcb3fc973fb0002006c810108d718fa00d33f305224810108f459f2a782106473747270748018c8cb05cb025005cf165003"
    "fa0213cb6acb1f12cb3fc973fb00000af400c9ed54696225e5"
)
# Representation hash of the canonical wallet v4r2 code (verified in tests).
WALLET_V4R2_CODE_HASH = "feb5ff6820e2ff0d9483e7e0d62c817d846789fb4ae580c878866d959dabd5c0"

OP_JETTON_TRANSFER = 0x0F8A7EA5
OP_JETTON_INTERNAL_TRANSFER = 0x178D4519
OP_JETTON_TRANSFER_NOTIFICATION = 0x7362D09C
OP_EXCESSES = 0xD53276DB

SEND_MODE_PAY_FEES_SEPARATELY = 1
SEND_MODE_IGNORE_ERRORS = 2
SEND_MODE_CARRY_ALL_BALANCE = 128

_CODE: Cell | None = None


def wallet_code() -> Cell:
    global _CODE
    if _CODE is None:
        _CODE = Cell.one_from_boc(bytes.fromhex(WALLET_V4R2_CODE_HEX))
    return _CODE


def data_cell(public_key: bytes, subwallet_id: int, seqno: int = 0) -> Cell:
    return (begin_cell()
            .store_uint(seqno, 32)
            .store_uint(subwallet_id, 32)
            .store_bytes(public_key)
            .store_bit(0)  # empty plugins dict
            .end_cell())


def state_init(public_key: bytes, subwallet_id: int) -> StateInit:
    return StateInit(code=wallet_code(), data=data_cell(public_key, subwallet_id))


def wallet_address(public_key: bytes, subwallet_id: int, workchain: int = 0) -> Address:
    return Address((workchain, state_init(public_key, subwallet_id).serialize().hash))


def friendly(addr: Address, *, bounceable: bool, testnet: bool) -> str:
    return addr.to_str(is_user_friendly=True, is_url_safe=True, is_bounceable=bounceable, is_test_only=testnet)


def parse_address(s: str) -> Address:
    return Address(s.strip())


def raw(addr: str | Address) -> str:
    a = addr if isinstance(addr, Address) else parse_address(addr)
    return a.to_str(is_user_friendly=False)


# ── Message bodies ─────────────────────────────────────────────────────────

def jetton_transfer_body(*, query_id: int, amount: int, destination: Address, response_destination: Address,
                         forward_ton_amount: int = 0) -> Cell:
    return (begin_cell()
            .store_uint(OP_JETTON_TRANSFER, 32)
            .store_uint(query_id, 64)
            .store_coins(amount)
            .store_address(destination)
            .store_address(response_destination)
            .store_bit(0)  # no custom payload
            .store_coins(forward_ton_amount)
            .store_bit(0)  # forward payload inline (empty)
            .end_cell())


def parse_jetton_internal_transfer(body: Cell) -> dict | None:
    """Decode an incoming ``internal_transfer`` received by a jetton wallet."""
    s = body.begin_parse()
    if s.remaining_bits < 32:
        return None
    op = s.load_uint(32)
    if op != OP_JETTON_INTERNAL_TRANSFER:
        return None
    query_id = s.load_uint(64)
    amount = s.load_coins()
    sender = s.load_address()
    comment = None
    try:  # response_address, forward_ton_amount, forward_payload:(Either Cell ^Cell)
        s.load_address()
        s.load_coins()
        payload = s.load_ref().begin_parse() if s.load_bit() else s
        comment = _text_comment(payload)
    except Exception:
        pass  # a transfer without (or with a malformed) forward payload simply carries no comment
    return {"query_id": query_id, "amount": amount, "from": sender.to_str(is_user_friendly=False) if sender else None,
            "comment": comment}


def _text_comment(s) -> str | None:
    """A TEP-74 text comment: op 0 followed by a snake-encoded string. Encrypted comments are not readable."""
    if s.remaining_bits < 32 or s.load_uint(32) != 0:
        return None
    return s.load_snake_string()


def comment_of(body: Cell) -> str | None:
    try:
        return _text_comment(body.begin_parse())
    except Exception:
        return None


def normalize_memo(text: str | None) -> str | None:
    """Comparison form of a payment comment: case and whitespace are ignored, nothing else is."""
    if not text:
        return None
    return "".join(text.split()).upper() or None


# ── External message construction (used only by signer / offline tooling) ──

def internal_message(*, dest: Address, value: int, bounce: bool, body: Cell | None = None,
                     init: StateInit | None = None) -> MessageAny:
    info = InternalMsgInfo(True, bounce, False, None, dest, CurrencyCollection(grams=value, other=None), 0, 0, 0, 0)
    return MessageAny(info=info, init=init, body=body or Cell.empty())


def signing_payload(subwallet_id: int, seqno: int, valid_until: int, messages: list[tuple[int, MessageAny]]) -> Cell:
    b = begin_cell().store_uint(subwallet_id, 32)
    b.store_uint(valid_until, 32)
    b.store_uint(seqno, 32)
    b.store_uint(0, 8)  # op: simple send
    for mode, msg in messages:
        b.store_cell(WalletMessage(send_mode=mode, message=msg).serialize())
    return b.end_cell()


def build_external(*, public_key: bytes, subwallet_id: int, seqno: int, messages: list[tuple[int, MessageAny]],
                   sign, valid_until: int | None = None) -> tuple[Cell, str]:
    """Return (external message cell, base64 BOC). ``sign`` maps 32-byte hash → 64-byte signature."""
    if not 1 <= len(messages) <= 4:
        raise ValueError("wallet v4 accepts 1..4 messages")
    vu = valid_until if valid_until is not None else int(time.time()) + 120
    payload = signing_payload(subwallet_id, seqno, vu, messages)
    signature = sign(payload.hash)
    body = Builder().store_bytes(signature).store_cell(payload).end_cell()
    addr = wallet_address(public_key, subwallet_id)
    init = state_init(public_key, subwallet_id) if seqno == 0 else None
    ext = MessageAny(info=ExternalMsgInfo(None, addr, 0), init=init, body=body)
    cell = ext.serialize()
    return cell, b64encode(cell.to_boc()).decode()


def boc_b64_to_cell(b64: str) -> Cell:
    return Cell.one_from_boc(b64decode(b64))
