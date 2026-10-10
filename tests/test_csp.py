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


def test_pages_have_no_inline_scripts_or_handlers(authed):
    client, _ = authed
    for path in ("/admin", "/dashboard"):
        html = client.get(path).get_data(as_text=True)
        for tag in re.findall(r"<script\b[^>]*>", html):
            assert "src=" in tag, f"{path}: inline script {tag}"
        assert not re.search(r"\son[a-z]+\s*=", html), f"{path}: inline event handler"
