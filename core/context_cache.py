"""Small persistent cache for deterministic GraphRAG context assembly.

The cache deliberately lives outside the graph database.  It uses SQLite so a
single Azure worker does not need Redis or another resident service, while
still surviving ordinary worker restarts.  Entries are content addressed by
the caller and are invalidated when the graph database file changes.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional


_SCHEMA = """
CREATE TABLE IF NOT EXISTS context_cache (
    cache_key TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    accessed_at INTEGER NOT NULL,
    payload_bytes INTEGER NOT NULL,
    payload_json TEXT NOT NULL
)
"""

_LOCK = threading.RLock()
_STATS = {"hits": 0, "misses": 0, "writes": 0, "evictions": 0, "errors": 0}
_CACHE_FORMAT_VERSION = 1


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(int(os.getenv(name, str(default))), minimum)
    except (TypeError, ValueError):
        return default


def enabled() -> bool:
    return _env_flag("KGTS_CONTEXT_CACHE_ENABLED", True)


def cache_limits() -> tuple[int, int, int]:
    return (
        _env_int("KGTS_CONTEXT_CACHE_MAX_ENTRIES", 128, 1),
        _env_int("KGTS_CONTEXT_CACHE_TTL_SECONDS", 3600, 0),
        _env_int("KGTS_CONTEXT_CACHE_MAX_BYTES", 32 * 1024 * 1024, 1024),
    )


def cache_path() -> Path:
    explicit = os.getenv("KGTS_CONTEXT_CACHE_DIR", "").strip()
    if explicit:
        directory = Path(explicit)
    else:
        directory = Path(os.getenv("APP_RUNTIME_DIR", ".runtime")) / "context-cache"
    return directory / "context-cache.sqlite3"


def _clock_tick() -> int:
    return time.time_ns()


def graph_revision(db_path: Optional[str | Path] = None) -> str:
    """Return a cheap revision token that changes after graph writes."""
    explicit = str(db_path or os.getenv("GRAPH_DB_PATH") or os.getenv("KNOWLEDGE_GRAPH_DB_PATH") or "").strip()
    if not explicit:
        explicit = str(Path(os.getenv("APP_RUNTIME_DIR", ".runtime")) / "knowledge_graph.db")
    path = Path(explicit)
    parts = [str(path.resolve())]
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            stat = candidate.stat()
            parts.extend([str(candidate), str(stat.st_mtime_ns), str(stat.st_size)])
        except OSError:
            parts.extend([str(candidate), "missing"])
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]


def make_key(identity: Dict[str, Any]) -> str:
    value = {"format": _CACHE_FORMAT_VERSION, **identity}
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _connect() -> sqlite3.Connection:
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(_SCHEMA)
    return conn


def get(cache_key: str) -> Optional[Dict[str, Any]]:
    if not enabled():
        return None
    now = _clock_tick()
    _max_entries, ttl, _max_bytes = cache_limits()
    try:
        with _LOCK, _connect() as conn:
            row = conn.execute(
                "SELECT created_at, payload_json FROM context_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if row is None:
                _STATS["misses"] += 1
                return None
            if ttl and (now - int(row[0])) / 1_000_000_000 > ttl:
                conn.execute("DELETE FROM context_cache WHERE cache_key = ?", (cache_key,))
                _STATS["misses"] += 1
                _STATS["evictions"] += 1
                return None
            try:
                payload = json.loads(row[1])
            except (TypeError, ValueError):
                conn.execute("DELETE FROM context_cache WHERE cache_key = ?", (cache_key,))
                _STATS["misses"] += 1
                _STATS["errors"] += 1
                return None
            conn.execute("UPDATE context_cache SET accessed_at = ? WHERE cache_key = ?", (now, cache_key))
            _STATS["hits"] += 1
            return payload if isinstance(payload, dict) else None
    except (OSError, sqlite3.Error, TypeError, ValueError):
        _STATS["errors"] += 1
        _STATS["misses"] += 1
        return None


def set(cache_key: str, payload: Dict[str, Any]) -> bool:
    if not enabled() or not isinstance(payload, dict):
        return False
    max_entries, _ttl, max_bytes = cache_limits()
    try:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        payload_bytes = len(encoded.encode("utf-8"))
        if payload_bytes > max_bytes:
            return False
        now = _clock_tick()
        with _LOCK, _connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO context_cache
                   (cache_key, created_at, accessed_at, payload_bytes, payload_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (cache_key, now, now, payload_bytes, encoded),
            )
            rows = conn.execute(
                "SELECT cache_key FROM context_cache ORDER BY accessed_at DESC"
            ).fetchall()
            removed = 0
            total = 0
            for index, row in enumerate(rows):
                size_row = conn.execute(
                    "SELECT payload_bytes FROM context_cache WHERE cache_key = ?", (row[0],)
                ).fetchone()
                size = int(size_row[0]) if size_row else 0
                if index >= max_entries or total + size > max_bytes:
                    conn.execute("DELETE FROM context_cache WHERE cache_key = ?", (row[0],))
                    removed += 1
                else:
                    total += size
            _STATS["writes"] += 1
            _STATS["evictions"] += removed
            return True
    except (OSError, sqlite3.Error, TypeError, ValueError):
        _STATS["errors"] += 1
        return False


def status() -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "enabled": enabled(),
        "path": str(cache_path()),
        **cache_limits_to_dict(),
        **_STATS,
    }
    try:
        with _LOCK, _connect() as conn:
            row = conn.execute("SELECT COUNT(*), COALESCE(SUM(payload_bytes), 0) FROM context_cache").fetchone()
            result.update({"entries": int(row[0]), "bytes": int(row[1])})
    except (OSError, sqlite3.Error):
        result.update({"entries": 0, "bytes": 0})
    return result


def cache_limits_to_dict() -> Dict[str, int]:
    max_entries, ttl, max_bytes = cache_limits()
    return {"max_entries": max_entries, "ttl_seconds": ttl, "max_bytes": max_bytes}


def clear() -> None:
    try:
        with _LOCK, _connect() as conn:
            conn.execute("DELETE FROM context_cache")
    except (OSError, sqlite3.Error):
        _STATS["errors"] += 1
