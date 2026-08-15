"""goal_io.py — CRUD markdown for anja Goals (Fase 18.A).

Filosofia file-first: ogni goal è una dir markdown sotto:
  <hub>/goals/<slug>/                          (scope=hub, meta-goals)
  <workspace>/.anjawiki/goals/<slug>/           (scope=workspace:<name>)

Struttura:
  goals/<slug>/
  ├── goal.md          # frontmatter YAML + body markdown
  ├── journal.md       # verdetti judge datati (append-only)
  └── reflections.md   # post-mortem / pivot notes (manuali)

Stdlib only.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional


VALID_STATUSES = ["active", "achieved", "abandoned", "paused", "failed"]
VALID_PRIORITIES = ["low", "medium", "high"]
VALID_VERDICTS = ["on_track", "drift", "blocked", "achieved", "failed"]

# Phase A — Autonomy levels
# L0 Observer:   solo legge + journal/briefing. NO side-effect.
# L1 Advisor:    L0 + auto-kanban + proposed_edits in inbox. NO execution.
# L2 Gated:      L1 + pending_actions queue → approval Telegram/UI → execute
# L3 Autonomous: esegue da solo entro budget. Killswitch su drift consecutivi.
VALID_AUTONOMY_LEVELS = [0, 1, 2, 3]
AUTONOMY_DEFAULT = 1


# ============================================================
# Path resolution
# ============================================================

def goals_root(hub_path: Path, scope: str = "hub") -> Path:
    """Dove vivono i goal per uno scope dato.

    scope='hub'                 → <hub>/goals/
    scope='workspace:<name>'    → <hub>/workspaces/<name>/.anjawiki/goals/
    """
    if scope == "hub":
        return hub_path / "goals"
    if scope.startswith("workspace:"):
        ws = scope.split(":", 1)[1]
        return hub_path / "workspaces" / ws / ".anjawiki" / "goals"
    raise ValueError(f"unsupported scope: {scope}")


def goal_dir(hub_path: Path, scope: str, goal_id: str) -> Path:
    return goals_root(hub_path, scope) / goal_id


def slugify(title: str) -> str:
    s = re.sub(r"[^\w\s-]", "", title.lower(), flags=re.UNICODE)
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s[:60] or f"goal-{int(time.time())}"


# ============================================================
# Frontmatter parse/write
# ============================================================

_FM_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Estrai frontmatter YAML semplice (key: value, listas inline). Return (meta, body)."""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    meta_raw, body = m.group(1), m.group(2)
    meta: dict = {}
    cur_list_key: Optional[str] = None
    for raw in meta_raw.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            cur_list_key = None
            continue
        if cur_list_key and line.startswith("  - "):
            meta.setdefault(cur_list_key, []).append(line[4:].strip())
            continue
        if ":" not in line:
            cur_list_key = None
            continue
        cur_list_key = None
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if not val:
            cur_list_key = key
            meta[key] = []
            continue
        if val.startswith("{") and val.endswith("}"):
            # M1 — JSON dict inline (responsabile_llm, escalation_llm)
            try:
                meta[key] = json.loads(val)
                continue
            except Exception:
                pass
        if val.startswith("[") and val.endswith("]"):
            # Try JSON parse first (handles list of dicts: assigned_agents, etc.)
            try:
                parsed = json.loads(val)
                meta[key] = parsed
                continue
            except Exception:
                pass
            inner = val[1:-1].strip()
            if not inner:
                meta[key] = []
            else:
                meta[key] = [x.strip().strip('"').strip("'") for x in inner.split(",")]
            continue
        if val.lower() in ("true", "false"):
            meta[key] = val.lower() == "true"
            continue
        if re.match(r"^-?\d+$", val):
            meta[key] = int(val)
            continue
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            meta[key] = val[1:-1]
            continue
        meta[key] = val
    return meta, body


def dump_frontmatter(meta: dict) -> str:
    """Dump dict come YAML frontmatter (minimal style)."""
    lines = ["---"]
    for k, v in meta.items():
        if v is None:
            continue
        if isinstance(v, bool):
            lines.append(f"{k}: {str(v).lower()}")
        elif isinstance(v, (int, float)):
            lines.append(f"{k}: {v}")
        elif isinstance(v, list):
            if not v:
                lines.append(f"{k}: []")
            elif all(isinstance(x, str) for x in v):
                lines.append(f"{k}:")
                for item in v:
                    lines.append(f"  - {item}")
            else:
                # List di dict o misto → JSON inline (preserva struttura)
                lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
        elif isinstance(v, str):
            # Escape se contiene char speciali
            if any(c in v for c in [":", "#", "[", "]", "{", "}", '"', "'"]) or v.startswith(" ") or v.endswith(" "):
                lines.append(f'{k}: "{v.replace(chr(34), chr(92)+chr(34))}"')
            else:
                lines.append(f"{k}: {v}")
        else:
            lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


# ============================================================
# CRUD
# ============================================================

def create_goal(hub_path: Path, scope: str, title: str, *,
                deadline: Optional[str] = None,
                priority: str = "medium",
                responsabile: Optional[str] = None,
                success_criteria: Optional[list[str]] = None,
                judge_cron: str = "0 18 * * 0",
                judge_model: Optional[str] = None,
                judge_provider: Optional[str] = None,
                judge_effort: Optional[str] = None,  # M1 — per-role LLM config
                judge_agent: Optional[str] = None,   # M1 — agent dedicato per il judge (default: il responsabile)
                responsabile_llm: Optional[dict] = None,  # M1 — {provider, model, effort}
                escalation_llm: Optional[dict] = None,    # M1 — LLM del CEO
                body_md: str = "",
                anti_patterns: Optional[list[str]] = None,  # M1 — cose da NON fare
                judge_rubric: str = "",                      # M1 — "come il judge deve valutare" instructions
                tags: Optional[list[str]] = None,
                owner: str = "vincent",
                assigned_agents: Optional[list[dict]] = None,
                escalation_to: Optional[str] = None,
                escalation_trigger: str = "drift_consecutive_3",
                # Phase A — Autonomy & pipeline cron
                autonomy_level: int = AUTONOMY_DEFAULT,
                pipeline_cron: str = "",
                execution_budget: Optional[dict] = None) -> dict:
    """Crea nuovo goal. Ritorna {id, path, scope}.

    assigned_agents: list di {role, agent, cadence?, inputs?}
    escalation_to: agent target (es. 'anja') se trigger soddisfatto.
    """
    root = goals_root(hub_path, scope)
    root.mkdir(parents=True, exist_ok=True)
    gid = slugify(title)
    # Disambiguate se esiste
    base = gid
    i = 2
    while (root / gid).exists():
        gid = f"{base}-{i}"
        i += 1

    gdir = root / gid
    gdir.mkdir(parents=True)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    meta = {
        "id": gid,
        "title": title,
        "scope": scope,
        "created": today,
        "deadline": deadline or "",
        "status": "active",
        "priority": priority if priority in VALID_PRIORITIES else "medium",
        "owner": owner,
        "responsabile": responsabile or "",
        # M1 — per-role LLM config (dict {provider, model, effort})
        "responsabile_llm": responsabile_llm or {},
        "success_criteria": success_criteria or [],
        # M1 — anti-pattern + judge rubric (lifted dal body per essere queryable)
        "anti_patterns": anti_patterns or [],
        "judge_rubric": judge_rubric or "",
        # Judge config
        "judge_agent": judge_agent or "",  # se vuoto → usa responsabile
        "judge_cron": judge_cron,
        "judge_model": judge_model or "",
        "judge_provider": judge_provider or "",
        "judge_effort": judge_effort or "",
        # Fase 18.B — Team hierarchy
        "assigned_agents": assigned_agents or [],
        "escalation_to": escalation_to or "",
        "escalation_llm": escalation_llm or {},
        "escalation_trigger": escalation_trigger,
        "escalated": False,
        # Phase A — Autonomy & pipeline cron
        "autonomy_level": autonomy_level if autonomy_level in VALID_AUTONOMY_LEVELS else AUTONOMY_DEFAULT,
        "pipeline_cron": pipeline_cron or "",
        "execution_budget": execution_budget or {},
        "linked_tasks": [],
        "linked_routines": [],
        "tags": tags or [],
    }
    goal_md = dump_frontmatter(meta) + "\n# " + title + "\n\n" + (body_md or "")
    (gdir / "goal.md").write_text(goal_md, encoding="utf-8")
    (gdir / "journal.md").write_text(f"# Journal — {title}\n\n", encoding="utf-8")
    (gdir / "reflections.md").write_text(f"# Reflections — {title}\n\n", encoding="utf-8")
    _update_index(hub_path, scope)
    return {"id": gid, "path": str(gdir), "scope": scope}


def read_goal(hub_path: Path, scope: str, goal_id: str) -> Optional[dict]:
    """Read goal completo: meta + body + journal entries + reflections."""
    gdir = goal_dir(hub_path, scope, goal_id)
    goal_md_file = gdir / "goal.md"
    if not goal_md_file.is_file():
        return None
    meta, body = parse_frontmatter(goal_md_file.read_text(encoding="utf-8"))
    journal_file = gdir / "journal.md"
    journal_text = journal_file.read_text(encoding="utf-8") if journal_file.is_file() else ""
    reflections_file = gdir / "reflections.md"
    reflections_text = reflections_file.read_text(encoding="utf-8") if reflections_file.is_file() else ""
    return {
        "meta": meta,
        "body": body,
        "journal": journal_text,
        "journal_entries": _parse_journal_entries(journal_text),
        "reflections": reflections_text,
        "path": str(gdir),
    }


def list_goals(hub_path: Path, scope: Optional[str] = None,
               status: Optional[str] = None) -> list[dict]:
    """Lista goal. Se scope=None → tutti scopes (hub + workspaces). Filtra per status."""
    scopes_to_scan: list[str] = []
    if scope:
        scopes_to_scan = [scope]
    else:
        # Hub + all workspaces
        scopes_to_scan = ["hub"]
        ws_root = hub_path / "workspaces"
        if ws_root.is_dir():
            for ws_dir in sorted(ws_root.iterdir()):
                if ws_dir.is_dir():
                    scopes_to_scan.append(f"workspace:{ws_dir.name}")
    out = []
    for sc in scopes_to_scan:
        root = goals_root(hub_path, sc)
        if not root.is_dir():
            continue
        for gdir in sorted(root.iterdir()):
            if not gdir.is_dir():
                continue
            gmd = gdir / "goal.md"
            if not gmd.is_file():
                continue
            try:
                meta, _ = parse_frontmatter(gmd.read_text(encoding="utf-8"))
                if status and meta.get("status") != status:
                    continue
                # 1-liner status: last verdict if any
                journal_text = (gdir / "journal.md").read_text(encoding="utf-8") if (gdir / "journal.md").is_file() else ""
                last_verdict = _last_verdict(journal_text)
                out.append({
                    "id": meta.get("id", gdir.name),
                    "title": meta.get("title", ""),
                    "scope": meta.get("scope", sc),
                    "status": meta.get("status", "active"),
                    "priority": meta.get("priority", "medium"),
                    "deadline": meta.get("deadline", ""),
                    "responsabile": meta.get("responsabile", ""),
                    "judge_cron": meta.get("judge_cron", ""),
                    # Phase A — required by scheduler routing + UI
                    "pipeline_cron": meta.get("pipeline_cron", ""),
                    "autonomy_level": meta.get("autonomy_level", 1),
                    "assigned_agents": meta.get("assigned_agents", []),
                    "execution_budget": meta.get("execution_budget", {}),
                    "last_verdict": last_verdict,
                    "tags": meta.get("tags", []),
                    "path": str(gdir),
                })
            except Exception:
                continue
    return out


def update_goal(hub_path: Path, scope: str, goal_id: str, updates: dict) -> Optional[dict]:
    """Update fields in frontmatter. Whitelisted keys."""
    allowed = {
        "title", "deadline", "status", "priority", "responsabile",
        "success_criteria", "judge_cron", "judge_model", "judge_provider", "judge_effort",
        "tags", "linked_tasks", "linked_routines",
        # Fase 18.B
        "assigned_agents", "escalation_to", "escalation_trigger", "escalated",
        # M1 — extended schema
        "responsabile_llm", "escalation_llm", "judge_agent",
        "anti_patterns", "judge_rubric",
        # Phase A — Autonomy + pipeline
        "autonomy_level", "pipeline_cron", "execution_budget",
    }
    gdir = goal_dir(hub_path, scope, goal_id)
    gmd = gdir / "goal.md"
    if not gmd.is_file():
        return None
    meta, body = parse_frontmatter(gmd.read_text(encoding="utf-8"))
    changed = False
    rejected_fields = []
    # Phase A — cron sanitization
    cron_fields = {"judge_cron", "pipeline_cron"}
    for k, v in (updates or {}).items():
        if k not in allowed:
            rejected_fields.append(f"{k}: not whitelisted")
            continue
        # Validate cron expressions (best-effort, requires croniter)
        if k in cron_fields and v:
            try:
                from croniter import croniter
                # croniter parses 5-field standard cron
                if not croniter.is_valid(str(v).strip()):
                    rejected_fields.append(f"{k}: invalid cron '{v}'")
                    continue
            except ImportError:
                pass
            except Exception as _e:
                rejected_fields.append(f"{k}: cron parse error: {_e}")
                continue
        # Validate autonomy_level range
        if k == "autonomy_level":
            try:
                iv = int(v)
                if iv not in VALID_AUTONOMY_LEVELS:
                    rejected_fields.append(f"autonomy_level: out of range {VALID_AUTONOMY_LEVELS}")
                    continue
                v = iv
            except Exception:
                rejected_fields.append(f"autonomy_level: not integer")
                continue
        if meta.get(k) != v:
            meta[k] = v
            changed = True
    if changed:
        meta["updated"] = datetime.utcnow().strftime("%Y-%m-%d")
        gmd.write_text(dump_frontmatter(meta) + body, encoding="utf-8")
    return {"id": goal_id, "scope": scope, "updated": changed, "meta": meta, "rejected_fields": rejected_fields}


def archive_goal(hub_path: Path, scope: str, goal_id: str, outcome: str,
                 reflection: str = "") -> Optional[dict]:
    """Marca status (achieved/abandoned/failed) e scrive reflections."""
    if outcome not in ("achieved", "abandoned", "failed"):
        return {"error": f"invalid outcome: {outcome}"}
    res = update_goal(hub_path, scope, goal_id, {"status": outcome})
    if not res:
        return None
    if reflection:
        append_reflection(hub_path, scope, goal_id, reflection)
    return {"id": goal_id, "scope": scope, "status": outcome}


# ============================================================
# M3 — Activity log (append-only JSONL)
# ============================================================

def activity_log_path(hub_path: Path, scope: str, goal_id: str) -> Path:
    return goal_dir(hub_path, scope, goal_id) / "activity.jsonl"


def append_activity(hub_path: Path, scope: str, goal_id: str, event: dict) -> Optional[dict]:
    """Append un evento all'activity.jsonl del goal.

    event campi standard: {ts, agent, level, event_type, msg, payload?}
    Restituisce l'evento normalizzato (con ts auto-generato se assente).
    """
    gdir = goal_dir(hub_path, scope, goal_id)
    if not gdir.is_dir():
        return None
    p = gdir / "activity.jsonl"
    normalized = {
        "ts":         event.get("ts") or datetime.utcnow().strftime("%H:%M:%S"),
        "ts_iso":     event.get("ts_iso") or datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "agent":      event.get("agent") or "system",
        "level":      event.get("level") or "info",
        "event_type": event.get("event_type") or "log",
        "msg":        event.get("msg") or "",
    }
    if "payload" in event:
        normalized["payload"] = event["payload"]
    try:
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(normalized, ensure_ascii=False) + "\n")
        return normalized
    except Exception:
        return None


def read_activity(hub_path: Path, scope: str, goal_id: str, since_ts: str = "",
                  limit: int = 500) -> list:
    """Leggi gli ultimi N eventi. Se since_ts (ISO) passed, solo eventi successivi."""
    p = activity_log_path(hub_path, scope, goal_id)
    if not p.is_file():
        return []
    events = []
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if since_ts and (ev.get("ts_iso") or "") <= since_ts:
                    continue
                events.append(ev)
    except Exception:
        return []
    return events[-limit:]


# ============================================================
# Phase B — Pending actions (L2 gated execution queue)
# ============================================================

def pending_actions_path(hub_path: Path, scope: str, goal_id: str) -> Path:
    return goal_dir(hub_path, scope, goal_id) / "pending_actions.jsonl"


def write_pending_action(hub_path: Path, scope: str, goal_id: str, *,
                        agent: str, action_type: str, payload: dict,
                        expires_in_min: int = 30,
                        rationale: str = "") -> Optional[dict]:
    """Crea pending action e la appende a pending_actions.jsonl.

    type: free-form action label decisa dal judge (es. 'send_email',
        'create_doc', 'publish_post', ...). Serve come tag user-facing
        e per il budget filtering.
    payload: dict strutturato. Per esecuzione L3 deve contenere
        {mcp_server, mcp_tool, args} così l'executor sa quale tool MCP invocare.
    """
    gdir = goal_dir(hub_path, scope, goal_id)
    if not gdir.is_dir():
        return None
    import secrets as _s
    now = datetime.utcnow()
    aid = f"act_{int(now.timestamp())}_{_s.token_hex(3)}"
    expires_at = (now.timestamp() + expires_in_min * 60)
    record = {
        "id": aid,
        "goal_id": goal_id,
        "scope": scope,
        "agent": agent,
        "type": action_type,
        "payload": payload or {},
        "rationale": rationale or "",
        "status": "pending",
        "created_at": now.isoformat(timespec="seconds") + "Z",
        "expires_at_ts": expires_at,
        "expires_in_min": expires_in_min,
        "resolved_at": None,
        "resolved_by": None,
        "resolution_note": "",
        "telegram_message_id": None,
    }
    p = pending_actions_path(hub_path, scope, goal_id)
    try:
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        return None
    try:
        import notification_bus as _nb
        _nb.publish(
            hub_path,
            source="goal",
            category="action_needed",
            title=f"Pending action: {action_type}",
            body=(rationale or "")[:300] or f"Goal {goal_id} richiede approval",
            action={"label": "Review", "url": f"/goals/{scope}/{goal_id}#pending", "type": "navigate"},
            payload={"action_id": aid, "goal_id": goal_id, "scope": scope,
                     "type": action_type, "expires_in_min": expires_in_min},
            scope=scope if scope.startswith("workspace:") else "hub",
        )
    except Exception:
        pass
    return record


def _read_all_pending_records(hub_path: Path, scope: str, goal_id: str) -> list:
    p = pending_actions_path(hub_path, scope, goal_id)
    if not p.is_file():
        return []
    records: dict = {}  # by id (later writes override = idempotente per resolve)
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    if r.get("id"):
                        records[r["id"]] = r
                except Exception:
                    continue
    except Exception:
        return []
    return list(records.values())


def list_pending_actions(hub_path: Path, scope: str, goal_id: str,
                         status: Optional[str] = "pending",
                         expire_old: bool = True) -> list:
    """Lista actions. Se expire_old=True, marca come 'expired' quelle scadute."""
    records = _read_all_pending_records(hub_path, scope, goal_id)
    if expire_old:
        now_ts = datetime.utcnow().timestamp()
        for r in records:
            if r.get("status") == "pending" and r.get("expires_at_ts", 0) < now_ts:
                resolve_pending_action(hub_path, scope, goal_id, r["id"],
                                       resolution="expired", note="auto-expired by timeout",
                                       by="system")
                r["status"] = "expired"
                r["resolved_by"] = "system"
                r["resolution_note"] = "auto-expired by timeout"
    if status:
        records = [r for r in records if r.get("status") == status]
    return sorted(records, key=lambda r: r.get("created_at", ""), reverse=True)


def resolve_pending_action(hub_path: Path, scope: str, goal_id: str,
                          action_id: str, *, resolution: str,
                          note: str = "", by: str = "user") -> Optional[dict]:
    """Resolve action: status diventa approved/rejected/expired/executed.

    Append-only: scrive un nuovo record con stesso ID + status aggiornato.
    """
    records = _read_all_pending_records(hub_path, scope, goal_id)
    found = next((r for r in records if r.get("id") == action_id), None)
    if not found:
        return None
    updated = dict(found)
    updated["status"] = resolution
    updated["resolved_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    updated["resolved_by"] = by
    updated["resolution_note"] = note
    p = pending_actions_path(hub_path, scope, goal_id)
    try:
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(updated, ensure_ascii=False) + "\n")
        return updated
    except Exception:
        return None


def get_pending_action(hub_path: Path, scope: str, goal_id: str, action_id: str) -> Optional[dict]:
    records = _read_all_pending_records(hub_path, scope, goal_id)
    return next((r for r in records if r.get("id") == action_id), None)


def set_pending_telegram_message(hub_path: Path, scope: str, goal_id: str,
                                action_id: str, message_id: int, chat_id: int) -> bool:
    """Marca un'action come notificata su Telegram per evitare doppi send."""
    records = _read_all_pending_records(hub_path, scope, goal_id)
    found = next((r for r in records if r.get("id") == action_id), None)
    if not found:
        return False
    updated = dict(found)
    updated["telegram_message_id"] = message_id
    updated["telegram_chat_id"] = chat_id
    p = pending_actions_path(hub_path, scope, goal_id)
    try:
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(updated, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


# ============================================================
# Phase C — Budget tracking + execution audit
# ============================================================

def executions_path(hub_path: Path, scope: str, goal_id: str) -> Path:
    return goal_dir(hub_path, scope, goal_id) / "executions.jsonl"


def append_execution(hub_path: Path, scope: str, goal_id: str, record: dict) -> bool:
    """Append record di esecuzione (audit trail completo)."""
    gdir = goal_dir(hub_path, scope, goal_id)
    if not gdir.is_dir():
        return False
    p = gdir / "executions.jsonl"
    rec = dict(record or {})
    rec["ts"] = rec.get("ts") or (datetime.utcnow().isoformat(timespec="seconds") + "Z")
    try:
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def list_executions(hub_path: Path, scope: str, goal_id: str, limit: int = 100) -> list:
    p = executions_path(hub_path, scope, goal_id)
    if not p.is_file():
        return []
    out = []
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return out[-limit:]


def check_execution_budget(hub_path: Path, scope: str, goal_id: str,
                          budget: dict) -> dict:
    """Verifica che L3 possa eseguire un'altra action oggi.

    Budget domain-agnostic: limiti su numero di action/giorno + (opzionale) lista
    di action_type vietati. Restituisce {ok: bool, reason: str, stats: {...}}.

    Schema budget atteso:
        {
          "max_actions_per_day": <int>,         # opzionale, hard cap
          "max_actions_per_type": {<type>: N},  # opzionale, cap per tipo
          "disallow_types": ["<type>", ...],    # opzionale, blocca tipi
        }
    """
    if not budget:
        return {"ok": True, "reason": "no budget configured", "stats": {}}
    today_utc = datetime.utcnow().strftime("%Y-%m-%d")
    executions = list_executions(hub_path, scope, goal_id, limit=500)
    today_execs = [e for e in executions if (e.get("ts", "") or "").startswith(today_utc)]
    today_total = len(today_execs)
    by_type: dict = {}
    for e in today_execs:
        t = e.get("type", "")
        by_type[t] = by_type.get(t, 0) + 1
    stats = {"today_total": today_total, "by_type": by_type}

    max_actions = budget.get("max_actions_per_day")
    if max_actions is not None and today_total >= int(max_actions):
        return {"ok": False, "reason": f"max_actions_per_day reached ({today_total}/{max_actions})",
                "stats": stats}

    max_per_type = budget.get("max_actions_per_type") or {}
    for t, cap in max_per_type.items():
        cur = by_type.get(t, 0)
        if cur >= int(cap):
            return {"ok": False, "reason": f"max_actions_per_type[{t}] reached ({cur}/{cap})",
                    "stats": stats}

    disallow = set(budget.get("disallow_types") or [])
    # Nota: il caller dovrebbe controllare il type del payload prima di chiamare;
    # qui non blocchiamo perché non vediamo l'action corrente. La whitelist va
    # applicata in execute_pending_action.
    stats["disallow_types"] = list(disallow)

    return {"ok": True, "reason": "within budget", "stats": stats}


def delete_goal(hub_path: Path, scope: str, goal_id: str) -> Optional[dict]:
    """Hard delete: rimuove dir goal + cleanup suggestions orfane."""
    import shutil
    gdir = goal_dir(hub_path, scope, goal_id)
    if not gdir.is_dir():
        return None
    try:
        shutil.rmtree(gdir)
        # Cleanup orphan suggestions di questo goal
        try:
            inbox = hub_path / "goals" / ".suggestions_inbox.json"
            if inbox.is_file():
                items = json.loads(inbox.read_text(encoding="utf-8"))
                kept = [s for s in items if s.get("goal_id") != goal_id]
                if len(kept) != len(items):
                    inbox.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        return {"id": goal_id, "scope": scope, "deleted": True}
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# F4 — Specialist notes (input strutturato dei membri del team)
# ============================================================

def notes_dir(hub_path: Path, scope: str, goal_id: str) -> Path:
    return goal_dir(hub_path, scope, goal_id) / "notes"


def write_specialist_note(hub_path: Path, scope: str, goal_id: str, *,
                         role: str, agent: str, llm: dict, run_id: str,
                         output: dict, body_md: str = "") -> Optional[Path]:
    """Salva una nota di specialist in notes/YYYY-MM-DDTHHMMSS-<role>.md.

    Frontmatter strutturato + body markdown. Una nota per role per run_id.
    """
    nd = notes_dir(hub_path, scope, goal_id)
    nd.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H%M%S")
    safe_role = re.sub(r"[^a-z0-9-]+", "-", role.lower())
    fname = f"{ts}-{safe_role}.md"
    p = nd / fname
    meta = {
        "role": role,
        "agent": agent,
        "llm": llm or {},
        "run_id": run_id,
        "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "output": output or {},
    }
    try:
        p.write_text(dump_frontmatter(meta) + (body_md or ""), encoding="utf-8")
        return p
    except Exception:
        return None


def read_notes_for_run(hub_path: Path, scope: str, goal_id: str, run_id: str) -> list:
    """Restituisce tutte le notes di un run_id specifico, in ordine cronologico."""
    nd = notes_dir(hub_path, scope, goal_id)
    if not nd.is_dir():
        return []
    out = []
    for f in sorted(nd.glob("*.md")):
        try:
            text = f.read_text(encoding="utf-8")
            meta, body = parse_frontmatter(text)
            if meta.get("run_id") != run_id:
                continue
            out.append({**meta, "body": body, "path": str(f)})
        except Exception:
            continue
    return out


def read_recent_notes(hub_path: Path, scope: str, goal_id: str, limit: int = 10) -> list:
    """Notes più recenti, indipendenti dal run_id (per UI feed)."""
    nd = notes_dir(hub_path, scope, goal_id)
    if not nd.is_dir():
        return []
    out = []
    for f in sorted(nd.glob("*.md"), reverse=True)[:limit]:
        try:
            text = f.read_text(encoding="utf-8")
            meta, body = parse_frontmatter(text)
            out.append({**meta, "body": body[:500], "filename": f.name})
        except Exception:
            continue
    return out


# ============================================================
# F1+F2+F3 — Briefing context builder (la "lavagna" dell'ufficio)
# ============================================================

_BASELINE_RE = re.compile(r"^(equity_start_usdt|pnl_baseline_usdt|equity_start_usd|start_ts_ms):\s*([0-9.,]+)", re.M)


def read_reflections(hub_path: Path, scope: str, goal_id: str) -> str:
    """Legge reflections.md raw (decisioni umane + baseline persistente)."""
    gdir = goal_dir(hub_path, scope, goal_id)
    f = gdir / "reflections.md"
    if not f.is_file():
        return ""
    try:
        return f.read_text(encoding="utf-8")
    except Exception:
        return ""


def parse_baseline(reflections_text: str) -> dict:
    """Estrai chiavi baseline numeriche da reflections.md.

    Cerca pattern tipo `equity_start_usdt: 49278.23` (case-insensitive, anywhere).
    """
    out = {}
    for m in _BASELINE_RE.finditer(reflections_text):
        key = m.group(1)
        val = m.group(2).replace(",", "")
        try:
            out[key] = float(val)
        except Exception:
            out[key] = val
    return out


def save_baseline_to_reflections(hub_path: Path, scope: str, goal_id: str,
                                 baseline: dict) -> bool:
    """Salva baseline (equity_start ecc.) in reflections.md SOLO se non già presenti.

    Pattern: append section "## Baseline (auto-set at <ts>)" con chiavi.
    Non sovrascrive se già esistono (write-once).
    """
    if not baseline:
        return False
    existing = parse_baseline(read_reflections(hub_path, scope, goal_id))
    to_save = {k: v for k, v in baseline.items() if k not in existing}
    if not to_save:
        return False
    gdir = goal_dir(hub_path, scope, goal_id)
    f = gdir / "reflections.md"
    if not gdir.is_dir():
        return False
    lines = []
    if not f.is_file() or f.stat().st_size == 0:
        lines.append("# Reflections\n")
    lines.append(f"\n## Baseline (auto-set at {datetime.utcnow().isoformat(timespec='seconds')}Z)\n")
    for k, v in to_save.items():
        lines.append(f"- {k}: {v}\n")
    try:
        with open(f, "a", encoding="utf-8") as fh:
            fh.write("".join(lines))
        return True
    except Exception:
        return False


def resolve_linked_tasks(hub_path: Path, scope: str, goal_id: str,
                         linked_ids: list) -> list:
    """Risolvi linked_tasks (lista di id) → list di {id, title, status, tags, age_hours, assignee}.

    Usa kanban_io se disponibile, altrimenti ritorna stub.
    """
    if not linked_ids:
        return []
    try:
        import kanban_io as _k
    except Exception:
        return [{"id": i, "title": "(kanban_io unavailable)", "status": "?"} for i in linked_ids]
    out = []
    for tid in linked_ids:
        try:
            t = _k.get_task(hub_path, int(tid))
            if not t:
                continue
            age_h = 0.0
            try:
                ct = t.get("created_at") or t.get("updated_at")
                if ct:
                    dt = datetime.fromisoformat(ct.replace("Z", "+00:00"))
                    age_h = round((datetime.utcnow().replace(tzinfo=dt.tzinfo) - dt).total_seconds() / 3600, 1)
            except Exception:
                pass
            out.append({
                "id": t.get("id"),
                "title": t.get("title", "")[:100],
                "status": t.get("status", "?"),
                "tags": t.get("tags") or [],
                "assignee": t.get("assignee", ""),
                "age_hours": age_h,
            })
        except Exception:
            continue
    return out


def verdict_trend_summary(journal_entries: list, last_n: int = 10) -> dict:
    """Aggrega trend verdicts: count per tipo + last_n streak + total count."""
    entries = journal_entries or []
    total = len(entries)
    recent = entries[-last_n:]
    counts = {}
    for e in recent:
        v = e.get("verdict") or "?"
        counts[v] = counts.get(v, 0) + 1
    # Streak: quanti dello stesso verdict consecutivi alla fine
    streak_verdict = recent[-1].get("verdict") if recent else None
    streak_n = 0
    for e in reversed(recent):
        if e.get("verdict") == streak_verdict:
            streak_n += 1
        else:
            break
    return {
        "total": total,
        "last_n_count": len(recent),
        "by_type": counts,
        "streak_verdict": streak_verdict,
        "streak_n": streak_n,
    }


def build_briefing_block(hub_path: Path, scope: str, goal_id: str,
                        goal_meta: dict, journal_entries: list) -> str:
    """Compose il blocco BRIEFING markdown da iniettare nel system prompt del judge.

    Contenuto:
    - Baseline numerica da reflections (equity_start, ecc.)
    - GOAL START_TS_MS (filtro tassativo trade history)
    - Trend verdict aggregato (ultimi 10) + streak
    - Linked kanban risolti (status + age)
    - Decisioni recenti da reflections.md (raw body, max 1200 char)
    """
    chunks: list[str] = ["\n\n## 📋 BRIEFING — stato condiviso del goal\n"]

    # 0) Goal start_ts (CRITICO — filtra trade history)
    created_str = goal_meta.get("created", "")
    start_ts_ms = None
    today_utc = None
    if created_str:
        try:
            from datetime import datetime as _dt, timezone as _tz
            # goal start: 00:00 UTC del giorno di creazione
            d = _dt.strptime(created_str, "%Y-%m-%d").replace(tzinfo=_tz.utc)
            start_ts_ms = int(d.timestamp() * 1000)
            # today UTC: aware now, replace to midnight UTC
            now_utc = _dt.now(_tz.utc)
            today_utc = now_utc.strftime("%Y-%m-%d")
            today_midnight = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
            today_start_ts_ms = int(today_midnight.timestamp() * 1000)
            chunks.append(f"### ⏱ Time window (TASSATIVO)\n")
            chunks.append(f"- **goal_created_utc**: `{created_str}` → `start_ts_ms: {start_ts_ms}`\n")
            chunks.append(f"- **today_utc**: `{today_utc}` → `today_start_ts_ms: {today_start_ts_ms}`\n")
            chunks.append(f"- **REGOLA**: per metriche P/L del goal usa SOLO trade con `ts >= start_ts_ms` ({start_ts_ms}).\n")
            chunks.append(f"  Per `closed_pnl_today_utc` usa SOLO trade con `ts >= today_start_ts_ms` ({today_start_ts_ms}).\n")
            chunks.append(f"  IGNORA tassativamente trade più vecchi (storico pre-goal).\n\n")
        except Exception:
            pass

    # 1) Baseline (write-once)
    refl_text = read_reflections(hub_path, scope, goal_id)
    baseline = parse_baseline(refl_text)
    if baseline:
        chunks.append("### Baseline (persistente)\n")
        for k, v in baseline.items():
            chunks.append(f"- {k}: {v}\n")
    else:
        chunks.append("### Baseline\n_(non ancora settata — al primo run leggi lo stato iniziale del dominio via i tool MCP del workspace e salva i valori di partenza emettendo `metrics.start_*` nel JSON output)_\n")

    # 2) Trend verdict
    trend = verdict_trend_summary(journal_entries, last_n=10)
    chunks.append(f"\n### Trend verdict (ultimi {trend['last_n_count']} di {trend['total']} totali)\n")
    if trend["total"] == 0:
        chunks.append("- Nessun verdict precedente (primo run)\n")
    else:
        for v, n in trend["by_type"].items():
            chunks.append(f"- {v}: {n}\n")
        if trend["streak_n"] >= 2:
            chunks.append(f"- ⚠️ streak corrente: {trend['streak_n']}× **{trend['streak_verdict']}** consecutivi\n")

    # 3) Linked kanban resolved
    linked_ids = goal_meta.get("linked_tasks") or []
    if linked_ids:
        tasks = resolve_linked_tasks(hub_path, scope, goal_id, linked_ids)
        if tasks:
            chunks.append(f"\n### Kanban linked ({len(tasks)} task)\n")
            for t in tasks:
                tag_marker = " 🤖" if "auto:judge" in (t.get("tags") or []) else ""
                age = f" ({t['age_hours']:.0f}h ago)" if t.get("age_hours") else ""
                chunks.append(f"- #{t['id']} [{t['status']}]{tag_marker} {t['title']}{age}\n")

    # 3.5) Signals dal script runtime (D2)
    try:
        signal_file = goal_dir(hub_path, scope, goal_id) / "signals.jsonl"
        if signal_file.is_file():
            recent_signals = []
            with open(signal_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        recent_signals.append(json.loads(line))
                    except Exception:
                        continue
            recent_signals = recent_signals[-15:]  # ultimi 15
            if recent_signals:
                chunks.append(f"\n### 🛰 Signals dai monitor script (ultimi {len(recent_signals)})\n")
                for s in recent_signals:
                    et = s.get("event_type", "?")
                    src = s.get("script", "?")
                    ts = s.get("ts", "?")
                    payload = s.get("payload") or {}
                    payload_str = json.dumps(payload, ensure_ascii=False)[:120] if payload else ""
                    chunks.append(f"- `{ts}` [{src}] **{et}** {payload_str}\n")
    except Exception:
        pass

    # 4) Reflections (decisioni umane)
    if refl_text:
        # Salta header e linee baseline auto-generate per non duplicare
        cleaned_lines = []
        skip_baseline_section = False
        for line in refl_text.splitlines():
            if line.strip().startswith("## Baseline"):
                skip_baseline_section = True
                continue
            if skip_baseline_section and line.startswith("- "):
                continue
            if skip_baseline_section and line.startswith("##"):
                skip_baseline_section = False
            if not skip_baseline_section:
                cleaned_lines.append(line)
        human_decisions = "\n".join(cleaned_lines).strip()
        if human_decisions and human_decisions != "# Reflections":
            chunks.append("\n### Decisioni umane / pivot (da reflections.md)\n")
            chunks.append(human_decisions[:1200])
            if len(human_decisions) > 1200:
                chunks.append("\n_(...truncated)_\n")
            chunks.append("\n")

    return "".join(chunks)


def append_reflection(hub_path: Path, scope: str, goal_id: str, text: str) -> bool:
    gdir = goal_dir(hub_path, scope, goal_id)
    rf = gdir / "reflections.md"
    if not gdir.is_dir():
        return False
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    entry = f"\n## [{ts}] reflection\n\n{text}\n"
    with rf.open("a", encoding="utf-8") as f:
        f.write(entry)
    return True


# ============================================================
# Journal append + parse
# ============================================================

def append_journal(hub_path: Path, scope: str, goal_id: str,
                   verdict: str, agent: str, body_md: str) -> bool:
    """Append entry parsabile in journal.md.

    Formato: `## [YYYY-MM-DD HH:MM] judge:<agent> | verdict: <status>\\n\\n<body>\\n`
    """
    if verdict not in VALID_VERDICTS:
        return False
    gdir = goal_dir(hub_path, scope, goal_id)
    jf = gdir / "journal.md"
    if not gdir.is_dir():
        return False
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    entry = f"\n## [{ts}] judge:{agent} | verdict: {verdict}\n\n{body_md.strip()}\n"
    with jf.open("a", encoding="utf-8") as f:
        f.write(entry)
    return True


_ENTRY_RE = re.compile(r"^## \[(.+?)\] judge:(.+?) \| verdict: (\w+)\s*$", re.MULTILINE)


def _parse_journal_entries(text: str) -> list[dict]:
    """Parse journal.md → list di {ts, agent, verdict, body}."""
    entries = []
    matches = list(_ENTRY_RE.finditer(text))
    for i, m in enumerate(matches):
        ts = m.group(1)
        agent = m.group(2)
        verdict = m.group(3)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        entries.append({"ts": ts, "agent": agent, "verdict": verdict, "body": body})
    return entries


def _last_verdict(journal_text: str) -> Optional[dict]:
    entries = _parse_journal_entries(journal_text)
    return entries[-1] if entries else None


# ============================================================
# Index (1-line per goal, per scope)
# ============================================================

def _update_index(hub_path: Path, scope: str):
    """Aggiorna index.md della cartella goals/ con 1-liner per goal attivo."""
    root = goals_root(hub_path, scope)
    if not root.is_dir():
        return
    goals = list_goals(hub_path, scope=scope)
    lines = ["# Goals — index", "", f"_Updated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}_", ""]
    active = [g for g in goals if g["status"] == "active"]
    closed = [g for g in goals if g["status"] != "active"]
    if active:
        lines.append("## Active\n")
        for g in active:
            lv = g.get("last_verdict")
            lv_str = f" — {lv['verdict']} ({lv['ts']})" if lv else ""
            deadline = f" · deadline: {g['deadline']}" if g.get("deadline") else ""
            lines.append(f"- **[{g['title']}]({g['id']}/goal.md)** [{g['priority']}]{deadline}{lv_str}")
        lines.append("")
    if closed:
        lines.append("## Closed\n")
        for g in closed:
            lines.append(f"- {g['title']} [{g['status']}]({g['id']}/goal.md)")
        lines.append("")
    (root / "index.md").write_text("\n".join(lines), encoding="utf-8")


# ============================================================
# Helpers for context_composer (1-liner status block)
# ============================================================

def goals_summary_block(hub_path: Path, scope: str, max_items: int = 5) -> str:
    """Block ~30 token/goal per system prompt injection."""
    goals = list_goals(hub_path, scope=scope, status="active")
    if not goals:
        return ""
    goals = goals[:max_items]
    lines = [f"## Active goals ({scope})"]
    for g in goals:
        lv = g.get("last_verdict")
        verdict_str = f" [{lv['verdict']}]" if lv else ""
        deadline = f" · due {g['deadline']}" if g.get("deadline") else ""
        lines.append(f"- {g['title']}{deadline}{verdict_str}")
    return "\n".join(lines)


def hub_workspaces_goals_overview(hub_path: Path, max_per_ws: int = 2) -> str:
    """Per hub context: overview di goal attivi su tutti i workspace."""
    out_lines = []
    ws_root = hub_path / "workspaces"
    if not ws_root.is_dir():
        return ""
    for ws_dir in sorted(ws_root.iterdir()):
        if not ws_dir.is_dir():
            continue
        scope = f"workspace:{ws_dir.name}"
        goals = list_goals(hub_path, scope=scope, status="active")[:max_per_ws]
        if not goals:
            continue
        out_lines.append(f"### {ws_dir.name}")
        for g in goals:
            lv = g.get("last_verdict")
            verdict_str = f" [{lv['verdict']}]" if lv else ""
            out_lines.append(f"  - {g['title']}{verdict_str}")
    if not out_lines:
        return ""
    return "## Workspaces goals overview\n" + "\n".join(out_lines)
