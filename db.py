"""Database helpers: connection, schema, deterministic seeding, demo reset."""
from __future__ import annotations

import os
import random
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

import flask

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_name  TEXT NOT NULL CHECK (length(customer_name) BETWEEN 2 AND 80),
    customer_email TEXT,
    product        TEXT NOT NULL CHECK (length(product) BETWEEN 2 AND 80),
    amount_cents   INTEGER NOT NULL CHECK (amount_cents > 0),
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','paid','shipped','completed','cancelled')),
    note           TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_status  ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);
CREATE TABLE IF NOT EXISTS order_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    from_status TEXT,
    to_status   TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""

# Демо-бизнес: вымышленная пекарня «Хлебный день», заказы из Telegram.
# Начальные цены в копейках; после миграции каталогом управляет панель.
_INITIAL_PRODUCTS: list[tuple[str, int]] = [
    ("Медовик, 1 кг", 240000),
    ("Чизкейк «Нью-Йорк»", 320000),
    ("Набор круассанов (6 шт.)", 115000),
    ("Эклеры (8 шт.)", 144000),
    ("Капкейки (12 шт.)", 216000),
    ("Вишнёвый пирог", 110000),
    ("Хлебная подписка на неделю", 189000),
    ("Набор имбирного печенья", 78000),
    ("Фокачча с розмарином", 39000),
    ("Ржаной хлеб на закваске", 14000),
    ("Пшеничный батон", 9000),
    ("Багет", 11000),
    ("Чиабатта", 13000),
    ("Бородинский хлеб", 12500),
    ("Круассан с маслом", 20000),
    ("Булочка с корицей", 14000),
    ("Пирожок с яблоком", 8000),
    ("Ватрушка с творогом", 10500),
    ("Синнабон со сливочным кремом", 24000),
    ("Слойка с сыром", 16000),
    ("Печенье с шоколадом (3 шт.)", 21000),
]
# Seed data only; live catalogues and order pricing are read from my_products.
PRODUCT_CATALOGUE = [item for i, item in enumerate(_INITIAL_PRODUCTS, 1) if i != 7]

# Вымышленные клиенты: случайные сочетания частых имён и фамилий.
_CUSTOMER_NAMES: list[str] = [
    "Анна Смирнова", "Дмитрий Кузнецов", "Елена Попова", "Сергей Волков",
    "Ольга Морозова", "Иван Новиков", "Мария Фёдорова", "Алексей Лебедев",
    "Наталья Соколова", "Павел Егоров", "Татьяна Козлова", "Андрей Орлов",
    "Ирина Белова", "Михаил Зайцев", "Екатерина Павлова", "Никита Семёнов",
    "Светлана Голубева", "Роман Виноградов", "Юлия Титова", "Артём Киселёв",
    "Дарья Макарова", "Владимир Фролов", "Полина Жукова", "Кирилл Медведев",
    "Алина Тихонова", "Денис Комаров", "Оксана Давыдова", "Максим Гусев",
    "Вера Сорокина", "Игорь Беляев", "Лариса Калинина", "Степан Родионов",
    "Зоя Никитина", "Глеб Сидоров", "Алиса Ершова", "Тимур Сафин",
    "Карина Абрамова", "Олег Панов", "Марина Дорофеева", "Ярослав Исаев",
    "Валерия Ермакова", "Антон Шишкин", "Любовь Фомина", "Руслан Галкин",
    "Диана Кузьмина", "Евгений Логинов", "София Антонова", "Борис Ковалёв",
    "Настя Шубина", "Леонид Маслов", "Виктория Яковлева", "Фёдор Пономарёв",
]

_TRANSLIT = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n",
     "o", "p", "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "",
     "y", "", "e", "yu", "ya"],
))


def _email_local(name: str) -> str:
    """«Анна Смирнова» -> «anna.smirnova» (латиница для демо-адресов)."""
    letters = "".join(_TRANSLIT.get(c, c) for c in name.lower() if c.isalpha() or c == " ")
    return letters.strip().replace(" ", ".")


def utcnow() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(UTC)


_MEMORY_CONN: sqlite3.Connection | None = None


def reset_memory_conn() -> None:
    """Drop the in-memory singleton (used by test fixtures between runs)."""
    global _MEMORY_CONN
    if _MEMORY_CONN is not None:
        try:
            _MEMORY_CONN.close()
        except sqlite3.Error:
            pass
        _MEMORY_CONN = None


def _ensure_parent_dir(path: str) -> None:
    if path == ":memory:":
        return
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)


def get_db() -> sqlite3.Connection:
    """Return a per-request connection stored on flask.g.

    In-memory databases are process-wide singletons so tables survive
    across multiple connections, which is required for test suites.
    """
    if "db" not in flask.g:
        db_path = flask.current_app.config["DATABASE_PATH"]
        _ensure_parent_dir(db_path)
        if db_path == ":memory:":
            global _MEMORY_CONN
            if _MEMORY_CONN is None:
                conn = sqlite3.connect(db_path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("PRAGMA journal_mode = WAL")
                _MEMORY_CONN = conn
            flask.g.db = _MEMORY_CONN
        else:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            flask.g.db = conn
    return flask.g.db


def close_db(_exc: BaseException | None = None) -> None:
    """Teardown handler: close the request-scoped connection if any.

    The in-memory singleton is intentionally kept alive past request end.
    """
    conn = flask.g.pop("db", None)
    if conn is not None and conn is not _MEMORY_CONN:
        conn.close()


SCHEMA_VERSION = 4

_ORDERS_V1_COLUMNS: list[tuple[str, str]] = [
    ("source", "TEXT NOT NULL DEFAULT 'panel' CHECK (source IN ('panel','telegram'))"),
    ("customer_phone", "TEXT"),
    ("telegram_user_id", "INTEGER"),
    ("telegram_username", "TEXT"),
    ("delivery_date", "TEXT"),
    ("idempotency_key", "TEXT"),
]

_ORDER_ITEMS_SQL = """
CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id         INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product          TEXT NOT NULL,
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents > 0),
    quantity         INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 20)
);
CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);
"""


def migrate(conn: sqlite3.Connection) -> None:
    """Идемпотентная миграция схемы через PRAGMA user_version."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return
    if version < 1:
        have = {r[1] for r in conn.execute("PRAGMA table_info(orders)")}
        for name, ddl in _ORDERS_V1_COLUMNS:
            if name not in have:
                conn.execute(f"ALTER TABLE orders ADD COLUMN {name} {ddl}")
        # SQLite не умеет ADD COLUMN ... UNIQUE: уникальность через индекс.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_idem"
            " ON orders(idempotency_key)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_tg_user ON orders(telegram_user_id)"
        )
        conn.executescript(_ORDER_ITEMS_SQL)
    if version < 2:
        conn.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE competitor_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE CHECK(length(name) BETWEEN 1 AND 200),
                last_price_cents INTEGER NOT NULL CHECK(last_price_cents > 0),
                updated_at TEXT NOT NULL
            );
            CREATE TABLE competitor_price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL REFERENCES competitor_products(id) ON DELETE CASCADE,
                old_price_cents INTEGER NOT NULL CHECK(old_price_cents > 0),
                new_price_cents INTEGER NOT NULL CHECK(new_price_cents > 0),
                change_percent REAL NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE my_products_mapping (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                competitor_product_id INTEGER NOT NULL UNIQUE
                    REFERENCES competitor_products(id) ON DELETE CASCADE,
                my_product_name TEXT NOT NULL,
                my_price_cents INTEGER NOT NULL CHECK(my_price_cents > 0)
            );
            CREATE INDEX idx_competitor_history_product ON competitor_price_history(product_id);
            PRAGMA user_version = 2;
            COMMIT;
        """)
    if version < 3:
        conn.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS my_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE CHECK(length(name) BETWEEN 2 AND 80),
                price_cents INTEGER NOT NULL CHECK(typeof(price_cents)='integer' AND price_cents BETWEEN 1 AND 1000000000),
                is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS my_price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL REFERENCES my_products(id),
                old_price_cents INTEGER NOT NULL CHECK(old_price_cents > 0),
                new_price_cents INTEGER NOT NULL CHECK(new_price_cents > 0),
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_my_price_history_product ON my_price_history(product_id);
        """)
        try:
            now = utcnow().isoformat()
            # Keep legacy IDs stable: subscription ID 7 is retired, never reassigned.
            for pid, (name, price) in enumerate(_INITIAL_PRODUCTS, 1):
                conn.execute(
                    "INSERT OR IGNORE INTO my_products(id,name,price_cents,is_active,updated_at)"
                    " VALUES (?,?,?,?,?)", (pid, name, price, int(pid != 7), now),
                )
            conn.execute("ALTER TABLE my_products_mapping RENAME TO my_products_mapping_legacy")
            conn.execute("""CREATE TABLE my_products_mapping (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                competitor_product_id INTEGER NOT NULL UNIQUE
                    REFERENCES competitor_products(id) ON DELETE CASCADE,
                my_product_id INTEGER NOT NULL REFERENCES my_products(id)
            )""")
            conn.execute("""INSERT INTO my_products_mapping(id,competitor_product_id,my_product_id)
                SELECT m.id,m.competitor_product_id,p.id FROM my_products_mapping_legacy m
                JOIN my_products p ON p.name=m.my_product_name""")
            conn.execute("DROP TABLE my_products_mapping_legacy")
            conn.execute("PRAGMA user_version = 3")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    if version < 4:
        # Names avoid overwriting IDs already allocated to custom products.
        # Existing prices, visibility and order snapshots remain untouched.
        try:
            conn.execute("BEGIN IMMEDIATE")
            now = utcnow().isoformat()
            for name, price in _INITIAL_PRODUCTS[9:]:
                conn.execute(
                    "INSERT INTO my_products(name,price_cents,is_active,updated_at)"
                    " VALUES (?,?,1,?) ON CONFLICT(name) DO NOTHING",
                    (name, price, now),
                )
            conn.execute("PRAGMA user_version = 4")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def init_db(app: flask.Flask) -> None:
    """Create schema; seed demo orders only when DEMO_MODE is on (idempotent)."""
    with app.app_context():
        conn = get_db()
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
        migrate(conn)
        order_count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        if order_count == 0 and app.config.get("DEMO_MODE"):
            seed_demo_orders(conn, rng_seed=42)
            conn.commit()


def seed_demo_orders(conn: sqlite3.Connection, rng_seed: int = 42) -> None:
    """Вставить ~60 заказов за 60 дней с согласованной историей статусов.

    Deterministic when called on an empty table using the same seed.
    """
    rng = random.Random(rng_seed)
    now = utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    total_orders = 60

    for idx in range(total_orders):
        days_ago = total_orders - 1 - idx  # 59..0 -> spread evenly
        day = today - timedelta(days=days_ago)
        hour = rng.randint(6, 22)
        minute = rng.randint(0, 59)
        # Случайный час сегодняшних суток может оказаться позже "сейчас": не пускаем в будущее.
        created_dt = min(day.replace(hour=hour, minute=minute), now)
        created_at = created_dt.isoformat()
        updated_at = created_at

        skew = days_ago / max(1, total_orders - 1)  # 0=newest, 1=oldest
        r = rng.random()
        if skew > 0.65:  # oldest orders: mostly completed/cancelled
            if r < 0.62:
                final = "completed"
            elif r < 0.80:
                final = "cancelled"
            elif r < 0.93:
                final = "shipped"
            else:
                final = "paid"
        elif skew > 0.30:  # middle age: more shipped/paid
            if r < 0.42:
                final = "shipped"
            elif r < 0.70:
                final = "paid"
            elif r < 0.82:
                final = "completed"
            elif r < 0.92:
                final = "pending"
            else:
                final = "cancelled"
        else:  # newest: mostly pending/paid
            if r < 0.48:
                final = "pending"
            elif r < 0.86:
                final = "paid"
            elif r < 0.93:
                final = "shipped"
            elif r < 0.97:
                final = "cancelled"
            else:
                final = "completed"

        product, price = rng.choice(PRODUCT_CATALOGUE)
        customer = rng.choice(_CUSTOMER_NAMES)
        email_local = _email_local(customer)
        email_domain = rng.choice(["example.com", "example.org", "example.net"])
        customer_email = f"{email_local}@{email_domain}"

        cur = conn.execute(
            "INSERT INTO orders (customer_name, customer_email, product, amount_cents,"
            " status, note, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (customer, customer_email, product, price, final, None, created_at, updated_at),
        )
        order_id = cur.lastrowid

        if final == "pending":
            path: Iterable[str | None] = (None, "pending")
        elif final == "paid":
            path = (None, "pending", "paid")
        elif final == "shipped":
            path = (None, "pending", "paid", "shipped")
        elif final == "completed":
            path = (None, "pending", "paid", "shipped", "completed")
        else:  # cancelled: either pending->cancelled or paid->cancelled
            if rng.random() < 0.6:
                path = (None, "pending", "cancelled")
            else:
                path = (None, "pending", "paid", "cancelled")

        event_time = created_dt
        pair_iter = list(zip(path, tuple(path)[1:]))  # type: ignore[arg-type]
        for i, (from_s, to_s) in enumerate(pair_iter):
            event_dt = min(event_time + timedelta(minutes=i * rng.randint(30, 240)), now)
            conn.execute(
                "INSERT INTO order_events (order_id, from_status, to_status, created_at)"
                " VALUES (?,?,?,?)",
                (order_id, from_s, to_s, event_dt.isoformat()),
            )


def reset_demo_orders(conn: sqlite3.Connection, rng_seed: int = 42) -> None:
    """Delete all orders + events, then run the deterministic seed."""
    conn.execute("DELETE FROM order_events")
    conn.execute("DELETE FROM orders")
    seed_demo_orders(conn, rng_seed=rng_seed)
    conn.commit()
