# Orders Admin Panel

[Русская версия (основная)](README.ru.md)

An admin panel for a small business that takes orders in messengers: one table with statuses, search and filters, CSV export and sales stats. Flask 3, SQLite, vanilla JS, no build step. Demo data is a made-up bakery ("Хлебный день"), amounts in rubles.

A learning project by a beginner developer. The demo runs on invented data.

![Orders panel, light theme](img/dashboard-light.png)

![Dark theme](img/dashboard-dark.png)

## Quick start

```bash
pip install -r requirements.txt
DEMO_MODE=1 FLASK_DEBUG=1 python app.py     # http://127.0.0.1:5000, admin / password123
# or
docker compose up --build                   # http://localhost:5000
```

`FLASK_DEBUG=1` only turns off the `Secure` cookie flag for plain `http://` on your machine.

## Modes

| | `DEMO_MODE=1` | `DEMO_MODE=0` (default) |
|---|---|---|
| Credentials shown on the login page | yes | no |
| Demo orders seeded on first start | yes | no |
| `POST /api/demo/reset` | yes | 404 |
| Default password `password123` | allowed | app refuses to start |

## Configuration

See [`.env.example`](.env.example): `SECRET_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `DEMO_MODE`, `FLASK_DEBUG`, `DATABASE_PATH`, `TRUST_PROXY`, `COOKIE_SECURE`, `LOG_LEVEL`, `PANEL_API_KEY` (Telegram bot integration under `/api/integrations/*`; disabled with 404 when unset, min 32 chars). The app does not read `.env` itself; export the variables or pass the file to Docker.

## Order statuses

`pending` -> `paid` | `cancelled`; `paid` -> `shipped` | `cancelled`; `shipped` -> `completed`. Anything else returns 409 and every allowed change is logged in `order_events`. Amounts are stored in kopecks.

## API and security

Endpoint list, error formats, filters and the security notes (deny-by-default `/api/*`, CSRF, CSP, rate limit, HSTS) are described in [README.ru.md](README.ru.md). `GET /healthz` returns `{"status": "ok"}`.

### Bot integration (`/api/integrations/*`)

Requires `PANEL_API_KEY` (≥ 32 chars) and `Authorization: Bearer <key>` header.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/integrations/catalog` | Product catalogue with `id`, `name`, `price_cents` |
| `POST` | `/api/integrations/orders` | Create order from Telegram bot |

`POST /api/integrations/orders` body:
```json
{
  "customer_name": "Анна",
  "telegram_user_id": 123456,
  "telegram_username": "anna_s",
  "customer_phone": "+79991234567",
  "delivery_date": "2026-11-01",
  "note": "Without nuts",
  "items": [{"product_id": 1, "quantity": 2}]
}
```
Requires `Idempotency-Key` header (UUID). Prices are computed from the catalogue — the bot only sends `product_id` and `quantity`. Returns 201 on creation, 200 on duplicate key, 422 on validation error.

## Tests

```bash
pip install -r requirements-dev.txt
ruff check . && pytest -q
```

## Third-party files

Chart.js 4.5.1 (MIT) in `js/vendor`; Font Awesome Free 7.3.1 icons (CC BY 4.0) as a generated `css/vendor/fa-icons.css` (see `scripts/build_icons.py`). No external requests at runtime.

## License

MIT, see [LICENSE](LICENSE).
