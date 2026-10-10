"""Order listing: filters, search, date/amount ranges, sort, pagination, SQLi."""
from __future__ import annotations

import pytest


def _headers(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf}


def _seed(authed, app):
    """Create a tiny known corpus via the public API.

    Returns (client, csrf, ids_by_key) where ids_by_key is a dict with keys like
    ``"alice_paid_dashboard"`` -> order id.
    """
    import db as db_mod
    from db import utcnow
    from datetime import timedelta

    client, csrf = authed
    with app.app_context():
        conn = db_mod.get_db()
        conn.execute("DELETE FROM order_events")
        conn.execute("DELETE FROM orders")
        today = utcnow().date()

        def ts(offset):
            d = today - timedelta(days=offset)
            return utcnow().replace(year=d.year, month=d.month, day=d.day, hour=10, minute=0, second=0, microsecond=0).isoformat()

        fixtures = [
            ("Alice Bloggs",  "a@ex.com",  "Dashboard",    990,    "paid",      ts(0),  "alice_paid_dashboard"),
            ("Bob Smith",     "b@ex.com",  "Landing kit",  490,    "pending",   ts(1),  "bob_pending_kit"),
            ("Carol Danvers", "c@ex.com",  "Telegram bot", 320,    "shipped",   ts(2),  "carol_shipped_bot"),
            ("Dave Franco",   "d@ex.com",  "Web app",      1200,   "completed", ts(5),  "dave_completed_web"),
            ("Eve Harrington","e@ex.com",  "Scraper",      210,    "cancelled", ts(10), "eve_cancelled_scraper"),
        ]
        out: dict[str, int] = {}
        for name, email, product, cents, status, created, key in fixtures:
            cur = conn.execute(
                "INSERT INTO orders (customer_name,customer_email,product,amount_cents,status,note,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (name, email, product, cents, status, "", created, created),
            )
            oid = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO order_events (order_id,from_status,to_status,created_at)"
                " VALUES (?,?,?,?)",
                (oid, None, status, created),
            )
            out[key] = oid
        conn.commit()
    return client, csrf, out


def test_filter_status_paid_returns_only_paid(authed, app):
    client, csrf, ids = _seed(authed, app)
    resp = client.get("/api/orders?status=paid")
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["total"] == 1
    assert body["items"][0]["customer_name"] == "Alice Bloggs"
    assert body["items"][0]["id"] == ids["alice_paid_dashboard"]


def test_filter_search_q_by_customer_name_like(authed, app):
    client, csrf, ids = _seed(authed, app)
    resp = client.get("/api/orders?q=Bloggs")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == ids["alice_paid_dashboard"]

def test_filter_search_q_by_hash_id(authed, app):
    """Both ``1001`` and ``#1001`` should find the order whose id is 1."""
    client, csrf, ids = _seed(authed, app)
    first_id = next(iter(ids.values()))
    for query in (f"{first_id + 1000}", f"#{first_id + 1000}"):
        resp = client.get(f"/api/orders?q={query}")
        assert resp.status_code == 200, (query, resp.get_json())
        body = resp.get_json()
        assert body["total"] >= 1
        assert any(i["id"] == first_id for i in body["items"]), (query, body)

def test_filter_date_from_to_range(authed, app):
    import db as db_mod
    from db import utcnow
    from datetime import timedelta

    client, csrf, _ids = _seed(authed, app)
    today = utcnow().date()
    today_str = today.isoformat()
    three_ago = (today - timedelta(days=3)).isoformat()
    resp = client.get(f"/api/orders?date_from={three_ago}&date_to={today_str}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["total"] == 3

def test_filter_min_max_amount(authed, app):
    client, csrf, _ids = _seed(authed, app)
    resp = client.get("/api/orders?min_amount=5.00&max_amount=10.00")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["total"] == 1
    assert body["items"][0]["product"] == "Dashboard"
    assert body["items"][0]["amount_cents"] == 990

def test_sort_unknown_column_returns_422(authed, app):
    client, csrf, _ids = _seed(authed, app)
    resp = client.get("/api/orders?sort=nonexistent_column")
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["error"] == "validation_failed"
    assert "sort" in body["errors"]

def test_pagination_page_2_returns_right_subset(authed, app):
    client, csrf, _ids = _seed(authed, app)
    resp = client.get("/api/orders?per_page=2&page=2&sort=id&dir=asc")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["page"] == 2
    assert body["per_page"] == 2
    assert body["total"] == 5
    assert body["pages"] == 3
    assert len(body["items"]) == 2

def test_sql_injection_payload_in_q_is_harmless(authed, app):
    client, csrf, _ids = _seed(authed, app)
    sqli_payload = "'; DROP TABLE orders;--"
    resp = client.get(f"/api/orders?q={sqli_payload}")
    assert resp.status_code == 200
    import db as db_mod
    with app.app_context():
        conn = db_mod.get_db()
        count_after = conn.execute("SELECT COUNT(*) AS c FROM orders").fetchone()["c"]
        assert count_after == 5

def test_sort_amount_cents_asc_applies(authed, app):
    client, csrf, _ids = _seed(authed, app)
    resp = client.get("/api/orders?sort=amount_cents&dir=asc")
    assert resp.status_code == 200
    amounts = [r["amount_cents"] for r in resp.get_json()["items"]]
    assert amounts == sorted(amounts)
