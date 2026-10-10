"""Database helpers: connection, schema, deterministic seeding, demo reset."""
from __future__ import annotations

import os
import random
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

import flask

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_name  TEXT NOT NULL CHECK (length(customer_name) BETWEEN 2 AND 80),
    customer_email TEXT,
    product        TEXT NOT NULL CHECK (length(product) BETWEEN 2 AND 80),
    amount_cents   INTEGER NOT NULL CHECK (amount_cents > 0),
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','paid','shipped','completed','cancelled')),
    note           TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_status  ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);
CREATE TABLE IF NOT EXISTS order_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    from_status TEXT,
    to_status   TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""

# Demo business: a small home bakery taking orders in Telegram / WhatsApp.
# Prices are stored in kopecks (RUB cents).
PRODUCT_CATALOGUE: list[tuple[str, int]] = [
    ("Medovik cake, 1 kg", 240000),
    ("New York cheesecake", 320000),
    ("Croissant box (6 pcs)", 115000),
    ("Eclairs (8 pcs)", 144000),
    ("Cupcakes (12 pcs)", 216000),
    ("Cherry pie", 110000),
    ("Weekly bread subscription", 189000),
    ("Ginger cookie box", 78000),
    ("Rosemary focaccia", 39000),
]

_CUSTOMER_NAMES: list[str] = [
    "Anna Smirnova", "Dmitry Kuznetsov", "Elena Popova", "Sergey Volkov",
    "Olga Morozova", "Ivan Novikov", "Maria Fedorova", "Alexey Lebedev",
    "Natalia Sokolova", "Pavel Egorov", "Tatiana Kozlova", "Andrey Orlov",
    "Irina Belova", "Mikhail Zaitsev", "Ekaterina Pavlova", "Nikita Semenov",
    "Svetlana Golubeva", "Roman Vinogradov", "Yulia Titova", "Artem Kiselev",
    "Daria Makarova", "Vladimir Frolov", "Polina Zhukova", "Kirill Medvedev",
    "Alina Tikhonova", "Denis Komarov", "Oksana Davydova", "Maxim Gusev",
    "Vera Sorokina", "Igor Belyaev", "Larisa Kalinina", "Stepan Rodionov",
    "Zoya Nikitina", "Gleb Sidorov", "Alisa Ershova", "Timur Safin",
    "Karina Abramova", "Oleg Panov", "Marina Dorofeeva", "Yaroslav Isaev",
    "Valeria Ermakova", "Anton Shishkin", "Lyubov Fomina", "Ruslan Galkin",
    "Diana Kuzmina", "Evgeny Loginov", "Sofia Antonova", "Boris Kovalev",
    "Nastya Shubina", "Leonid Maslov", "Vika Yakovleva", "Fedor Ponomarev",
]


def utcnow() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(UTC)


_MEMORY_CONN: sqlite3.Connection | None = None


def reset_memory_conn() -> None:
    """Drop the in-memory singleton (used by test fixtures between runs)."""
    global _MEMORY_CONN
    if _MEMORY_CONN is not None:
        try:
            _MEMORY_CONN.close()
        except sqlite3.Error:
            pass
        _MEMORY_CONN = None


def _ensure_parent_dir(path: str) -> None:
    if path == ":memory:":
        return
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)


def get_db() -> sqlite3.Connection:
    """Return a per-request connection stored on flask.g.

    In-memory databases are process-wide singletons so tables survive
    across multiple connections, which is required for test suites.
    """
    if "db" not in flask.g:
        db_path = flask.current_app.config["DATABASE_PATH"]
        _ensure_parent_dir(db_path)
        if db_path == ":memory:":
            global _MEMORY_CONN
            if _MEMORY_CONN is None:
                conn = sqlite3.connect(db_path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("PRAGMA journal_mode = WAL")
                _MEMORY_CONN = conn
            flask.g.db = _MEMORY_CONN
        else:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            flask.g.db = conn
    return flask.g.db


def close_db(_exc: BaseException | None = None) -> None:
    """Teardown handler: close the request-scoped connection if any.

    The in-memory singleton is intentionally kept alive past request end.
    """
    conn = flask.g.pop("db", None)
    if conn is not None and conn is not _MEMORY_CONN:
        conn.close()


def init_db(app: flask.Flask) -> None:
    """Create schema and seed demo orders (idempotent)."""
    with app.app_context():
        conn = get_db()
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
        order_count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        if order_count == 0:
            seed_demo_orders(conn, rng_seed=42)
            conn.commit()


def seed_demo_orders(conn: sqlite3.Connection, rng_seed: int = 42) -> None:
    """Insert ~60 orders across 60 days with consistent event history.

    Deterministic when called on an empty table using the same seed.
    """
    rng = random.Random(rng_seed)
    today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    total_orders = 60

    statuses_order = ["pending", "paid", "shipped", "completed", "cancelled"]

    for idx in range(total_orders):
        days_ago = total_orders - 1 - idx  # 59..0 -> spread evenly
        day = today - timedelta(days=days_ago)
        hour = rng.randint(6, 22)
        minute = rng.randint(0, 59)
        created_at = day.replace(hour=hour, minute=minute).isoformat()
        updated_at = created_at

        skew = days_ago / max(1, total_orders - 1)  # 0=newest, 1=oldest
        r = rng.random()
        if skew > 0.65:  # oldest orders: mostly completed/cancelled
            if r < 0.62:
                final = "completed"
            elif r < 0.80:
                final = "cancelled"
            elif r < 0.93:
                final = "shipped"
            else:
                final = "paid"
        elif skew > 0.30:  # middle age: more shipped/paid
            if r < 0.42:
                final = "shipped"
            elif r < 0.70:
                final = "paid"
            elif r < 0.82:
                final = "completed"
            elif r < 0.92:
                final = "pending"
            else:
                final = "cancelled"
        else:  # newest: mostly pending/paid
            if r < 0.48:
                final = "pending"
            elif r < 0.86:
                final = "paid"
            elif r < 0.93:
                final = "shipped"
            elif r < 0.97:
                final = "cancelled"
            else:
                final = "completed"

        product, price = rng.choice(PRODUCT_CATALOGUE)
        customer = rng.choice(_CUSTOMER_NAMES)
        email_local = "".join(c for c in customer.lower() if c.isalpha() or c == " ")
        email_local = email_local.replace(" ", ".")
        email_domain = rng.choice(["example.com", "example.org", "example.net"])
        customer_email = f"{email_local}@{email_domain}"

        cur = conn.execute(
            "INSERT INTO orders (customer_name, customer_email, product, amount_cents,"
            " status, note, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (customer, customer_email, product, price, final, None, created_at, updated_at),
        )
        order_id = cur.lastrowid

        if final == "pending":
            path: Iterable[str | None] = (None, "pending")
        elif final == "paid":
            path = (None, "pending", "paid")
        elif final == "shipped":
            path = (None, "pending", "paid", "shipped")
        elif final == "completed":
            path = (None, "pending", "paid", "shipped", "completed")
        else:  # cancelled: either pending->cancelled or paid->cancelled
            if rng.random() < 0.6:
                path = (None, "pending", "cancelled")
            else:
                path = (None, "pending", "paid", "cancelled")

        event_time = day.replace(hour=hour, minute=minute)
        pair_iter = list(zip(path, tuple(path)[1:]))  # type: ignore[arg-type]
        for i, (from_s, to_s) in enumerate(pair_iter):
            event_dt = event_time + timedelta(minutes=i * rng.randint(30, 240))
            conn.execute(
                "INSERT INTO order_events (order_id, from_status, to_status, created_at)"
                " VALUES (?,?,?,?)",
                (order_id, from_s, to_s, event_dt.isoformat()),
            )


def reset_demo_orders(conn: sqlite3.Connection, rng_seed: int = 42) -> None:
    """Delete all orders + events, then run the deterministic seed."""
    conn.execute("DELETE FROM order_events")
    conn.execute("DELETE FROM orders")
    seed_demo_orders(conn, rng_seed=rng_seed)
    conn.commit()
