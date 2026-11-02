import os
import tempfile
from pathlib import Path

import pytest

_tmp = Path(tempfile.mkdtemp(prefix="cryptus-test-"))
os.environ.update({
    "CRYPTUS_ENV": "test",
    "CRYPTUS_DATA_DIR": str(_tmp),
    "CRYPTUS_DATABASE_URL": f"sqlite:///{_tmp / 'test.db'}",
    "CRYPTUS_SECRET_KEY": "test-secret-key-0123456789-abcdefghijklmnop",
    "CRYPTUS_SETUP_TOKEN": "setup-token-for-tests",
    "CRYPTUS_COOKIE_SECURE": "false",
    "CRYPTUS_PUBLIC_URL": "http://testserver",
})

ABANDON = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"


@pytest.fixture(scope="session")
def ton_mnemonic():
    from pytoniq_core.crypto.keys import mnemonic_new
    return mnemonic_new(24)


@pytest.fixture(scope="session")
def ring(ton_mnemonic):
    from cryptus_signer.keys import KeyRing
    from cryptus_signer.keystore import Secrets
    return KeyRing(Secrets(ABANDON, "", ton_mnemonic))


@pytest.fixture()
def db_url(tmp_path):
    return f"sqlite:///{tmp_path / 'cryptus.db'}"


@pytest.fixture()
def client(db_url, monkeypatch):
    from fastapi.testclient import TestClient

    from cryptus import db
    from cryptus.migrate import upgrade

    upgrade(db_url)
    db.configure(db_url)
    monkeypatch.setattr("cryptus.config.get_settings", __import__("cryptus.config", fromlist=["x"]).get_settings)
    from cryptus.app import create_app
    with TestClient(create_app()) as c:
        yield c


def owner_login(client):
    r = client.post("/api/setup/initialize", json={
        "token": "setup-token-for-tests",
        "owner": {"email": "owner@example.com", "name": "Owner", "password": "correct horse battery staple"},
        "business": {"name": "Acme Goods", "default_currency": "USD"},
    })
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return r.json()
