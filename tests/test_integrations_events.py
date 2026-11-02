"""Блок 1, этап B: GET /api/integrations/events."""
from __future__ import annotations

import pytest

import db as db_mod
from tests.test_integrations_orders import _app, _bearer

URL = "/api/integrations/events"


@pytest.fixture()
def bot():
    app = _app()
    yield app, app.test_client()
    db_mod.reset_memory_conn()


def _order(app, source="telegram", events=("pending",)):
    with app.app_context():
        conn = db_mod.get_db()
        now = "2026-10-16T10:00:00+00:00"
        cur = conn.execute(
            "INSERT INTO orders (customer_name, product, amount_cents, status, source,"
            " created_at, updated_at) VALUES ('Тест', 'Медовик', 100, 'pending', ?, ?, ?)",
            (source, now, now),
        )
        oid = cur.lastrowid
        prev = None
        for st in events:
            conn.execute(
                "INSERT INTO order_events (order_id, from_status, to_status, created_at)"
                " VALUES (?, ?, ?, ?)", (oid, prev, st, now))
            prev = st
        conn.commit()
        return oid


def test_empty_list(bot):
    _, c = bot
    r = c.get(URL, headers=_bearer())
    assert r.status_code == 200 and r.get_json() == []


def test_cursor(bot):
    app, c = bot
    oid = _order(app, events=("pending", "paid", "shipped"))
    all_ev = c.get(URL, headers=_bearer()).get_json()
    assert [e["status"] for e in all_ev] == ["pending", "paid", "shipped"]
    assert set(all_ev[0]) == {"id", "order_id", "status", "created_at"}
    assert all_ev[0]["order_id"] == oid
    rest = c.get(f"{URL}?since_id={all_ev[0]['id']}", headers=_bearer()).get_json()
    assert [e["id"] for e in rest] == [all_ev[1]["id"], all_ev[2]["id"]]


def test_only_telegram_orders(bot):
    app, c = bot
    _order(app, source="panel")
    tg = _order(app, source="telegram")
    ev = c.get(URL, headers=_bearer()).get_json()
    assert [e["order_id"] for e in ev] == [tg]


def test_no_token_401(bot):
    _, c = bot
    assert c.get(URL).status_code == 401


def test_limit(bot):
    app, c = bot
    _order(app, events=("pending", "paid", "shipped", "completed"))
    assert len(c.get(f"{URL}?limit=2", headers=_bearer()).get_json()) == 2
    assert len(c.get(f"{URL}?limit=1000", headers=_bearer()).get_json()) == 4


@pytest.mark.parametrize("q", ["limit=0", "limit=x", "since_id=-1", "since_id=abc"])
def test_bad_params_422(bot, q):
    _, c = bot
    assert c.get(f"{URL}?{q}", headers=_bearer()).status_code == 422
