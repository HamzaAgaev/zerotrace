from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


class ControlStore:
    """A's metadata tables in the same SQLite file as B's telemetry repository."""

    def __init__(self, path: Path):
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS control_records ("
            "kind TEXT NOT NULL, key TEXT NOT NULL, payload TEXT NOT NULL, "
            "PRIMARY KEY(kind, key))"
        )

    def get(self, kind: str, key: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT payload FROM control_records WHERE kind=? AND key=?", (kind, key)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, kind: str, key: str, value: dict[str, Any]) -> None:
        self.put_many([(kind, key, value)])

    def put_many(self, values: list[tuple[str, str, dict]]) -> None:
        rows = [
            (kind, key, json.dumps(value, ensure_ascii=False, allow_nan=False))
            for kind, key, value in values
        ]
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                # A snapshot ID excludes generated_at, so keep the first persisted snapshot.
                self.connection.executemany(
                    "INSERT INTO control_records(kind,key,payload) VALUES(?,?,?) "
                    "ON CONFLICT(kind,key) DO UPDATE SET payload=excluded.payload "
                    "WHERE control_records.kind NOT IN ('analytics_snapshot', 'scenario')",
                    rows,
                )
                self.connection.execute("COMMIT")
            except BaseException:
                self.connection.execute("ROLLBACK")
                raise

    def items(self, kind: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT payload FROM control_records WHERE kind=? ORDER BY key", (kind,)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def close(self) -> None:
        with self.lock:
            self.connection.close()
