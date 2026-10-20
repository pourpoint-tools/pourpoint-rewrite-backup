"""Persistent original byte ranges, scoped to one source and immutable identity."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from threading import RLock


class RangeCache:
    def __init__(self, directory: Path, source: str) -> None:
        directory = directory / source
        directory.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._db = sqlite3.connect(directory / "ranges.sqlite3", check_same_thread=False)
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS ranges (
                key TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL,
                data BLOB NOT NULL, digest TEXT NOT NULL, PRIMARY KEY (key, start, end));
        """)

    def metadata(self, key: str) -> bytes | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
            return bytes(row[0]) if row else None

    def pin(self, key: str, value: bytes) -> bytes:
        with self._lock, self._db:
            self._db.execute("INSERT OR IGNORE INTO metadata VALUES (?, ?)", (key, value))
            result = self.metadata(key)
            assert result is not None
            return result

    def pieces(self, key: str, start: int, end: int) -> list[tuple[int, bytes]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT start, data, digest FROM ranges WHERE key = ? AND start < ? AND end > ? ORDER BY start",
                (key, end, start),
            ).fetchall()
        result: list[tuple[int, bytes]] = []
        for offset, data, digest in rows:
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError("cached range SHA-256 mismatch")
            result.append((offset, bytes(data)))
        return result

    def put(self, key: str, start: int, data: bytes) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO ranges VALUES (?, ?, ?, ?, ?)",
                (key, start, start + len(data), data, hashlib.sha256(data).hexdigest()),
            )

    def close(self) -> None:
        with self._lock:
            self._db.close()
