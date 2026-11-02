"""End-to-end: install wizard → wallet → merchant API → detection → confirmation → webhook.

The blockchain is replaced by a scripted adapter *in tests only*; the gateway
code paths (allocation, upsert, refresh, state machine, events) are real.
"""
import json

import pytest

from cryptus.chains.base import Health, Observed, ScanResult, TxState
from tests.conftest import owner_login


class ScriptedChain:
    def __init__(self):
        self.observed: list[Observed] = []
        self.state: dict[str, TxState] = {}

    def health(self):
        return Health(True, 100)

    def prepare_deposit(self, item):
        return {}

    def scan(self, items):
        return ScanResult([o for o in self.observed if o.deposit_id in {i.deposit_id for i in items}], {"block": 100}, 100)

    def refresh(self, txid, idx, asset, address):
        return self.state.get(txid, TxState(False))

    def balance(self, *a, **k):
        return 0

    def head(self):
        return 100


@pytest.fixture()
def chain(monkeypatch):
    c = ScriptedChain()
    monkeypatch.setattr("cryptus.services.monitor.adapter_for", lambda db, key: c)
    monkeypatch.setattr("cryptus.services.invoices.adapter_for", lambda db, key: c)
    return c


def _configure(client, ring):
    xpub = ring.evm_account_xpub()
    r = client.put("/api/admin/networks/eth-sepolia", json={"endpoint_url": "https://rpc.example", "enabled": True})
    assert r.status_code == 200, r.text
    kc = client.post("/api/admin/keychains", json={"family": "evm", "scope": "evm", "public_material": xpub,
                                                   "label": "Treasury"}).json()
    assert kc["preview"][0]["address"] == "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"
    bad = client.post(f"/api/admin/keychains/{kc['id']}/verify", json={"address": "0x" + "1" * 40})
    assert bad.status_code == 400
    assert client.post(f"/api/admin/keychains/{kc['id']}/verify",
                       json={"address": kc["preview"][0]["address"]}).status_code == 200
    assert client.put("/api/admin/wallets/eth-sepolia", json={"keychain_id": kc["id"]}).status_code == 200
    key = client.post("/api/admin/api-keys", json={"name": "shop", "mode": "test"}).json()["key"]
    return key


def test_setup_requires_token(client):
    r = client.post("/api/setup/initialize", json={
        "token": "wrong", "owner": {"email": "a@b.co", "name": "x", "password": "correct horse battery staple"},
        "business": {"name": "x"}})
    assert r.status_code == 403
    owner_login(client)
    assert client.get("/api/setup/status").json()["has_owner"] is True
    again = client.post("/api/setup/initialize", json={
        "token": "setup-token-for-tests", "owner": {"email": "z@b.co", "name": "x",
                                                    "password": "correct horse battery staple"},
        "business": {"name": "x"}})
    assert again.status_code == 409


def test_csrf_and_roles(client):
    owner_login(client)
    csrf = client.headers.pop("X-CSRF-Token")
    assert client.put("/api/admin/settings/business", json={"name": "x"}).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    assert client.put("/api/admin/settings/business", json={"name": "Acme"}).status_code == 200


def test_full_payment_lifecycle(client, ring, chain):
    from cryptus import db as dbm
    from cryptus.models import Event, Invoice, WebhookDelivery, WebhookEndpoint
    from cryptus.security import encrypt_secret
    from cryptus.services import monitor, webhooks

    owner_login(client)
    key = _configure(client, ring)
    with dbm.session_scope() as s:
        s.add(WebhookEndpoint(url="https://shop.example/hook", secret_enc=encrypt_secret("whsec_test"), events=[],
                              mode="test"))
    api = {"Authorization": f"Bearer {key}"}

    assert client.post("/api/v1/invoices", json={"amount": "1", "currency": "USD"}, headers={}).status_code == 401
    assets = client.get("/api/v1/assets", headers=api).json()["data"]
    assert [a["id"] for a in assets] == ["eth-sepolia:native"]

    body = {"amount": "0.25", "currency": "eth-sepolia:native", "order_id": "A-1"}
    r1 = client.post("/api/v1/invoices", json=body, headers={**api, "Idempotency-Key": "k1"})
    assert r1.status_code == 201, r1.text
    inv = r1.json()
    replay = client.post("/api/v1/invoices", json=body, headers={**api, "Idempotency-Key": "k1"}).json()
    assert replay["id"] == inv["id"]
    conflict = client.post("/api/v1/invoices", json={**body, "amount": "9"}, headers={**api, "Idempotency-Key": "k1"})
    assert conflict.status_code == 409
    inv2 = client.post("/api/v1/invoices", json=body, headers=api).json()

    addr = inv["payment"]["address"]
    assert addr != inv2["payment"]["address"]  # unique deposit address per invoice
    assert inv["number"].startswith("CR-") and inv["payment"]["amount_due"] == "0.25"
    assert inv["status"] == "pending"

    # Public checkout view leaks no merchant metadata
    pub = client.get(f"/api/public/invoices/{inv['id']}").json()
    assert "metadata" not in pub and "order_id" not in pub and pub["payment"]["qr_svg"].startswith("<svg")

    dep_id = inv["payment"]["id"]
    chain.observed = [Observed(dep_id, "0xabc", -1, "eth-sepolia:native", 25 * 10**16, "0xpayer", 99, "0xblk", 2)]
    chain.state["0xabc"] = TxState(True, 2, 99, "0xblk", amount=25 * 10**16)
    with dbm.session_scope() as s:
        monitor.run_network(s, "eth-sepolia")
    got = client.get(f"/api/v1/invoices/{inv['id']}", headers=api).json()
    assert got["status"] == "processing" and got["payments"][0]["status"] == "pending"

    chain.state["0xabc"] = TxState(True, 3, 99, "0xblk", amount=25 * 10**16)
    with dbm.session_scope() as s:
        monitor.run_network(s, "eth-sepolia")
    got = client.get(f"/api/v1/invoices/{inv['id']}", headers=api).json()
    assert got["status"] == "paid" and not got["flags"]["overpaid"]

    # Reorg: the transaction vanishes from the canonical chain → invoice reverts
    chain.state["0xabc"] = TxState(False)
    with dbm.session_scope() as s:
        monitor.run_network(s, "eth-sepolia")
    got = client.get(f"/api/v1/invoices/{inv['id']}", headers=api).json()
    assert got["status"] != "paid" and got["payments"][0]["status"] == "orphaned"

    with dbm.session_scope() as s:
        types = [e.type for e in s.query(Event).filter(Event.invoice_id == inv["id"]).order_by(Event.created_at)]
        assert {"invoice.created", "payment.detected", "invoice.processing", "payment.confirmed", "invoice.paid",
                "invoice.reverted", "payment.orphaned"} <= set(types)
        d = s.query(WebhookDelivery).first()
        body = json.dumps(webhooks.envelope(d.event), separators=(",", ":")).encode()
        assert webhooks.verify("whsec_test", body, webhooks.sign("whsec_test", body))
        assert not webhooks.verify("whsec_other", body, webhooks.sign("whsec_test", body))
        assert s.get(Invoice, inv["id"]).mode == "test"


def test_live_key_cannot_see_test_invoices(client, ring, chain):
    owner_login(client)
    test_key = _configure(client, ring)
    live_key = client.post("/api/admin/api-keys", json={"name": "live", "mode": "live"}).json()["key"]
    inv = client.post("/api/v1/invoices", json={"amount": "0.1", "currency": "eth-sepolia:native"},
                      headers={"Authorization": f"Bearer {test_key}"}).json()
    r = client.get(f"/api/v1/invoices/{inv['id']}", headers={"Authorization": f"Bearer {live_key}"})
    assert r.status_code == 404
    # live mode has no operational mainnet assets → refused, never silently downgraded
    r = client.post("/api/v1/invoices", json={"amount": "10", "currency": "USD"},
                    headers={"Authorization": f"Bearer {live_key}"})
    assert r.status_code == 409


def test_audit_chain_detects_tampering(client):
    from cryptus import db as dbm
    from cryptus.models import AuditEntry
    from cryptus.services.audit import verify_chain
    owner_login(client)
    client.put("/api/admin/settings/business", json={"name": "Acme 2"})
    with dbm.session_scope() as s:
        assert verify_chain(s) == (True, None)
        e = s.query(AuditEntry).order_by(AuditEntry.id).first()
        e.action = "forged"
    with dbm.session_scope() as s:
        ok, at = verify_chain(s)
        assert not ok and at is not None


def test_planned_network_cannot_be_configured(client):
    owner_login(client)
    r = client.put("/api/admin/networks/solana", json={"endpoint_url": "https://x", "enabled": True})
    assert r.status_code == 400
    rows = {n["key"]: n["status"] for n in client.get("/api/admin/networks").json()}
    assert rows["solana"] == "planned" and rows["btc"] == "not_configured"
