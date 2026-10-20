"""SQLite state: the artists followed and every release seen.

A release is 'baseline' (it was out before its artist was followed, so it is
never downloaded), 'queued' (handed to deemix) or 'failed' (tried; tried
again next run).
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS artists (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, first_seen TEXT NOT NULL,
  mode TEXT NOT NULL,              -- baseline: new releases only; backfill: everything
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS releases (
  id INTEGER PRIMARY KEY, artist_id INTEGER NOT NULL, title TEXT NOT NULL,
  type TEXT NOT NULL, date TEXT NOT NULL, state TEXT NOT NULL,
  at TEXT NOT NULL, error TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def get_meta(self, k: str) -> str | None:
        row = self.db.execute("SELECT v FROM meta WHERE k = ?", (k,)).fetchone()
        return row["v"] if row else None

    def set_meta(self, k: str, v: str) -> None:
        self.db.execute("INSERT INTO meta VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", (k, v))
        self.db.commit()

    def artist(self, artist_id: int):
        return self.db.execute("SELECT * FROM artists WHERE id = ?", (artist_id,)).fetchone()

    def artists(self):
        return self.db.execute("SELECT * FROM artists ORDER BY active DESC, name COLLATE NOCASE").fetchall()

    def add_artist(self, artist_id: int, name: str, mode: str) -> None:
        self.db.execute("INSERT INTO artists (id, name, first_seen, mode) VALUES (?, ?, ?, ?)",
                        (artist_id, name, now(), mode))
        self.db.commit()

    def set_active(self, ids: set[int]) -> None:
        self.db.execute("UPDATE artists SET active = 0")
        self.db.executemany("UPDATE artists SET active = 1 WHERE id = ?", [(i,) for i in ids])
        self.db.commit()

    def release_state(self, release_id: int) -> str | None:
        row = self.db.execute("SELECT state FROM releases WHERE id = ?", (release_id,)).fetchone()
        return row["state"] if row else None

    def mark(self, rel: dict, artist_id: int, state: str, error: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO releases VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "state = excluded.state, at = excluded.at, error = excluded.error",
            (rel["id"], artist_id, rel["title"], rel["type"], rel["date"], state, now(), error))
        self.db.commit()

    def titles(self, artist_id: int):
        return self.db.execute("SELECT id, title, type FROM releases WHERE artist_id = ? "
                               "AND state IN ('baseline', 'queued')", (artist_id,)).fetchall()

    def counts(self, artist_id: int) -> dict:
        rows = self.db.execute("SELECT state, COUNT(*) n FROM releases WHERE artist_id = ? GROUP BY state",
                               (artist_id,)).fetchall()
        return {r["state"]: r["n"] for r in rows}

    def recent(self, limit: int = 20):
        return self.db.execute("SELECT r.*, a.name artist FROM releases r JOIN artists a ON a.id = r.artist_id "
                               "WHERE r.state != 'baseline' ORDER BY r.at DESC LIMIT ?", (limit,)).fetchall()
