"""Session-protected assortment management; integration reads share the same catalogue."""
import sqlite3

from flask import Blueprint, jsonify, request

from db import get_db, utcnow

bp = Blueprint("products", __name__)
MAX_PRICE_CENTS = 1_000_000_000


def active_products():
    return [dict(row) for row in get_db().execute(
        "SELECT id,name,price_cents FROM my_products WHERE is_active=1 ORDER BY id"
    )]


def _valid(body, creating=False):
    if not isinstance(body, dict) or not body or set(body) - {"name", "price_cents", "is_active"}:
        return False
    if creating and not {"name", "price_cents"} <= body.keys():
        return False
    if "name" in body and (not isinstance(body["name"], str) or not 2 <= len(body["name"].strip()) <= 80):
        return False
    if "price_cents" in body and (type(body["price_cents"]) is not int or not 1 <= body["price_cents"] <= MAX_PRICE_CENTS):
        return False
    return "is_active" not in body or type(body["is_active"]) is bool


@bp.get("/api/products")
def products():
    return jsonify(items=[dict(row) for row in get_db().execute("SELECT * FROM my_products ORDER BY id")])


@bp.post("/api/products")
def create_product():
    body = request.get_json(silent=True)
    if not _valid(body, creating=True):
        return jsonify(error="validation_failed"), 422
    conn = get_db()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO my_products(name,price_cents,is_active,updated_at) VALUES (?,?,?,?)",
                (body["name"].strip(), body["price_cents"], body.get("is_active", True), utcnow().isoformat()),
            )
        return jsonify(dict(conn.execute("SELECT * FROM my_products WHERE id=?", (cur.lastrowid,)).fetchone())), 201
    except sqlite3.IntegrityError:
        return jsonify(error="validation_failed"), 422


@bp.patch("/api/products/<int:product_id>")
def update_product(product_id):
    body = request.get_json(silent=True)
    if not _valid(body):
        return jsonify(error="validation_failed"), 422
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        old = conn.execute("SELECT * FROM my_products WHERE id=?", (product_id,)).fetchone()
        if old is None:
            conn.rollback()
            return jsonify(error="not_found"), 404
        name = body.get("name", old["name"]).strip()
        price = body.get("price_cents", old["price_cents"])
        active = body.get("is_active", old["is_active"])
        now = utcnow().isoformat()
        if price != old["price_cents"]:
            conn.execute(
                "INSERT INTO my_price_history(product_id,old_price_cents,new_price_cents,created_at) VALUES (?,?,?,?)",
                (product_id, old["price_cents"], price, now),
            )
        conn.execute("UPDATE my_products SET name=?,price_cents=?,is_active=?,updated_at=? WHERE id=?",
                     (name, price, active, now, product_id))
        conn.commit()
        return jsonify(dict(conn.execute("SELECT * FROM my_products WHERE id=?", (product_id,)).fetchone()))
    except sqlite3.IntegrityError:
        conn.rollback()
        return jsonify(error="validation_failed"), 422
    except Exception:
        conn.rollback()
        raise


@bp.get("/api/products/history")
def price_history():
    return jsonify(items=[dict(row) for row in get_db().execute(
        "SELECT h.*,p.name FROM my_price_history h JOIN my_products p ON p.id=h.product_id ORDER BY h.id DESC LIMIT 100"
    )])
