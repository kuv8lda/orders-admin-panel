"""POST /api/integrations/forget (без сети)."""
from __future__ import annotations

import pytest

import db as db_mod
from tests.test_integrations_orders import _app, _bearer, _payload

ORDERS = "/api/integrations/orders"
FORGET = "/api/integrations/forget"


@pytest.fixture()
def bot():
    app = _app()
    yield app.test_client()
    db_mod.reset_memory_conn()


def _create(bot, key="k-1"):
    r = bot.post(ORDERS, json=_payload(), headers={**_bearer(), "Idempotency-Key": key})
    return r.get_json()["id"], _payload()["telegram_user_id"]


def _row(bot, oid):
    with bot.application.app_context():
        return dict(db_mod.get_db().execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone())


def test_anonymizes(bot):
    oid, uid = _create(bot)
    r = bot.post(FORGET, json={"telegram_user_id": uid}, headers=_bearer())
    assert r.status_code == 200
    row = _row(bot, oid)
    assert row["customer_name"] == "Удалено"
    assert row["customer_phone"] is None
    assert row["telegram_username"] is None


def test_orders_remain(bot):
    oid, uid = _create(bot)
    bot.post(FORGET, json={"telegram_user_id": uid}, headers=_bearer())
    row = _row(bot, oid)
    assert row["id"] == oid and row["amount_cents"] > 0


def test_no_token_401(bot):
    assert bot.post(FORGET, json={"telegram_user_id": 1}).status_code == 401


def test_unknown_user_idempotent(bot):
    for _ in range(2):
        r = bot.post(FORGET, json={"telegram_user_id": 999999}, headers=_bearer())
        assert r.status_code == 200
