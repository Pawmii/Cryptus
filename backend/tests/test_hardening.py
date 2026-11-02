"""Regression tests for the pre-production review fixes."""
from datetime import timedelta

import pytest
from eth_account import Account

from cryptus.chains.base import Observed, TxState
from cryptus.core import evm_abi
from tests.conftest import owner_login
from tests.test_flow import ScriptedChain, _configure


@pytest.fixture()
def chain(monkeypatch):
    c = ScriptedChain()
    monkeypatch.setattr("cryptus.services.monitor.adapter_for", lambda db, key: c)
    monkeypatch.setattr("cryptus.services.invoices.adapter_for", lambda db, key: c)
    return c


# ── Settings can no longer lock operators out ─────────────────────────────

@pytest.mark.parametrize("section,body", [
    ("security", {"session_hours": 0}),
    ("security", {"session_hours": "abc"}),
    ("security", {"require_mfa_for_admins": "yes"}),
    ("invoice", {"default_expiry_minutes": 1}),
    ("invoice", {"underpay_tolerance_bps": 5000}),
    ("compliance", {"terms_url": "javascript:alert(1)"}),
    ("compliance", {"max_invoice_amount": -5}),
])
def test_invalid_settings_rejected(client, section, body):
    owner_login(client)
    r = client.put(f"/api/admin/settings/{section}", json=body)
    assert r.status_code == 400, r.text
    assert client.get("/api/auth/me").status_code == 200  # still able to use the console


def test_mfa_requirement_is_enforced(client):
    import pyotp
    from cryptus import db as dbm
    from cryptus.models import User
    owner_login(client)
    # cannot require 2FA for admins before having it yourself
    assert client.put("/api/admin/settings/security", json={"require_mfa_for_admins": True}).status_code == 400
    secret = client.post("/api/auth/totp/begin").json()["secret"]
    assert client.post("/api/auth/totp/confirm", json={"code": pyotp.TOTP(secret).now()}).status_code == 200
    assert client.put("/api/admin/settings/security", json={"require_mfa_for_admins": True}).status_code == 200
    with dbm.session_scope() as s:  # simulate an admin without 2FA
        s.query(User).update({"totp_enabled": False})
    r = client.post("/api/admin/api-keys", json={"name": "x", "mode": "test"})
    assert r.status_code == 403 and "two-factor" in r.json()["detail"]
    assert client.get("/api/admin/invoices").status_code == 200  # reading stays available


def test_totp_code_cannot_be_replayed(client):
    import pyotp
    owner_login(client)
    secret = client.post("/api/auth/totp/begin").json()["secret"]
    code = pyotp.TOTP(secret).now()
    assert client.post("/api/auth/totp/confirm", json={"code": code}).status_code == 200
    assert client.post("/api/auth/totp/disable", json={"code": code}).status_code == 400  # same code again


# ── Payments ──────────────────────────────────────────────────────────────

def test_reincluded_payment_is_revived(client, ring, chain):
    from cryptus import db as dbm
    from cryptus.services import monitor
    owner_login(client)
    key = _configure(client, ring)
    inv = client.post("/api/v1/invoices", json={"amount": "0.1", "currency": "eth-sepolia:native"},
                      headers={"Authorization": f"Bearer {key}"}).json()
    dep = inv["payment"]["id"]
    obs = Observed(dep, "0xfeed", -1, "eth-sepolia:native", 10**17, None, 50, "0xa", 5)
    chain.observed, chain.state["0xfeed"] = [obs], TxState(True, 5, 50, "0xa", amount=10**17)
    with dbm.session_scope() as s:
        monitor.run_network(s, "eth-sepolia")
    chain.observed, chain.state["0xfeed"] = [], TxState(False)  # reorged away
    with dbm.session_scope() as s:
        monitor.run_network(s, "eth-sepolia")
    assert client.get(f"/api/admin/invoices/{inv['id']}").json()["payments"][0]["status"] == "orphaned"
    obs2 = Observed(dep, "0xfeed", -1, "eth-sepolia:native", 10**17, None, 52, "0xb", 4)  # re-mined
    chain.observed, chain.state["0xfeed"] = [obs2], TxState(True, 4, 52, "0xb", amount=10**17)
    with dbm.session_scope() as s:
        monitor.run_network(s, "eth-sepolia")
    got = client.get(f"/api/admin/invoices/{inv['id']}").json()
    assert got["payments"][0]["status"] == "confirmed" and got["status"] == "paid"


def test_balance_reconciliation_waits_for_scan(client, ring):
    """No double credit when the balance moves before the block scan reaches the payment."""
    from cryptus import db as dbm
    from cryptus.models import Payment
    from cryptus.services.monitor import _reconcile_evm_native

    owner_login(client)
    key = _configure(client, ring)
    client.post("/api/v1/invoices", json={"amount": "0.1", "currency": "eth-sepolia:native"},
                headers={"Authorization": f"Bearer {key}"})

    class Adapter:
        def balance(self, *a, **k):
            return 10**17

    with dbm.session_scope() as s:
        assert _reconcile_evm_native(s, Adapter(), "eth-sepolia", 3, head=100, scanned_to=90) == set()
        assert s.query(Payment).count() == 0
        assert len(_reconcile_evm_native(s, Adapter(), "eth-sepolia", 3, head=100, scanned_to=100)) == 1


def test_fiat_test_invoice_uses_mainnet_price(client, ring, chain, monkeypatch):
    from decimal import Decimal
    owner_login(client)
    key = _configure(client, ring)
    monkeypatch.setattr("cryptus.services.rates._fetch",
                        lambda ids, vs, k, pro: {ids[0]: {vs.lower(): Decimal("2500")}})
    r = client.post("/api/v1/invoices", json={"amount": "25.00", "currency": "USD", "asset": "eth-sepolia:native"},
                    headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 201, r.text
    assert r.json()["payment"]["amount_due"] == "0.01"


# ── Consolidation ─────────────────────────────────────────────────────────

def test_only_paid_unswept_deposits_are_eligible(client, ring, chain):
    from cryptus import db as dbm
    from cryptus.db import utcnow
    from cryptus.models import DepositAddress, Invoice
    from cryptus.services import monitor
    from cryptus.services.consolidation import _eligible_deposits

    owner_login(client)
    key = _configure(client, ring)
    h = {"Authorization": f"Bearer {key}"}
    paid = client.post("/api/v1/invoices", json={"amount": "0.1", "currency": "eth-sepolia:native"}, headers=h).json()
    unpaid = client.post("/api/v1/invoices", json={"amount": "0.1", "currency": "eth-sepolia:native"}, headers=h).json()
    chain.observed = [Observed(paid["payment"]["id"], "0x1", -1, "eth-sepolia:native", 10**17, None, 1, "b", 9)]
    chain.state["0x1"] = TxState(True, 9, 1, "b", amount=10**17)
    with dbm.session_scope() as s:
        monitor.run_network(s, "eth-sepolia")
        inv = s.get(Invoice, unpaid["id"])
        inv.expires_at = utcnow() - timedelta(days=3)
        for d in inv.deposits:
            d.monitor_until = utcnow() - timedelta(days=2)
        monitor.expire_due(s)
    with dbm.session_scope() as s:
        assert [d.id for d in _eligible_deposits(s, "eth-sepolia")] == [paid["payment"]["id"]]
        s.get(DepositAddress, paid["payment"]["id"]).swept_at = utcnow()
    with dbm.session_scope() as s:
        assert _eligible_deposits(s, "eth-sepolia") == []  # swept, nothing new since


def test_decode_raw_transactions():
    acct = Account.create()
    to = "0x000000000000000000000000000000000000dEaD"
    t2 = acct.sign_transaction({"chainId": 11155111, "nonce": 0, "to": to, "value": 7, "data": b"", "gas": 21000,
                                "maxFeePerGas": 10, "maxPriorityFeePerGas": 1, "type": 2}).raw_transaction.hex()
    legacy = acct.sign_transaction({"chainId": 56, "nonce": 0, "to": to, "value": 9,
                                    "data": bytes.fromhex(evm_abi.encode_transfer(to, 1)[2:]),
                                    "gas": 60000, "gasPrice": 10}).raw_transaction.hex()
    a = evm_abi.decode_raw_transaction(t2)
    b = evm_abi.decode_raw_transaction("0x" + legacy.removeprefix("0x"))
    assert (a["to"], a["value"], a["chain_id"], a["data"]) == (to, 7, 11155111, "0x")
    assert (b["to"], b["value"], b["chain_id"]) == (to, 9, 56) and b["data"].startswith("0xa9059cbb")


def test_csv_export_neutralises_formulas(client, ring, chain):
    owner_login(client)
    key = _configure(client, ring)
    client.post("/api/v1/invoices", json={"amount": "0.1", "currency": "eth-sepolia:native",
                                          "order_id": "=HYPERLINK(\"http://x\")"},
                headers={"Authorization": f"Bearer {key}"})
    r = client.get("/api/admin/invoices/export.csv")
    assert r.status_code == 200
    assert "'=HYPERLINK" in r.text and ",=HYPERLINK" not in r.text


def test_console_routes_hidden_from_public_schema(monkeypatch):
    from cryptus.config import Settings
    import cryptus.app as app_mod
    monkeypatch.setattr(app_mod, "get_settings",
                        lambda: Settings(env="production", secret_key="x" * 40, database_url="sqlite://"))
    paths = app_mod.create_app().openapi()["paths"]
    assert any(p.startswith("/api/v1/") for p in paths)
    assert not any(p.startswith(("/api/admin", "/api/auth", "/api/setup")) for p in paths)


def test_worker_lease_is_exclusive(client):
    from cryptus.worker import acquire_lease
    assert acquire_lease("a") is True
    assert acquire_lease("b") is False
    assert acquire_lease("a") is True
