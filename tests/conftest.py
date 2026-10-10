"""Shared pytest fixtures for the Orders Admin Panel test suite."""
from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from typing import Any

import pytest
import werkzeug.security

import auth as auth_mod
import db as db_mod
from app import create_app


TEST_PASSWORD = "password123"
TEST_PASSWORD_HASH = werkzeug.security.generate_password_hash(TEST_PASSWORD)


@pytest.fixture()
def app_context(app):
    with app.app_context():
        yield

@pytest.fixture()
def app() -> Iterator[Any]:
    """Flask app wired to a throwaway :memory: SQLite database.

    ``SECRET_KEY`` and ``ADMIN_PASSWORD_HASH`` are pinned so tests are
    deterministic and independent of environment / on-disk state.
    """
    auth_mod.reset_rate_limit()
    db_mod.reset_memory_conn()
    config = {
        "TESTING": True,
        "DATABASE_PATH": ":memory:",
        "SECRET_KEY": "pytest-fixed-secret-key",
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD_HASH": TEST_PASSWORD_HASH,
        "DEBUG": False,
    }
    application = create_app(config)
    yield application
    auth_mod.reset_rate_limit()
    db_mod.reset_memory_conn()


@pytest.fixture()
def client(app):
    """Flask test client for the in-memory app."""
    return app.test_client()


@pytest.fixture()
def authed(client) -> tuple[Any, str]:
    """Client logged in via ``/api/login`` plus the negotiated CSRF token."""
    resp = client.post(
        "/api/login",
        json={"username": "admin", "password": TEST_PASSWORD},
    )
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    csrf = body["csrf_token"]
    return client, csrf


@pytest.fixture()
def seed_small(app, app_context):
    """Create a tiny, hand-verifiable order fixture.

    Returns a dict with the exact cents / status / dates so tests can compute
    expected revenue manually and compare against ``/api/stats``.
    """
    from db import utcnow
    from datetime import timedelta

    today = utcnow().date()
    conn = db_mod.get_db()
    conn.execute("DELETE FROM order_events")
    conn.execute("DELETE FROM orders")

    fixtures = [
        (today - timedelta(days=2), "pending", "Dashboard", 990_00),
        (today - timedelta(days=2), "paid", "Landing kit", 490_00),
        (today - timedelta(days=1), "shipped", "Telegram bot", 320_00),
        (today, "completed", "Web app", 1200_00),
        (today, "cancelled", "Scraper", 210_00),
    ]
    created_ids: list[int] = []
    for i, (d, status, product, cents) in enumerate(fixtures):
        ts = utcnow().replace(year=d.year, month=d.month, day=d.day).isoformat()
        cur = conn.execute(
            "INSERT INTO orders (customer_name,customer_email,product,amount_cents,status,note,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (f"Cust{i}", f"c{i}@example.com", product, cents, status, "", ts, ts),
        )
        oid = int(cur.lastrowid)
        chain: list[tuple[Any, str]] = [(None, "pending")]
        if status == "paid":
            chain.append(("pending", "paid"))
        elif status == "shipped":
            chain.append(("pending", "paid"))
            chain.append(("paid", "shipped"))
        elif status == "completed":
            chain.append(("pending", "paid"))
            chain.append(("paid", "shipped"))
            chain.append(("shipped", "completed"))
        elif status == "cancelled":
            chain.append(("pending", "cancelled"))
        for frm, to in chain:
            conn.execute(
                "INSERT INTO order_events (order_id,from_status,to_status,created_at)"
                " VALUES (?,?,?,?)",
                (oid, frm, to, ts),
            )
        created_ids.append(oid)
    conn.commit()
    return {
        "ids": created_ids,
        "revenue_statuses_total_cents": 490_00 + 320_00 + 1200_00,
        "orders_count": 5,
        "pending_count": 1,
    }
