"""Base SQLite locale : état de l'index (fichiers) et conversations."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    sha TEXT, size INTEGER, mtime REAL,
    status TEXT, error TEXT, method TEXT,
    pages INTEGER DEFAULT 0, chunks INTEGER DEFAULT 0, chars INTEGER DEFAULT 0,
    indexed_at REAL
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY, title TEXT, created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY, conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT, content TEXT, meta TEXT, status TEXT, created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);
"""


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._con = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA foreign_keys=ON")
        self._con.executescript(SCHEMA)
        # Réponses restées « en cours » lors d'un arrêt brutal
        self._con.execute(
            "UPDATE messages SET status='error', content=COALESCE(NULLIF(content,''),'⚠️ Réponse interrompue (redémarrage du service).') WHERE status='pending'"
        )

    def _q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._con.execute(sql, args).fetchall()

    def _x(self, sql: str, args: tuple = ()) -> None:
        with self._lock:
            self._con.execute(sql, args)

    # ---------- meta ----------
    def get_meta(self, key: str) -> str | None:
        rows = self._q("SELECT value FROM meta WHERE key=?", (key,))
        return rows[0]["value"] if rows else None

    def set_meta(self, key: str, value: str) -> None:
        self._x("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    # ---------- fichiers ----------
    def files(self) -> dict[str, dict[str, Any]]:
        return {r["path"]: dict(r) for r in self._q("SELECT * FROM files ORDER BY path")}

    def file(self, path: str) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM files WHERE path=?", (path,))
        return dict(rows[0]) if rows else None

    def save_file(self, path: str, **fields: Any) -> None:
        cur = self.file(path) or {"path": path}
        cur.update(fields)
        cols = ["path", "sha", "size", "mtime", "status", "error", "method", "pages", "chunks", "chars", "indexed_at"]
        vals = tuple(cur.get(c) for c in cols)
        self._x(
            f"INSERT OR REPLACE INTO files({','.join(cols)}) VALUES({','.join('?' * len(cols))})", vals
        )

    def delete_file(self, path: str) -> None:
        self._x("DELETE FROM files WHERE path=?", (path,))

    def clear_files(self) -> None:
        self._x("DELETE FROM files")

    # ---------- conversations ----------
    def create_conversation(self, title: str) -> str:
        cid = uuid.uuid4().hex
        now = time.time()
        self._x("INSERT INTO conversations VALUES(?,?,?,?)", (cid, title[:120], now, now))
        return cid

    def conversation(self, cid: str) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM conversations WHERE id=?", (cid,))
        return dict(rows[0]) if rows else None

    def conversations(self, limit: int = 200) -> list[dict[str, Any]]:
        return [dict(r) for r in self._q("SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ?", (limit,))]

    def rename_conversation(self, cid: str, title: str) -> None:
        self._x("UPDATE conversations SET title=? WHERE id=?", (title[:120], cid))

    def delete_conversation(self, cid: str) -> None:
        self._x("DELETE FROM messages WHERE conversation_id=?", (cid,))
        self._x("DELETE FROM conversations WHERE id=?", (cid,))

    def add_message(self, cid: str, role: str, content: str, status: str = "done", meta: dict | None = None) -> str:
        mid = uuid.uuid4().hex
        now = time.time()
        self._x(
            "INSERT INTO messages VALUES(?,?,?,?,?,?,?)",
            (mid, cid, role, content, json.dumps(meta or {}, ensure_ascii=False), status, now),
        )
        self._x("UPDATE conversations SET updated_at=? WHERE id=?", (now, cid))
        return mid

    def update_message(self, mid: str, content: str, status: str, meta: dict | None = None) -> None:
        self._x(
            "UPDATE messages SET content=?, status=?, meta=? WHERE id=?",
            (content, status, json.dumps(meta or {}, ensure_ascii=False), mid),
        )

    def messages(self, cid: str) -> list[dict[str, Any]]:
        out = []
        for r in self._q("SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at", (cid,)):
            d = dict(r)
            d["meta"] = json.loads(d["meta"] or "{}")
            out.append(d)
        return out
