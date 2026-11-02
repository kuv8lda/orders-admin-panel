"""Price ingestion, strict thresholds, mappings and authorization boundaries."""
import sqlite3

import pytest

import db

TOKEN = "competitor-test-key-" + "x" * 32


def send(client, price=10000, name="Кофе зерновой", **kwargs):
    return client.post("/api/competitor/prices", json={
        "items": [{"name": name, "price_cents": price}]
    }, headers={"Authorization": f"Bearer {TOKEN}"}, **kwargs)


@pytest.fixture(autouse=True)
def token(app):
    app.config["PANEL_API_KEY"] = TOKEN


def test_token_boundary(client, authed, app):
    assert client.post("/api/competitor/prices", json={}).status_code == 401
    assert client.post("/api/competitor/prices", headers={
        "Authorization": "Bearer wrong"
    }, json={}).status_code == 401
    other = app.test_client()
    for path in ("/api/competitor/comparison", "/api/competitor/history", "/api/orders"):
        assert other.get(path, headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 401
    assert send(other).status_code == 200
    app.config["PANEL_API_KEY"] = None
    assert send(other).status_code == 404


def test_first_repeat_change(authed):
    client, _ = authed
    assert send(client).get_json() == {"created": 1, "changed": 0}
    assert send(client).get_json() == {"created": 0, "changed": 0}
    assert client.get("/api/competitor/history").get_json()["items"] == []
    assert send(client, 9500).get_json()["changed"] == 1
    row = client.get("/api/competitor/history").get_json()["items"][0]
    assert (row["old_price_cents"], row["new_price_cents"], row["change_percent"]) == (10000, 9500, -5)


def test_strict_threshold_and_cursor(authed):
    client, _ = authed
    send(client)
    send(client, 9500)
    assert client.get("/api/competitor/history?significant=1").get_json()["items"] == []
    assert client.get("/api/competitor/comparison?significant=1").get_json()["items"] == []
    send(client, 10000)
    events = client.get("/api/competitor/history?significant=1").get_json()["items"]
    assert len(events) == 1
    assert len(client.get("/api/competitor/comparison?significant=1").get_json()["items"]) == 1
    assert client.get(f'/api/competitor/history?after={events[0]["id"]}').get_json()["items"] == []


@pytest.mark.parametrize("price", [True, False, 0, -1, 1.5, "100", None, 10**30])
def test_invalid_prices(client, price):
    assert send(client, price).status_code == 422


@pytest.mark.parametrize("body", [None, [], {}, {"items": []}, {"items": [None]},
    {"items": [{"name": " ", "price_cents": 1}]},
    {"items": [{"name": "x", "price_cents": 1}, {"name": " x ", "price_cents": 2}]},
    {"items": [{"name": "valid", "price_cents": 1}, {"name": "bad", "price_cents": 0}]}])
def test_invalid_batches_atomic(client, app, body):
    response = client.post("/api/competitor/prices", json=body,
                           headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 422
    with app.app_context():
        assert db.get_db().execute("SELECT COUNT(*) FROM competitor_products").fetchone()[0] == 0


def test_mapping_csrf_catalogue_and_difference(authed):
    client, csrf = authed
    send(client, 250000)
    product = client.get("/api/competitor/comparison").get_json()["items"][0]
    assert product["difference_cents"] is None
    path = f'/api/competitor/products/{product["id"]}/mapping'
    body = {"my_product_name": db.PRODUCT_CATALOGUE[0][0]}
    assert client.post(path, json=body).status_code == 403
    headers = {"X-CSRF-Token": csrf}
    assert client.post(path, json={"my_product_name": "unknown"}, headers=headers).status_code == 422
    assert client.post(path, json=body, headers=headers).status_code == 200
    assert client.post(path, json=body, headers=headers).status_code == 200
    product = client.get("/api/competitor/comparison").get_json()["items"][0]
    assert product["difference_cents"] == -10000
    assert product["my_price_cents"] == 240000


def test_last_twenty_and_full_history_filter(authed):
    client, _ = authed
    send(client, 10000, "first")
    send(client, 9000, "first")
    send(client, 10000, "second")
    for price in range(10001, 10025):
        send(client, price, "second")
    history = client.get("/api/competitor/history").get_json()["items"]
    assert len(history) == 20
    assert history[0]["id"] > history[-1]["id"]
    filtered = client.get("/api/competitor/comparison?significant=1").get_json()["items"]
    assert [p["name"] for p in filtered] == ["first"]


def test_bot_snapshot_contract_and_live_own_prices(authed, app):
    client, csrf = authed
    path = "/api/integrations/competitor-prices"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get(path).status_code == 401
    assert client.get(path, headers=headers).get_json() == []
    send(client, 250000, "Торт конкурента")
    snapshot = client.get(path, headers=headers)
    assert snapshot.headers["Cache-Control"] == "no-store"
    assert snapshot.get_json() == [{
        "competitor_id": 1, "competitor_name": "Конкурент", "product_id": 1,
        "product_name": "Торт конкурента", "price_cents": 250000, "own_price_cents": None,
    }]
    writes = {"X-CSRF-Token": csrf}
    client.post("/api/competitor/products/1/mapping", headers=writes, json={"my_product_id": 1})
    client.patch("/api/products/1", headers=writes, json={"price_cents": 230000})
    send(client, 260000, "Торт конкурента")
    item = client.get(path, headers=headers).get_json()[0]
    assert (item["competitor_id"], item["product_id"]) == (1, 1)
    assert (item["price_cents"], item["own_price_cents"]) == (260000, 230000)
    app.config["PANEL_API_KEY"] = None
    assert client.get(path, headers=headers).status_code == 404


def test_migration_v1_keeps_orders(app):
    with app.app_context():
        conn = db.get_db()
        count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        for table in ("my_products_mapping", "competitor_price_history", "competitor_products"):
            conn.execute(f"DROP TABLE {table}")
        conn.execute("PRAGMA user_version = 1")
        db.migrate(conn)
        db.migrate(conn)
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == count
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO competitor_price_history VALUES (1,999,1,2,100,'now')")
