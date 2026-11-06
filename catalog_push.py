"""Durable, ordered delivery of catalogue snapshots to the Telegram bot.

The SQLite outbox is deliberately kept independent from Flask request state.  A
product transaction creates one immutable snapshot and advances the durable
revision.  Delivery always claims the lowest outstanding revision, so retries
can never overtake an earlier change.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

log = logging.getLogger("orders.catalog_push")

_DELIVERY_LEASE_SECONDS = 60
_MAX_RETRY_SECONDS = 300
_REQUEST_TIMEOUT_SECONDS = 3


def catalogue_snapshot(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return the exact active catalogue exposed to the bot, in stable order."""
    return [
        dict(row)
        for row in conn.execute(
            "SELECT id,name,price_cents FROM my_products WHERE is_active=1 ORDER BY id"
        )
    ]


def versioned_catalogue(conn: sqlite3.Connection) -> dict[str, Any]:
    """Read baseline version and catalogue from one SQLite snapshot transaction."""
    conn.execute("BEGIN")
    try:
        row = conn.execute("SELECT version FROM catalog_push_revision WHERE id=1").fetchone()
        if row is None:
            raise RuntimeError("catalogue revision is not initialized")
        payload = {"catalog": catalogue_snapshot(conn), "version": int(row["version"])}
        conn.commit()
        return payload
    except Exception:
        conn.rollback()
        raise


def enqueue_catalogue_push(conn: sqlite3.Connection, *, queue: bool = True) -> int:
    """Advance the revision and, when enabled, append its immutable snapshot.

    A disabled push integration still needs a durable version for the versioned
    pull endpoint, but must not accumulate undeliverable outbox rows.
    """
    catalog = catalogue_snapshot(conn) if queue else None
    row = conn.execute("SELECT version FROM catalog_push_revision WHERE id=1").fetchone()
    if row is None:
        conn.execute("INSERT INTO catalog_push_revision(id,version) VALUES (1,1)")
        version = 2
    else:
        version = int(row["version"]) + 1
    conn.execute("UPDATE catalog_push_revision SET version=? WHERE id=1", (version,))
    if queue:
        conn.execute(
            "INSERT INTO catalog_push_outbox(version,catalog_json,created_at,next_attempt_at)"
            " VALUES (?,?,?,0)",
            (
                version,
                json.dumps(catalog, ensure_ascii=False, separators=(",", ":")),
                datetime.now(UTC).isoformat(),
            ),
        )
    return version


def _claim_next(conn: sqlite3.Connection, now: float) -> sqlite3.Row | None:
    """Lease the oldest due snapshot, without allowing newer revisions through."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT version,catalog_json,attempts,next_attempt_at,lease_until"
            " FROM catalog_push_outbox ORDER BY version LIMIT 1"
        ).fetchone()
        if row is None or row["next_attempt_at"] > now or row["lease_until"] > now:
            conn.commit()
            return None
        conn.execute(
            "UPDATE catalog_push_outbox SET lease_until=? WHERE version=?",
            (now + _DELIVERY_LEASE_SECONDS, row["version"]),
        )
        conn.commit()
        return row
    except Exception:
        conn.rollback()
        raise


def _mark_delivered(conn: sqlite3.Connection, version: int) -> None:
    with conn:
        conn.execute("DELETE FROM catalog_push_outbox WHERE version=?", (version,))


def _mark_retry(conn: sqlite3.Connection, version: int, attempts: int, now: float) -> None:
    delay = min(_MAX_RETRY_SECONDS, 2 ** min(attempts, 8))
    with conn:
        conn.execute(
            "UPDATE catalog_push_outbox"
            " SET attempts=?, next_attempt_at=?, lease_until=0 WHERE version=?",
            (attempts, now + delay, version),
        )


def _post_json(url: str, token: str, body: bytes) -> None:
    request = Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with closing(urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS)) as response:
        status = response.getcode()
        if not 200 <= status < 300:
            raise RuntimeError(f"unexpected catalogue push status {status}")


def deliver_next(
    conn: sqlite3.Connection,
    url: str | None,
    token: str | None,
    *,
    now: float | None = None,
    post: Callable[[str, str, bytes], None] = _post_json,
) -> bool:
    """Deliver one due snapshot and return whether an HTTP attempt was made.

    All failure modes leave the immutable snapshot in SQLite for exponential
    retry.  A process crash after the HTTP request merely repeats the same
    revision after the lease expires; receivers must make equal revisions
    idempotent.
    """
    if not url or not token:
        return False
    timestamp = time.time() if now is None else now
    row = _claim_next(conn, timestamp)
    if row is None:
        return False
    version = int(row["version"])
    try:
        post(url, token, json.dumps({"catalog": json.loads(row["catalog_json"]), "version": version},
                                    ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (HTTPError, URLError, OSError, RuntimeError, ValueError) as exc:
        attempts = int(row["attempts"]) + 1
        _mark_retry(conn, version, attempts, timestamp)
        log.warning("catalogue push version %s failed (attempt %s): %s", version, attempts, exc)
    except Exception:
        # A custom transport used by an embedding application must not leave a
        # leased row stranded when it raises an unexpected exception.
        attempts = int(row["attempts"]) + 1
        _mark_retry(conn, version, attempts, timestamp)
        log.exception("catalogue push version %s failed unexpectedly", version)
    else:
        _mark_delivered(conn, version)
        log.info("catalogue push version %s delivered", version)
    return True


class CataloguePushDispatcher:
    """Best-effort worker backed by the SQLite outbox, safe across processes."""

    def __init__(self, database_path: str, url: str | None, token: str | None):
        self._database_path = database_path
        self._url = url
        self._token = token
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self._url or not self._token or self._database_path == ":memory:":
            return
        self._thread = threading.Thread(target=self._run, name="catalogue-push", daemon=True)
        self._thread.start()

    def notify(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        while True:
            try:
                with closing(sqlite3.connect(self._database_path)) as conn:
                    conn.row_factory = sqlite3.Row
                    conn.execute("PRAGMA foreign_keys = ON")
                    attempted = deliver_next(conn, self._url, self._token)
                if attempted:
                    continue
            except Exception:
                log.exception("catalogue push worker could not access the outbox")
            self._wake.wait(timeout=1)
            self._wake.clear()
