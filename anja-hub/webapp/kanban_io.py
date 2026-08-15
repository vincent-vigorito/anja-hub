"""kanban_io.py — Fase 15 — Kanban task layer storage (SQLite).

Database: <hub>/data/kanban.db (workspace principale hub).

Schema:
  tasks:
    id INTEGER PK
    title TEXT NOT NULL
    body TEXT
    status TEXT (triage|todo|ready|running|blocked|done|archived)
    assignee TEXT (es. 'anja', 'anja-finanze', 'human:vincent')
    scope TEXT (es. 'hub', 'workspace:finanze')
    parent_id INTEGER (FK tasks.id, NULL se root)
    priority INTEGER (0-3, default 1)
    tags TEXT (JSON array)
    due_at TEXT (ISO datetime, nullable)
    created_at TEXT NOT NULL
    updated_at TEXT NOT NULL
    completed_at TEXT (nullable)
    block_reason TEXT (nullable)
    metadata TEXT (JSON, free-form)

  task_runs:
    id INTEGER PK
    task_id INTEGER FK
    started_at TEXT NOT NULL
    ended_at TEXT
    output TEXT
    error TEXT
    status TEXT (started|completed|failed)

  task_deps:
    task_id INTEGER FK
    depends_on_id INTEGER FK
    PRIMARY KEY (task_id, depends_on_id)

  task_comments:
    id INTEGER PK
    task_id INTEGER FK
    author TEXT
    content TEXT
    created_at TEXT

Stdlib only (sqlite3).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


VALID_STATUSES = ("triage", "todo", "ready", "running", "blocked", "done", "archived")
DEFAULT_STATUS = "todo"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_path(hub_path: Path) -> Path:
    return hub_path / "data" / "kanban.db"


def get_conn(hub_path: Path) -> sqlite3.Connection:
    p = db_path(hub_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        body TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'todo',
        assignee TEXT DEFAULT '',
        scope TEXT NOT NULL DEFAULT 'hub',
        parent_id INTEGER,
        priority INTEGER DEFAULT 1,
        tags TEXT DEFAULT '[]',
        due_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT,
        block_reason TEXT,
        metadata TEXT DEFAULT '{}',
        FOREIGN KEY (parent_id) REFERENCES tasks(id) ON DELETE SET NULL
    );

    CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
    CREATE INDEX IF NOT EXISTS idx_tasks_scope ON tasks(scope);
    CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee);
    CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id);

    CREATE TABLE IF NOT EXISTS task_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER NOT NULL,
        started_at TEXT NOT NULL,
        ended_at TEXT,
        output TEXT DEFAULT '',
        error TEXT,
        status TEXT NOT NULL DEFAULT 'started',
        FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_runs_task ON task_runs(task_id);

    CREATE TABLE IF NOT EXISTS task_deps (
        task_id INTEGER NOT NULL,
        depends_on_id INTEGER NOT NULL,
        PRIMARY KEY (task_id, depends_on_id),
        FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
        FOREIGN KEY (depends_on_id) REFERENCES tasks(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS task_comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER NOT NULL,
        author TEXT DEFAULT '',
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_comments_task ON task_comments(task_id);
    """)


# ====================================================================
# CRUD: Tasks
# ====================================================================

def _row_to_dict(row) -> dict:
    if row is None:
        return None
    d = dict(row)
    # Parse JSON fields
    try:
        d["tags"] = json.loads(d.get("tags") or "[]")
    except Exception:
        d["tags"] = []
    try:
        d["metadata"] = json.loads(d.get("metadata") or "{}")
    except Exception:
        d["metadata"] = {}
    return d


def create_task(hub_path: Path, *, title: str, body: str = "", status: str = DEFAULT_STATUS,
                 assignee: str = "", scope: str = "hub", parent_id: Optional[int] = None,
                 priority: int = 1, tags: Optional[list] = None,
                 due_at: Optional[str] = None, metadata: Optional[dict] = None) -> dict:
    """Crea task. Ritorna dict del task creato."""
    if not title.strip():
        raise ValueError("title required")
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status}")
    now = _now()
    conn = get_conn(hub_path)
    try:
        cur = conn.execute(
            """INSERT INTO tasks (title, body, status, assignee, scope, parent_id,
                priority, tags, due_at, created_at, updated_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (title.strip(), body or "", status, assignee or "", scope or "hub",
             parent_id, int(priority), json.dumps(tags or []),
             due_at, now, now, json.dumps(metadata or {})),
        )
        task_id = cur.lastrowid
        return get_task(hub_path, task_id)
    finally:
        conn.close()


def get_task(hub_path: Path, task_id: int) -> Optional[dict]:
    conn = get_conn(hub_path)
    try:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return None
        d = _row_to_dict(row)
        # Load deps + comments + runs
        deps = conn.execute("SELECT depends_on_id FROM task_deps WHERE task_id = ?", (task_id,)).fetchall()
        d["depends_on"] = [r[0] for r in deps]
        d["blocks"] = [r[0] for r in conn.execute(
            "SELECT task_id FROM task_deps WHERE depends_on_id = ?", (task_id,)).fetchall()]
        d["comments"] = [_row_to_dict(c) for c in conn.execute(
            "SELECT * FROM task_comments WHERE task_id = ? ORDER BY created_at ASC", (task_id,)).fetchall()]
        d["runs"] = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM task_runs WHERE task_id = ? ORDER BY started_at DESC LIMIT 10", (task_id,)).fetchall()]
        return d
    finally:
        conn.close()


def normalize_workspace_scope(hub_path: Path, scope: Optional[str]) -> Optional[str]:
    """Mappa `project:X` o bare `X` → `workspace:X` quando X è un workspace dell'hub.

    Le card/goal di un workspace sono salvati con scope `workspace:<name>`, ma chat e
    Telegram usano `project:<name>` (o nominano il workspace senza prefisso): senza questa
    normalizzazione la query a scope esatto torna vuota. I project dev esterni (senza
    `<hub>/workspaces/X`) restano invariati. La guardia "/"/".." evita path traversal.
    """
    if not scope:
        return scope
    name = None
    if scope.startswith("project:"):
        name = scope.split(":", 1)[1]
    elif ":" not in scope and scope not in ("hub", "user-global"):
        name = scope
    if name and "/" not in name and ".." not in name and (hub_path / "workspaces" / name).is_dir():
        return f"workspace:{name}"
    return scope


def list_tasks(hub_path: Path, *, scope: Optional[str] = None, status: Optional[str] = None,
               assignee: Optional[str] = None, parent_id: Optional[int] = None,
               linked_goal: Optional[str] = None, due_within_h: Optional[int] = None,
               include_archived: bool = False, limit: int = 200) -> list:
    """Lista task con filtri opzionali. linked_goal filtra per metadata.linked_goal."""
    scope = normalize_workspace_scope(hub_path, scope)  # project:X / X → workspace:X (copre REST + tool MCP)
    conn = get_conn(hub_path)
    try:
        clauses = []
        params: list = []
        if scope:
            clauses.append("scope = ?")
            params.append(scope)
        if status:
            if status == "active":
                clauses.append("status NOT IN ('done', 'archived')")
            else:
                clauses.append("status = ?")
                params.append(status)
        if assignee:
            clauses.append("assignee = ?")
            params.append(assignee)
        if parent_id is not None:
            clauses.append("parent_id = ?")
            params.append(parent_id)
        if due_within_h is not None:
            cutoff = (datetime.now(timezone.utc) + timedelta(hours=int(due_within_h))).isoformat(timespec="seconds")
            clauses.append("due_at IS NOT NULL AND due_at != '' AND due_at <= ?")
            params.append(cutoff)
        if not include_archived and (not status or status not in ("archived", "active")):
            clauses.append("status != 'archived'")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM tasks {where} ORDER BY priority DESC, created_at DESC LIMIT ?"
        params.append(int(limit))
        rows = conn.execute(sql, params).fetchall()
        out = [_row_to_dict(r) for r in rows]
        # Fase 18.C.3 — Post-filter linked_goal (metadata JSON, no native column)
        if linked_goal:
            out = [t for t in out if (t.get("metadata") or {}).get("linked_goal") == linked_goal]
        return out
    finally:
        conn.close()


def update_status(hub_path: Path, task_id: int, status: str,
                   block_reason: Optional[str] = None) -> Optional[dict]:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status}")
    now = _now()
    conn = get_conn(hub_path)
    try:
        completed_at = now if status == "done" else None
        conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ?, "
            "completed_at = COALESCE(?, completed_at), "
            "block_reason = ? WHERE id = ?",
            (status, now, completed_at, block_reason or None, task_id),
        )
        return get_task(hub_path, task_id)
    finally:
        conn.close()


def update_task(hub_path: Path, task_id: int, **fields) -> Optional[dict]:
    """Generic update. fields whitelist: title, body, assignee, scope, priority, tags, due_at, metadata."""
    allowed = {"title", "body", "assignee", "scope", "priority", "tags", "due_at", "metadata"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return get_task(hub_path, task_id)
    set_clauses = []
    params: list = []
    for k, v in updates.items():
        if k in ("tags", "metadata"):
            v = json.dumps(v)
        set_clauses.append(f"{k} = ?")
        params.append(v)
    set_clauses.append("updated_at = ?")
    params.append(_now())
    params.append(task_id)
    conn = get_conn(hub_path)
    try:
        conn.execute(f"UPDATE tasks SET {', '.join(set_clauses)} WHERE id = ?", params)
        return get_task(hub_path, task_id)
    finally:
        conn.close()


def delete_task(hub_path: Path, task_id: int) -> bool:
    conn = get_conn(hub_path)
    try:
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return cur.rowcount > 0
    finally:
        conn.close()


# ====================================================================
# Deps + Comments + Runs
# ====================================================================

def add_dependency(hub_path: Path, task_id: int, depends_on_id: int) -> bool:
    if task_id == depends_on_id:
        return False
    conn = get_conn(hub_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO task_deps (task_id, depends_on_id) VALUES (?, ?)",
            (task_id, depends_on_id),
        )
        return True
    finally:
        conn.close()


def remove_dependency(hub_path: Path, task_id: int, depends_on_id: int) -> bool:
    conn = get_conn(hub_path)
    try:
        cur = conn.execute(
            "DELETE FROM task_deps WHERE task_id = ? AND depends_on_id = ?",
            (task_id, depends_on_id),
        )
        return cur.rowcount > 0
    finally:
        conn.close()


def add_comment(hub_path: Path, task_id: int, content: str, author: str = "") -> dict:
    now = _now()
    conn = get_conn(hub_path)
    try:
        cur = conn.execute(
            "INSERT INTO task_comments (task_id, author, content, created_at) VALUES (?, ?, ?, ?)",
            (task_id, author or "", content, now),
        )
        return {"id": cur.lastrowid, "task_id": task_id, "author": author, "content": content, "created_at": now}
    finally:
        conn.close()


def start_run(hub_path: Path, task_id: int) -> int:
    """Marks task as 'running', creates a run entry. Returns run_id."""
    now = _now()
    conn = get_conn(hub_path)
    try:
        cur = conn.execute(
            "INSERT INTO task_runs (task_id, started_at, status) VALUES (?, ?, 'started')",
            (task_id, now),
        )
        run_id = cur.lastrowid
        conn.execute("UPDATE tasks SET status = 'running', updated_at = ? WHERE id = ?", (now, task_id))
        return run_id
    finally:
        conn.close()


def end_run(hub_path: Path, run_id: int, *, status: str = "completed",
            output: str = "", error: Optional[str] = None) -> None:
    now = _now()
    conn = get_conn(hub_path)
    try:
        conn.execute(
            "UPDATE task_runs SET ended_at = ?, status = ?, output = ?, error = ? WHERE id = ?",
            (now, status, output, error, run_id),
        )
    finally:
        conn.close()


# ====================================================================
# Dispatcher helpers
# ====================================================================

def deps_satisfied(hub_path: Path, task_id: int) -> bool:
    """True se tutti i task da cui dipende sono 'done'."""
    conn = get_conn(hub_path)
    try:
        rows = conn.execute("""
            SELECT t.id, t.status FROM task_deps d
            JOIN tasks t ON t.id = d.depends_on_id
            WHERE d.task_id = ?
        """, (task_id,)).fetchall()
        if not rows:
            return True
        return all(r["status"] == "done" for r in rows)
    finally:
        conn.close()


def auto_promote_ready(hub_path: Path) -> list[int]:
    """Promuove tasks 'todo' a 'ready' se tutti i parent/deps sono done.

    Ritorna lista degli id promossi.
    """
    conn = get_conn(hub_path)
    try:
        todos = conn.execute(
            "SELECT id FROM tasks WHERE status = 'todo'"
        ).fetchall()
        promoted = []
        now = _now()
        for r in todos:
            tid = r["id"]
            if deps_satisfied(hub_path, tid):
                conn.execute(
                    "UPDATE tasks SET status = 'ready', updated_at = ? WHERE id = ?",
                    (now, tid),
                )
                promoted.append(tid)
        return promoted
    finally:
        conn.close()


def stats(hub_path: Path) -> dict:
    """Conteggi per status (per UI badges)."""
    conn = get_conn(hub_path)
    try:
        rows = conn.execute("""
            SELECT status, COUNT(*) as cnt FROM tasks
            WHERE status != 'archived'
            GROUP BY status
        """).fetchall()
        out = {s: 0 for s in VALID_STATUSES}
        for r in rows:
            out[r["status"]] = r["cnt"]
        out["total_active"] = sum(out[s] for s in VALID_STATUSES if s not in ("done", "archived"))
        return out
    finally:
        conn.close()


def find_similar_active(hub_path: Path, scope: str, title: str,
                         linked_goal: Optional[str] = None,
                         threshold: float = 0.75) -> list:
    """Trova task attivi (non done/archived) con titolo simile per dedup auto:judge.

    Normalizza i titoli (lower, alphanum-only, prime 80 chars) e usa SequenceMatcher.ratio().
    Filtra opzionalmente per metadata.linked_goal. Ritorna lista di task matching ordinati per ratio desc.
    """
    import re as _re
    from difflib import SequenceMatcher as _SM

    def _norm(s: str) -> str:
        s = (s or "").lower()
        s = _re.sub(r"[^a-z0-9 ]+", " ", s)
        s = _re.sub(r"\s+", " ", s).strip()
        return s[:80]

    target = _norm(title)
    if not target:
        return []
    candidates = list_tasks(hub_path, scope=scope, status="active",
                             linked_goal=linked_goal, include_archived=False, limit=300)
    matches = []
    for t in candidates:
        cand = _norm(t.get("title", ""))
        if not cand:
            continue
        ratio = _SM(None, target, cand).ratio()
        if ratio >= threshold:
            matches.append((ratio, t))
    matches.sort(key=lambda x: x[0], reverse=True)
    return [{"ratio": round(r, 3), **t} for r, t in matches]


def search_tasks(hub_path: Path, query: str, limit: int = 30) -> list:
    """Ricerca full-text in title+body."""
    if not query.strip():
        return []
    q = f"%{query.strip()}%"
    conn = get_conn(hub_path)
    try:
        rows = conn.execute("""
            SELECT * FROM tasks
            WHERE (title LIKE ? OR body LIKE ?) AND status != 'archived'
            ORDER BY priority DESC, created_at DESC LIMIT ?
        """, (q, q, int(limit))).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


if __name__ == "__main__":
    # Smoke test
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        hub = Path(td)
        # Create some tasks
        t1 = create_task(hub, title="Genera report Q1", scope="workspace:finanze",
                          assignee="anja-finanze", priority=2)
        t2 = create_task(hub, title="Verifica P/L", scope="workspace:finanze",
                          assignee="anja-finanze", parent_id=t1["id"])
        add_dependency(hub, t2["id"], t1["id"])
        add_comment(hub, t1["id"], "Started today", author="anja")

        print("All tasks:", json.dumps(list_tasks(hub), indent=2, ensure_ascii=False))
        print("\nStats:", stats(hub))
        print("\nPromote ready:", auto_promote_ready(hub))
        update_status(hub, t1["id"], "done")
        print("\nAfter t1 done, promote:", auto_promote_ready(hub))
        print("\nt2 after promote:", get_task(hub, t2["id"])["status"])
