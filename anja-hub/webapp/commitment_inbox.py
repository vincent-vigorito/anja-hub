"""commitment_inbox.py — F-Proactive-3 — Coda dei "commitment signal".

Coda interna (NON il notification_bus, che è per il Bell utente) dove il sensore
post-chat deposita i follow-up impliciti estratti dalla conversazione. Il Kanban
Steward (kanban_dispatcher step 5) la consuma: dedup → crea task → mark done.

DB: <hub>/data/commitment_inbox.db (WAL, autocommit). Hub-level: un solo inbox,
ogni signal porta il proprio `scope` (dove andrà il task).

Schema:
  id INTEGER PK
  ts TEXT ISO 8601 UTC
  text TEXT (il follow-up, diventa titolo task)
  due_at TEXT (ISO datetime, nullable)
  scope TEXT (hub|workspace:<name>)
  source_conv TEXT (conv id di origine)
  status TEXT (pending|done|skipped)

Stdlib only (sqlite3).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_path(hub_path: Path) -> Path:
    return hub_path / "data" / "commitment_inbox.db"


def get_conn(hub_path: Path) -> sqlite3.Connection:
    p = db_path(hub_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS commitments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        text TEXT NOT NULL,
        due_at TEXT,
        scope TEXT NOT NULL DEFAULT 'hub',
        source_conv TEXT,
        status TEXT NOT NULL DEFAULT 'pending'
    );
    CREATE INDEX IF NOT EXISTS idx_commit_status ON commitments(status);
    CREATE INDEX IF NOT EXISTS idx_commit_ts ON commitments(ts);
    """)
    return conn


def enqueue(hub_path: Path, *, text: str, due_at: Optional[str] = None,
            scope: str = "hub", source_conv: str = "") -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("text required")
    ts = _now()
    conn = get_conn(hub_path)
    try:
        cur = conn.execute(
            "INSERT INTO commitments (ts, text, due_at, scope, source_conv, status) "
            "VALUES (?, ?, ?, ?, ?, 'pending')",
            (ts, text, due_at, scope or "hub", source_conv or ""),
        )
        return {"id": cur.lastrowid, "ts": ts, "text": text, "due_at": due_at,
                "scope": scope or "hub", "source_conv": source_conv or "", "status": "pending"}
    finally:
        conn.close()


def list_pending(hub_path: Path, limit: int = 50) -> list:
    conn = get_conn(hub_path)
    try:
        rows = conn.execute(
            "SELECT * FROM commitments WHERE status = 'pending' ORDER BY id ASC LIMIT ?",
            (int(limit),)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark_done(hub_path: Path, commit_id: int, status: str = "done") -> bool:
    if status not in ("done", "skipped"):
        raise ValueError("status must be done|skipped")
    conn = get_conn(hub_path)
    try:
        cur = conn.execute(
            "UPDATE commitments SET status = ? WHERE id = ?", (status, commit_id))
        return cur.rowcount > 0
    finally:
        conn.close()


def count_today(hub_path: Path) -> int:
    """Quanti signal creati oggi (UTC) — per il cap giornaliero del sensore."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = get_conn(hub_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM commitments WHERE ts LIKE ?", (today + "%",)).fetchone()
        return int(row["c"])
    finally:
        conn.close()
