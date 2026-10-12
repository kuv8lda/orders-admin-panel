"""Демо-данные: русский каталог, цены в копейках, латинские e-mail."""
from __future__ import annotations

import re

import db as db_mod


def test_catalogue_has_nine_russian_items_with_stable_prices():
    prices = dict(db_mod.PRODUCT_CATALOGUE)
    assert len(prices) == 9
    assert prices["Медовик, 1 кг"] == 240000
    assert prices["Набор круассанов (6 шт.)"] == 115000
    assert prices["Фокачча с розмарином"] == 39000
    assert all(re.search(r"[А-Яа-я]", name) for name in prices)


def test_seeded_orders_use_catalogue_and_cyrillic_names(app, app_context):
    rows = db_mod.get_db().execute(
        "SELECT customer_name, customer_email, product, amount_cents FROM orders"
    ).fetchall()
    assert len(rows) == 60
    catalogue = dict(db_mod.PRODUCT_CATALOGUE)
    for r in rows:
        assert catalogue[r["product"]] == r["amount_cents"]
        assert re.search(r"[А-Яа-я]", r["customer_name"])
        assert re.fullmatch(r"[a-z.]+@example\.(com|org|net)", r["customer_email"])


def test_seeded_timestamps_are_never_in_the_future(app, app_context):
    from datetime import datetime

    now = db_mod.utcnow()
    conn = db_mod.get_db()
    stamps = [r[0] for r in conn.execute("SELECT created_at FROM orders")]
    stamps += [r[0] for r in conn.execute("SELECT created_at FROM order_events")]
    assert stamps
    assert all(datetime.fromisoformat(s) <= now for s in stamps)
