"""Full 5×5 status-transition matrix.

Valid transitions (return 200 and record an event):
    pending  → {paid, cancelled}
    paid     → {shipped, cancelled}
    shipped  → {completed}
    completed → (none)
    cancelled → (none)

Everything else returns 409 ``invalid_transition``.
Terminal states additionally confirm no mutations occur.
"""
from __future__ import annotations

import pytest

TRANSITIONS = {
    "pending": ("paid", "cancelled"),
    "paid": ("shipped", "cancelled"),
    "shipped": ("completed",),
    "completed": (),
    "cancelled": (),
}
ALL_STATUSES = ("pending", "paid", "shipped", "completed", "cancelled")


def _headers(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf}


def _make_order(app, status: str) -> int:
    """Insert an order directly at a given status with a consistent event chain."""
    import db as db_mod
    from db import utcnow

    with app.app_context():
        conn = db_mod.get_db()
        ts = utcnow().isoformat()
        cur = conn.execute(
            "INSERT INTO orders (customer_name,customer_email,product,amount_cents,status,note,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (f"User-{status}", f"u@{status}.tld", "Landing kit", 490_00, status, "", ts, ts),
        )
        oid = int(cur.lastrowid)
        chain: list[tuple[object, str]] = [(None, "pending")]
        if status in {"paid", "shipped", "completed"}:
            chain.append(("pending", "paid"))
        if status in {"shipped", "completed"}:
            chain.append(("paid", "shipped"))
        if status == "completed":
            chain.append(("paid", "shipped"))
            chain.append(("shipped", "completed"))
        if status == "cancelled":
            chain.append(("pending", "cancelled"))
        for frm, to in chain:
            conn.execute(
                "INSERT INTO order_events (order_id,from_status,to_status,created_at)"
                " VALUES (?,?,?,?)",
                (oid, frm, to, ts),
            )
        conn.commit()
        return oid


@pytest.mark.parametrize("current", ALL_STATUSES)
@pytest.mark.parametrize("target", ALL_STATUSES)
def test_status_transition_matrix(authed, app, current: str, target: str):
    client, csrf = authed
    oid = _make_order(app, current)
    resp = client.patch(
        f"/api/orders/{oid}/status",
        json={"status": target},
        headers=_headers(csrf),
    )
    valid = target in TRANSITIONS.get(current, ())
    if valid:
        assert resp.status_code == 200, (current, target, resp.get_json())
        assert resp.get_json()["status"] == target
        import db as db_mod
        with app.app_context():
            conn = db_mod.get_db()
            last_ev = conn.execute(
                "SELECT from_status, to_status FROM order_events WHERE order_id = ?"
                " ORDER BY id DESC LIMIT 1",
                (oid,),
            ).fetchone()
            assert (last_ev["from_status"], last_ev["to_status"]) == (current, target)
    else:
        assert resp.status_code == 409, (current, target, resp.status_code, resp.get_json())
        body = resp.get_json()
        assert body["error"] == "invalid_transition"
        assert body["from"] == current
        assert body["to"] == target


def test_terminal_completed_status_cannot_transition_anywhere(authed, app):
    client, csrf = authed
    oid = _make_order(app, "completed")
    for target in ALL_STATUSES:
        resp = client.patch(
            f"/api/orders/{oid}/status",
            json={"status": target},
            headers=_headers(csrf),
        )
        assert resp.status_code == 409, target


def test_terminal_cancelled_status_cannot_transition_anywhere(authed, app):
    client, csrf = authed
    oid = _make_order(app, "cancelled")
    for target in ALL_STATUSES:
        resp = client.patch(
            f"/api/orders/{oid}/status",
            json={"status": target},
            headers=_headers(csrf),
        )
        assert resp.status_code == 409, target


def test_unknown_status_returns_422_not_409(authed, app):
    client, csrf = authed
    oid = _make_order(app, "pending")
    resp = client.patch(
        f"/api/orders/{oid}/status",
        json={"status": "nonsense"},
        headers=_headers(csrf),
    )
    assert resp.status_code == 422
    assert "status" in resp.get_json().get("errors", {})
