"""Result storage port for B and an optional portable local implementation."""
from __future__ import annotations

import json
import sqlite3
from threading import Lock
from typing import Protocol

from analytics_support import AnalyticsError


class ResultStore(Protocol):
    def put_many(self, items: list[tuple[str, str, dict]]) -> None: ...
    def get(self, kind: str, key: str) -> dict: ...


class LocalResultStore:
    """Local CLI adapter; the product can inject its repository through ResultStore."""
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = Lock()
        self.db.execute("CREATE TABLE IF NOT EXISTS analytics_artifact "
                        "(kind TEXT, id TEXT, body TEXT NOT NULL, PRIMARY KEY(kind,id))")

    def put_many(self, items):
        rows = [(kind, key, json.dumps(value, ensure_ascii=False, allow_nan=False))
                for kind, key, value in items]
        with self.lock, self.db:
            self.db.executemany("INSERT OR IGNORE INTO analytics_artifact VALUES(?,?,?)", rows)

    def get(self, kind, key):
        with self.lock:
            row = self.db.execute("SELECT body FROM analytics_artifact WHERE kind=? AND id=?",
                                  (kind, key)).fetchone()
        if row is None:
            raise AnalyticsError("result_not_found", "Requested analytics result is unavailable", 404)
        return json.loads(row[0])

    def close(self):
        self.db.close()
