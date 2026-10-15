"""Этап 2.4: источник, позиции, ссылка на @username, CSV, фильтр по источнику."""
from __future__ import annotations

import csv
import io

import pytest

import db as db_mod

XSS = "<img src=x onerror=alert(1)>"


def _insert(conn, name, source="telegram", username="baker_fan", phone="+79990001122",
            delivery="2026-10-20", items=(("Медовик, 1 кг", 240000, 2),)):
    ts = db_mod.utcnow().isoformat()
    cur = conn.execute(
        "INSERT INTO orders (customer_name, customer_email, product, amount_cents, status,"
        " note, created_at, updated_at, source, customer_phone, telegram_user_id,"
        " telegram_username, delivery_date) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (name, None, "Медовик, 1 кг", 480000, "pending", None, ts, ts,
         source, phone, 123, username, delivery),
    )
    oid = cur.lastrowid
    for product, price, qty in items:
        conn.execute(
            "INSERT INTO order_items (order_id, product, unit_price_cents, quantity)"
            " VALUES (?,?,?,?)", (oid, product, price, qty))
    conn.execute(
        "INSERT INTO order_events (order_id, from_status, to_status, created_at)"
        " VALUES (?,?,?,?)", (oid, None, "pending", ts))
    conn.commit()
    return oid


@pytest.fixture()
def tg(authed, app):
    client, csrf = authed
    with app.app_context():
        conn = db_mod.get_db()
        conn.execute("DELETE FROM order_events")
        conn.execute("DELETE FROM orders")
        conn.commit()
        ids = {
            "tg": _insert(conn, "Анна Смирнова"),
            "xss": _insert(conn, XSS, username="evil<script>"),
            "panel": _insert(conn, "Панельный Клиент", source="panel",
                             username=None, phone=None, delivery=None, items=()),
            "formula": _insert(conn, "=HYPERLINK(1)", phone="+1", delivery="-1",
                               items=(("=cmd|' /C calc'!A0", 100, 1),)),
        }
    return client, ids


def test_filter_by_source(tg):
    client, _ = tg
    r = client.get("/api/orders?source=telegram&per_page=100").get_json()
    assert r["total"] == 3 and {i["source"] for i in r["items"]} == {"telegram"}
    r = client.get("/api/orders?source=panel").get_json()
    assert r["total"] == 1 and r["items"][0]["source"] == "panel"
    assert client.get("/api/orders").get_json()["total"] == 4


@pytest.mark.parametrize("bad", ["email", "TELEGRAM", "' OR 1=1 --"])
def test_filter_bad_source_422(tg, bad):
    client, _ = tg
    assert client.get("/api/orders", query_string={"source": bad}).status_code == 422


def test_detail_has_items_phone_delivery_link(tg):
    client, ids = tg
    d = client.get(f"/api/orders/{ids['tg']}").get_json()
    assert d["items"] == [{"product": "Медовик, 1 кг", "unit_price_cents": 240000, "quantity": 2}]
    assert d["customer_phone"] == "+79990001122"
    assert d["delivery_date"] == "2026-10-20"
    assert d["telegram_url"] == "https://t.me/baker_fan"


def test_detail_panel_order_has_no_link_and_empty_items(tg):
    client, ids = tg
    d = client.get(f"/api/orders/{ids['panel']}").get_json()
    assert d["items"] == [] and d["telegram_url"] is None and d["source"] == "panel"


def test_list_exposes_source_and_safe_link(tg):
    client, ids = tg
    items = {i["id"]: i for i in client.get("/api/orders?per_page=100").get_json()["items"]}
    assert items[ids["tg"]]["source"] == "telegram"
    assert items[ids["tg"]]["telegram_url"] == "https://t.me/baker_fan"
    assert items[ids["xss"]]["telegram_url"] is None  # невалидный username -> без ссылки


def test_xss_name_returned_as_json_data_not_html(tg):
    client, ids = tg
    resp = client.get(f"/api/orders/{ids['xss']}")
    assert resp.mimetype == "application/json"
    assert resp.get_json()["customer_name"] == XSS
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert "script-src 'self'" in resp.headers["Content-Security-Policy"]


def test_csv_new_columns(tg):
    client, ids = tg
    resp = client.get("/api/orders/export.csv?source=telegram")
    rows = list(csv.reader(io.StringIO(resp.get_data(as_text=True).lstrip("\ufeff"))))
    assert rows[0][-4:] == ["source", "customer_phone", "delivery_date", "items"]
    assert len(rows) == 4
    by_id = {r[0]: r for r in rows[1:]}
    r = by_id[f"#{ids['tg'] + 1000}"]
    # телефон +7XXXXXXXXXX выводится как +7 (XXX) XXX XX-XX, перед ним табуляция,
    # чтобы Excel не принял «+» за начало формулы
    assert r[-4:] == ["telegram", "\t+7 (999) 000 11-22", "2026-10-20", "Медовик, 1 кг x2"]


def test_csv_formula_injection_new_columns(tg):
    client, ids = tg
    text = client.get("/api/orders/export.csv").get_data(as_text=True).lstrip("\ufeff")
    rows = {r[0]: r for r in csv.reader(io.StringIO(text))}
    r = rows[f"#{ids['formula'] + 1000}"]
    assert r[1].startswith("'=")      # имя
    assert r[-3] == "'+1"             # phone
    assert r[-2] == "'-1"             # delivery_date
    assert r[-1].startswith("'=")     # items
    for cell in r:
        assert not cell.startswith(("=", "+", "-", "@"))


def test_csv_source_filter_invalid_422(tg):
    client, _ = tg
    assert client.get("/api/orders/export.csv?source=x").status_code == 422
