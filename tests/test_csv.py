"""CSV export: BOM, correct headers, filter awareness, formula injection guard."""
from __future__ import annotations

import csv
import io


def _headers(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf}


def _seed(authed, app):
    import db as db_mod
    from db import utcnow
    from datetime import timedelta

    client, csrf = authed
    with app.app_context():
        conn = db_mod.get_db()
        conn.execute("DELETE FROM order_events")
        conn.execute("DELETE FROM orders")
        today = utcnow().date()

        def ts(offset):
            d = today - timedelta(days=offset)
            return utcnow().replace(year=d.year, month=d.month, day=d.day, hour=10, minute=0, second=0, microsecond=0).isoformat()

        fixtures = [
            ("Alice Bloggs",   "a@ex.com", "Dashboard",   990_00, "paid",    ts(0)),
            ("=SUM(A1:A10)",   "b@ex.com", "Landing kit", 490_00, "pending", ts(1)),
            ("@cmd|' /C calc", "c@ex.com", "Scraper",     210_00, "paid",    ts(2)),
        ]
        for name, email, product, cents, status, created in fixtures:
            cur = conn.execute(
                "INSERT INTO orders (customer_name,customer_email,product,amount_cents,status,note,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (name, email, product, cents, status, "", created, created),
            )
            oid = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO order_events (order_id,from_status,to_status,created_at)"
                " VALUES (?,?,?,?)",
                (oid, None, status, created),
            )
        conn.commit()
    return client, csrf


def test_csv_bom_is_utf8_byte_order_mark(authed, app):
    client, csrf = _seed(authed, app)
    resp = client.get("/api/orders/export.csv")
    assert resp.status_code == 200
    raw = resp.get_data()
    assert raw[:3] == b"\xef\xbb\xbf", "expected UTF-8 BOM (EF BB BF)"


def test_csv_header_row_matches_spec(authed, app):
    client, csrf = _seed(authed, app)
    resp = client.get("/api/orders/export.csv")
    text = resp.get_data(as_text=True)
    assert text.startswith("\ufeff")
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff")))
    header = next(reader)
    assert header == [
        "ID", "Customer", "Email", "Product", "Amount", "Status", "Created", "Updated",
    ]

def test_csv_row_count_matches_filtered_api_total(authed, app):
    client, csrf = _seed(authed, app)
    api_resp = client.get("/api/orders?status=paid")
    total_api = api_resp.get_json()["total"]
    csv_resp = client.get("/api/orders/export.csv?status=paid")
    text = csv_resp.get_data(as_text=True).lstrip("\ufeff")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    header_plus_data = len(rows) - 1
    assert header_plus_data == total_api

def test_csv_formula_prefixes_are_escaped_with_single_quote(authed, app):
    client, csrf = _seed(authed, app)
    resp = client.get("/api/orders/export.csv")
    text = resp.get_data(as_text=True).lstrip("\ufeff")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    customers = [r[1] for r in rows[1:]]
    for customer in customers:
        if not customer:
            continue
        if customer[0] in {"=", "+", "-", "@", "\t", "\n", "\r"}:
            assert False, (
                f"Unescaped formula prefix found in CSV customer column: {customer!r}"
            )
    dangerous_rows = [r for r in rows[1:] if "SUM" in r[1] or "cmd" in r[1]]
    assert len(dangerous_rows) == 2
    for r in dangerous_rows:
        customer_cell = r[1]
        assert customer_cell.startswith("'"), (
            f"Expected single-quote escape for formula-prefixed cell, got {customer_cell!r}"
        )

def test_csv_content_disposition_filename_contains_today(authed, app):
    from db import utcnow
    client, csrf = _seed(authed, app)
    resp = client.get("/api/orders/export.csv")
    disposition = resp.headers.get("Content-Disposition", "")
    assert "attachment" in disposition
    assert f'orders_{utcnow().date().isoformat()}.csv' in disposition
