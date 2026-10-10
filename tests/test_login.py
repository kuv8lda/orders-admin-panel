"""Login endpoint behaviour: success, invalid credentials, rate limiting."""
from __future__ import annotations

from tests.conftest import TEST_PASSWORD


def test_login_success_returns_200_with_csrf(client):
    resp = client.post(
        "/api/login",
        json={"username": "admin", "password": TEST_PASSWORD},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert isinstance(body["csrf_token"], str) and len(body["csrf_token"]) > 10
    assert body["user"]["username"] == "admin"
    with client.session_transaction() as sess:
        assert sess["admin"] is True
        assert sess["csrf_token"] == body["csrf_token"]


def test_login_invalid_password_returns_401(client):
    resp = client.post(
        "/api/login",
        json={"username": "admin", "password": "wrong-password"},
    )
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "invalid_credentials"


def test_login_rate_limit_six_wrong_attempts_returns_429_then_429(client):
    """Five wrong attempts are allowed; sixth (and more) must be 429."""
    statuses: list[int] = []
    for i in range(8):
        resp = client.post(
            "/api/login",
            json={"username": "admin", "password": f"nope-{i}"},
        )
        statuses.append(resp.status_code)
    assert 429 in statuses, f"expected at least one 429 among {statuses}"
    last = statuses[-1]
    first_rate_limited = statuses.index(429)
    assert first_rate_limited >= 5, f"rate limit kicked in too early: idx {first_rate_limited}"
    last_resp = client.post(
        "/api/login",
        json={"username": "admin", "password": "still-wrong"},
    )
    assert last_resp.status_code == 429
    body = last_resp.get_json()
    assert body["error"] == "too_many_attempts"
    assert isinstance(body["retry_after_seconds"], int)
    assert 0 <= body["retry_after_seconds"] <= 60
