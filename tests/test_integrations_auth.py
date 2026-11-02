"""Токен интеграции: выключена без токена, 401 без/с неверным, 200 с верным."""
from __future__ import annotations

import pytest

import auth as auth_mod
import db as db_mod
from app import create_app
from tests.conftest import TEST_PASSWORD, TEST_PASSWORD_HASH

TOKEN = "t" * 40
URL = "/api/integrations/catalog"


def _make(token):
    auth_mod.reset_rate_limit()
    db_mod.reset_memory_conn()
    return create_app({
        "TESTING": True, "DATABASE_PATH": ":memory:", "SECRET_KEY": "k",
        "ADMIN_USERNAME": "admin", "ADMIN_PASSWORD_HASH": TEST_PASSWORD_HASH,
        "DEMO_MODE": True, "PANEL_API_KEY": token,
    })


@pytest.fixture()
def bot_client():
    app = _make(TOKEN)
    yield app.test_client()
    db_mod.reset_memory_conn()


def _bearer(t):
    return {"Authorization": f"Bearer {t}"}


def test_disabled_without_token_returns_404():
    c = _make(None).test_client()
    assert c.get(URL).status_code == 404
    assert c.get(URL, headers=_bearer(TOKEN)).status_code == 404
    assert c.get("/api/integrations/anything").status_code == 404
    db_mod.reset_memory_conn()


def test_short_token_refuses_to_start():
    with pytest.raises(RuntimeError):
        _make("short")
    db_mod.reset_memory_conn()


def test_no_header_returns_401(bot_client):
    assert bot_client.get(URL).status_code == 401


@pytest.mark.parametrize("header", [
    "Bearer wrong", "Bearer ", "Bearer", TOKEN, "Basic " + TOKEN, "Bearer " + TOKEN[:-1],
    "Bearer " + TOKEN + "x", "Bearer тест",
])
def test_bad_token_returns_401(bot_client, header):
    assert bot_client.get(URL, headers={"Authorization": header}).status_code == 401


def test_valid_token_works(bot_client):
    resp = bot_client.get(URL, headers=_bearer(TOKEN))
    assert resp.status_code == 200
    items = resp.get_json()
    assert items and set(items[0]) == {"id", "name", "price_cents"}


def test_valid_token_unknown_integration_path_is_404(bot_client):
    assert bot_client.get("/api/integrations/nope", headers=_bearer(TOKEN)).status_code == 404


def test_session_does_not_replace_token(bot_client):
    bot_client.post("/api/login", json={"username": "admin", "password": TEST_PASSWORD})
    assert bot_client.get(URL).status_code == 401


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/orders"), ("GET", "/api/stats"), ("GET", "/api/orders/export"),
    ("POST", "/api/orders"), ("PATCH", "/api/orders/1/status"), ("POST", "/api/logout"),
])
def test_token_does_not_open_regular_api(bot_client, method, path):
    resp = bot_client.open(path, method=method, headers=_bearer(TOKEN), json={})
    assert resp.status_code == 401
