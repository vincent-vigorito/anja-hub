"""Decision trail (M-DecisionTrail): il "perché" delle azioni autonome.

Ogni decisione proattiva (steward crea task, judge promuove memoria, coding worker
verifica, heartbeat consegna, commitment estrae) registra un record strutturato
{trigger, decision, rationale, alternative, confidence}. L'activity widget mostra
COSA è successo; questo mostra PERCHÉ — l'abilitatore per fidarsi ad alzare l'autonomia.

SQLite WAL, stesso pattern di notification_bus/cost_store. `record()` è best-effort
(non solleva: tracciare non deve rompere il path decisionale).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ACTORS = ("steward", "judge", "goal_l3", "coding", "heartbeat", "commitment", "dialectic", "other")


def db_path(hub_path: Path) -> Path:
    return Path(hub_path) / "data" / "decisions.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_conn(hub_path: Path) -> sqlite3.Connection:
    p = db_path(hub_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            actor TEXT NOT NULL DEFAULT 'other',
            trigger TEXT NOT NULL DEFAULT '',
            decision TEXT NOT NULL DEFAULT '',
            rationale TEXT NOT NULL DEFAULT '',
            alternative TEXT NOT NULL DEFAULT '',
            confidence REAL,
            scope TEXT NOT NULL DEFAULT 'hub',
            ref TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dec_ts ON decisions(ts DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dec_actor ON decisions(actor)")
    return conn


def record(hub_path, *, actor: str, trigger: str = "", decision: str = "",
           rationale: str = "", alternative: str = "", confidence: Optional[float] = None,
           scope: str = "hub", ref: str = "") -> dict:
    """Registra una decisione autonoma. Best-effort, non solleva."""
    try:
        rec = {
            "ts": _now(), "actor": actor or "other", "trigger": (trigger or "")[:500],
            "decision": (decision or "")[:500], "rationale": (rationale or "")[:1000],
            "alternative": (alternative or "")[:500],
            "confidence": float(confidence) if confidence is not None else None,
            "scope": scope or "hub", "ref": ref or "",
        }
        conn = get_conn(hub_path)
        conn.execute(
            "INSERT INTO decisions (ts,actor,trigger,decision,rationale,alternative,confidence,scope,ref) "
            "VALUES (:ts,:actor,:trigger,:decision,:rationale,:alternative,:confidence,:scope,:ref)", rec)
        conn.close()
        return rec
    except Exception:
        return {}


def recent(hub_path, limit: int = 100, actor: Optional[str] = None) -> list:
    try:
        conn = get_conn(hub_path)
        if actor:
            rows = conn.execute("SELECT * FROM decisions WHERE actor=? ORDER BY ts DESC, id DESC LIMIT ?",
                                (actor, int(limit))).fetchall()
        else:
            rows = conn.execute("SELECT * FROM decisions ORDER BY ts DESC, id DESC LIMIT ?",
                                (int(limit),)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def stats(hub_path) -> dict:
    try:
        conn = get_conn(hub_path)
        by_actor = [dict(r) for r in conn.execute(
            "SELECT actor, COUNT(*) AS n, AVG(confidence) AS avg_conf FROM decisions GROUP BY actor ORDER BY n DESC")]
        total = conn.execute("SELECT COUNT(*) AS n FROM decisions").fetchone()["n"]
        conn.close()
        return {"total": total, "by_actor": by_actor}
    except Exception:
        return {"total": 0, "by_actor": []}
