"""Content-Security-Policy и отсутствие inline-скриптов в страницах."""
from __future__ import annotations

import re


def _script_src(csp: str) -> str:
    return next(p for p in csp.split(";") if p.strip().startswith("script-src"))


def test_csp_header_on_pages_and_api(authed):
    client, _ = authed
    for path in ("/admin", "/dashboard", "/healthz", "/api/me"):
        csp = client.get(path).headers.get("Content-Security-Policy", "")
        assert "default-src 'self'" in csp, path
        assert "frame-ancestors 'none'" in csp, path
        assert "unsafe-inline" not in _script_src(csp), path
        assert "unsafe-eval" not in csp, path


def _pages(app, authed):
    """Страница входа (отдельный гостевой клиент) и дашборд администратора."""
    auth_client, _ = authed
    guest = app.test_client()
    return {
        "/admin": guest.get("/admin").get_data(as_text=True),
        "/dashboard": auth_client.get("/dashboard").get_data(as_text=True),
    }


def test_pages_have_no_inline_scripts_or_handlers(app, authed):
    for path, html in _pages(app, authed).items():
        for tag in re.findall(r"<script\b[^>]*>", html):
            assert "src=" in tag, f"{path}: inline script {tag}"
        assert not re.search(r"\son[a-z]+\s*=", html), f"{path}: inline event handler"


def test_csp_allows_no_external_hosts(client):
    csp = client.get("/healthz").headers["Content-Security-Policy"]
    assert "http" not in csp
    assert _script_src(csp).strip() == "script-src 'self'"


def test_chartjs_is_served_locally(authed):
    client, _ = authed
    html = client.get("/dashboard").get_data(as_text=True)
    assert 'src="js/vendor/chart.umd.min.js"' in html
    assert "cdn.jsdelivr.net" not in html
    assert "cdnjs.cloudflare.com" not in html
    resp = client.get("/js/vendor/chart.umd.min.js")
    assert resp.status_code == 200
    assert resp.get_data(as_text=True).startswith("/*!\n * Chart.js v4.")


def test_icons_are_served_locally(app, authed):
    client, _ = authed
    for path, html in _pages(app, authed).items():
        assert 'href="css/vendor/fa-icons.css"' in html, path
        assert 'href="img/favicon.svg"' in html, path
        assert not re.search(r"https?://", html), path
    resp = client.get("/css/vendor/fa-icons.css")
    assert resp.status_code == 200
    css = resp.get_data(as_text=True)
    assert ".fa-solid.fa-plus" in css
    assert "url(http" not in css
