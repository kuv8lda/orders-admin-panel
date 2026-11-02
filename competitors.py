"""Competitor price ingestion and session-protected comparison API."""
import logging
import time
from threading import Lock

from flask import Blueprint, current_app, jsonify, request

from scripts.competitor_parser import fetch_catalogue

from db import get_db, utcnow
from products import active_products

bp = Blueprint("competitors", __name__)
MAX_PRICE_CENTS = 1_000_000_000
OWN_PRODUCT_JOIN = (
    " LEFT JOIN my_products_mapping m ON m.competitor_product_id=p.id"
    " LEFT JOIN my_products mine ON mine.id=COALESCE(m.my_product_id,"
    " (SELECT id FROM my_products WHERE name=p.name AND is_active=1))"
)
# Integer arithmetic preserves the strict >5% boundary without rounding.
SIGNIFICANT_CHANGE = "ABS(h.new_price_cents-h.old_price_cents)*100 > h.old_price_cents*5"


@bp.post("/api/competitor/prices")
def receive_prices():
    body = request.get_json(silent=True)
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list) or not 1 <= len(items) <= 500:
        return jsonify(error="validation_failed"), 422
    clean = {}
    for item in items:
        if not isinstance(item, dict):
            return jsonify(error="validation_failed"), 422
        name, price = item.get("name"), item.get("price_cents")
        if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 200
                or type(price) is not int or not 0 < price <= MAX_PRICE_CENTS):
            return jsonify(error="validation_failed"), 422
        name = name.strip()
        if name in clean:
            return jsonify(error="validation_failed"), 422
        clean[name] = price

    return jsonify(store_prices(clean))


def store_prices(clean):
    conn = get_db()
    now = utcnow().isoformat()
    created = changed = 0
    try:
        # Serialize read/compare/write so concurrent deliveries cannot lose history.
        conn.execute("BEGIN IMMEDIATE")
        for name, price in clean.items():
            row = conn.execute(
                "SELECT id, last_price_cents FROM competitor_products WHERE name = ?", (name,)
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO competitor_products(name,last_price_cents,updated_at) VALUES (?,?,?)",
                    (name, price, now),
                )
                created += 1
                continue
            old = row["last_price_cents"]
            if old != price:
                conn.execute(
                    "INSERT INTO competitor_price_history"
                    "(product_id,old_price_cents,new_price_cents,change_percent,created_at)"
                    " VALUES (?,?,?,?,?)", (row["id"], old, price, (price-old)*100/old, now),
                )
                changed += 1
            conn.execute(
                "UPDATE competitor_products SET last_price_cents=?,updated_at=? WHERE id=?",
                (price, now, row["id"]),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"created": created, "changed": changed}


@bp.record_once
def configure_sync(state):
    state.app.extensions["competitor_sync"] = {"lock": Lock(), "checked_at": None, "ok": False}


@bp.post("/api/competitor/sync")
def sync_prices():
    # The URL is server configuration, never a browser-supplied fetch target.
    state = current_app.extensions["competitor_sync"]
    with state["lock"]:
        now = time.monotonic()
        if state["checked_at"] is not None and now - state["checked_at"] < 15:
            return jsonify(ok=state["ok"])
        try:
            items = fetch_catalogue(current_app.config["COMPETITOR_SOURCE_URL"])
            result = store_prices({item["name"]: item["price_cents"] for item in items})
        except (OSError, ValueError) as exc:
            logging.getLogger(__name__).warning("Competitor sync failed (%s)", type(exc).__name__)
            state.update(checked_at=time.monotonic(), ok=False)
            return jsonify(ok=False)
        state.update(checked_at=time.monotonic(), ok=True)
        return jsonify(ok=True, **result)


@bp.get("/api/competitor/comparison")
def comparison():
    conn = get_db()
    significant = request.args.get("significant", "0")
    if significant not in {"0", "1"}:
        return jsonify(error="validation_failed"), 422
    condition = (
        " WHERE EXISTS (SELECT 1 FROM competitor_price_history h WHERE h.product_id=p.id"
        f" AND {SIGNIFICANT_CHANGE})"
    ) if significant == "1" else ""
    products = [dict(r) for r in conn.execute(
        "SELECT p.*, mine.id AS my_product_id, mine.name AS my_product_name, mine.price_cents AS my_price_cents FROM competitor_products p"
        + OWN_PRODUCT_JOIN + condition + " ORDER BY p.name"
    )]
    for p in products:
        p["difference_cents"] = (
            p["my_price_cents"] - p["last_price_cents"] if p["my_price_cents"] is not None else None
        )
    latest = conn.execute("SELECT COALESCE(MAX(id),0) FROM competitor_price_history").fetchone()[0]
    return jsonify(items=products, latest_event_id=latest,
                   source=current_app.config.get("COMPETITOR_SOURCE_URL", "http://127.0.0.1:5001"),
                   catalogue=active_products())


@bp.get("/api/integrations/competitor-prices")
def integration_prices():
    # A single source today; product IDs stay stable across price deliveries.
    rows = get_db().execute(
        "SELECT 1 AS competitor_id, 'Конкурент' AS competitor_name,"
        " p.id AS product_id,p.name AS product_name,p.last_price_cents AS price_cents,"
        " mine.price_cents AS own_price_cents FROM competitor_products p"
        + OWN_PRODUCT_JOIN + " ORDER BY p.id"
    )
    response = jsonify([dict(row) for row in rows])
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/api/competitor/history")
def history():
    significant = request.args.get("significant", "0")
    after = request.args.get("after", "0")
    if significant not in {"0", "1"} or not after.isascii() or not after.isdigit() or len(after) > 18:
        return jsonify(error="validation_failed"), 422
    condition = f" AND {SIGNIFICANT_CHANGE}" if significant == "1" else ""
    rows = get_db().execute(
        "SELECT h.*, p.name FROM competitor_price_history h"
        " JOIN competitor_products p ON p.id=h.product_id WHERE h.id > ?" + condition +
        " ORDER BY h.id DESC LIMIT 20", (int(after),)
    ).fetchall()
    return jsonify(items=[dict(r) for r in rows])


@bp.post("/api/competitor/products/<int:product_id>/mapping")
def save_mapping(product_id):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="validation_failed"), 422
    conn = get_db()
    pid = body.get("my_product_id")
    if pid is not None:
        if type(pid) is not int:
            return jsonify(error="validation_failed"), 422
        mine = conn.execute("SELECT id FROM my_products WHERE id=? AND is_active=1", (pid,)).fetchone()
    else:
        name = body.get("my_product_name")
        if not isinstance(name, str):
            return jsonify(error="validation_failed"), 422
        mine = conn.execute("SELECT id FROM my_products WHERE name=? AND is_active=1", (name,)).fetchone()
    if mine is None:
        return jsonify(error="validation_failed"), 422
    if not conn.execute("SELECT 1 FROM competitor_products WHERE id=?", (product_id,)).fetchone():
        return jsonify(error="not_found"), 404
    with conn:
        conn.execute(
            "INSERT INTO my_products_mapping(competitor_product_id,my_product_id)"
            " VALUES (?,?) ON CONFLICT(competitor_product_id) DO UPDATE SET"
            " my_product_id=excluded.my_product_id",
            (product_id, mine["id"]),
        )
    return jsonify(ok=True)
