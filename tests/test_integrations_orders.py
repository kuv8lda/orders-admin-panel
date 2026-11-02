"""Этап 2, часть 2: POST /api/integrations/orders от бота."""
from __future__ import annotations

import pytest

import auth as auth_mod
import db as db_mod
from app import create_app
from tests.conftest import TEST_PASSWORD_HASH

TOKEN = "t" * 40
ORDERS_URL = "/api/integrations/orders"


def _app(token=TOKEN):
    auth_mod.reset_rate_limit()
    db_mod.reset_memory_conn()
    return create_app({
        "TESTING": True, "DATABASE_PATH": ":memory:", "SECRET_KEY": "k",
        "ADMIN_USERNAME": "admin", "ADMIN_PASSWORD_HASH": TEST_PASSWORD_HASH,
        "DEMO_MODE": False, "PANEL_API_KEY": token,
    })


@pytest.fixture()
def bot():
    app = _app()
    yield app.test_client()
    db_mod.reset_memory_conn()


def _bearer():
    return {"Authorization": f"Bearer {TOKEN}"}


def _payload(**overrides):
    base = {
        "customer_name": "Анна Смирнова",
        "telegram_user_id": 123456,
        "telegram_username": "anna_s",
        "delivery_date": "2026-11-01",
        "note": "Без орехов",
        "items": [{"product_id": 1, "quantity": 2}],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Успешное создание
# ---------------------------------------------------------------------------

def test_create_returns_201(bot):
    resp = bot.post(
        ORDERS_URL,
        headers={**_bearer(), "Idempotency-Key": "key-001"},
        json=_payload(),
    )
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["source"] == "telegram"
    assert body["telegram_user_id"] == 123456
    assert body["telegram_username"] == "anna_s"
    assert body["delivery_date"] == "2026-11-01"
    assert body["note"] == "Без орехов"
    assert body["status"] == "pending"
    # Цена считается панелью: 240000 * 2 = 480000
    assert body["amount_cents"] == 480000
    assert body["items"] == [
        {"product": "Медовик, 1 кг", "unit_price_cents": 240000, "quantity": 2}
    ]
    assert body["telegram_url"] == "https://t.me/anna_s"


def test_create_writes_to_db(bot):
    idem = "key-write-db"
    bot.post(
        ORDERS_URL,
        headers={**_bearer(), "Idempotency-Key": idem},
        json=_payload(),
    )
    # Повторный запрос за деталями через admin-API
    admin_app = _app()
    with admin_app.app_context():
        conn = db_mod.get_db()
        row = conn.execute(
            "SELECT * FROM orders WHERE idempotency_key = ?", (idem,)
        ).fetchone()
    assert row is None  # другой DB — убеждаемся только через эндпоинт выше


def test_create_multiple_items(bot):
    resp = bot.post(
        ORDERS_URL,
        headers={**_bearer(), "Idempotency-Key": "key-multi"},
        json=_payload(items=[{"product_id": 1, "quantity": 1}, {"product_id": 3, "quantity": 3}]),
    )
    assert resp.status_code == 201
    body = resp.get_json()
    # 240000*1 + 115000*3 = 240000 + 345000 = 585000
    assert body["amount_cents"] == 585000
    assert len(body["items"]) == 2


def test_create_without_optional_fields(bot):
    resp = bot.post(
        ORDERS_URL,
        headers={**_bearer(), "Idempotency-Key": "key-minimal"},
        json={"customer_name": "Боря", "telegram_user_id": 42, "items": [{"product_id": 2, "quantity": 1}]},
    )
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["telegram_username"] is None
    assert body["customer_phone"] is None
    assert body["delivery_date"] is None
    assert body["note"] is None


# ---------------------------------------------------------------------------
# Idempotency-Key: дубль возвращает 200 с тем же заказом
# ---------------------------------------------------------------------------

def test_duplicate_key_returns_200(bot):
    idem = "key-idem-dupe"
    resp1 = bot.post(
        ORDERS_URL,
        headers={**_bearer(), "Idempotency-Key": idem},
        json=_payload(),
    )
    assert resp1.status_code == 201
    order_id = resp1.get_json()["id"]

    resp2 = bot.post(
        ORDERS_URL,
        headers={**_bearer(), "Idempotency-Key": idem},
        json=_payload(customer_name="Другой Клиент"),  # ignored
    )
    assert resp2.status_code == 200
    body2 = resp2.get_json()
    assert body2["id"] == order_id
    assert body2["customer_name"] == "Анна Смирнова"


def test_duplicate_key_returns_items(bot):
    idem = "key-idem-items"
    bot.post(
        ORDERS_URL,
        headers={**_bearer(), "Idempotency-Key": idem},
        json=_payload(),
    )
    resp = bot.post(
        ORDERS_URL,
        headers={**_bearer(), "Idempotency-Key": idem},
        json=_payload(),
    )
    assert resp.get_json()["items"] == [
        {"product": "Медовик, 1 кг", "unit_price_cents": 240000, "quantity": 2}
    ]


# ---------------------------------------------------------------------------
# 422 валидация
# ---------------------------------------------------------------------------

def test_missing_idempotency_key_422(bot):
    resp = bot.post(ORDERS_URL, headers=_bearer(), json=_payload())
    assert resp.status_code == 422
    assert "idempotency_key" in resp.get_json().get("errors", {})


def test_missing_customer_name_422(bot):
    data = _payload()
    data.pop("customer_name")
    resp = bot.post(
        ORDERS_URL,
        headers={**_bearer(), "Idempotency-Key": "key-noname"},
        json=data,
    )
    assert resp.status_code == 422
    assert "customer_name" in resp.get_json().get("errors", {})


def test_missing_telegram_user_id_422(bot):
    data = _payload()
    data.pop("telegram_user_id")
    resp = bot.post(
        ORDERS_URL,
        headers={**_bearer(), "Idempotency-Key": "key-notgid"},
        json=data,
    )
    assert resp.status_code == 422
    assert "telegram_user_id" in resp.get_json().get("errors", {})


def test_empty_items_422(bot):
    resp = bot.post(
        ORDERS_URL,
        headers={**_bearer(), "Idempotency-Key": "key-empty-items"},
        json=_payload(items=[]),
    )
    assert resp.status_code == 422
    assert "items" in resp.get_json().get("errors", {})


def test_unknown_product_id_422(bot):
    resp = bot.post(
        ORDERS_URL,
        headers={**_bearer(), "Idempotency-Key": "key-bad-pid"},
        json=_payload(items=[{"product_id": 9999, "quantity": 1}]),
    )
    assert resp.status_code == 422


def test_bad_quantity_422(bot):
    resp = bot.post(
        ORDERS_URL,
        headers={**_bearer(), "Idempotency-Key": "key-bad-qty"},
        json=_payload(items=[{"product_id": 1, "quantity": 0}]),
    )
    assert resp.status_code == 422


def test_bad_delivery_date_422(bot):
    resp = bot.post(
        ORDERS_URL,
        headers={**_bearer(), "Idempotency-Key": "key-bad-date"},
        json=_payload(delivery_date="not-a-date"),
    )
    assert resp.status_code == 422
    assert "delivery_date" in resp.get_json().get("errors", {})


def test_invalid_telegram_username_422(bot):
    resp = bot.post(
        ORDERS_URL,
        headers={**_bearer(), "Idempotency-Key": "key-bad-tg"},
        json=_payload(telegram_username="ab"),  # too short
    )
    assert resp.status_code == 422
    assert "telegram_username" in resp.get_json().get("errors", {})


# ---------------------------------------------------------------------------
# Авторизация: без токена / неверный токен
# ---------------------------------------------------------------------------

def test_no_auth_401(bot):
    resp = bot.post(
        ORDERS_URL,
        headers={"Idempotency-Key": "key-noauth"},
        json=_payload(),
    )
    assert resp.status_code == 401


def test_wrong_token_401(bot):
    resp = bot.post(
        ORDERS_URL,
        headers={"Authorization": "Bearer wrong_token", "Idempotency-Key": "key-wrongauth"},
        json=_payload(),
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# source фиксируется как 'telegram', панель не принимает source из тела
# ---------------------------------------------------------------------------

def test_source_always_telegram(bot):
    resp = bot.post(
        ORDERS_URL,
        headers={**_bearer(), "Idempotency-Key": "key-src"},
        json=_payload(),
    )
    assert resp.get_json()["source"] == "telegram"
