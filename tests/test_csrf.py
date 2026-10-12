"""CSRF header enforcement for mutating API calls."""
from __future__ import annotations

from tests.conftest import TEST_PASSWORD


def _create_order_payload():
    return {
        "customer_name": "Alice Smith",
        "customer_email": "alice@example.com",
        "product": "Landing kit",
        "amount": "4.90",
    }


def test_login_is_csrf_exempt(client):
    """POST /api/login has no CSRF requirement – user is not authed yet."""
    resp = client.post(
        "/api/login",
        json={"username": "admin", "password": TEST_PASSWORD},
        headers={"X-CSRF-Token": ""},
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_post_orders_without_csrf_returns_403(client):
    client.post("/api/login", json={"username": "admin", "password": TEST_PASSWORD})
    resp = client.post("/api/orders", json=_create_order_payload())
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "csrf"


def test_patch_order_status_without_csrf_returns_403(authed):
    client, csrf = authed
    created = client.post(
        "/api/orders",
        json=_create_order_payload(),
        headers={"X-CSRF-Token": csrf},
    )
    oid = created.get_json()["id"]
    resp = client.patch(f"/api/orders/{oid}/status", json={"status": "paid"})
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "csrf"


def test_post_logout_without_csrf_returns_403(authed):
    client, _ = authed
    resp = client.post("/api/logout")
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "csrf"


def test_mutating_request_with_wrong_csrf_value_returns_403(authed):
    client, _csrf = authed
    resp = client.post(
        "/api/orders",
        json=_create_order_payload(),
        headers={"X-CSRF-Token": "completely-wrong-token"},
    )
    assert resp.status_code == 403


def test_integration_token_needs_no_csrf_but_session_post_still_does(authed):
    """Токен освобождает только /api/integrations/*; обычные POST по-прежнему требуют CSRF."""
    from tests.test_integrations_auth import TOKEN, _make
    c = _make(TOKEN).test_client()
    assert c.get("/api/integrations/catalog",
                 headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    client, _ = authed
    assert client.post("/api/orders", json=_create_order_payload()).status_code == 403
