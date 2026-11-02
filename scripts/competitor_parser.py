"""Read the competitor catalogue and deliver snapshots to the panel."""
import argparse
import json
import logging
import os
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from dotenv import load_dotenv

log = logging.getLogger("competitor_parser")
MAX_BYTES = 2_000_000


class CatalogueParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []
        self.item = None
        self.in_name = False
        self.name_parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()
        if tag == "tr" and "product" in classes:
            self.item = {}
            self.name_parts = []
        if self.item is None:
            return
        if tag == "td" and "product-name" in classes:
            self.in_name = True
        if tag == "td" and "product-price" in classes:
            raw = attrs.get("data-price-kopecks", "")
            if not raw.isascii() or not raw.isdigit() or len(raw) > 10:
                raise ValueError("Invalid price in competitor catalogue")
            self.item["price_cents"] = int(raw)

    def handle_data(self, data):
        if self.in_name:
            self.name_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "td":
            self.in_name = False
        if tag == "tr" and self.item is not None:
            self.item["name"] = "".join(self.name_parts).strip()
            self.items.append(self.item)
            self.item = None


def parse_catalogue(html):
    parser = CatalogueParser()
    parser.feed(html)
    items = parser.items
    if not 1 <= len(items) <= 500:
        raise ValueError("Competitor catalogue must contain 1–500 products")
    names = set()
    for item in items:
        name, price = item["name"], item.get("price_cents", 0)
        if not 1 <= len(name) <= 200 or name in names or not 0 < price <= 1_000_000_000:
            raise ValueError("Invalid or duplicate product in competitor catalogue")
        names.add(name)
    return items


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the panel's credential to a redirected host.
        return None


def fetch_catalogue(source):
    opener = build_opener(NoRedirects())
    # The source request intentionally carries no panel credential.
    with opener.open(Request(source, headers={"Accept": "text/html"}), timeout=10) as response:
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("Competitor catalogue is too large")
        return parse_catalogue(raw.decode("utf-8"))


def sync_once(source, panel, token):
    items = fetch_catalogue(source)
    opener = build_opener(NoRedirects())
    request = Request(
        panel.rstrip("/") + "/api/competitor/prices",
        data=json.dumps({"items": items}).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with opener.open(request, timeout=10) as response:
        result = json.loads(response.read(MAX_BYTES))
    log.info("Snapshot delivered: products=%s created=%s changed=%s",
             len(items), result["created"], result["changed"])
    return result


def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=os.getenv("COMPETITOR_SOURCE_URL", "http://127.0.0.1:5001"))
    parser.add_argument("--panel", default=os.getenv("PANEL_URL", "http://127.0.0.1:5000"))
    parser.add_argument("--interval", type=float, default=15)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    token = os.getenv("PANEL_API_KEY", "").strip()
    if len(token) < 32:
        parser.error("PANEL_API_KEY must contain at least 32 characters")
    if not 1 <= args.interval <= 86400:
        parser.error("--interval must be between 1 and 86400 seconds")
    for url in (args.source, args.panel):
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            parser.error("URLs must be HTTP(S) addresses without credentials")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        while True:
            try:
                sync_once(args.source, args.panel, token)
            except (OSError, ValueError, KeyError) as exc:
                # Do not log request objects or credentials.
                log.error("Price synchronization failed (%s)", type(exc).__name__)
                if args.once:
                    return 1
            if args.once:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
