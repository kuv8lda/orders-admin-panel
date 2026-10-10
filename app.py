"""Flask application factory and all HTTP routes.

Public:
    create_app(config=None) -> Flask
Run directly:
    python app.py  # starts Werkzeug dev server on 127.0.0.1:5000
"""
from __future__ import annotations

import csv
import io
import logging
import os
import re
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from flask import (
    Flask,
    Response,
    current_app,
    jsonify,
    make_response,
    redirect,
    render_template_string,
    request,
    send_from_directory,
    session,
)
from werkzeug.exceptions import HTTPException, NotFound
from werkzeug.middleware.proxy_fix import ProxyFix

import auth as auth_mod
from auth import (
    DEFAULT_PASSWORD,
    DEFAULT_USERNAME,
    check_rate_limit,
    csrf_token,
    load_or_generate_secret_key,
    seconds_until_reset,
    verify_password,
)
from db import (
    close_db,
    get_db,
    init_db,
    reset_demo_orders,
    utcnow,
)

# ---------------------------------------------------------------------------
# Constants shared across routes
# ---------------------------------------------------------------------------

ROOT = os.path.dirname(os.path.abspath(__file__))

log = logging.getLogger("orders")

_FALSE_VALUES = frozenset(("", "0", "false", "off", "no"))


def _env_flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() not in _FALSE_VALUES


def _configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    if not isinstance(level, int):
        level = logging.INFO
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    log.setLevel(level)

API_ALLOWLIST_NO_AUTH: set[tuple[str, str]] = {
    ("POST", "/api/login"),
    ("GET", "/api/me"),
}

CSRF_EXEMPT: set[tuple[str, str]] = {
    ("POST", "/api/login"),
}

STATUS_VALUES: frozenset[str] = frozenset(
    ("pending", "paid", "shipped", "completed", "cancelled")
)
REVENUE_STATUSES: frozenset[str] = frozenset(("paid", "shipped", "completed"))

STATUS_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "pending": ("paid", "cancelled"),
    "paid": ("shipped", "cancelled"),
    "shipped": ("completed",),
    "completed": (),
    "cancelled": (),
}

ORDER_SORT_WHITELIST: frozenset[str] = frozenset(
    ("id", "customer_name", "product", "amount_cents", "status", "created_at")
)

# Chart.js и Font Awesome пока грузятся с CDN. Когда файлы лягут в js/vendor и
# css/vendor, оба хоста отсюда нужно убрать.
CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self' https://cdn.jsdelivr.net",
        "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com",
        "font-src 'self' https://cdnjs.cloudflare.com",
        "img-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    )
)

STATIC_FILES_DENYLIST = frozenset(
    (".py", ".db", ".txt", ".env", ".md", ".xz")
)

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\n", "\r")

NOT_FOUND_BODY = """
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>404 Not Found</title></head>
<body style="font-family:system-ui;padding:48px"><h1>404</h1><p>Page not found.</p>
<a href="/" style="color:#4f46e5">Go home</a></body></html>
"""


class JsonApiError(HTTPException):
    """Raised to return a JSON error response with a specific HTTP status.

    code/description are inherited by werkzeug rendering machinery; our
    error handler always re-serialises the ``body`` attribute as JSON.
    """

    def __init__(
        self,
        status: int,
        message: str,
        fields: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self.code = status
        self.body: dict[str, Any] = {"error": message}
        if fields:
            self.body["errors"] = fields
        self.response: Response | None = None  # type: ignore[assignment]


def _raise(status: int, message: str, fields: dict[str, str] | None = None) -> None:
    """Unconditionally raise a JsonApiError."""
    raise JsonApiError(status, message, fields)

# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_app(config: dict[str, Any] | None = None) -> Flask:
    """Create a configured Flask application.

    ``config`` overrides are applied last and can be used by tests to force
    ``DATABASE_PATH=':memory:'``, ``TESTING=True``, a fixed ``SECRET_KEY``,
    or a pre-computed ``ADMIN_PASSWORD_HASH``.
    """
    _configure_logging()
    app = Flask(__name__, static_folder=None)
    instance_path = config.pop("INSTANCE_PATH", None) if config else None
    if instance_path:
        app.instance_path = instance_path

    secret_from_env = os.environ.get("SECRET_KEY", "").strip()
    if secret_from_env:
        app.config["SECRET_KEY"] = secret_from_env
    elif not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = load_or_generate_secret_key(ROOT)

    if not app.config.get("DATABASE_PATH"):
        app.config["DATABASE_PATH"] = os.path.join(ROOT, "instance", "app.db")
    if "DEBUG" not in app.config or app.config["DEBUG"] is None:
        app.config["DEBUG"] = os.environ.get("FLASK_DEBUG", "0").strip() not in {"0", "", "false", "off"}
    if not app.config.get("ADMIN_USERNAME"):
        app.config["ADMIN_USERNAME"] = DEFAULT_USERNAME
    if "ADMIN_PASSWORD_HASH" not in app.config:
        app.config["ADMIN_PASSWORD_HASH"] = None

    env_db = os.environ.get("DATABASE_PATH", "").strip()
    if env_db and not (config and "DATABASE_PATH" in config):
        if not os.path.isabs(env_db):
            env_db = os.path.join(ROOT, env_db)
        app.config["DATABASE_PATH"] = env_db

    if config:
        for key, value in config.items():
            app.config[key] = value

    if "DEMO_MODE" not in app.config:
        app.config["DEMO_MODE"] = _env_flag("DEMO_MODE")
    app.config["DEMO_MODE"] = bool(app.config["DEMO_MODE"])
    if "TRUST_PROXY" not in app.config:
        app.config["TRUST_PROXY"] = _env_flag("TRUST_PROXY")

    username_env = os.environ.get("ADMIN_USERNAME") or None
    password_env = os.environ.get("ADMIN_PASSWORD") or None
    if not app.config["DEMO_MODE"] and not app.config.get("ADMIN_PASSWORD_HASH"):
        _require_real_password(password_env)
    u, h = auth_mod.get_admin_credentials(
        username_override=username_env,
        password_override=password_env,
        password_hash_override=app.config.get("ADMIN_PASSWORD_HASH"),
    )
    app.config["ADMIN_USERNAME"] = u
    app.config["ADMIN_PASSWORD_HASH"] = h
    app.secret_key = app.config["SECRET_KEY"]

    if app.config["TRUST_PROXY"]:
        # Один доверенный реверс-прокси: берём последний адрес из X-Forwarded-For.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)  # type: ignore[method-assign]

    _configure_session_cookie(app)

    app.teardown_appcontext(close_db)
    app.before_request(_before_request_guard)
    app.after_request(_after_security_headers)

    _register_routes(app)

    init_db(app)
    log.info(
        "app ready: demo_mode=%s trust_proxy=%s integrations=%s",
        app.config["DEMO_MODE"],
        app.config["TRUST_PROXY"],
        "on" if app.config.get("BOT_API_TOKEN") else "off",
    )
    return app


def _require_real_password(password: str | None) -> None:
    """Вне демо-режима не стартуем с пустым или паролем по умолчанию."""
    if not password or password == DEFAULT_PASSWORD:
        raise RuntimeError(
            "DEMO_MODE=0: задайте ADMIN_PASSWORD (не пустой и не '"
            + DEFAULT_PASSWORD
            + "'). Для публичного демо запустите с DEMO_MODE=1."
        )


def _configure_session_cookie(app: Flask) -> None:
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=(
            not app.config.get("DEBUG", False) and _env_flag("COOKIE_SECURE", "1")
        ),
    )


# ---------------------------------------------------------------------------
# Request guards
# ---------------------------------------------------------------------------


def _before_request_guard() -> Response | None:
    path = request.path
    method = request.method
    endpoint_key = (method, path)

    if path.startswith("/api/"):
        if endpoint_key not in API_ALLOWLIST_NO_AUTH:
            if not session.get("admin"):
                return _json_error("unauthorized", 401)
        admin_session = bool(session.get("admin"))
        if (
            method in {"POST", "PATCH", "DELETE"}
            and endpoint_key not in CSRF_EXEMPT
            and admin_session
        ):
            expected = session.get("csrf_token") or ""
            provided = request.headers.get("X-CSRF-Token") or ""
            if not expected or not provided or expected != provided:
                return _json_error("csrf", 403)
    return None


def _after_security_headers(response: Response) -> Response:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    if not current_app.config.get("DEBUG", False):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    path = request.path
    if path == "/dashboard" or path.startswith("/api/") or path == "/healthz":
        response.headers["Cache-Control"] = "no-store"
    return response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _json_error(message: str, status: int, fields: dict[str, str] | None = None) -> Response:
    body: dict[str, Any] = {"error": message}
    if fields:
        body["errors"] = fields
    return jsonify(body), status


def _json_validation_errors(fields: dict[str, str]) -> Response:
    return _json_error("validation_failed", 422, fields)


def _is_authed() -> bool:
    return bool(session.get("admin"))


def _admin_only() -> None:
    if not _is_authed():
        _raise(401, "unauthorized")


def _page_or_json_404() -> Response:
    accept = request.headers.get("Accept", "")
    if "application/json" in accept:
        return jsonify({"error": "not_found"}), 404
    return make_response(render_template_string(NOT_FOUND_BODY), 404)


def _is_path_denied(path: str) -> bool:
    normalized_path = os.path.normpath(path).lstrip("/\\")
    if normalized_path.startswith("..") or os.path.isabs(normalized_path):
        return True
    _, ext = os.path.splitext(normalized_path)
    if ext in STATIC_FILES_DENYLIST:
        return True
    return False


def _send_static(subdir: str, path: str) -> Response:
    if _is_path_denied(path):
        return _page_or_json_404()
    # Explicitly deny instance/app.db
    if subdir == "instance" and path == "app.db":
        return _page_or_json_404()
    full_dir = os.path.join(ROOT, subdir)
    try:
        return send_from_directory(full_dir, path)
    except NotFound:
        return _page_or_json_404()





def _render_page(name: str) -> str:
    with open(os.path.join(ROOT, name), "r", encoding="utf-8") as fh:
        source = fh.read()
    return render_template_string(source, demo_mode=bool(current_app.config["DEMO_MODE"]))


def _validate_email(value: str) -> bool:
    if not 3 <= len(value) <= 200:
        return False
    return bool(EMAIL_RE.match(value.strip()))


def _parse_amount_to_cents(raw: Any) -> tuple[int | None, str | None]:
    """Parse a user-supplied amount (string/number) into integer kopecks (RUB cents).

    Returns (cents, error_message). cents is None on error.
    Tolerates floating-point representation artifacts such as 0.1+0.2
    being serialized as 0.30000000000000004 by rounding to 2 decimals first
    and accepting the result if the round-trip error is below 1e-9.
    """
    if raw is None:
        return None, "Amount is required"
    try:
        d = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None, "Amount must be a number"
    if d.is_nan() or d.is_infinite():
        return None, "Amount must be a number"
    eps = Decimal("1e-9")
    quantized = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign, digits, exponent = d.as_tuple()
    raw_fraction_digits = max(0, -exponent)
    if raw_fraction_digits > 2 and abs(d - quantized) > eps:
        return None, "Amount supports at most 2 decimal places"
    cents_decimal = (quantized * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    cents = int(cents_decimal)
    if cents <= 0:
        return None, "Amount must be greater than 0"
    if cents > 100_000_000:
        return None, "Amount must not exceed 1,000,000.00"
    return cents, None


def _escape_like(q: str) -> str:
    out = []
    for ch in q:
        if ch in {"\\", "%", "_"}:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def _csv_safe(value: object) -> str:
    s = "" if value is None else str(value)
    if s and s[0] in CSV_FORMULA_PREFIXES:
        return "'" + s
    return s


def _parse_iso_date(s: str) -> date | None:
    try:
        return date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def _register_routes(app: Flask) -> None:
    get: Callable[..., Any] = app.get
    post: Callable[..., Any] = app.post
    patch: Callable[..., Any] = app.patch

    # ---- Pages -------------------------------------------------------------

    @get("/")
    def p_home() -> Response:
        return redirect("/dashboard" if _is_authed() else "/admin", code=302)

    @get("/admin")
    def p_admin() -> Response:
        if _is_authed():
            return redirect("/dashboard", code=302)
        return make_response(_render_page("admin.html"))

    @get("/dashboard")
    def p_dashboard() -> Response:
        if not _is_authed():
            next_url = "/dashboard"
            return redirect(f"/admin?next={next_url}", code=302)
        return make_response(_render_page("dashboard.html"))

    @get("/dashboard.html")
    def p_dashboard_legacy() -> Response:
        return redirect("/dashboard", code=301)

    @get("/healthz")
    def p_healthz() -> Response:
        return jsonify({"status": "ok"})

    # ---- Static assets (whitelisted directories) --------------------------

    @get("/css/<path:path>")
    def s_css(path: str) -> Response:
        return _send_static("css", path)

    @get("/js/<path:path>")
    def s_js(path: str) -> Response:
        return _send_static("js", path)

    @get("/img/<path:path>")
    def s_img(path: str) -> Response:
        return _send_static("img", path)

    # ---- Public API --------------------------------------------------------

    @post("/api/login")
    def a_login() -> Response:
        remote = request.remote_addr or "unknown"
        if not check_rate_limit(remote, max_attempts=5, window_sec=60):
            log.warning("login rate limited ip=%s", remote)
            wait = seconds_until_reset(remote, 60)
            return (
                jsonify(
                    {
                        "error": "too_many_attempts",
                        "retry_after_seconds": wait,
                    }
                ),
                429,
            )
        data: dict[str, Any] = request.get_json(silent=True) or {}
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        expected_user = app.config["ADMIN_USERNAME"]
        expected_hash = app.config["ADMIN_PASSWORD_HASH"]
        ok_user = username == expected_user
        ok_pass = verify_password(expected_hash, password)
        if not (ok_user and ok_pass):
            log.warning("login failed ip=%s", remote)
            return jsonify({"error": "invalid_credentials"}), 401
        log.info("login ok ip=%s", remote)
        session.clear()
        session["admin"] = True
        token = csrf_token()
        return (
            jsonify(
                {
                    "ok": True,
                    "csrf_token": token,
                    "user": {"username": expected_user},
                }
            ),
            200,
        )

    @get("/api/me")
    def a_me() -> Response:
        authed = _is_authed()
        token = csrf_token() if authed else session.get("csrf_token")
        username = app.config["ADMIN_USERNAME"] if authed else None
        return jsonify(
            {
                "admin": authed,
                "csrf_token": token,
                "username": username,
            }
        )

    @post("/api/logout")
    def a_logout() -> Response:
        _admin_only()
        session.clear()
        return jsonify({"ok": True})

    # ---- Orders API --------------------------------------------------------

    def _orders_where(params: dict[str, Any]) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        status = (request.args.get("status") or "").strip()
        if status:
            if status not in STATUS_VALUES:
                raise ValueError("status")
            clauses.append("status = ?")
            values.append(status)
        q = (request.args.get("q") or "").strip()
        if q:
            like = f"%{_escape_like(q)}%"
            numeric_q = q.lstrip("#")
            numeric_id: int | None = None
            if numeric_q.isdigit():
                try:
                    numeric_id = int(numeric_q) - 1000
                    if numeric_id <= 0:
                        numeric_id = None
                except (TypeError, ValueError):
                    numeric_id = None
            if numeric_id is not None:
                clauses.append(
                    "(customer_name LIKE ? ESCAPE '\\'"
                    " OR customer_email LIKE ? ESCAPE '\\'"
                    " OR product LIKE ? ESCAPE '\\'"
                    " OR id = ?)"
                )
                values.extend([like, like, like, numeric_id])
            else:
                clauses.append(
                    "(customer_name LIKE ? ESCAPE '\\'"
                    " OR customer_email LIKE ? ESCAPE '\\'"
                    " OR product LIKE ? ESCAPE '\\')"
                )
                values.extend([like, like, like])
        date_from = _parse_iso_date(request.args.get("date_from") or "")
        if date_from:
            clauses.append("DATE(created_at) >= DATE(?)")
            values.append(date_from.isoformat())
        date_to = _parse_iso_date(request.args.get("date_to") or "")
        if date_to:
            clauses.append("DATE(created_at) <= DATE(?)")
            values.append(date_to.isoformat())
        min_cents, err = _parse_amount_to_cents(request.args.get("min_amount") or None)
        if request.args.get("min_amount") is not None and request.args.get("min_amount") != "":
            if err:
                raise ValueError("min_amount")
            clauses.append("amount_cents >= ?")
            values.append(min_cents)
        max_cents, err = _parse_amount_to_cents(request.args.get("max_amount") or None)
        if request.args.get("max_amount") is not None and request.args.get("max_amount") != "":
            if err:
                raise ValueError("max_amount")
            clauses.append("amount_cents <= ?")
            values.append(max_cents)
        return clauses, values

    def _orders_query_with_filters(
        paginate: bool = True,
    ) -> tuple[list[dict[str, Any]], int, int, int, int]:
        args: dict[str, Any] = {}
        try:
            clauses, values = _orders_where(args)
        except ValueError as exc:
            field = exc.args[0] if exc.args else "filter"
            _raise(422, "validation_failed", {field: f"Invalid value for {field}"})
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        sort = (request.args.get("sort") or "created_at").strip() or "created_at"
        if sort not in ORDER_SORT_WHITELIST:
            _raise(422, "validation_failed", {"sort": "Unknown sort column"})
        dir_raw = (request.args.get("dir") or "desc").strip().lower()
        direction = "DESC" if dir_raw == "desc" else "ASC"

        conn = get_db()
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM orders {where}", values
        ).fetchone()["c"]

        page_raw = request.args.get("page")
        per_raw = request.args.get("per_page")
        try:
            page = max(1, int(page_raw)) if page_raw else 1
        except (TypeError, ValueError):
            page = 1
        try:
            per_page = max(1, int(per_raw)) if per_raw else 10
        except (TypeError, ValueError):
            per_page = 10
        per_page = min(per_page, 100)
        pages = (total + per_page - 1) // per_page if total else 1

        sql = (
            f"SELECT * FROM orders {where}"
            f" ORDER BY {sort} {direction}"
        )
        if paginate:
            sql += f" LIMIT ? OFFSET ?"
            values = list(values) + [per_page, (page - 1) * per_page]
        rows = conn.execute(sql, values).fetchall()
        items = [dict(r) for r in rows]
        return items, total, page, per_page, pages

    @get("/api/orders")
    def a_orders_list() -> Response:
        _admin_only()
        items, total, page, per_page, pages = _orders_query_with_filters(paginate=True)
        return jsonify(
            {
                "items": items,
                "total": total,
                "page": page,
                "per_page": per_page,
                "pages": pages,
            }
        )

    @post("/api/orders")
    def a_orders_create() -> Response:
        _admin_only()
        data: dict[str, Any] = request.get_json(silent=True) or {}
        errors: dict[str, str] = {}
        customer_name = (data.get("customer_name") or "").strip()
        customer_email = (data.get("customer_email") or "").strip() or None
        product = (data.get("product") or "").strip()
        amount_raw = data.get("amount")
        note_raw = data.get("note") or None
        note = note_raw.strip() if isinstance(note_raw, str) else None

        if not (2 <= len(customer_name) <= 80):
            errors["customer_name"] = "Customer name must be 2-80 characters"
        if customer_email and not _validate_email(customer_email):
            errors["customer_email"] = "Email is invalid"
        if not (2 <= len(product) <= 80):
            errors["product"] = "Product must be 2-80 characters"
        cents, amount_err = _parse_amount_to_cents(amount_raw)
        if amount_err:
            errors["amount"] = amount_err
        if note is not None and len(note) > 500:
            errors["note"] = "Note must not exceed 500 characters"
        if errors:
            return _json_validation_errors(errors)

        conn = get_db()
        ts = utcnow().isoformat()
        cur = conn.execute(
            "INSERT INTO orders (customer_name, customer_email, product, amount_cents,"
            " status, note, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (customer_name, customer_email, product, cents, "pending", note or None, ts, ts),
        )
        order_id = cur.lastrowid
        conn.execute(
            "INSERT INTO order_events (order_id, from_status, to_status, created_at)"
            " VALUES (?,?,?,?)",
            (order_id, None, "pending", ts),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        return jsonify(dict(row)), 201

    @get("/api/orders/<int:order_id>")
    def a_orders_get(order_id: int) -> Response:
        _admin_only()
        conn = get_db()
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not row:
            return jsonify({"error": "not_found"}), 404
        events = conn.execute(
            "SELECT * FROM order_events WHERE order_id = ? ORDER BY id ASC",
            (order_id,),
        ).fetchall()
        payload = dict(row)
        payload["events"] = [dict(e) for e in events]
        return jsonify(payload)

    @patch("/api/orders/<int:order_id>/status")
    def a_orders_status(order_id: int) -> Response:
        _admin_only()
        data: dict[str, Any] = request.get_json(silent=True) or {}
        target = (data.get("status") or "").strip()
        if target not in STATUS_VALUES:
            return _json_validation_errors({"status": "Unknown status"})
        conn = get_db()
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not row:
            return jsonify({"error": "not_found"}), 404
        current = row["status"]
        allowed = STATUS_TRANSITIONS.get(current, ())
        if target not in allowed:
            return jsonify({"error": "invalid_transition",
                            "from": current, "to": target}), 409
        ts = utcnow().isoformat()
        conn.execute(
            "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
            (target, ts, order_id),
        )
        conn.execute(
            "INSERT INTO order_events (order_id, from_status, to_status, created_at)"
            " VALUES (?,?,?,?)",
            (order_id, current, target, ts),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        return jsonify(dict(updated))

    @get("/api/orders/export.csv")
    def a_orders_export() -> Response:
        _admin_only()
        items, *_ = _orders_query_with_filters(paginate=False)
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
        writer.writerow([
            "ID", "Customer", "Email", "Product", "Amount", "Status", "Created", "Updated",
        ])
        for row in items:
            cents = row["amount_cents"] or 0
            amount_str = f"{(Decimal(cents) / Decimal('100')).quantize(Decimal('0.01'))}"
            writer.writerow([
                _csv_safe(f"#{row['id'] + 1000}"),
                _csv_safe(row["customer_name"]),
                _csv_safe(row["customer_email"] or ""),
                _csv_safe(row["product"]),
                _csv_safe(amount_str),
                _csv_safe(row["status"]),
                _csv_safe(row["created_at"]),
                _csv_safe(row["updated_at"]),
            ])
        csv_body = buf.getvalue()
        today = utcnow().date().isoformat()
        payload = "\ufeff" + csv_body
        resp = make_response(payload)
        resp.headers["Content-Type"] = "text/csv; charset=utf-8"
        resp.headers["Content-Disposition"] = (
            f'attachment; filename="orders_{today}.csv"'
        )
        return resp

    # ---- Stats & activity --------------------------------------------------

    @get("/api/stats")
    def a_stats() -> Response:
        _admin_only()
        try:
            days = int(request.args.get("days") or 30)
        except (TypeError, ValueError):
            days = 30
        if days not in {7, 30, 90}:
            days = 30
        today = utcnow().date()
        window_start = today - timedelta(days=days - 1)
        prev_start = window_start - timedelta(days=days)
        prev_end = window_start - timedelta(days=1)

        def _compute(start: date, end: date) -> dict[str, Any]:
            clauses = ["DATE(created_at) BETWEEN DATE(?) AND DATE(?)"]
            params: list[Any] = [start.isoformat(), end.isoformat()]
            where = "WHERE " + " AND ".join(clauses)
            conn = get_db()
            rev_row = conn.execute(
                f"SELECT COALESCE(SUM(amount_cents),0) AS s,"
                f" COUNT(*) AS c"
                f" FROM orders {where} AND status IN (?,?,?)",
                params + ["paid", "shipped", "completed"],
            ).fetchone()
            revenue_cents = int(rev_row["s"])
            revenue_count = int(rev_row["c"])
            orders_row = conn.execute(
                f"SELECT COUNT(*) AS c FROM orders {where}", params
            ).fetchone()
            orders_count = int(orders_row["c"])
            avg = (revenue_cents // revenue_count) if revenue_count else 0
            by_status_rows = conn.execute(
                f"SELECT status, COUNT(*) AS c FROM orders {where}"
                f" GROUP BY status", params
            ).fetchall()
            by_status: dict[str, int] = {s: 0 for s in STATUS_VALUES}
            for r in by_status_rows:
                by_status[r["status"]] = int(r["c"])
            top_rows = conn.execute(
                f"SELECT product, COALESCE(SUM(amount_cents),0) AS s, COUNT(*) AS c"
                f" FROM orders {where} AND status IN (?,?,?)"
                f" GROUP BY product ORDER BY s DESC LIMIT 5",
                params + ["paid", "shipped", "completed"],
            ).fetchall()
            top_products = [
                {"product": r["product"],
                 "revenue_cents": int(r["s"]),
                 "orders": int(r["c"])}
                for r in top_rows
            ]
            series_rows = conn.execute(
                f"SELECT DATE(created_at) AS d,"
                f" COALESCE(SUM(CASE WHEN status IN (?,?,?) THEN amount_cents ELSE 0 END),0) AS s,"
                f" COUNT(*) AS c"
                f" FROM orders {where}"
                f" GROUP BY DATE(created_at)",
                ["paid", "shipped", "completed"] + params,
            ).fetchall()
            series_map: dict[str, tuple[int, int]] = {
                r["d"]: (int(r["s"]), int(r["c"])) for r in series_rows
            }
            series: list[dict[str, Any]] = []
            cursor = start
            while cursor <= end:
                key = cursor.isoformat()
                s, c = series_map.get(key, (0, 0))
                series.append({"date": key, "revenue_cents": s, "orders": c})
                cursor += timedelta(days=1)
            return {
                "revenue_cents": revenue_cents,
                "orders": orders_count,
                "avg_order_cents": avg,
                "by_status": by_status,
                "top_products": top_products,
                "series": series,
            }

        conn = get_db()
        pending_count = int(
            conn.execute("SELECT COUNT(*) AS c FROM orders WHERE status = 'pending'")
            .fetchone()["c"]
        )

        current = _compute(window_start, today)
        previous = _compute(prev_start, prev_end)
        kpi = {
            "revenue_cents": current["revenue_cents"],
            "orders": current["orders"],
            "avg_order_cents": current["avg_order_cents"],
            "pending": pending_count,
        }
        kpi_prev = {
            "revenue_cents": previous["revenue_cents"],
            "orders": previous["orders"],
            "avg_order_cents": previous["avg_order_cents"],
            # pending_prev is intentionally same as pending because it is defined
            # as "today's current pending" in the spec (not period-scoped).
            "pending": pending_count,
        }
        return jsonify(
            {
                "period_days": days,
                "kpi": kpi,
                "kpi_prev": kpi_prev,
                "series": current["series"],
                "by_status": current["by_status"],
                "top_products": current["top_products"],
            }
        )

    @get("/api/activity")
    def a_activity() -> Response:
        _admin_only()
        try:
            limit = int(request.args.get("limit") or 10)
        except (TypeError, ValueError):
            limit = 10
        limit = min(max(1, limit), 100)
        conn = get_db()
        rows = conn.execute(
            "SELECT e.id AS id, e.order_id AS order_id, o.id AS oid,"
            " o.customer_name AS customer_name, o.product AS product,"
            " e.from_status AS from_status, e.to_status AS to_status,"
            " e.created_at AS created_at"
            " FROM order_events e"
            " JOIN orders o ON o.id = e.order_id"
            " ORDER BY e.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "id": r["id"],
                    "order_id": r["order_id"],
                    "order_display_id": r["oid"] + 1000,
                    "customer_name": r["customer_name"],
                    "product": r["product"],
                    "from_status": r["from_status"],
                    "to_status": r["to_status"],
                    "created_at": r["created_at"],
                }
            )
        return jsonify(out)

    if app.config["DEMO_MODE"]:

        @post("/api/demo/reset")
        def a_demo_reset() -> Response:
            _admin_only()
            conn = get_db()
            reset_demo_orders(conn, rng_seed=42)
            log.info("demo data reset")
            return jsonify({"ok": True})

    # ---- Catch-all: 404 ----------------------------------------------------

    @app.errorhandler(JsonApiError)
    def _json_api_error(err: JsonApiError) -> Response:
        resp = jsonify(err.body)
        resp.status_code = err.code or 500
        return resp

    @app.errorhandler(404)
    def _not_found(_err: Any) -> Response:
        return _page_or_json_404()

    @app.errorhandler(405)
    def _method_not_allowed(_err: Any) -> Response:
        return jsonify({"error": "method_not_allowed"}), 405


# ---------------------------------------------------------------------------
# Dev server entrypoint
# ---------------------------------------------------------------------------


def _ensure_instance() -> None:
    for d in ("instance", "img"):
        path = os.path.join(ROOT, d)
        if not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)


if __name__ == "__main__":
    _ensure_instance()
    application = create_app()
    print(
        " * Orders Admin Panel: http://127.0.0.1:5000\n"
        f" * Database: {application.config['DATABASE_PATH']}"
    )
    if application.config["DEMO_MODE"]:
        print(f" * Демо-режим. Логин: {application.config['ADMIN_USERNAME']} / {DEFAULT_PASSWORD}")
    application.run(
        host="127.0.0.1",
        port=5000,
        debug=application.config.get("DEBUG", False),
    )
