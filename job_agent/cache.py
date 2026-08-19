"""A small on-disk cache for expensive fetches."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from typing import Any

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS fetch_cache (
    key        TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    fetched_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cache_fetched ON fetch_cache(fetched_at);
"""


def _connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(config.CACHE_DIR / "fetch_cache.sqlite3", timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def get(key: str, max_age_days: float) -> dict[str, Any] | None:
    """Return the cached payload, or None when absent or older than max_age_days."""
    if max_age_days <= 0:
        return None
    try:
        with closing(_connect()) as conn:
            row = conn.execute(
                "SELECT payload, fetched_at FROM fetch_cache WHERE key = ?", (key,)
            ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    if (time.time() - row["fetched_at"]) > max_age_days * 86400:
        return None
    try:
        return json.loads(row["payload"])
    except ValueError:
        return None


def put(key: str, payload: dict[str, Any]) -> None:
    try:
        with closing(_connect()) as conn, conn:
            conn.execute(
                "INSERT INTO fetch_cache (key, payload, fetched_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET payload = excluded.payload, "
                "fetched_at = excluded.fetched_at",
                (key, json.dumps(payload), time.time()),
            )
    except sqlite3.Error:
        pass


def prune(max_age_days: float) -> int:
    """Drop entries past their useful life. Returns the number removed."""
    cutoff = time.time() - max_age_days * 86400
    try:
        with closing(_connect()) as conn, conn:
            cur = conn.execute("DELETE FROM fetch_cache WHERE fetched_at < ?", (cutoff,))
            return cur.rowcount or 0
    except sqlite3.Error:
        return 0


def stats() -> tuple[int, float]:
    """(entries, age in days of the oldest entry)."""
    try:
        with closing(_connect()) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n, MIN(fetched_at) AS oldest FROM fetch_cache"
            ).fetchone()
    except sqlite3.Error:
        return 0, 0.0
    if not row or not row["n"]:
        return 0, 0.0
    return row["n"], (time.time() - row["oldest"]) / 86400
