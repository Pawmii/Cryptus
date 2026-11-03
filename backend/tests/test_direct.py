"""TON direct receiving: payments go straight to the merchant's wallet and are matched by memo.

The TON adapter runs for real against a scripted Toncenter (httpx.MockTransport); message bodies are built
with the same TL-B encoders wallets use, and two bodies captured from TON mainnet pin the parser to reality.
"""
import base64
import hashlib
import time
from decimal import Decimal

import httpx
import pytest
from pytoniq_core import Address, Cell, begin_cell

from cryptus.chains import ton as ton_mod
from cryptus.chains.base import AssetInfo, NetConf, WatchItem
from cryptus.chains.ton import TonAdapter
from cryptus.core import ton_wallet
from cryptus.core.networks import NETWORKS, TON_USDT_MASTER_RAW
from tests.conftest import owner_login

MERCHANT = "0:852443f8599fe6a5da34fe43049ac4e0beb3071bb2bfb56635ea9421287c283a"
SENDER = "0:a4be46a2341da3aaaf8cadbf3074260038932bcf8079c314633148a5a664dd9b"
JETTON_WALLET = "0:32081d54752b9340a75c777a94af0483f07329dd296eb4f915bb45e477b93f8a"

# Captured from TON mainnet (Fragment receiving USDT and GRAM with comments).
MAINNET_JETTON_BODY = ("te6cckEBAgEAfwABpxeNRRmetTOHdyf5lDOThwgAGRgfaupHFVUIlRNm3W6oxrPJyAUveXyumArCOxCebeMAAyMD7V1I4qqhE"
                       "qJs263VGNZ5OQCl7y+V0wFYR2ITzbxEBwEATAAAAAAyNTAgVGVsZWdyYW0gU3RhcnMgCgpSZWYjaW9hdlFWR3FtrR5ABg==")
MAINNET_NATIVE_BODY = "te6cckEBAQEAJwAASgAAAAA1MCBUZWxlZ3JhbSBTdGFycyAKClJlZiNVTHdMaFVxaG1jpRVv"


def b64(cell: Cell) -> str:
    return base64.b64encode(cell.to_boc()).decode()


def comment_cell(text: str) -> Cell:
    return begin_cell().store_uint(0, 32).store_snake_string(text).end_cell()


def jetton_body(amount: int, comment: str | None, *, inline: bool = False) -> Cell:
    b = (begin_cell().store_uint(ton_wallet.OP_JETTON_INTERNAL_TRANSFER, 32).store_uint(7, 64).store_coins(amount)
         .store_address(Address(SENDER)).store_address(Address(SENDER)).store_coins(1))
    if comment is None:
        return b.store_bit(0).end_cell()
    payload = comment_cell(comment)
    return (b.store_bit(0).store_cell(payload) if inline else b.store_bit(1).store_ref(payload)).end_cell()


def tx(lt: int, now: int, *, account: str = MERCHANT, value: int = 0, body: Cell | None = None, src: str = SENDER,
       bounce_ok: bool = False, seq: int | None = 990, failed: bool = False) -> dict:
    return {"hash": base64.b64encode(hashlib.sha256(f"{account}{lt}".encode()).digest()).decode(),
            "lt": str(lt), "now": now, "account": account, "mc_block_seqno": seq,
            "in_msg": {"source": src, "value": str(value), "bounced": False,
                       "message_content": {"body": b64(body or Cell.empty()), "decoded": None}},
            "description": {"aborted": failed, "compute_ph": {"success": not failed}, "action": {"success": True},
                            "credit_ph": {"credit": str(value)}, "bounce": {"type": "ok"} if bounce_ok else None}}


class FakeToncenter:
    def __init__(self, global_id: int = -239):
        self.txs: list[dict] = []
        self.calls: list[dict] = []
        self.global_id = global_id

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, q = request.url.path, dict(request.url.params)
        if path == "/api/v3/masterchainInfo":
            return httpx.Response(200, json={"last": {"seqno": 1000, "global_id": self.global_id}})
        if path == "/api/v3/transactions":
            self.calls.append(q)
            if "hash" in q:
                return httpx.Response(200, json={"transactions": [t for t in self.txs if t["hash"] == q["hash"]]})
            rows = sorted((t for t in self.txs if ton_mod.norm(t["account"]) == ton_mod.norm(q["account"])
                           and int(t["lt"]) >= int(q.get("start_lt", 0)) and t["now"] >= int(q.get("start_utime", 0))),
                          key=lambda t: int(t["lt"]), reverse=q.get("sort") == "desc")
            return httpx.Response(200, json={"transactions": rows[:int(q.get("limit", 40))]})
        return httpx.Response(404, json={"error": path})


def adapter(fake: FakeToncenter, cursor: dict | None = None, network: str = "ton") -> TonAdapter:
    conf = NetConf(network, "toncenter", "https://toncenter.example", {}, {}, cursor or {}, 1)
    return TonAdapter(NETWORKS[network], conf, http=httpx.Client(transport=httpx.MockTransport(fake)))


NATIVE = AssetInfo("ton:native", "native", "GRAM", 9, None)
USDT = AssetInfo(f"ton:jetton:{TON_USDT_MASTER_RAW}", "jetton", "USDT", 6, TON_USDT_MASTER_RAW)


def item(dep: str, memo: str, created: int, asset: AssetInfo = NATIVE, **meta) -> WatchItem:
    return WatchItem(dep, MERCHANT, "UQ…", asset, {"memo": memo, "created_ts": created, "direct": True, **meta})


# ── Parsing ───────────────────────────────────────────────────────────────

def test_parses_real_mainnet_comments():
    parsed = ton_wallet.parse_jetton_internal_transfer(Cell.one_from_boc(base64.b64decode(MAINNET_JETTON_BODY)))
    assert parsed["amount"] == 3_750_000 and parsed["comment"] == "250 Telegram Stars \n\nRef#ioavQVGqm"
    assert ton_wallet.comment_of(Cell.one_from_boc(base64.b64decode(MAINNET_NATIVE_BODY))) == \
        "50 Telegram Stars \n\nRef#ULwLhUqhm"


def test_forward_payload_variants():
    assert ton_wallet.parse_jetton_internal_transfer(jetton_body(5, "CR-1", inline=True))["comment"] == "CR-1"
    assert ton_wallet.parse_jetton_internal_transfer(jetton_body(5, "CR-1"))["comment"] == "CR-1"
    assert ton_wallet.parse_jetton_internal_transfer(jetton_body(5, None))["comment"] is None
    encrypted = begin_cell().store_uint(0x2167DA4B, 32).store_uint(0, 64).end_cell()
    assert ton_wallet.comment_of(encrypted) is None
    assert ton_wallet.normalize_memo("  cr-2026-000001 \n") == "CR-2026-000001"
    assert ton_wallet.normalize_memo("   ") is None


# ── Adapter: following the merchant wallet ────────────────────────────────

def test_native_direct_scan_matches_memo_and_respects_cursor_lag():
    t0 = int(time.time()) - 600
    fake = FakeToncenter()
    gram = 10**9
    fake.txs = [
        tx(100, t0 - 1000, value=gram, body=comment_cell("CR-2026-000001")),     # before the memo existed
        tx(110, t0 + 10, value=5 * gram, body=comment_cell(" cr-2026-000001 ")),  # case/space-insensitive
        tx(120, t0 + 20, value=gram, body=comment_cell("thanks!")),              # merchant's own business
        tx(130, t0 + 30, value=gram, body=comment_cell("CR-2026-000002"), bounce_ok=True),  # bounced back
        tx(140, t0 + 40, value=2 * gram, body=comment_cell("CR-2026-000002")),
        tx(150, t0 + 50, value=gram),                                            # no comment
        tx(160, t0 + 60, value=1, body=begin_cell().store_uint(ton_wallet.OP_JETTON_TRANSFER_NOTIFICATION, 32)
           .store_uint(0, 64).store_coins(10**6).store_address(Address(SENDER)).end_cell()),
        tx(170, int(time.time()) - 5, value=3 * gram, body=comment_cell("CR-2026-000003")),  # recent
    ]
    items = [item("A", "CR-2026-000001", t0), item("B", "CR-2026-000002", t0), item("C", "CR-2026-000003", t0)]
    res = adapter(fake).scan(items)
    got = {(o.deposit_id, o.amount) for o in res.observed}
    assert got == {("A", 5 * gram), ("B", 2 * gram), ("C", 3 * gram)}
    assert all(o.detail["memo"] for o in res.observed) and res.observed[0].confirmations == 11
    assert fake.calls[0]["sort"] == "asc" and int(fake.calls[0]["start_utime"]) == t0 - ton_mod.MEMO_CLOCK_SKEW
    # The cursor stops before transactions younger than the lag, so they are read again next pass.
    assert res.cursor["accounts"][MERCHANT] == "160"
    again = adapter(fake, res.cursor).scan(items)
    assert fake.calls[-1]["start_lt"] == "161"
    assert {o.deposit_id for o in again.observed} == {"C"}


def test_native_direct_scan_paginates(monkeypatch):
    monkeypatch.setattr(ton_mod, "DIRECT_PAGE", 2)
    t0 = int(time.time()) - 3600
    fake = FakeToncenter()
    fake.txs = [tx(100 + i, t0 + i, value=10**9, body=comment_cell(f"M-{i % 3}")) for i in range(7)]
    items = [item(f"D{i}", f"M-{i}", t0) for i in range(3)]
    res = adapter(fake).scan(items)
    assert sorted(o.amount for o in res.observed if o.deposit_id == "D0") == [10**9] * 3
    assert len(res.observed) == 7 and res.cursor["accounts"][MERCHANT] == "106"


def test_jetton_direct_scan(monkeypatch):
    t0 = int(time.time()) - 600
    fake = FakeToncenter()
    fake.txs = [
        tx(200, t0 + 5, account=JETTON_WALLET, body=jetton_body(25_000_000, "CR-2026-000009")),
        tx(210, t0 + 6, account=JETTON_WALLET, body=jetton_body(1_000_000, "other")),
        tx(220, t0 + 7, account=JETTON_WALLET, body=jetton_body(9_000_000, "CR-2026-000009"), failed=True),
    ]
    it = item("J", "CR-2026-000009", t0, USDT, jetton_wallet=JETTON_WALLET)
    checks = []
    monkeypatch.setattr(TonAdapter, "verify_jetton_wallet", lambda self, jw, owner, master: checks.append(jw) or True)
    res = adapter(fake).scan([it])
    assert [(o.deposit_id, o.amount, o.sender) for o in res.observed] == [("J", 25_000_000, SENDER)]
    assert checks == [JETTON_WALLET] and res.deposit_meta["J"]["jetton_wallet_verified"] is True
    assert res.observed[0].detail["jetton_wallet"] == JETTON_WALLET

    monkeypatch.setattr(TonAdapter, "verify_jetton_wallet", lambda self, jw, owner, master: False)
    res = adapter(fake).scan([item("J", "CR-2026-000009", t0, USDT, jetton_wallet=JETTON_WALLET)])
    assert res.observed[0].detail["rejected"] == "jetton wallet owner/master mismatch"


# ── End to end through the console, merchant API, monitor and checkout ────

def _merchant_testnet() -> str:
    return Address(MERCHANT).to_str(is_user_friendly=True, is_url_safe=True, is_bounceable=False, is_test_only=True)


@pytest.fixture()
def direct_wallet(client, monkeypatch):
    from cryptus.chains.ton import GetMethodFailed

    class Probe:
        def account_state(self, address):
            return {"state": "active", "balance": 0}

        def seqno(self, address):
            if address == JETTON_WALLET:
                raise GetMethodFailed("get-method seqno exited with 11")
            return 4

    monkeypatch.setattr("cryptus.services.infra.adapter_for", lambda db, key: Probe())
    owner_login(client)
    assert client.put("/api/admin/networks/ton-testnet", json={"endpoint_url": "https://toncenter.example",
                                                               "enabled": True}).status_code == 200
    body = {"family": "ton", "scope": "ton-testnet", "mode": "direct", "label": "Tonkeeper"}
    mainnet_form = Address(MERCHANT).to_str(is_user_friendly=True, is_bounceable=False)
    r = client.post("/api/admin/keychains", json={**body, "public_material": mainnet_form})
    assert r.status_code == 400 and "testnet" in r.text
    r = client.post("/api/admin/keychains", json={**body, "public_material": JETTON_WALLET})
    assert r.status_code == 400 and "not a wallet" in r.text
    kc = client.post("/api/admin/keychains", json={**body, "public_material": _merchant_testnet()}).json()
    assert kc["mode"] == "direct" and kc["preview"] == [{"index": 0, "path": "memo", "address": _merchant_testnet()}]
    bounceable = Address(MERCHANT).to_str(is_user_friendly=True, is_url_safe=True, is_bounceable=True, is_test_only=True)
    assert client.post(f"/api/admin/keychains/{kc['id']}/verify", json={"address": bounceable}).status_code == 200
    assert client.put("/api/admin/wallets/ton-testnet", json={"keychain_id": kc["id"]}).status_code == 200
    return client.post("/api/admin/api-keys", json={"name": "shop", "mode": "test"}).json()["key"]


def test_direct_receive_end_to_end(client, direct_wallet, monkeypatch):
    from cryptus import db as dbm
    from cryptus.models import NetworkConfig
    from cryptus.services import consolidation, monitor

    fake = FakeToncenter(global_id=-3)
    monkeypatch.setattr("cryptus.services.monitor.adapter_for",
                        lambda db, key: adapter(fake, db.get(NetworkConfig, key).scan_cursor, "ton-testnet"))
    monkeypatch.setattr("cryptus.services.invoices.adapter_for", lambda db, key: adapter(fake, None, "ton-testnet"))
    auth = {"Authorization": f"Bearer {direct_wallet}"}
    inv = client.post("/api/v1/invoices", json={"amount": "1.5", "currency": "ton-testnet:native"}, headers=auth).json()
    pay = inv["payment"]
    assert pay["address"] == _merchant_testnet() and pay["memo"] == inv["number"]
    assert pay["payment_uri"] == f"ton://transfer/{_merchant_testnet()}?amount=1500000000&text={inv['number']}"
    public = client.get(f"/api/public/invoices/{inv['id']}").json()
    assert public["payment"]["memo"] == inv["number"]

    fake.txs = [tx(500, int(time.time()) - 120, value=1_500_000_000, body=comment_cell(inv["number"].lower()))]
    with dbm.session_scope() as s:
        monitor.run_network(s, "ton-testnet")
    got = client.get(f"/api/v1/invoices/{inv['id']}", headers=auth).json()
    assert got["status"] == "paid" and got["payments"][0]["amount"] == "1.5"

    with dbm.session_scope() as s:  # funds are already in the merchant's wallet; nothing to sweep
        assert consolidation.plan(s, "ton-testnet", force=True) == []
    r = client.put("/api/admin/wallets/ton-testnet", json={"set_destination": True, "destination": _merchant_testnet()})
    assert r.status_code == 400
    assert client.put("/api/admin/wallets/ton-testnet", json={"sweep_enabled": True}).status_code == 400
    net = next(n for n in client.get("/api/admin/networks").json() if n["key"] == "ton-testnet")
    assert net["wallet"]["receiving_address"] == _merchant_testnet() and net["wallet"]["gas_address"] is None
    assert client.get("/api/admin/keychains").status_code == 200


def test_each_quote_gets_its_own_memo(client, direct_wallet, monkeypatch):
    from cryptus import db as dbm
    from cryptus.models import Asset

    jetton = "0:" + "ab" * 32
    with dbm.session_scope() as s:
        s.add(Asset(id=f"ton-testnet:jetton:{jetton}", network="ton-testnet", kind="jetton", symbol="tUSDT",
                    name="Test USDT", decimals=6, contract=jetton, builtin=False, enabled=True,
                    verified_at=dbm.utcnow(), verification={}))
    monkeypatch.setattr("cryptus.services.rates.rate", lambda db, asset, cur: (Decimal("2"), "test"))
    monkeypatch.setattr("cryptus.services.invoices.adapter_for", lambda db, key: adapter(FakeToncenter(-3), None,
                                                                                          "ton-testnet"))
    monkeypatch.setattr(TonAdapter, "jetton_wallet_of", lambda self, master, owner: JETTON_WALLET)
    auth = {"Authorization": f"Bearer {direct_wallet}"}
    inv = client.post("/api/v1/invoices", json={"amount": "10", "currency": "USD"}, headers=auth).json()
    memos = []
    for asset in ("ton-testnet:native", f"ton-testnet:jetton:{jetton}", "ton-testnet:native"):
        r = client.post(f"/api/public/invoices/{inv['id']}/select", json={"asset": asset})
        assert r.status_code == 200, r.text
        memos.append(r.json()["payment"]["memo"])
    n = inv["number"]
    assert memos == [n, f"{n}-2", f"{n}-3"]
    uri = r.json()["payment"]["payment_uri"]
    assert uri.endswith(f"&text={n}-3")
