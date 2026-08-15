"""notification_bus.py — F-Notify-1 — Notification Bus centrale.

Pub/sub unificato per eventi cross-componente (goals, routines, kanban, chat,
scripts, telegram, mcp, webapp). Persistenza SQLite + broadcast SSE in-process.

DB: <hub>/data/notifications.db (WAL, autocommit, indexed su ts+read+source).

Schema notifica:
  id INTEGER PK
  ts TEXT ISO 8601 UTC
  source TEXT (goal|routine|kanban|chat|script|daemon|telegram|mcp|webapp|...)
  category TEXT (info|success|warn|error|action_needed)
  severity INTEGER 0..4 (info=0 success=1 warn=2 error=3 action_needed=4)
  title TEXT
  body TEXT
  action TEXT (JSON {label, url, type})
  payload TEXT (JSON free-form)
  read INTEGER 0|1
  scope TEXT (hub|workspace:<name>)

Stdlib only (sqlite3 + asyncio).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


VALID_CATEGORIES = ("info", "success", "warn", "error", "action_needed")
CATEGORY_SEVERITY = {
    "info": 0,
    "success": 1,
    "warn": 2,
    "error": 3,
    "action_needed": 4,
}


# ============================================================
# Storage
# ============================================================

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_path(hub_path: Path) -> Path:
    return hub_path / "data" / "notifications.db"


def get_conn(hub_path: Path) -> sqlite3.Connection:
    p = db_path(hub_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        source TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'info',
        severity INTEGER NOT NULL DEFAULT 0,
        title TEXT NOT NULL,
        body TEXT DEFAULT '',
        action TEXT,
        payload TEXT,
        read INTEGER NOT NULL DEFAULT 0,
        scope TEXT NOT NULL DEFAULT 'hub'
    );
    CREATE INDEX IF NOT EXISTS idx_notif_ts ON notifications(ts DESC);
    CREATE INDEX IF NOT EXISTS idx_notif_read ON notifications(read);
    CREATE INDEX IF NOT EXISTS idx_notif_source ON notifications(source);
    CREATE INDEX IF NOT EXISTS idx_notif_scope ON notifications(scope);
    """)


def _row_to_dict(row) -> dict:
    if row is None:
        return None
    d = dict(row)
    for k in ("action", "payload"):
        raw = d.get(k)
        if raw:
            try:
                d[k] = json.loads(raw)
            except Exception:
                d[k] = None
        else:
            d[k] = None
    d["read"] = bool(d.get("read"))
    return d


# ============================================================
# In-memory pub/sub for SSE subscribers (process-level)
# ============================================================

# Each subscriber is an asyncio.Queue. Bounded to prevent DoS by slow client.
_subscribers: "set[asyncio.Queue]" = set()
_SUBSCRIBER_BUFFER_SIZE = 100
# Tracks max id pubblicato same-process: serve al db_poller per saltare quelli
# già broadcastati e ribroadcastare solo notifiche scritte da process esterni.
_last_local_id: int = 0


def subscribe() -> asyncio.Queue:
    """Register new subscriber. Returns its queue (bounded)."""
    q: asyncio.Queue = asyncio.Queue(maxsize=_SUBSCRIBER_BUFFER_SIZE)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def _broadcast(event: dict) -> None:
    """Push event to all subscribers. Drops on full queue (slow consumer)."""
    if not _subscribers:
        return
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            # Slow consumer: drop event for that subscriber, don't block others.
            pass


async def db_poller_loop(hub_path: Path, interval: float = 3.0) -> None:
    """Background task: scopre notifiche scritte cross-process (es. routines
    runner daemon) e le ribroadcasta agli SSE subscribers same-process.

    Idempotente: tiene traccia di `last_seen_id` e legge solo > last_seen_id.
    Le notifiche pubblicate same-process via publish() arrivano già nelle queue,
    quindi quelle riapparse qui sarebbero duplicate — questo è OK perché il
    polling-aware client può deduplicare per id, ma per evitare overhead
    inizializziamo last_seen_id al max corrente e amplifichiamo solo il delta.
    """
    global _last_local_id
    last_seen_id = 0
    try:
        latest = list_notifications(hub_path, limit=1)
        if latest:
            last_seen_id = latest[0]["id"]
            _last_local_id = max(_last_local_id, last_seen_id)
    except Exception:
        pass

    while True:
        try:
            await asyncio.sleep(interval)
            if not _subscribers:
                continue
            # Use _last_local_id come watermark: skippa quelle già broadcastate
            # in-process via publish(); recupera solo cross-process writes.
            new_items = list_notifications(hub_path, since_id=_last_local_id, limit=200)
            if new_items:
                for ev in reversed(new_items):
                    _broadcast(ev)
                    if ev["id"] > _last_local_id:
                        _last_local_id = ev["id"]
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[notif_bus] db_poller error: {e}")


# ============================================================
# Public API
# ============================================================

def publish(hub_path: Path, *, source: str, title: str,
            category: str = "info", body: str = "",
            action: Optional[dict] = None, payload: Optional[dict] = None,
            scope: str = "hub") -> dict:
    """Pubblica notifica: persist SQLite + broadcast in-memory subscribers.

    Ritorna il dict della notifica creata (con id assegnato).
    Safe to call from sync context — broadcast non blocca.
    """
    if not source or not title:
        raise ValueError("source and title required")
    if category not in VALID_CATEGORIES:
        raise ValueError(f"invalid category: {category}")
    severity = CATEGORY_SEVERITY[category]
    ts = _now()
    action_json = json.dumps(action) if action else None
    payload_json = json.dumps(payload) if payload else None
    conn = get_conn(hub_path)
    try:
        cur = conn.execute(
            """INSERT INTO notifications
               (ts, source, category, severity, title, body, action, payload, read, scope)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (ts, source, category, severity, title, body or "",
             action_json, payload_json, scope or "hub"),
        )
        nid = cur.lastrowid
    finally:
        conn.close()
    global _last_local_id
    if nid > _last_local_id:
        _last_local_id = nid
    notif = {
        "id": nid, "ts": ts, "source": source, "category": category,
        "severity": severity, "title": title, "body": body or "",
        "action": action, "payload": payload, "read": False,
        "scope": scope or "hub",
    }
    _broadcast(notif)
    return notif


def list_notifications(hub_path: Path, *, unread_only: bool = False,
                       source: Optional[str] = None,
                       category: Optional[str] = None,
                       min_severity: Optional[int] = None,
                       scope: Optional[str] = None,
                       since_id: Optional[int] = None,
                       limit: int = 50) -> list:
    """Lista notifiche con filtri. Ordinata per ts DESC."""
    clauses = []
    params: list = []
    if unread_only:
        clauses.append("read = 0")
    if source:
        clauses.append("source = ?")
        params.append(source)
    if category:
        clauses.append("category = ?")
        params.append(category)
    if min_severity is not None:
        clauses.append("severity >= ?")
        params.append(int(min_severity))
    if scope:
        clauses.append("scope = ?")
        params.append(scope)
    if since_id is not None:
        clauses.append("id > ?")
        params.append(int(since_id))
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT * FROM notifications {where} ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    conn = get_conn(hub_path)
    try:
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def count_unread(hub_path: Path, *, scope: Optional[str] = None) -> int:
    conn = get_conn(hub_path)
    try:
        if scope:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM notifications WHERE read = 0 AND scope = ?",
                (scope,)).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM notifications WHERE read = 0").fetchone()
        return int(row["c"])
    finally:
        conn.close()


def mark_read(hub_path: Path, notif_id: int) -> bool:
    conn = get_conn(hub_path)
    try:
        cur = conn.execute(
            "UPDATE notifications SET read = 1 WHERE id = ?", (notif_id,))
        return cur.rowcount > 0
    finally:
        conn.close()


def mark_all_read(hub_path: Path, *, scope: Optional[str] = None) -> int:
    conn = get_conn(hub_path)
    try:
        if scope:
            cur = conn.execute(
                "UPDATE notifications SET read = 1 WHERE read = 0 AND scope = ?",
                (scope,))
        else:
            cur = conn.execute(
                "UPDATE notifications SET read = 1 WHERE read = 0")
        return cur.rowcount
    finally:
        conn.close()


def delete_notification(hub_path: Path, notif_id: int) -> bool:
    conn = get_conn(hub_path)
    try:
        cur = conn.execute("DELETE FROM notifications WHERE id = ?", (notif_id,))
        return cur.rowcount > 0
    finally:
        conn.close()


def cleanup(hub_path: Path, *, older_than_days: int = 30,
            keep_unread: bool = True) -> int:
    """Elimina notifiche più vecchie di `older_than_days`. Se keep_unread=True,
    salva quelle ancora non lette. Ritorna numero righe rimosse."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat(timespec="seconds")
    conn = get_conn(hub_path)
    try:
        if keep_unread:
            cur = conn.execute(
                "DELETE FROM notifications WHERE ts < ? AND read = 1", (cutoff,))
        else:
            cur = conn.execute(
                "DELETE FROM notifications WHERE ts < ?", (cutoff,))
        return cur.rowcount
    finally:
        conn.close()


def stats(hub_path: Path) -> dict:
    """Aggregato per source + category, comodo per UI badge globali."""
    conn = get_conn(hub_path)
    try:
        by_source = {r["source"]: r["c"] for r in conn.execute(
            "SELECT source, COUNT(*) AS c FROM notifications WHERE read = 0 GROUP BY source"
        ).fetchall()}
        by_category = {r["category"]: r["c"] for r in conn.execute(
            "SELECT category, COUNT(*) AS c FROM notifications WHERE read = 0 GROUP BY category"
        ).fetchall()}
        total_unread = sum(by_source.values())
        return {
            "total_unread": total_unread,
            "by_source": by_source,
            "by_category": by_category,
            "subscribers": len(_subscribers),
        }
    finally:
        conn.close()


if __name__ == "__main__":
    # Smoke test
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        hub = Path(td)
        n1 = publish(hub, source="goal", title="Pipeline drift on paper-review-q1",
                     category="warn", body="3rd consecutive verdict=drift",
                     action={"label": "View goal", "url": "/goals/x", "type": "navigate"},
                     payload={"goal_id": "paper-review-q1"}, scope="workspace:research")
        n2 = publish(hub, source="routine", title="news-arxiv done",
                     category="success", body="3.2s, 12 items ingested")
        n3 = publish(hub, source="script", title="agent_loop crashed",
                     category="error", body="OOM")
        assert count_unread(hub) == 3
        assert mark_read(hub, n1["id"]) is True
        assert count_unread(hub) == 2
        assert len(list_notifications(hub, unread_only=True)) == 2
        assert len(list_notifications(hub, min_severity=3)) == 1  # only error
        print("smoke OK:", stats(hub))
