"""Authentication helpers: credentials, rate limiting, CSRF tokens."""
from __future__ import annotations

import os
import secrets
import time
from collections import defaultdict
from collections.abc import MutableMapping
from typing import Any

from flask import session
from werkzeug.security import check_password_hash, generate_password_hash

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "password123"

_rate_buckets: MutableMapping[str, list[float]] = defaultdict(list)


def load_or_generate_secret_key(app_dir: str) -> str:
    """Load SECRET_KEY from env or instance/secret_key file; generate + persist if missing."""
    key = os.environ.get("SECRET_KEY", "").strip()
    if key:
        return key
    store = os.path.join(app_dir, "instance", "secret_key")
    if os.path.isfile(store):
        with open(store, "r", encoding="utf-8") as fh:
            saved = fh.read().strip()
        if saved:
            return saved
    new_key = secrets.token_urlsafe(48)
    os.makedirs(os.path.dirname(store), exist_ok=True)
    with open(store, "w", encoding="utf-8") as fh:
        fh.write(new_key)
    try:
        os.chmod(store, 0o600)
    except OSError:
        pass
    return new_key


def get_admin_credentials(
    username_override: str | None = None,
    password_override: str | None = None,
    password_hash_override: str | None = None,
) -> tuple[str, str]:
    """Return (username, password_hash) using env or injected overrides.

    For tests, pass password_hash_override directly to skip hashing.
    """
    username = (
        username_override
        or os.environ.get("ADMIN_USERNAME", "").strip()
        or DEFAULT_USERNAME
    )
    if password_hash_override:
        return username, password_hash_override
    password = (
        password_override
        or os.environ.get("ADMIN_PASSWORD", "").strip()
        or DEFAULT_PASSWORD
    )
    return username, generate_password_hash(password)


def verify_password(password_hash: str, candidate: str) -> bool:
    """Constant-time comparison via werkzeug."""
    return bool(check_password_hash(password_hash, candidate or ""))


def check_rate_limit(ip: str, max_attempts: int = 5, window_sec: int = 60) -> bool:
    """Return True if attempt is allowed; False if the IP is over the limit."""
    now = time.monotonic()
    bucket = _rate_buckets[ip]
    cutoff = now - window_sec
    while bucket and bucket[0] < cutoff:
        bucket.pop(0)
    if len(bucket) >= max_attempts:
        return False
    bucket.append(now)
    return True


def seconds_until_reset(ip: str, window_sec: int = 60) -> int:
    """Best-effort guess of seconds remaining in the window for an IP."""
    bucket = _rate_buckets.get(ip, [])
    if not bucket:
        return 0
    cutoff = time.monotonic() - window_sec
    oldest = next((ts for ts in bucket if ts >= cutoff), bucket[0])
    remaining = int(oldest + window_sec - time.monotonic()) + 1
    return max(0, remaining)


def reset_rate_limit() -> None:
    """Drop all rate-limit buckets. Used between pytest tests."""
    _rate_buckets.clear()


def csrf_token(sess: MutableMapping[str, Any] | None = None) -> str:
    """Return (and lazily create) the CSRF token for the current session."""
    container = session if sess is None else sess
    token = container.get("csrf_token") if hasattr(container, "get") else None  # type: ignore[union-attr]
    if not token:
        token = secrets.token_urlsafe(32)
        container["csrf_token"] = token  # type: ignore[index]
    return token
