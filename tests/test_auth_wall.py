"""Deny-by-default authentication wall.

Every ``/api/*`` endpoint outside the allowlist must return 401 when called
without a session cookie.  Page-level redirects (/dashboard, /admin) are
also exercised here.
"""
from __future__ import annotations

import pytest


def test_protected_api_endpoints_return_401_without_session(app, client):
    """Enumerate app.url_map and verify /api/* guard (allowlist only)."""
    allowlist = {
        ("POST", "/api/login"),
        ("GET", "/api/me"),
    }
    protected: list[tuple[str, str]] = []
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith("/api/"):
            continue
        if rule.rule.endswith("/<path:path>") or "<" in rule.rule:
            continue
        for method in rule.methods or {"GET"}:
            if method in {"HEAD", "OPTIONS"}:
                continue
            key = (method, rule.rule)
            if key in allowlist:
                continue
            protected.append(key)
    assert protected, "expected at least one protected /api route"
    failures: list[str] = []
    for method, path in protected:
        resp = client.open(path, method=method)
        if resp.status_code != 401:
            failures.append(f"{method} {path} -> {resp.status_code}, expected 401")
    assert not failures, "\n".join(failures)


def test_dashboard_redirects_anonymous_to_admin(client):
    resp = client.get("/dashboard", follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers.get("Location", "")
    assert location.startswith("/admin")
    assert "next=/dashboard" in location or "next=%2Fdashboard" in location


def test_admin_redirects_authed_to_dashboard(authed):
    client, _ = authed
    resp = client.get("/admin", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers.get("Location", "").endswith("/dashboard")
