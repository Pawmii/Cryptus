"""TON consolidation for per-order deposit addresses: message construction, external-signature checks, and
confirmation only once the value has actually arrived (every hop of the trace succeeded)."""
import base64

import pytest
from pytoniq_core import Address

from cryptus.chains.base import NetConf
from cryptus.chains.ton import TonAdapter
from cryptus.core import ton_wallet
from cryptus.core.networks import NETWORKS
from cryptus_signer.signing import sign_ton
from tests.conftest import owner_login
from tests.test_direct import FakeToncenter, tx

NET = NETWORKS["ton"]
DEST = ton_wallet.friendly(ton_wallet.wallet_address(bytes(32), 698983191), bounceable=False, testnet=False)
DEST_RAW = Address(DEST).to_str(is_user_friendly=False)
JW = "0:" + "11" * 32           # the deposit's USDT jetton wallet
DEST_JW = "0:" + "22" * 32      # the merchant's USDT jetton wallet


def _sign(ring, intent, role="deposit", index=3, seqno=0):
    return sign_ton(ring, NET, {"role": role, "index": index, "seqno": seqno, "intent": intent}, None).result


def test_sweep_messages(ring):
    native = _sign(ring, {"type": "sweep_native", "destination": DEST})
    src, dests = ton_wallet.parse_wallet_external(ton_wallet.boc_b64_to_cell(native["boc"]))
    assert src == ton_wallet.wallet_address(ring.ton_public_key, 0x40000003).to_str(is_user_friendly=False)
    assert dests == [DEST_RAW]
    jetton = _sign(ring, {"type": "sweep_jetton", "jetton_wallet": JW, "destination": DEST, "amount": 25_000_000,
                          "attach": 50_000_000})
    _, dests = ton_wallet.parse_wallet_external(ton_wallet.boc_b64_to_cell(jetton["boc"]))
    assert dests == [JW, DEST_RAW]  # USDT transfer, then the leftover gas back to the merchant
    assert jetton["msg_hash"] == ton_wallet.boc_b64_to_cell(jetton["boc"]).hash.hex()


# ── confirmation follows the trace ────────────────────────────────────────

def _h(n: int) -> str:
    return base64.b64encode(bytes([n]) * 32).decode()


def _tx(account, in_hash, *, outs=(), value=0, ok=True, bounced_back=False, src="0:" + "33" * 32):
    t = tx(1, 1, account=account, value=value, src=src)
    t["hash"] = _h(200 + len(in_hash))
    t["in_msg"]["hash"] = in_hash
    t["out_msgs"] = [{"hash": h, "destination": d, "value": str(v), "opcode": op} for h, d, v, op in outs]
    if not ok:
        t["description"].update(aborted=True, compute_ph={"success": False})
    if bounced_back:
        t["description"]["bounce"] = {"type": "ok"}
    return t


class TraceFake(FakeToncenter):
    def __init__(self, by_msg):
        super().__init__()
        self.by_msg = by_msg

    def __call__(self, request):
        if request.url.path == "/api/v3/transactionsByMessage":
            t = self.by_msg.get(request.url.params["msg_hash"])
            import httpx
            return httpx.Response(200, json={"transactions": [t] if t else []})
        return super().__call__(request)


SRC = "0:" + "44" * 32


def outcome(wallet_tx, by_msg, intent, destination=DEST):
    ad = TonAdapter(NET, NetConf("ton", "toncenter", "https://x", {}, {}, {}, 1),
                    http=__import__("httpx").Client(transport=__import__("httpx").MockTransport(TraceFake(by_msg))))
    return ad.sweep_outcome(wallet_tx, intent, destination)


def test_native_sweep_confirmed_only_when_credited():
    intent = {"type": "sweep_native", "destination": DEST}
    w = _tx(SRC, _h(1), outs=[(_h(2), DEST_RAW, 2_499_000_000, None)])
    assert outcome(w, {}, intent) == ("pending", None)
    assert outcome(w, {_h(2): _tx(DEST_RAW, _h(2), value=2_499_000_000, src=SRC)}, intent) == ("ok", 2_499_000_000)
    bounced = _tx(DEST_RAW, _h(2), value=2_499_000_000, src=SRC, bounced_back=True)
    assert outcome(w, {_h(2): bounced}, intent)[0] == "failed"
    assert outcome(_tx(SRC, _h(1), ok=False), {}, intent)[0] == "failed"           # wallet rejected / aborted
    assert outcome(_tx(SRC, _h(1), outs=[(_h(2), "0:" + "55" * 32, 1, None)]), {}, intent)[0] == "failed"


def test_jetton_sweep_follows_both_jetton_wallets():
    intent = {"type": "sweep_jetton", "jetton_wallet": JW, "destination": DEST, "amount": 25_000_000}
    w = _tx(SRC, _h(1), outs=[(_h(2), JW, 50_000_000, "0x0f8a7ea5"), (_h(3), DEST_RAW, 40_000_000, None)])
    hop1 = _tx(JW, _h(2), outs=[(_h(4), DEST_JW, 49_000_000, "0x178d4519")], src=SRC)
    hop2 = _tx(DEST_JW, _h(4), src=JW)
    assert outcome(w, {}, intent)[0] == "pending"
    assert outcome(w, {_h(2): hop1}, intent)[0] == "pending"
    assert outcome(w, {_h(2): hop1, _h(4): hop2}, intent)[0] == "ok"
    assert outcome(w, {_h(2): _tx(JW, _h(2), ok=False, src=SRC)}, intent)[0] == "failed"   # e.g. not enough gas
    assert outcome(w, {_h(2): hop1, _h(4): _tx(DEST_JW, _h(4), ok=False, src=JW)}, intent)[0] == "failed"


# ── watch-only: an externally signed message must match the plan ─────────

@pytest.fixture()
def external_sweep(client, ring, monkeypatch):
    from cryptus import db as dbm
    from cryptus.models import Sweep

    owner_login(client)
    sent = []

    class Broadcast:
        def broadcast(self, raw):
            sent.append(raw)
            return "ab" * 32

    monkeypatch.setattr("cryptus.services.consolidation.adapter_for", lambda db, key: Broadcast())
    src = ton_wallet.friendly(ton_wallet.wallet_address(ring.ton_public_key, 0x40000003), bounceable=False,
                              testnet=False)
    with dbm.session_scope() as s:
        sw = Sweep(network="ton", asset_id="ton:native", kind="sweep", deposit_ids=[], source_address=src,
                   destination=DEST, amount="1000000000", status="awaiting_external_signature",
                   idempotency_key="t1", artifact={"role": "deposit", "index": 3,
                                                   "intent": {"type": "sweep_native", "destination": DEST}})
        s.add(sw)
        s.flush()
        return sw.id, sent


def test_external_ton_signature_must_match_plan(client, ring, external_sweep):
    from cryptus import db as dbm
    from cryptus.models import Sweep
    from cryptus.services import consolidation
    from cryptus.services.audit import Actor

    sid, sent = external_sweep
    elsewhere = ton_wallet.friendly(ton_wallet.wallet_address(bytes([7]) * 32, 698983191), bounceable=False,
                                    testnet=False)
    other = _sign(ring, {"type": "sweep_native", "destination": elsewhere})
    wrong_wallet = _sign(ring, {"type": "sweep_native", "destination": DEST}, index=4)
    good = _sign(ring, {"type": "sweep_native", "destination": DEST})
    with dbm.session_scope() as s:
        sw = s.get(Sweep, sid)
        for bad, why in ((other["boc"], "not part of the planned sweep"), (wrong_wallet["boc"], "different wallet"),
                         ("not-a-boc", "could not decode")):
            with pytest.raises(consolidation.SweepError, match=why):
                consolidation.submit_external(s, sw, bad, Actor("user", "u", "op"))
        assert not sent
        consolidation.submit_external(s, sw, good["boc"], Actor("user", "u", "op"))
        assert sw.status == "broadcast" and sw.artifact["msg_hash"] == good["msg_hash"] and sent == [good["boc"]]


def test_only_owner_can_waive_approvals(client):
    owner_login(client)
    r = client.post("/api/admin/treasury/approval", json={"require_approval": False})
    assert r.status_code == 200 and r.json()["require_approval"] is False
    assert client.get("/api/admin/sweeps").json()["treasury"]["require_approval"] is False
    pw = "another correct horse battery staple"
    tmp = client.post("/api/admin/users", json={"email": "adm@example.com", "name": "Adm", "role": "admin",
                                                "password": pw})
    assert tmp.status_code == 200, tmp.text
    client.post("/api/auth/logout")
    client.headers.pop("X-CSRF-Token", None)
    r = client.post("/api/auth/login", json={"email": "adm@example.com", "password": pw})
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert client.post("/api/admin/treasury/approval", json={"require_approval": True}).status_code == 403
