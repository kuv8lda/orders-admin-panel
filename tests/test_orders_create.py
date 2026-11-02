"""Order creation: happy path plus every validation rule in the spec."""
from __future__ import annotations

import pytest


def _headers(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf}


def test_create_order_happy_path_201(authed, app):
    client, csrf = authed
    payload = {
        "customer_name": "Sinéad O'Brien",
        "customer_email": "sinead@example.ie",
        "product": "Dashboard",
        "amount": "9.90",
        "note": "VIP customer — follow up personally.",
    }
    resp = client.post("/api/orders", json=payload, headers=_headers(csrf))
    assert resp.status_code == 201, resp.get_json()
    body = resp.get_json()
    assert body["status"] == "pending"
    assert body["customer_name"] == "Sinéad O'Brien"
    assert body["amount_cents"] == 990
    assert body["customer_email"] == "sinead@example.ie"
    assert body["note"] == "VIP customer — follow up personally."
    assert isinstance(body["id"], int) and body["id"] > 0
    with app.app_context():
        conn = pytest.importorskip("db").get_db()
        ev = conn.execute(
            "SELECT from_status, to_status FROM order_events WHERE order_id = ? ORDER BY id",
            (body["id"],),
        ).fetchall()
        assert [tuple(r) for r in ev] == [(None, "pending")]


def test_create_order_amount_decimal_0_1_plus_0_2_is_exactly_30_cents(authed, app):
    """Using Decimal parsing, 0.1 + 0.2 must come out as exactly 30 cents."""
    client, csrf = authed
    total = 0.1 + 0.2
    payload = {
        "customer_name": "Decimal Tester",
        "product": "Scraper",
        "amount": f"{total:.17g}",
    }
    resp = client.post("/api/orders", json=payload, headers=_headers(csrf))
    assert resp.status_code == 201, resp.get_json()
    assert resp.get_json()["amount_cents"] == 30


@pytest.mark.parametrize(
    ("payload_overrides", "expected_field", "expected_msg_part"),
    [
        ({"customer_name": "X"}, "customer_name", "2-80"),
        ({"customer_name": ""}, "customer_name", "2-80"),
        ({"customer_name": "a" * 81}, "customer_name", "2-80"),
        ({"customer_email": "not-an-email"}, "customer_email", "invalid"),
        ({"product": "X"}, "product", "2-80"),
        ({"product": ""}, "product", "2-80"),
        ({"amount": 0}, "amount", "greater than 0"),
        ({"amount": "0"}, "amount", "greater than 0"),
        ({"amount": "-1"}, "amount", "greater than 0"),
        ({"amount": "abc"}, "amount", "number"),
        ({"amount": "0.001"}, "amount", "2 decimal"),
        ({"amount": "1000000.01"}, "amount", "1,000,000"),
        ({"amount": "9999999999"}, "amount", "1,000,000"),
        ({"note": "a" * 501}, "note", "500"),
    ],
)
def test_create_order_validation_failures(
    authed, app, payload_overrides, expected_field, expected_msg_part
):
    client, csrf = authed
    base = {
        "customer_name": "Jane Doe",
        "customer_email": "jane@example.com",
        "product": "Landing kit",
        "amount": "49.00",
    }
    base.update(payload_overrides)
    resp = client.post("/api/orders", json=base, headers=_headers(csrf))
    assert resp.status_code == 422, (payload_overrides, resp.get_json())
    body = resp.get_json()
    assert body["error"] == "validation_failed"
    assert expected_field in body["errors"], (payload_overrides, body["errors"])
    assert expected_msg_part.lower() in body["errors"][expected_field].lower()

def test_create_order_whitespace_in_name_and_product_is_trimmed(authed, app):
    client, csrf = authed
    payload = {
        "customer_name": "   Alice  ",
        "product": "  Landing kit  ",
        "amount": "4.90",
    }
    resp = client.post("/api/orders", json=payload, headers=_headers(csrf))
    assert resp.status_code == 201, resp.get_json()
    body = resp.get_json()
    assert body["customer_name"] == "Alice"
    assert body["product"] == "Landing kit"

def test_create_order_optional_omitted_email_is_none(authed, app):
    client, csrf = authed
    payload = {
        "customer_name": "No Email User",
        "product": "Bot upgrade",
        "amount": "1.80",
    }
    resp = client.post("/api/orders", json=payload, headers=_headers(csrf))
    assert resp.status_code == 201, resp.get_json()
    assert resp.get_json()["customer_email"] is None

def test_create_order_empty_email_is_treated_as_none(authed, app):
    client, csrf = authed
    payload = {
        "customer_name": "Blank Email User",
        "customer_email": "   ",
        "product": "Analytics",
        "amount": "5.40",
    }
    resp = client.post("/api/orders", json=payload, headers=_headers(csrf))
    assert resp.status_code == 201, resp.get_json()
    assert resp.get_json()["customer_email"] is None
