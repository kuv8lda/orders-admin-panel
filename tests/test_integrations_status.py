"""PATCH /api/integrations/orders/<id>/status (без сети)."""
from __future__ import annotations

import pytest

import db as db_mod
from tests.test_integrations_orders import _app, _bearer, _payload

ORDERS = "/api/integrations/orders"


@pytest.fixture()
def bot():
    app = _app()
    yield app.test_client()
    db_mod.reset_memory_conn()


def _create(bot) -> int:
    r = bot.post(ORDERS, json=_payload(), headers={**_bearer(), "Idempotency-Key": "k-1"})
    return r.get_json()["id"]


def _patch(bot, oid, status="paid", headers=None):
    return bot.patch(f"{ORDERS}/{oid}/status", json={"status": status},
                     headers=_bearer() if headers is None else headers)


def test_valid_transition(bot):
    oid = _create(bot)
    r = _patch(bot, oid)
    assert r.status_code == 200
    assert r.get_json()["status"] == "paid"


def test_invalid_transition_409(bot):
    oid = _create(bot)
    r = _patch(bot, oid, "shipped")
    assert r.status_code == 409
    assert r.get_json()["error"] == "invalid_transition"


def test_unknown_order_404(bot):
    assert _patch(bot, 9999).status_code == 404


def test_no_token_401(bot):
    oid = _create(bot)
    assert _patch(bot, oid, headers={}).status_code == 401


@pytest.mark.parametrize("body", [{"status": "nope"}, {}, {"status": 5}])
def test_invalid_status_422(bot, body):
    oid = _create(bot)
    r = bot.patch(f"{ORDERS}/{oid}/status", json=body, headers=_bearer())
    assert r.status_code == 422


def test_event_written_and_updated_at(bot):
    oid = _create(bot)
    with bot.application.app_context():
        before = db_mod.get_db().execute(
            "SELECT updated_at FROM orders WHERE id=?", (oid,)).fetchone()[0]
    _patch(bot, oid)
    with bot.application.app_context():
        conn = db_mod.get_db()
        ev = conn.execute(
            "SELECT from_status, to_status FROM order_events WHERE order_id=?"
            " ORDER BY id DESC LIMIT 1", (oid,)).fetchone()
        after = conn.execute(
            "SELECT updated_at FROM orders WHERE id=?", (oid,)).fetchone()[0]
    assert (ev[0], ev[1]) == ("pending", "paid")
    assert after >= before
