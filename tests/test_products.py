"""Own prices, immutable order snapshots and live bot catalogue."""
import sqlite3

import pytest

import db

TOKEN = "catalog-test-" + "x" * 40


def catalog(client, app):
    app.config["PANEL_API_KEY"] = TOKEN
    response = client.get("/api/integrations/catalog", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    return response.get_json()


def test_update_reaches_catalog_mapping_and_audit(authed, app):
    client, csrf = authed
    headers = {"X-CSRF-Token": csrf}
    before = catalog(client, app)
    assert 7 not in {p["id"] for p in before}
    assert len(before) == 20
    client.post("/api/competitor/prices", headers={"Authorization": f"Bearer {TOKEN}"},
                json={"items": [{"name": "Медовик конкурента", "price_cents": 250000}]})
    assert client.post("/api/competitor/products/1/mapping", headers=headers,
                       json={"my_product_id": 1}).status_code == 200
    assert client.patch("/api/products/1", headers=headers, json={"price_cents": 230000}).status_code == 200
    assert catalog(client, app)[0]["price_cents"] == 230000
    item = client.get("/api/competitor/comparison").get_json()["items"][0]
    assert item["my_price_cents"] == 230000
    assert item["difference_cents"] == -20000
    history = client.get("/api/products/history").get_json()["items"]
    assert len(history) == 1
    assert (history[0]["old_price_cents"], history[0]["new_price_cents"]) == (240000, 230000)
    client.patch("/api/products/1", headers=headers, json={"price_cents": 230000})
    assert len(client.get("/api/products/history").get_json()["items"]) == 1
    # A rename keeps the ID mapping and does not fabricate a price change.
    client.patch("/api/products/1", headers=headers, json={"name": "Медовик домашний, 1 кг"})
    item = client.get("/api/competitor/comparison").get_json()["items"][0]
    assert item["my_product_id"] == 1
    assert item["my_product_name"] == "Медовик домашний, 1 кг"
    assert len(client.get("/api/products/history").get_json()["items"]) == 1
    # A uniqueness failure must roll back both the price write and audit insert.
    assert client.patch("/api/products/1", headers=headers, json={
        "name": "Чизкейк «Нью-Йорк»", "price_cents": 99900,
    }).status_code == 422
    assert catalog(client, app)[0]["price_cents"] == 230000
    assert len(client.get("/api/products/history").get_json()["items"]) == 1


def test_assortment_add_hide_reactivate(authed, app):
    client, csrf = authed
    headers = {"X-CSRF-Token": csrf}
    response = client.post("/api/products", headers=headers, json={"name": " Булочка с маком ", "price_cents": 18000})
    assert response.status_code == 201
    product = response.get_json()
    assert product["name"] == "Булочка с маком"
    assert product["id"] > 9
    assert product["id"] in {p["id"] for p in catalog(client, app)}
    assert client.patch(f'/api/products/{product["id"]}', headers=headers, json={"is_active": False}).status_code == 200
    assert product["id"] not in {p["id"] for p in catalog(client, app)}
    assert client.patch(f'/api/products/{product["id"]}', headers=headers, json={"is_active": True}).status_code == 200
    assert product["id"] in {p["id"] for p in catalog(client, app)}


@pytest.mark.parametrize("body", [{}, [], {"price_cents": True}, {"price_cents": 0},
    {"price_cents": 1.5}, {"price_cents": "100"}, {"price_cents": 10**20},
    {"name": " "}, {"is_active": 0}, {"unexpected": 1}])
def test_invalid_updates_atomic(authed, body):
    client, csrf = authed
    before = client.get("/api/products").get_json()
    assert client.patch("/api/products/1", json=body, headers={"X-CSRF-Token": csrf}).status_code == 422
    assert client.get("/api/products").get_json() == before
    assert client.get("/api/products/history").get_json()["items"] == []


def test_csrf_auth_duplicate_and_unknown(authed, app):
    client, csrf = authed
    assert client.patch("/api/products/1", json={"price_cents": 100}).status_code == 403
    headers = {"X-CSRF-Token": csrf}
    assert client.patch("/api/products/999", headers=headers, json={"price_cents": 100}).status_code == 404
    assert client.post("/api/products", headers=headers, json={"name": "Медовик, 1 кг", "price_cents": 100}).status_code == 422
    catalog(client, app)
    assert app.test_client().patch("/api/products/1", headers={"Authorization": f"Bearer {TOKEN}"},
                                   json={"price_cents": 100}).status_code == 401


def test_order_snapshots_not_repriced(authed, app):
    client, csrf = authed
    with app.app_context():
        before = [tuple(r) for r in db.get_db().execute("SELECT * FROM orders")]
    client.patch("/api/products/1", headers={"X-CSRF-Token": csrf}, json={"price_cents": 220000})
    with app.app_context():
        assert [tuple(r) for r in db.get_db().execute("SELECT * FROM orders")] == before


def test_v2_mapping_migrates_and_ids_stay_stable():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE competitor_products(id INTEGER PRIMARY KEY,name TEXT,last_price_cents INTEGER,updated_at TEXT);
        CREATE TABLE my_products_mapping(id INTEGER PRIMARY KEY,competitor_product_id INTEGER,my_product_name TEXT,my_price_cents INTEGER);
        INSERT INTO competitor_products VALUES(1,'Чужой торт',200000,'now');
        INSERT INTO my_products_mapping VALUES(1,1,'Медовик, 1 кг',240000);
        PRAGMA user_version=2;
    """)
    db.migrate(conn)
    assert conn.execute("SELECT my_product_id FROM my_products_mapping").fetchone()[0] == 1
    assert conn.execute("SELECT is_active FROM my_products WHERE id=7").fetchone()[0] == 0
    assert conn.execute("SELECT name FROM my_products WHERE id=8").fetchone()[0] == "Набор имбирного печенья"
    conn.execute("UPDATE my_products SET price_cents=100 WHERE id=1")
    conn.commit()
    db.migrate(conn)
    assert conn.execute("SELECT price_cents FROM my_products WHERE id=1").fetchone()[0] == 100
    conn.close()


def test_bot_orders_use_current_prices_and_reject_hidden(authed, app):
    client, csrf = authed
    catalog(client, app)
    headers = {"X-CSRF-Token": csrf}
    client.patch("/api/products/1", headers=headers, json={"price_cents": 210000})
    payload = {"customer_name": "Анна Смирнова", "telegram_user_id": 123456,
               "items": [{"product_id": 1, "quantity": 2}]}
    bot_headers = {"Authorization": f"Bearer {TOKEN}", "Idempotency-Key": "new-price"}
    response = client.post("/api/integrations/orders", headers=bot_headers, json=payload)
    assert response.status_code == 201
    assert response.get_json()["amount_cents"] == 420000
    client.patch("/api/products/1", headers=headers, json={"is_active": False})
    bot_headers["Idempotency-Key"] = "hidden-product"
    assert client.post("/api/integrations/orders", headers=bot_headers, json=payload).status_code == 422
