"""Path-traversal and source-file disclosure protection."""
from __future__ import annotations

import pytest


DANGEROUS_URLS = [
    "/app.py",
    "/app.db",
    "/server.js",
    "/instance/app.db",
    "/../app.py",
    "/css/../app.py",
    "/requirements.txt",
    "/.env",
    "/js/../../app.py",
    "/dashboard.htmlx",
    "/nonexistent-file.html",
]


@pytest.mark.parametrize("url", DANGEROUS_URLS)
def test_dangerous_urls_respond_404(client, url):
    resp = client.get(url)
    assert resp.status_code == 404, f"expected 404 for {url!r}, got {resp.status_code}"


def test_legacy_dashboard_html_redirects_permanently_to_dashboard(client):
    resp = client.get("/dashboard.html", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers.get("Location", "").endswith("/dashboard")


def test_static_assets_are_served(client):
    """Whitelisted asset directories should still respond 200."""
    assert client.get("/css/style.css").status_code == 200
    assert client.get("/js/common.js").status_code == 200


def test_nonexistent_static_asset_returns_404(client):
    assert client.get("/css/does-not-exist.css").status_code == 404
    assert client.get("/js/nope.js").status_code == 404


def test_root_redirects_to_login_when_anonymous(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/admin")


def test_root_redirects_to_dashboard_when_logged_in(authed):
    client, _ = authed
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/dashboard")


def test_login_page_is_open_and_blog_is_gone(client):
    assert client.get("/admin").status_code == 200
    assert client.get("/blog").status_code == 404
    assert client.get("/index.html").status_code == 404
