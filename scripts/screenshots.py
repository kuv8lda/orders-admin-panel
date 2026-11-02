"""Снять скриншоты для README (светлая и тёмная тема, таблица, мобильный вид).

Скрипт сам запускает панель на свободном порту с временной базой в DEMO_MODE=1,
снимает страницы через Playwright и кладёт PNG в img/.

    pip install playwright && playwright install chromium
    python scripts/screenshots.py

Если Chromium уже есть в системе, укажите путь: CHROME_PATH=/usr/bin/chromium python scripts/screenshots.py
Данные демо детерминированы, но даты на графике зависят от дня запуска.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "img"
# Часы в шапке меняются каждую секунду, прячем их, чтобы снимки не отличались зря.
HIDE_CLOCK = "#clock { visibility: hidden; }"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_ready(base: str) -> None:
    for _ in range(50):
        try:
            urllib.request.urlopen(f"{base}/healthz", timeout=1)
            return
        except OSError:
            time.sleep(0.2)
    raise SystemExit("панель не запустилась")


def main() -> int:
    OUT.mkdir(exist_ok=True)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory() as tmp:
        env = {
            **os.environ,
            "DEMO_MODE": "1",
            "COOKIE_SECURE": "0",
            "DATABASE_PATH": str(Path(tmp) / "app.db"),
            "SECRET_KEY": "screenshots-only",
            "LOG_LEVEL": "WARNING",
        }
        server = subprocess.Popen(
            [
                sys.executable, "-c",
                f"from app import create_app; create_app().run(port={port}, use_reloader=False)",
            ],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            wait_ready(base)
            with sync_playwright() as p:
                launch = {"executable_path": os.environ["CHROME_PATH"]} if os.environ.get("CHROME_PATH") else {}
                browser = p.chromium.launch(args=["--no-sandbox"], **launch)
                for theme in ("light", "dark"):
                    shoot_theme(browser, base, theme)
                browser.close()
        finally:
            server.terminate()
            server.wait(timeout=10)
    print("Готово:", ", ".join(sorted(f.name for f in OUT.glob("*.png"))))
    return 0


def new_page(browser, base: str, theme: str, width: int, height: int):
    ctx = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=1)
    ctx.add_init_script(f"localStorage.setItem('theme', '{theme}'); localStorage.setItem('lang', 'ru');")
    page = ctx.new_page()
    page.goto(f"{base}/admin")
    page.fill("#username", "admin")
    page.fill("#password", "password123")
    page.click("button[type=submit]")
    page.wait_for_url("**/dashboard**")
    page.add_style_tag(content=HIDE_CLOCK)
    page.wait_for_selector("#ordersBody tr[data-id]")
    page.wait_for_timeout(1200)  # анимации графиков
    return ctx, page


def shoot_theme(browser, base: str, theme: str) -> None:
    ctx, page = new_page(browser, base, theme, 1280, 960)
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(OUT / f"dashboard-{theme}.png"))
    page.evaluate("document.getElementById('orders').scrollIntoView()")
    page.wait_for_timeout(300)
    page.screenshot(path=str(OUT / f"orders-{theme}.png"))
    ctx.close()
    if theme == "light":
        ctx, page = new_page(browser, base, theme, 390, 844)
        page.screenshot(path=str(OUT / "mobile-light.png"))
        ctx.close()


if __name__ == "__main__":
    raise SystemExit(main())
