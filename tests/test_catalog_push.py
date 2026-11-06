"""Versioned catalogue snapshots and durable ordered push delivery."""
import json

import werkzeug.security

import db
from app import create_app
from catalog_push import deliver_next, enqueue_catalogue_push

TOKEN = "catalog-push-test-" + "x" * 40


def _catalogue(client, app):
    app.config["PANEL_API_KEY"] = TOKEN
    response = client.get("/api/integrations/catalog-versioned", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    return response.get_json()


def test_enabled_push_catalogue_and_outbox_share_product_transaction(authed, app):
    app.config.update(BOT_CATALOG_PUSH_URL="http://bot.test/catalog", BOT_CATALOG_PUSH_TOKEN=TOKEN)
    client, csrf = authed
    before = _catalogue(client, app)
    assert before["version"] == 1
    assert client.patch("/api/products/1", headers={"X-CSRF-Token": csrf},
                        json={"price_cents": 230000}).status_code == 200
    after = _catalogue(client, app)
    assert after["version"] == 2
    assert after["catalog"][0]["price_cents"] == 230000
    with app.app_context():
        row = db.get_db().execute(
            "SELECT version,catalog_json FROM catalog_push_outbox"
        ).fetchone()
    assert row["version"] == after["version"]
    assert json.loads(row["catalog_json"]) == after["catalog"]


def test_retry_preserves_order_and_snapshot_after_bot_outage(app, app_context):
    conn = db.get_db()
    with conn:
        conn.execute("UPDATE my_products SET price_cents=230000 WHERE id=1")
        first = enqueue_catalogue_push(conn)
        conn.execute("UPDATE my_products SET price_cents=220000 WHERE id=1")
        second = enqueue_catalogue_push(conn)
    calls = []

    def unavailable(_url, _token, _body):
        raise OSError("bot is down")

    assert deliver_next(conn, "http://bot.test/catalog", TOKEN, now=100, post=unavailable)
    # The retry delay for the oldest item prevents the newer snapshot overtaking it.
    assert not deliver_next(conn, "http://bot.test/catalog", TOKEN, now=101, post=calls.append)

    def delivered(_url, _token, body):
        calls.append(json.loads(body))

    assert deliver_next(conn, "http://bot.test/catalog", TOKEN, now=102, post=delivered)
    assert deliver_next(conn, "http://bot.test/catalog", TOKEN, now=102, post=delivered)
    assert [call["version"] for call in calls] == [first, second]
    assert calls[0]["catalog"][0]["price_cents"] == 230000
    assert calls[1]["catalog"][0]["price_cents"] == 220000
    assert conn.execute("SELECT COUNT(*) FROM catalog_push_outbox").fetchone()[0] == 0


def test_configured_panel_start_queues_current_catalogue(tmp_path):
    app = create_app({
        "TESTING": True,
        "DATABASE_PATH": str(tmp_path / "panel.sqlite3"),
        "SECRET_KEY": "test-secret",
        "ADMIN_PASSWORD_HASH": werkzeug.security.generate_password_hash("a-long-test-password"),
        "DEMO_MODE": False,
        "BOT_CATALOG_PUSH_URL": "http://bot.test/catalog",
        "BOT_CATALOG_PUSH_TOKEN": TOKEN,
    })
    with app.app_context():
        row = db.get_db().execute(
            "SELECT version,catalog_json FROM catalog_push_outbox ORDER BY version DESC LIMIT 1"
        ).fetchone()
    assert row["version"] == 2
    assert len(json.loads(row["catalog_json"])) == 20
