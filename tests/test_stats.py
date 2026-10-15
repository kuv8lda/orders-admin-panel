"""Statistics computation against the hand-verified ``seed_small`` fixture."""
from __future__ import annotations


def test_stats_kpi_revenue_matches_hand_computation(authed, seed_small):
    client, csrf = authed
    resp = client.get("/api/stats?days=7")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["kpi"]["revenue_cents"] == seed_small["revenue_statuses_total_cents"]
    assert body["kpi"]["orders"] == seed_small["orders_count"]
    assert body["kpi"]["pending"] == seed_small["pending_count"]
    assert body["kpi"]["avg_order_cents"] == (
        seed_small["revenue_statuses_total_cents"] // 3
    )


def test_stats_series_has_exactly_days_entries_zero_filled(authed, seed_small):
    client, csrf = authed
    for days in (7, 30, 90):
        resp = client.get(f"/api/stats?days={days}")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["period_days"] == days
        assert len(body["series"]) == days, f"expected {days} zero-filled days, got {len(body['series'])}"
        dates = [s["date"] for s in body["series"]]
        assert len(set(dates)) == days
        for entry in body["series"]:
            assert isinstance(entry["revenue_cents"], int)
            assert isinstance(entry["orders"], int)
            assert entry["revenue_cents"] >= 0
            assert entry["orders"] >= 0


def test_stats_kpi_prev_window_is_prior_same_length_period(authed, seed_small, app):
    """kpi_prev.orders should be 0 for seed_small (all 5 orders placed in last 3 days)."""
    client, csrf = authed
    import db as db_mod
    from db import utcnow
    from datetime import timedelta

    today = utcnow().date()
    conn = db_mod.get_db()
    window = 3
    cursor_start = today - timedelta(days=window - 1)
    prev_end = cursor_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=window - 1)

    cur_orders = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE DATE(created_at) BETWEEN DATE(?) AND DATE(?)",
        (cursor_start.isoformat(), today.isoformat()),
    ).fetchone()[0]
    prev_orders = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE DATE(created_at) BETWEEN DATE(?) AND DATE(?)",
        (prev_start.isoformat(), prev_end.isoformat()),
    ).fetchone()[0]
    assert cur_orders == 5
    assert prev_orders == 0

    resp = client.get(f"/api/stats?days={window}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["kpi"]["orders"] == 5
    assert body["kpi_prev"]["orders"] == 0
    assert body["kpi_prev"]["revenue_cents"] == 0
    assert body["kpi_prev"]["pending"] == body["kpi"]["pending"]


def test_stats_by_status_counts_match_fixture(authed, seed_small):
    client, csrf = authed
    resp = client.get("/api/stats?days=30")
    assert resp.status_code == 200
    bs = resp.get_json()["by_status"]
    assert bs["pending"] == 1
    assert bs["paid"] == 1
    assert bs["shipped"] == 1
    assert bs["completed"] == 1
    assert bs["cancelled"] == 1


def test_stats_top_products_contains_dashboard_and_web_app(authed, seed_small):
    client, csrf = authed
    resp = client.get("/api/stats?days=30")
    assert resp.status_code == 200
    products = {p["product"] for p in resp.get_json()["top_products"]}
    assert "Web app" in products
    assert "Landing kit" in products
