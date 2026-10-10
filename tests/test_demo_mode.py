"""DEMO_MODE=1 / DEMO_MODE=0, /healthz, HSTS и TRUST_PROXY."""
from __future__ import annotations

import os
from contextlib import contextmanager

import pytest

import auth as auth_mod
import db as db_mod
from app import create_app

STRONG_PASSWORD = "correct-horse-battery"


@contextmanager
def _env(**values: str | None):
    """Временно выставить переменные окружения (None = удалить)."""
    saved = {k: os.environ.get(k) for k in values}
    try:
        for k, v in values.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _prod_app(password: str = STRONG_PASSWORD, **extra):
    auth_mod.reset_rate_limit()
    db_mod.reset_memory_conn()
    config = {
        "TESTING": True,
        "DATABASE_PATH": ":memory:",
        "SECRET_KEY": "pytest-fixed-secret-key",
        "DEBUG": False,
        "DEMO_MODE": False,
        **extra,
    }
    with _env(ADMIN_USERNAME="admin", ADMIN_PASSWORD=password):
        return create_app(config)


@pytest.fixture(autouse=True)
def _clean():
    yield
    auth_mod.reset_rate_limit()
    db_mod.reset_memory_conn()


# ---- healthz ---------------------------------------------------------------


def test_healthz_is_public_and_minimal(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}
    assert resp.headers["Cache-Control"] == "no-store"


def test_healthz_does_not_touch_session(client):
    resp = client.get("/healthz")
    assert "Set-Cookie" not in resp.headers


# ---- DEMO_MODE=1 -------------------------------------------------------------


def test_demo_mode_shows_credentials_and_seeds(client):
    page = client.get("/admin").get_data(as_text=True)
    assert "password123" in page
    assert 'id="fillDemo"' in page


def test_demo_mode_reset_works(authed):
    client, csrf = authed
    resp = client.post("/api/demo/reset", headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}


def test_demo_mode_dashboard_has_reset_button(authed):
    client, _ = authed
    assert 'id="resetDemoBtn"' in client.get("/dashboard").get_data(as_text=True)


# ---- DEMO_MODE=0 -------------------------------------------------------------


def test_prod_refuses_default_password():
    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
        _prod_app(password="password123")


def test_prod_refuses_empty_password():
    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
        _prod_app(password="")


def test_prod_default_is_not_demo():
    """Без DEMO_MODE в окружении и в конфиге режим выключен."""
    auth_mod.reset_rate_limit()
    db_mod.reset_memory_conn()
    with _env(DEMO_MODE=None, ADMIN_USERNAME="admin", ADMIN_PASSWORD=STRONG_PASSWORD):
        application = create_app(
            {"TESTING": True, "DATABASE_PATH": ":memory:", "SECRET_KEY": "k", "DEBUG": False}
        )
    assert application.config["DEMO_MODE"] is False


def test_prod_hides_credentials_and_does_not_seed():
    application = _prod_app()
    client = application.test_client()
    page = client.get("/admin").get_data(as_text=True)
    assert "password123" not in page
    assert STRONG_PASSWORD not in page
    assert 'id="fillDemo"' not in page
    with application.app_context():
        count = db_mod.get_db().execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    assert count == 0


def test_prod_login_works_and_demo_reset_is_404():
    application = _prod_app()
    client = application.test_client()
    resp = client.post("/api/login", json={"username": "admin", "password": STRONG_PASSWORD})
    assert resp.status_code == 200
    csrf = resp.get_json()["csrf_token"]
    reset = client.post("/api/demo/reset", headers={"X-CSRF-Token": csrf})
    assert reset.status_code == 404
    dash = client.get("/dashboard").get_data(as_text=True)
    assert 'id="resetDemoBtn"' not in dash


def test_prod_demo_credentials_do_not_log_in():
    application = _prod_app()
    client = application.test_client()
    resp = client.post("/api/login", json={"username": "admin", "password": "password123"})
    assert resp.status_code == 401


# ---- HSTS ----------------------------------------------------------------


def test_hsts_present_when_not_debug(client):
    assert "max-age" in client.get("/healthz").headers.get("Strict-Transport-Security", "")


def test_hsts_absent_in_debug():
    application = _prod_app(DEBUG=True)
    assert "Strict-Transport-Security" not in application.test_client().get("/healthz").headers


# ---- TRUST_PROXY -----------------------------------------------------------


def _fail_logins(client, forwarded: str, n: int) -> list[int]:
    codes = []
    for _ in range(n):
        r = client.post(
            "/api/login",
            json={"username": "admin", "password": "wrong"},
            headers={"X-Forwarded-For": forwarded},
        )
        codes.append(r.status_code)
    return codes


def test_without_trust_proxy_xff_is_ignored():
    client = _prod_app().test_client()
    codes = _fail_logins(client, "10.0.0.1", 5) + _fail_logins(client, "10.0.0.2", 1)
    assert codes[-1] == 429  # всё ещё один и тот же клиент


def test_with_trust_proxy_each_forwarded_ip_has_own_bucket():
    client = _prod_app(TRUST_PROXY=True).test_client()
    codes = _fail_logins(client, "10.0.0.1", 5) + _fail_logins(client, "10.0.0.2", 1)
    assert codes[4] == 401
    assert codes[-1] == 401
    assert _fail_logins(client, "10.0.0.1", 1) == [429]
