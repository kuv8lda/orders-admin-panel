"""Миграция схемы этапа 2.2: старая БД, идемпотентность, ограничения."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

import db as db_mod

_OLD_SCHEMA = """
CREATE TABLE orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_name TEXT NOT NULL, customer_email TEXT,
    product TEXT NOT NULL, amount_cents INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', note TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE order_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    from_status TEXT, to_status TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


@pytest.fixture()
def workdir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


def _old_db(tmp_path):
    path = tmp_path / "old.db"
    c = sqlite3.connect(path)
    c.executescript(_OLD_SCHEMA)
    c.execute(
        "INSERT INTO orders (customer_name,product,amount_cents,status,created_at,updated_at)"
        " VALUES ('Анна Смирнова','Медовик',240000,'paid','2025-01-01T00:00:00','2025-01-01T00:00:00')"
    )
    c.execute(
        "INSERT INTO order_events (order_id,from_status,to_status,created_at)"
        " VALUES (1,NULL,'pending','2025-01-01T00:00:00')"
    )
    c.commit()
    c.close()
    return path


def _dump(conn):
    return (
        conn.execute("SELECT * FROM orders ORDER BY id").fetchall(),
        conn.execute("SELECT * FROM order_events ORDER BY id").fetchall(),
    )


def test_migrate_old_db_keeps_data(workdir):
    path = _old_db(workdir)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_mod.migrate(conn)
    row = conn.execute("SELECT * FROM orders WHERE id=1").fetchone()
    assert row["customer_name"] == "Анна Смирнова"
    assert row["product"] == "Медовик"
    assert row["amount_cents"] == 240000
    assert row["status"] == "paid"
    assert row["source"] == "panel"
    for col in ("customer_phone", "telegram_user_id", "telegram_username",
                "delivery_date", "idempotency_key"):
        assert row[col] is None
    assert conn.execute("SELECT COUNT(*) FROM order_events").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM order_items").fetchone()[0] == 0
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db_mod.SCHEMA_VERSION


def test_migrate_is_idempotent(workdir):
    conn = sqlite3.connect(_old_db(workdir))
    conn.row_factory = sqlite3.Row
    db_mod.migrate(conn)
    before = _dump(conn)
    schema_before = conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    db_mod.migrate(conn)
    db_mod.migrate(conn)
    assert _dump(conn) == before
    assert conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall() == schema_before
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db_mod.SCHEMA_VERSION


def test_constraints(workdir):
    conn = sqlite3.connect(_old_db(workdir))
    conn.execute("PRAGMA foreign_keys = ON")
    db_mod.migrate(conn)
    ins = ("INSERT INTO orders (customer_name,product,amount_cents,created_at,updated_at,"
           "source,idempotency_key) VALUES ('Иван','Торт',100,'t','t',?,?)")
    conn.execute(ins, ("telegram", "k1"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(ins, ("telegram", "k1"))  # UNIQUE
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(ins, ("web", "k2"))  # CHECK source
    conn.execute(ins, ("panel", None))
    conn.execute(ins, ("panel", None))  # NULL-ключи не конфликтуют


def test_items_cascade_on_delete(workdir):
    conn = sqlite3.connect(_old_db(workdir))
    conn.execute("PRAGMA foreign_keys = ON")
    db_mod.migrate(conn)
    conn.execute(
        "INSERT INTO order_items (order_id,product,unit_price_cents,quantity)"
        " VALUES (1,'Медовик',240000,2)"
    )
    conn.execute("DELETE FROM orders WHERE id=1")
    assert conn.execute("SELECT COUNT(*) FROM order_items").fetchone()[0] == 0


def test_fresh_db_has_new_schema(app, app_context):
    conn = db_mod.get_db()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db_mod.SCHEMA_VERSION
    cols = {r[1] for r in conn.execute("PRAGMA table_info(orders)")}
    assert {"source", "idempotency_key", "delivery_date"} <= cols
    assert conn.execute("SELECT COUNT(*) FROM orders WHERE source='panel'").fetchone()[0] > 0
