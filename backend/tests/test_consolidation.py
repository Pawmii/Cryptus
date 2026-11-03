"""End-to-end consolidation in managed mode: plan → gas top-up → approval → real signer → broadcast → receipt.

Only the blockchain is scripted; planning, approvals, the signer's policy checks and signing are real.
"""
import json

import pytest
from eth_account import Account

from cryptus.chains.base import Observed, TxState
from cryptus.core import evm_abi
from cryptus.core.derivation import evm_address, evm_gas_station
from cryptus.core.networks import NETWORKS
from tests.conftest import owner_login
from tests.test_flow import ScriptedChain

TOKEN = "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238"  # USDC on Sepolia
DEST = "0x000000000000000000000000000000000000dEaD"


class FakeEvm:
    def __init__(self):
        self.balances: dict[tuple[str, str], int] = {}
        self.sent: list[str] = []
        self.mined: dict[str, int] = {}
        self.price = 2 * 10**9

    def balance(self, address, asset, meta=None, block="latest"):
        return self.balances.get((address.lower(), asset.id), 0)

    def fee_params(self):
        return {"maxFeePerGas": self.price, "maxPriorityFeePerGas": 10**9}

    def estimate_gas(self, tx):
        return 52_000

    def nonce(self, address):
        return 0

    def simulate(self, tx):
        return None

    def broadcast(self, raw):
        self.sent.append(raw)
        return "0x" + Account.recover_transaction(raw)[2:6] + "%060x" % len(self.sent)

    def receipt_state(self, txid):
        if txid not in self.mined:
            return TxState(found=False)
        return TxState(True, self.mined[txid], 1, "0xblk")

    def tx_known(self, txid):
        return txid in self.mined


@pytest.fixture()
def setup_managed(client, ring, monkeypatch, tmp_path):
    from cryptus import db as dbm
    from cryptus.models import Asset
    from cryptus.services import audit, infra, settings as st, signer_client
    from cryptus_signer.policy import Policy
    from cryptus_signer.signing import SIGNERS

    owner_login(client)
    actor = audit.Actor("user", None, "test")
    with dbm.session_scope() as s:
        infra.configure_network(s, actor, "eth-sepolia", backend="jsonrpc", endpoint_url="https://rpc.example",
                                credentials=None, confirmations=None, options=None, enabled=True)
        kc = infra.create_keychain(s, actor, family="evm", scope="evm", public_material=ring.evm_account_xpub(),
                                   mode="managed", label="signer")
        kc.verified_at = __import__("cryptus.db", fromlist=["x"]).utcnow()
        infra.attach_wallet(s, actor, "eth-sepolia", kc.id)
        infra.set_destination(s, actor, "eth-sepolia", DEST)
        s.add(Asset(id=f"eth-sepolia:erc20:{TOKEN}", network="eth-sepolia", kind="erc20", symbol="USDC", name="USDC",
                    decimals=6, contract=TOKEN, builtin=False, enabled=True,
                    verified_at=__import__("cryptus.db", fromlist=["x"]).utcnow(), verification={}))
        st.put(s, "treasury", {"require_approval": True})

    pol_file = tmp_path / "policy.json"
    pol_file.write_text(json.dumps({"networks": {"eth-sepolia": {
        "allowlist": [DEST], "max_fee": "0.01", "max_gas_topup": "0.005", "tokens": [TOKEN]}}}))
    policy = Policy(pol_file)
    signed: list[dict] = []

    def fake_sign(network, kind, payload, request_id=None):
        net = NETWORKS[network]
        res = SIGNERS[kind][1](ring, net, payload, policy.for_network(network))
        signed.append(payload)
        return res.result

    monkeypatch.setattr(signer_client, "policy", lambda: policy.summary())
    monkeypatch.setattr(signer_client, "sign", fake_sign)
    fake = FakeEvm()
    monkeypatch.setattr("cryptus.services.consolidation.adapter_for", lambda db, key: fake)
    return fake, signed


def test_token_sweep_with_gas_topup(client, ring, monkeypatch, setup_managed):
    from cryptus import db as dbm
    from cryptus.models import DepositAddress, Sweep
    from cryptus.services import consolidation, monitor
    from cryptus.services.audit import Actor

    fake, signed = setup_managed
    scripted = ScriptedChain()
    monkeypatch.setattr("cryptus.services.monitor.adapter_for", lambda db, key: scripted)
    monkeypatch.setattr("cryptus.services.invoices.adapter_for", lambda db, key: scripted)
    key = client.post("/api/admin/api-keys", json={"name": "t", "mode": "test"}).json()["key"]
    inv = client.post("/api/v1/invoices", json={"amount": "25", "currency": f"eth-sepolia:erc20:{TOKEN}"},
                      headers={"Authorization": f"Bearer {key}"}).json()
    dep_id, addr = inv["payment"]["id"], inv["payment"]["address"]
    assert addr == evm_address(ring.evm_account_xpub(), 0).address

    # customer pays 25 USDC; confirmed
    scripted.observed = [Observed(dep_id, "0xpay", 0, f"eth-sepolia:erc20:{TOKEN}", 25_000_000, None, 1, "b", 9)]
    scripted.state["0xpay"] = TxState(True, 9, 1, "b", amount=25_000_000)
    with dbm.session_scope() as s:
        monitor.run_network(s, "eth-sepolia")
    assert client.get(f"/api/v1/invoices/{inv['id']}", headers={"Authorization": f"Bearer {key}"}).json()["status"] == "paid"

    fake.balances[(addr.lower(), f"eth-sepolia:erc20:{TOKEN}")] = 25_000_000  # on-chain truth
    with dbm.session_scope() as s:
        planned = consolidation.plan(s, "eth-sepolia", force=True)
        kinds = sorted(p.kind for p in planned)
        assert kinds == ["gas_topup", "sweep"]
        topup = next(p for p in planned if p.kind == "gas_topup")
        sweep = next(p for p in planned if p.kind == "sweep")
        assert sweep.depends_on == topup.id
        ids = (topup.id, sweep.id)
        for p in planned:
            consolidation.advance(s, p)  # planned → awaiting_approval
            assert p.status == "awaiting_approval"
            consolidation.approve(s, p, Actor("user", "u1", "op"))

    with dbm.session_scope() as s:
        t, w = s.get(Sweep, ids[0]), s.get(Sweep, ids[1])
        consolidation.advance(s, w)  # waits for the top-up
        assert w.status == "approved" and not fake.sent
        consolidation.advance(s, t)  # signs & broadcasts the top-up
        assert t.status == "broadcast", t.error
    raw_topup = fake.sent[-1]
    assert Account.recover_transaction(raw_topup) == evm_gas_station(ring.evm_account_xpub()).address
    assert evm_abi.decode_raw_transaction(raw_topup)["to"] == addr

    with dbm.session_scope() as s:
        t = s.get(Sweep, ids[0])
        fake.mined[t.txid] = 10
        fake.balances[(addr.lower(), "eth-sepolia:native")] = int(t.amount)
        consolidation.advance(s, t)  # tracked by receipt
        assert t.status == "confirmed"
    with dbm.session_scope() as s:
        w = s.get(Sweep, ids[1])
        consolidation.advance(s, w)
        assert w.status == "broadcast", w.error
    raw = fake.sent[-1]
    d = evm_abi.decode_raw_transaction(raw)
    assert Account.recover_transaction(raw) == addr and d["to"] == TOKEN
    assert evm_abi.decode_transfer(d["data"]) == (DEST, 25_000_000)

    with dbm.session_scope() as s:
        w = s.get(Sweep, ids[1])
        fake.mined[w.txid] = 10
        consolidation.advance(s, w)
        assert w.status == "confirmed"  # token sweeps used to be marked "reverted" here
        assert s.get(DepositAddress, dep_id).swept_at is not None
    assert [p["intent"]["type"] for p in signed] == ["gas_topup", "sweep_token"]


def test_fee_spike_blocks_signing(client, ring, monkeypatch, setup_managed):
    from cryptus import db as dbm
    from cryptus.models import Sweep
    from cryptus.services import consolidation, monitor
    from cryptus.services.audit import Actor

    fake, signed = setup_managed
    scripted = ScriptedChain()
    monkeypatch.setattr("cryptus.services.monitor.adapter_for", lambda db, key: scripted)
    monkeypatch.setattr("cryptus.services.invoices.adapter_for", lambda db, key: scripted)
    key = client.post("/api/admin/api-keys", json={"name": "t", "mode": "test"}).json()["key"]
    inv = client.post("/api/v1/invoices", json={"amount": "0.5", "currency": "eth-sepolia:native"},
                      headers={"Authorization": f"Bearer {key}"}).json()
    dep_id, addr = inv["payment"]["id"], inv["payment"]["address"]
    scripted.observed = [Observed(dep_id, "0xp", -1, "eth-sepolia:native", 5 * 10**17, None, 1, "b", 9)]
    scripted.state["0xp"] = TxState(True, 9, 1, "b", amount=5 * 10**17)
    with dbm.session_scope() as s:
        monitor.run_network(s, "eth-sepolia")
        from cryptus.models import NetworkWallet
        s.get(NetworkWallet, "eth-sepolia").max_fee = {"native": "0.001"}
    fake.balances[(addr.lower(), "eth-sepolia:native")] = 5 * 10**17
    with dbm.session_scope() as s:
        (sw,) = consolidation.plan(s, "eth-sepolia", force=True)
        consolidation.advance(s, sw)
        consolidation.approve(s, sw, Actor("user", "u1", "op"))
        sid = sw.id
    fake.price = 10**12  # gas spikes between approval and signing → fee far above the 0.001 cap
    with dbm.session_scope() as s:
        sw = s.get(Sweep, sid)
        consolidation.advance(s, sw)
        assert sw.status == "approved" and "exceeds the configured cap" in sw.error
    assert not fake.sent and not signed
    fake.price = 2 * 10**9  # fees normalise; value is recomputed from the live balance
    with dbm.session_scope() as s:
        sw = s.get(Sweep, sid)
        sw.next_attempt_at = None
        consolidation.advance(s, sw)
        assert sw.status == "broadcast", sw.error
        assert int(sw.amount) == 5 * 10**17 - 21_000 * 2 * 10**9


def test_failed_sweep_is_replanned_a_bounded_number_of_times(client, ring, monkeypatch, setup_managed):
    from cryptus import db as dbm
    from cryptus.models import Sweep
    from cryptus.services import consolidation, monitor

    fake, _ = setup_managed
    scripted = ScriptedChain()
    monkeypatch.setattr("cryptus.services.monitor.adapter_for", lambda db, key: scripted)
    monkeypatch.setattr("cryptus.services.invoices.adapter_for", lambda db, key: scripted)
    key = client.post("/api/admin/api-keys", json={"name": "t", "mode": "test"}).json()["key"]
    inv = client.post("/api/v1/invoices", json={"amount": "0.5", "currency": "eth-sepolia:native"},
                      headers={"Authorization": f"Bearer {key}"}).json()
    dep_id, addr = inv["payment"]["id"], inv["payment"]["address"]
    scripted.observed = [Observed(dep_id, "0xp", -1, "eth-sepolia:native", 5 * 10**17, None, 1, "b", 9)]
    scripted.state["0xp"] = TxState(True, 9, 1, "b", amount=5 * 10**17)
    with dbm.session_scope() as s:
        monitor.run_network(s, "eth-sepolia")
    fake.balances[(addr.lower(), "eth-sepolia:native")] = 5 * 10**17
    keys = []
    for _ in range(consolidation.MAX_REPLANS + 1):
        with dbm.session_scope() as s:
            planned = consolidation.plan(s, "eth-sepolia", force=True)
            again = consolidation.plan(s, "eth-sepolia", force=True)
            assert again == []  # while one is in flight, nothing is planned twice
            for sw in planned:
                keys.append(sw.idempotency_key)
                sw.status, sw.error = "failed", "message expired"  # e.g. never included on chain
    base = keys[0]
    assert keys == [base] + [f"{base}#{n}" for n in range(2, consolidation.MAX_REPLANS + 1)]
    with dbm.session_scope() as s:
        assert s.query(Sweep).count() == consolidation.MAX_REPLANS


def test_only_deposits_of_the_attached_keychain_are_swept(client, ring, monkeypatch, setup_managed):
    from embit import bip32, bip39

    from cryptus import db as dbm
    from cryptus.services import consolidation, infra, monitor
    from cryptus.services.audit import Actor

    fake, _ = setup_managed
    scripted = ScriptedChain()
    monkeypatch.setattr("cryptus.services.monitor.adapter_for", lambda db, key: scripted)
    monkeypatch.setattr("cryptus.services.invoices.adapter_for", lambda db, key: scripted)
    key = client.post("/api/admin/api-keys", json={"name": "t", "mode": "test"}).json()["key"]
    inv = client.post("/api/v1/invoices", json={"amount": "0.5", "currency": "eth-sepolia:native"},
                      headers={"Authorization": f"Bearer {key}"}).json()
    dep_id, addr = inv["payment"]["id"], inv["payment"]["address"]
    scripted.observed = [Observed(dep_id, "0xp", -1, "eth-sepolia:native", 5 * 10**17, None, 1, "b", 9)]
    scripted.state["0xp"] = TxState(True, 9, 1, "b", amount=5 * 10**17)
    with dbm.session_scope() as s:
        monitor.run_network(s, "eth-sepolia")
    fake.balances[(addr.lower(), "eth-sepolia:native")] = 5 * 10**17

    other = bip32.HDKey.from_seed(bip39.mnemonic_to_seed("legal winner thank year wave sausage worth useful legal "
                                                          "winner thank yellow")).derive("m/44h/60h/0h").to_public()
    actor = Actor("user", None, "test")
    with dbm.session_scope() as s:
        managed = __import__("cryptus.models", fromlist=["x"]).NetworkWallet
        original = s.get(managed, "eth-sepolia").keychain_id
        kc = infra.create_keychain(s, actor, family="evm", scope="evm", public_material=other.to_string(),
                                   mode="watch_only", label="other")
        kc.verified_at = dbm.utcnow()
        infra.attach_wallet(s, actor, "eth-sepolia", kc.id)
        infra.set_destination(s, actor, "eth-sepolia", DEST)
        assert consolidation.plan(s, "eth-sepolia", force=True) == []  # the new key cannot sign for old deposits
        infra.attach_wallet(s, actor, "eth-sepolia", original)
        infra.set_destination(s, actor, "eth-sepolia", DEST)
        (sw,) = consolidation.plan(s, "eth-sepolia", force=True)
        assert sw.deposit_ids == [dep_id]


def _paid_deposit(client, monkeypatch, fake, currency, amount_atomic, amount="0.5"):
    from cryptus import db as dbm
    from cryptus.services import monitor

    scripted = ScriptedChain()
    monkeypatch.setattr("cryptus.services.monitor.adapter_for", lambda db, key: scripted)
    monkeypatch.setattr("cryptus.services.invoices.adapter_for", lambda db, key: scripted)
    key = client.post("/api/admin/api-keys", json={"name": "t", "mode": "test"}).json()["key"]
    inv = client.post("/api/v1/invoices", json={"amount": amount, "currency": currency},
                      headers={"Authorization": f"Bearer {key}"}).json()
    dep_id, addr = inv["payment"]["id"], inv["payment"]["address"]
    scripted.observed = [Observed(dep_id, "0xp", -1 if currency.endswith("native") else 0, currency, amount_atomic,
                                  None, 1, "b", 9)]
    scripted.state["0xp"] = TxState(True, 9, 1, "b", amount=amount_atomic)
    with dbm.session_scope() as s:
        monitor.run_network(s, "eth-sepolia")
    fake.balances[(addr.lower(), currency)] = amount_atomic
    return dep_id, addr, scripted


def test_replan_limit_holds_when_each_failure_changes_the_balance(client, ring, monkeypatch, setup_managed):
    from cryptus import db as dbm
    from cryptus.services import consolidation

    fake, _ = setup_managed
    _, addr, _ = _paid_deposit(client, monkeypatch, fake, "eth-sepolia:native", 5 * 10**17)
    planned_total = 0
    for n in range(consolidation.MAX_REPLANS + 3):
        fake.balances[(addr.lower(), "eth-sepolia:native")] = 5 * 10**17 - n * 10**15  # e.g. fees burnt by a bounce
        with dbm.session_scope() as s:
            for sw in consolidation.plan(s, "eth-sepolia", force=True):
                planned_total += 1
                sw.status, sw.error = "failed", "bounced"
    assert planned_total == consolidation.MAX_REPLANS


def test_second_payment_of_the_same_amount_is_swept_again(client, ring, monkeypatch, setup_managed):
    from cryptus import db as dbm
    from cryptus.models import DepositAddress, Payment
    from cryptus.services import consolidation

    fake, _ = setup_managed
    dep_id, addr, _ = _paid_deposit(client, monkeypatch, fake, "eth-sepolia:native", 5 * 10**17)
    with dbm.session_scope() as s:
        (first,) = consolidation.plan(s, "eth-sepolia", force=True)
        first.status = "confirmed"
        s.get(DepositAddress, dep_id).swept_at = dbm.utcnow()
        assert consolidation.plan(s, "eth-sepolia", force=True) == []  # nothing new arrived
        p = s.query(Payment).filter_by(deposit_id=dep_id).one()
        s.add(Payment(**{c.name: getattr(p, c.name) for c in Payment.__table__.columns
                         if c.name not in ("id", "txid", "confirmed_at")},
                      txid="0xsecond", confirmed_at=dbm.utcnow() + __import__("datetime").timedelta(seconds=5)))
    with dbm.session_scope() as s:
        (again,) = consolidation.plan(s, "eth-sepolia", force=True)  # same balance as the first time
        assert again.idempotency_key == first.idempotency_key + "#2"


def test_cancelled_topup_cancels_its_sweep_and_is_not_redone(client, ring, monkeypatch, setup_managed):
    from cryptus import db as dbm
    from cryptus.models import Sweep
    from cryptus.services import consolidation
    from cryptus.services.audit import Actor

    fake, _ = setup_managed
    _paid_deposit(client, monkeypatch, fake, f"eth-sepolia:erc20:{TOKEN}", 25_000_000, amount="25")
    op = Actor("user", "u1", "op")
    with dbm.session_scope() as s:
        planned = consolidation.plan(s, "eth-sepolia", force=True)
        topup = next(p for p in planned if p.kind == "gas_topup")
        sweep = next(p for p in planned if p.kind == "sweep")
        for p in planned:
            consolidation.advance(s, p)
            consolidation.approve(s, p, op)
        consolidation.cancel(s, topup, op)
        consolidation.advance(s, sweep)
        assert sweep.status == "cancelled"
    with dbm.session_scope() as s:
        assert consolidation.plan(s, "eth-sepolia", force=True) == []
        assert s.query(Sweep).count() == 2 and not fake.sent


def test_no_topup_for_a_sweep_that_will_not_be_planned(client, ring, monkeypatch, setup_managed):
    from cryptus import db as dbm
    from cryptus.models import Sweep
    from cryptus.services import consolidation

    fake, _ = setup_managed
    _, addr, _ = _paid_deposit(client, monkeypatch, fake, f"eth-sepolia:erc20:{TOKEN}", 25_000_000, amount="25")
    for n in range(consolidation.MAX_REPLANS + 2):
        fake.balances[(addr.lower(), "eth-sepolia:native")] = n  # every failed attempt leaves a different gas balance
        with dbm.session_scope() as s:
            for sw in consolidation.plan(s, "eth-sepolia", force=True):
                if sw.kind == "sweep":
                    sw.status, sw.error = "failed", "signer refused"
                else:
                    sw.status = "confirmed"
    with dbm.session_scope() as s:
        kinds = [k for (k,) in s.query(Sweep.kind).all()]
        assert kinds.count("sweep") == consolidation.MAX_REPLANS
        assert kinds.count("gas_topup") == consolidation.MAX_REPLANS  # none funded after the sweep gave up
