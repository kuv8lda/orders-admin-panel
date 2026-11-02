"""Shared menu and non-destructive catalogue upgrade."""
import ast
from pathlib import Path

import pytest

import db


def test_v3_upgrade_preserves_custom_products_prices_and_visibility(app, app_context):
    conn = db.get_db()
    conn.execute("DELETE FROM my_products WHERE id > 9")
    conn.execute("INSERT INTO my_products(id,name,price_cents,is_active,updated_at) VALUES(10,'Авторский хлеб',33300,1,'before')")
    conn.execute("UPDATE my_products SET price_cents=250000,is_active=0 WHERE id=1")
    conn.execute("PRAGMA user_version=3")
    conn.commit()
    orders = [tuple(row) for row in conn.execute("SELECT * FROM orders")]
    db.migrate(conn)
    assert tuple(conn.execute("SELECT name,price_cents FROM my_products WHERE id=10").fetchone()) == ("Авторский хлеб", 33300)
    assert tuple(conn.execute("SELECT price_cents,is_active FROM my_products WHERE id=1").fetchone()) == (250000, 0)
    assert conn.execute("SELECT COUNT(*) FROM my_products WHERE name='Багет'").fetchone()[0] == 1
    assert [tuple(row) for row in conn.execute("SELECT * FROM orders")] == orders
    before = [tuple(row) for row in conn.execute("SELECT * FROM my_products")]
    db.migrate(conn)
    assert [tuple(row) for row in conn.execute("SELECT * FROM my_products")] == before


def test_bot_offline_menu_matches_fresh_panel_catalogue(app, app_context):
    path = Path(__file__).resolve().parents[2] / "khlebny-den-bot/khlebny_bot/catalog.py"
    if not path.exists():
        pytest.skip("Sibling bot checkout is not present")
    tree = ast.parse(path.read_text())
    fallback = ast.literal_eval(next(node.value for node in tree.body
                                    if isinstance(node, ast.AnnAssign) and node.target.id == "CATALOG"))
    actual = [dict(row) for row in db.get_db().execute(
        "SELECT id,name,price_cents FROM my_products WHERE is_active=1 ORDER BY id"
    )]
    assert actual == fallback
    assert len(actual) == 20
    assert 7 not in {item["id"] for item in actual}
    prices = {item["name"]: item["price_cents"] for item in actual}
    assert prices["Набор круассанов (6 шт.)"] < 6 * prices["Круассан с маслом"]
