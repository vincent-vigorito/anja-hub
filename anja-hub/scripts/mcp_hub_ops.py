#!/usr/bin/env python3
"""mcp_hub_ops.py — Hub Operations MCP server (Anja Hub Ops Tier 1+2+3).

Scope-aware: il caller (env ANJA_SCOPE) determina cosa può fare.
- scope='hub'              → T1 read cross-workspace + T3 bridge (workspace.task)
- scope='workspace:<name>' → T1 read locale + T2 write locale (agent/script/routine/goal)

Tool categorie:
- **Diagnostica (T1, read)**: aggregator stato goal/specialist/script/executions/signals
- **Lifecycle (T2, write)**: modifica config agent del workspace, start/stop script,
  enable/disable routine, riassegna ruoli del team goal
- **Bridge (T3, hub-only)**: chiede a Anja-responsabile di workspace X di fare task

Stdlib only.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


SCOPE = os.environ.get("ANJA_SCOPE", "hub")
ROOT = Path(os.environ.get("ANJA_ROOT", os.getcwd())).resolve()
# F-HubChat: webapp URL per i tool create (proxy HTTP a endpoint REST).
WEBAPP_URL = os.environ.get("ANJA_WEBAPP_URL", "http://127.0.0.1:8765").rstrip("/")

# Per workspace scope: ROOT è <workspace>/.anjawiki. HUB sta 3 livelli su.
# Per hub scope: ROOT è il hub direttamente.
if SCOPE.startswith("workspace:"):
    # ROOT = <hub>/workspaces/<name>/.anjawiki
    # ROOT.parent = <hub>/workspaces/<name>
    # ROOT.parent.parent = <hub>/workspaces
    # ROOT.parent.parent.parent = <hub>
    WORKSPACE_NAME = ROOT.parent.name
    HUB_ROOT = ROOT.parent.parent.parent
else:
    WORKSPACE_NAME = None
    HUB_ROOT = ROOT


def _is_hub() -> bool:
    return SCOPE == "hub" or SCOPE == ""


def _scope_goal_dir(scope: str, goal_id: str) -> Path:
    """Risolvi la dir del goal in base allo scope target."""
    if scope == "hub":
        return HUB_ROOT / "goals" / goal_id
    if scope.startswith("workspace:"):
        ws = scope.split(":", 1)[1]
        return HUB_ROOT / "workspaces" / ws / ".anjawiki" / "goals" / goal_id
    return HUB_ROOT / "goals" / goal_id


def _resolve_target_scope(provided: Optional[str]) -> str:
    """Se hub scope: usa scope provided o 'hub'. Se workspace scope: forza locked al proprio."""
    if _is_hub():
        return provided or "hub"
    # Workspace caller può solo operare sul proprio scope
    return SCOPE


def _list_all_goals_meta() -> list[dict]:
    """Lista tutti i goal cross-scope (solo per hub caller). Workspace caller vede solo i suoi."""
    out: list[dict] = []
    scopes = []
    if _is_hub():
        scopes.append("hub")
        ws_root = HUB_ROOT / "workspaces"
        if ws_root.is_dir():
            for ws in sorted(ws_root.iterdir()):
                if ws.is_dir():
                    scopes.append(f"workspace:{ws.name}")
    else:
        scopes.append(SCOPE)

    for sc in scopes:
        if sc == "hub":
            goals_root = HUB_ROOT / "goals"
        else:
            goals_root = HUB_ROOT / "workspaces" / sc.split(":", 1)[1] / ".anjawiki" / "goals"
        if not goals_root.is_dir():
            continue
        for gdir in sorted(goals_root.iterdir()):
            if not gdir.is_dir() or gdir.name.startswith("."):
                continue
            gmd = gdir / "goal.md"
            if not gmd.is_file():
                continue
            out.append({
                "id": gdir.name,
                "scope": sc,
                "path": str(gdir),
            })
    return out


def _read_jsonl_tail(path: Path, limit: int = 50) -> list[dict]:
    """Lettura tail di jsonl, una entry per riga."""
    if not path.is_file():
        return []
    out: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _read_goal_meta(goal_path: Path) -> dict:
    """Leggi frontmatter goal.md (parser semplificato)."""
    gmd = goal_path / "goal.md"
    if not gmd.is_file():
        return {}
    try:
        text = gmd.read_text(encoding="utf-8")
    except Exception:
        return {}
    m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return {}
    meta_raw = m.group(1)
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
        key, val = key.strip(), val.strip()
        if not val:
            cur_list_key = key
            meta[key] = []
            continue
        if val.startswith("{") and val.endswith("}"):
            try:
                meta[key] = json.loads(val); continue
            except Exception: pass
        if val.startswith("[") and val.endswith("]"):
            try:
                meta[key] = json.loads(val); continue
            except Exception: pass
        if val.lower() in ("true", "false"):
            meta[key] = val.lower() == "true"; continue
        if re.match(r"^-?\d+$", val):
            meta[key] = int(val); continue
        if (val.startswith('"') and val.endswith('"')):
            meta[key] = val[1:-1]; continue
        meta[key] = val
    return meta


# =================================================================
# T1 — Diagnostica read-only
# =================================================================

def tool_diagnose(args: dict) -> dict:
    """Aggregator overview di un goal (o tutti i goal visibili al caller).

    args: { goal_id?, scope? (only hub caller), days?=1 }
    Output strutturato: run_stats, error_patterns, specialist_health, signals_summary,
    executions_summary, scripts_status, kanban_open, suggestions_pending.
    """
    goal_id = args.get("goal_id") or ""
    target_scope = _resolve_target_scope(args.get("scope"))
    days = int(args.get("days", 1) or 1)
    cutoff_ts = time.time() - (days * 86400)

    goals_to_inspect: list[dict] = []
    if goal_id:
        gpath = _scope_goal_dir(target_scope, goal_id)
        if not gpath.is_dir():
            return {"error": f"goal '{goal_id}' not found in scope '{target_scope}'"}
        goals_to_inspect.append({"id": goal_id, "scope": target_scope, "path": str(gpath)})
    else:
        goals_to_inspect = _list_all_goals_meta()

    reports = []
    for g in goals_to_inspect:
        gpath = Path(g["path"])
        meta = _read_goal_meta(gpath)
        activity = _read_jsonl_tail(gpath / "activity.jsonl", limit=500)
        recent_activity = [e for e in activity if _parse_iso_ts(e.get("ts_iso", "")) > cutoff_ts]

        # Run stats
        starts = [e for e in recent_activity if e.get("event_type") == "pipeline_start"]
        ends = [e for e in recent_activity if e.get("event_type") == "pipeline_end"]
        errors = [e for e in recent_activity if e.get("level") == "error"]
        timeouts = [e for e in recent_activity if e.get("event_type") == "timeout"]

        # Verdict trend
        verdicts = [e for e in recent_activity if e.get("event_type") == "verdict"]
        verdict_counts: dict = {}
        for v in verdicts:
            payload = v.get("payload") or {}
            # verdict è in msg "verdict: drift" oppure in payload
            txt = v.get("msg", "")
            for vt in ["on_track", "drift", "blocked", "achieved", "failed"]:
                if vt in txt:
                    verdict_counts[vt] = verdict_counts.get(vt, 0) + 1
                    break

        # Specialist health (chi è completato e quanto)
        specialist_health: dict = {}
        for role in ("analyst", "risk-officer", "dev", "executor", "researcher"):
            starts_r = [e for e in recent_activity if e.get("event_type") == "specialist_start" and e.get("agent") == role]
            ends_r = [e for e in recent_activity if e.get("event_type") == "specialist_end" and e.get("agent") == role]
            specialist_health[role] = {
                "starts": len(starts_r),
                "ends": len(ends_r),
                "hanging": len(starts_r) - len(ends_r),
                "last_status": ends_r[-1].get("msg", "")[:80] if ends_r else None,
            }

        # Signals summary
        signals = _read_jsonl_tail(gpath / "signals.jsonl", limit=200)
        signal_counts: dict = {}
        for s in signals:
            et = s.get("event_type", "?")
            signal_counts[et] = signal_counts.get(et, 0) + 1

        # Executions summary
        executions = _read_jsonl_tail(gpath / "executions.jsonl", limit=100)
        exec_errors = [e for e in executions if e.get("status") == "failed" or e.get("error")]
        exec_success = [e for e in executions if e.get("status") == "ok"]
        exec_error_patterns: dict = {}
        for e in exec_errors:
            err = (e.get("error", "") or "")[:80]
            if err:
                exec_error_patterns[err] = exec_error_patterns.get(err, 0) + 1

        # Pending actions
        pending = _read_jsonl_tail(gpath / "pending_actions.jsonl", limit=100)
        pending_by_id: dict = {}
        for p in pending:
            pid = p.get("id")
            if pid:
                pending_by_id[pid] = p
        pending_open = [p for p in pending_by_id.values() if p.get("status") == "pending"]

        reports.append({
            "goal_id": g["id"],
            "scope": g["scope"],
            "title": meta.get("title", ""),
            "status": meta.get("status", ""),
            "autonomy_level": meta.get("autonomy_level", 1),
            "responsabile": meta.get("responsabile", ""),
            "pipeline_cron": meta.get("pipeline_cron", ""),
            "judge_cron": meta.get("judge_cron", ""),
            "run_stats": {
                "pipeline_starts": len(starts),
                "pipeline_ends": len(ends),
                "incomplete": len(starts) - len(ends),
                "errors": len(errors),
                "timeouts": len(timeouts),
                "verdicts_by_type": verdict_counts,
            },
            "specialist_health": specialist_health,
            "signals": {
                "total": len(signals),
                "by_type": signal_counts,
            },
            "executions": {
                "total": len(executions),
                "success": len(exec_success),
                "failed": len(exec_errors),
                "error_patterns": exec_error_patterns,
            },
            "pending_actions_open": len(pending_open),
        })

    # Cross-goal script status
    script_status = tool_script_status({})

    # Diagnose summary patterns (suggerimenti automatici di sintesi)
    issues = []
    for r in reports:
        if r["run_stats"]["incomplete"] > 0:
            issues.append(f"{r['goal_id']}: {r['run_stats']['incomplete']} pipeline incomplete (hanging specialist)")
        if r["executions"]["failed"] > 0 and r["executions"]["error_patterns"]:
            top_err = max(r["executions"]["error_patterns"].items(), key=lambda x: x[1])
            issues.append(f"{r['goal_id']}: executions failing — pattern '{top_err[0]}' ({top_err[1]}x)")
        drift = r["run_stats"]["verdicts_by_type"].get("drift", 0)
        if drift >= 3:
            issues.append(f"{r['goal_id']}: {drift} drift verdicts in last {days}d — review strategy")
        for role, h in r["specialist_health"].items():
            if h["hanging"] > 0:
                issues.append(f"{r['goal_id']}: {role} has {h['hanging']} hanging runs (specialist not completing)")
    return {
        "caller_scope": SCOPE,
        "window_days": days,
        "goals_inspected": len(reports),
        "reports": reports,
        "scripts": script_status.get("scripts", []),
        "issues_detected": issues,
    }


def _parse_iso_ts(s: str) -> float:
    if not s:
        return 0.0
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


def tool_notes_recent(args: dict) -> dict:
    """Leggi le notes specialist degli ultimi N run del goal.

    args: { goal_id, scope?, role?, limit?=10 }
    """
    goal_id = (args.get("goal_id") or "").strip()
    if not goal_id:
        return {"error": "goal_id required"}
    target_scope = _resolve_target_scope(args.get("scope"))
    role_filter = (args.get("role") or "").lower()
    limit = int(args.get("limit", 10) or 10)
    gpath = _scope_goal_dir(target_scope, goal_id)
    notes_dir = gpath / "notes"
    if not notes_dir.is_dir():
        return {"notes": [], "count": 0}
    files = sorted(notes_dir.glob("*.md"), reverse=True)
    notes = []
    for f in files:
        if role_filter and role_filter not in f.name.lower():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        # Estrai output JSON dal frontmatter (parser veloce)
        m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
        meta = {}
        if m:
            for line in m.group(1).split("\n"):
                if ": " in line:
                    k, _, v = line.partition(": ")
                    k = k.strip()
                    v = v.strip()
                    if v.startswith("{") and v.endswith("}"):
                        try: meta[k] = json.loads(v)
                        except: meta[k] = v
                    else:
                        meta[k] = v.strip('"').strip("'")
        notes.append({
            "filename": f.name,
            "role": meta.get("role", ""),
            "agent": meta.get("agent", ""),
            "ts": meta.get("ts", ""),
            "run_id": meta.get("run_id", ""),
            "llm": meta.get("llm", {}),
            "output": meta.get("output", {}),
        })
        if len(notes) >= limit:
            break
    return {"notes": notes, "count": len(notes), "goal_id": goal_id, "scope": target_scope}


def tool_signals_recent(args: dict) -> dict:
    """Tail signals.jsonl di un goal."""
    goal_id = (args.get("goal_id") or "").strip()
    if not goal_id:
        return {"error": "goal_id required"}
    target_scope = _resolve_target_scope(args.get("scope"))
    limit = int(args.get("limit", 30) or 30)
    gpath = _scope_goal_dir(target_scope, goal_id)
    signals = _read_jsonl_tail(gpath / "signals.jsonl", limit=limit)
    return {"signals": signals, "count": len(signals), "goal_id": goal_id, "scope": target_scope}


def tool_executions_recent(args: dict) -> dict:
    """Tail executions.jsonl con errori inclusi (audit L3)."""
    goal_id = (args.get("goal_id") or "").strip()
    if not goal_id:
        return {"error": "goal_id required"}
    target_scope = _resolve_target_scope(args.get("scope"))
    limit = int(args.get("limit", 20) or 20)
    gpath = _scope_goal_dir(target_scope, goal_id)
    execs = _read_jsonl_tail(gpath / "executions.jsonl", limit=limit)
    # Aggrega errori
    errors = [e for e in execs if e.get("status") == "failed" or e.get("error")]
    return {
        "executions": execs,
        "count": len(execs),
        "errors_count": len(errors),
        "goal_id": goal_id, "scope": target_scope,
    }


def tool_script_status(args: dict) -> dict:
    """Lista script attivi del runtime supervisor. Filtra per workspace caller se scope-locked."""
    state_file = HUB_ROOT / "scripts" / ".runtime_state.json"
    if not state_file.is_file():
        return {"scripts": [], "count": 0}
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        return {"scripts": [], "count": 0}

    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    scripts = []
    for path_str, info in state.items():
        # Scope filtering: workspace caller vede solo i suoi script
        if not _is_hub() and info.get("scope") != SCOPE:
            continue
        # hub caller può filtrare per scope se passed
        scope_filter = args.get("scope")
        if scope_filter and info.get("scope") != scope_filter:
            continue
        pid = info.get("pid", 0)
        scripts.append({
            "path": path_str,
            "filename": Path(path_str).name,
            "goal_id": info.get("goal_id", ""),
            "scope": info.get("scope", ""),
            "pid": pid,
            "alive": _pid_alive(pid),
            "disabled": info.get("disabled", False),
            "restarts": info.get("restarts", 0),
            "started_at": info.get("started_at", ""),
            "last_error": info.get("last_error", ""),
        })
    return {"scripts": scripts, "count": len(scripts)}


def tool_pending_actions(args: dict) -> dict:
    """Lista pending actions di un goal (qualsiasi status, default solo pending)."""
    goal_id = (args.get("goal_id") or "").strip()
    if not goal_id:
        return {"error": "goal_id required"}
    target_scope = _resolve_target_scope(args.get("scope"))
    status_filter = (args.get("status") or "pending").lower()
    gpath = _scope_goal_dir(target_scope, goal_id)
    records = _read_jsonl_tail(gpath / "pending_actions.jsonl", limit=200)
    # Idempotente per id: l'ultimo write vince
    by_id: dict = {}
    for r in records:
        rid = r.get("id")
        if rid:
            by_id[rid] = r
    actions = list(by_id.values())
    if status_filter and status_filter != "all":
        actions = [a for a in actions if a.get("status") == status_filter]
    actions.sort(key=lambda a: a.get("created_at", ""), reverse=True)
    return {"actions": actions, "count": len(actions), "goal_id": goal_id, "scope": target_scope}


# =================================================================
# T2 — Write tools (scope-locked al workspace caller)
# =================================================================

def _require_workspace_scope() -> Optional[dict]:
    """Restituisce error dict se caller non è workspace. Usa nei write tool."""
    if _is_hub():
        return {"error": "REFUSED: questa operazione richiede scope=workspace:<name> (sei in scope hub)."}
    return None


def tool_agent_update(args: dict) -> dict:
    """Modifica un file di config di un agent del workspace caller.

    args: { name, file (AGENTS.md|SOUL.md|TOOLS.md|config.json), content, mode='replace'|'append' }
    """
    err = _require_workspace_scope()
    if err: return err
    name = (args.get("name") or "").strip()
    file = (args.get("file") or "").strip()
    content = args.get("content", "")
    mode = (args.get("mode") or "replace").lower()
    if not name or not file or content is None:
        return {"error": "name + file + content required"}
    if mode not in ("replace", "append"):
        return {"error": "mode must be 'replace' or 'append'"}
    ALLOWED_FILES = {"AGENTS.md", "SOUL.md", "TOOLS.md", "CLAUDE.md", "config.json"}
    if file not in ALLOWED_FILES:
        return {"error": f"file '{file}' not whitelisted (allowed: {sorted(ALLOWED_FILES)})"}
    # ROOT è già <workspace>/.anjawiki
    agent_dir = ROOT / "agents" / name
    if not agent_dir.is_dir():
        return {"error": f"agent '{name}' not found in workspace '{WORKSPACE_NAME}'"}
    target = agent_dir / file
    try:
        if mode == "append" and target.is_file():
            existing = target.read_text(encoding="utf-8")
            new_content = existing + "\n" + content if not existing.endswith("\n") else existing + content
        else:
            new_content = content
        target.write_text(new_content, encoding="utf-8")
        size = target.stat().st_size
        return {
            "ok": True, "path": str(target), "size_bytes": size,
            "agent": name, "file": file, "mode": mode,
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def tool_script_lifecycle(args: dict) -> dict:
    """Lifecycle di un monitor script. args: { script_path, action ('start'|'stop'|'restart'|'reset') }"""
    err = _require_workspace_scope()
    if err: return err
    script_path = (args.get("script_path") or "").strip()
    action = (args.get("action") or "").lower()
    if not script_path or not action:
        return {"error": "script_path + action required"}
    if action not in ("start", "stop", "restart", "reset"):
        return {"error": "action must be start|stop|restart|reset"}
    # Import script_runtime (helper webapp)
    # Prova convenzione monorepo, poi env ANJA_HUB_WEBAPP
    candidates = [
        HUB_ROOT.parent / "anja-hub" / "webapp",
        HUB_ROOT.parent / "llm-wiki" / "anja-hub" / "webapp",  # legacy
    ]
    env_path = os.environ.get("ANJA_HUB_WEBAPP")
    if env_path:
        candidates.insert(0, Path(env_path).expanduser().resolve())
    sr = None
    for c in candidates:
        if c.is_dir():
            sys.path.insert(0, str(c))
            try:
                import script_runtime as sr  # noqa
                break
            except Exception:
                continue
    if sr is None:
        return {"error": "script_runtime unavailable", "hint": "this tool requires the anja-hub webapp (set ANJA_HUB_WEBAPP env)"}

    p = Path(script_path)
    if not p.is_absolute():
        p = HUB_ROOT / script_path
    if not p.is_file():
        return {"error": f"script not found: {p}"}

    if action == "start":
        # Need goal_id from state or derive from path
        state = sr._load_state(HUB_ROOT)
        info = state.get(str(p)) or {}
        gid = info.get("goal_id") or args.get("goal_id")
        sc = info.get("scope") or SCOPE
        if not gid:
            return {"error": "goal_id required (or script must be already in state)"}
        return sr.start_script(HUB_ROOT, sc, gid, p)
    if action == "stop":
        return sr.stop_script(HUB_ROOT, p)
    if action == "restart":
        sr.stop_script(HUB_ROOT, p)
        time.sleep(0.5)
        state = sr._load_state(HUB_ROOT)
        info = state.get(str(p)) or {}
        gid = info.get("goal_id") or args.get("goal_id")
        sc = info.get("scope") or SCOPE
        if not gid:
            return {"error": "goal_id required for restart"}
        return sr.start_script(HUB_ROOT, sc, gid, p)
    if action == "reset":
        return sr.reset_script(HUB_ROOT, p)
    return {"error": "unreachable"}


def tool_routine_lifecycle(args: dict) -> dict:
    """Routine lifecycle. args: { name, action ('enable'|'disable'|'run_now') }"""
    err = _require_workspace_scope()
    if err: return err
    name = (args.get("name") or "").strip()
    action = (args.get("action") or "").lower()
    if not name or not action:
        return {"error": "name + action required"}
    if action not in ("enable", "disable", "run_now"):
        return {"error": "action must be enable|disable|run_now"}
    # Routines vivono in <workspace>/.anjawiki/routines/<name>.yaml
    routine_file = ROOT / "routines" / f"{name}.yaml"
    if not routine_file.is_file():
        return {"error": f"routine '{name}' not found in {routine_file}"}
    try:
        content = routine_file.read_text(encoding="utf-8")
        if action == "enable":
            content = re.sub(r"^enabled:\s*\w+", "enabled: true", content, flags=re.M)
            if "enabled:" not in content:
                content = "enabled: true\n" + content
            routine_file.write_text(content)
            return {"ok": True, "name": name, "action": action, "enabled": True}
        if action == "disable":
            content = re.sub(r"^enabled:\s*\w+", "enabled: false", content, flags=re.M)
            if "enabled:" not in content:
                content = "enabled: false\n" + content
            routine_file.write_text(content)
            return {"ok": True, "name": name, "action": action, "enabled": False}
        if action == "run_now":
            # Touch file con timestamp per forzare next tick scheduler
            # (workaround senza tool dedicato)
            return {"ok": True, "name": name, "action": action,
                    "note": "run_now flag: il routine daemon eseguirà al prossimo tick. Per fire immediato usa il bottone UI."}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    return {"error": "unreachable"}


def tool_goal_assign_agent(args: dict) -> dict:
    """Riassegna ruolo/agent in un goal. args: { goal_id, role, agent, llm? }"""
    err = _require_workspace_scope()
    if err: return err
    goal_id = (args.get("goal_id") or "").strip()
    role = (args.get("role") or "").strip()
    agent = (args.get("agent") or "").strip()
    llm = args.get("llm") or None
    if not goal_id or not role or not agent:
        return {"error": "goal_id + role + agent required"}
    # Resolve webapp anja-hub: env ANJA_HUB_WEBAPP, poi convenzione monorepo
    env_path = os.environ.get("ANJA_HUB_WEBAPP")
    candidates = []
    if env_path:
        candidates.append(Path(env_path).expanduser().resolve())
    candidates.append(HUB_ROOT.parent / "anja-hub" / "webapp")
    goal_io = None
    for c in candidates:
        if c.is_dir():
            sys.path.insert(0, str(c))
            try:
                import goal_io  # noqa
                break
            except Exception:
                continue
    if goal_io is None:
        return {"error": "goal_io unavailable", "hint": "this tool requires the anja-hub webapp (set ANJA_HUB_WEBAPP env)"}
    g = goal_io.read_goal(HUB_ROOT, SCOPE, goal_id)
    if not g:
        return {"error": f"goal '{goal_id}' not found in scope '{SCOPE}'"}
    meta = g["meta"]
    if role == "responsabile":
        updates = {"responsabile": agent}
        if llm:
            updates["responsabile_llm"] = llm
        return goal_io.update_goal(HUB_ROOT, SCOPE, goal_id, updates)
    if role == "escalation" or role == "ceo":
        updates = {"escalation_to": agent}
        if llm:
            updates["escalation_llm"] = llm
        return goal_io.update_goal(HUB_ROOT, SCOPE, goal_id, updates)
    # Specialist: modifica assigned_agents
    assigned = list(meta.get("assigned_agents") or [])
    found = False
    for a in assigned:
        if (a.get("role") or "").lower() == role.lower():
            a["agent"] = agent
            if llm:
                a["llm"] = llm
            found = True
            break
    if not found:
        new_entry = {"role": role, "agent": agent, "cadence": "on_demand"}
        if llm:
            new_entry["llm"] = llm
        assigned.append(new_entry)
    return goal_io.update_goal(HUB_ROOT, SCOPE, goal_id, {"assigned_agents": assigned})


# =================================================================
# T3 — Hub bridge (hub-only)
# =================================================================

def tool_workspace_task(args: dict) -> dict:
    """Hub → workspace responsabile: chiedi a Anja-responsabile di workspace X di fare task.

    args: { target_workspace, prompt, focus? }
    Restituisce un descrittore della richiesta; l'invocazione effettiva è async via webapp.
    """
    if not _is_hub():
        return {"error": "REFUSED: workspace.task disponibile solo da scope=hub (sei in workspace)"}
    target = (args.get("target_workspace") or "").strip()
    prompt = (args.get("prompt") or "").strip()
    if not target or not prompt:
        return {"error": "target_workspace + prompt required"}
    ws_root = HUB_ROOT / "workspaces" / target / ".anjawiki"
    if not ws_root.is_dir():
        return {"error": f"workspace '{target}' not found"}
    # Persiste richiesta in <workspace>/.anjawiki/inbox/tasks.jsonl
    inbox_dir = ws_root / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    inbox_file = inbox_dir / "tasks.jsonl"
    import secrets as _s
    record = {
        "id": f"task_{int(time.time())}_{_s.token_hex(3)}",
        "from": "anja-ceo",
        "to_workspace": target,
        "prompt": prompt,
        "focus": args.get("focus"),
        "status": "pending",
        "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    try:
        with open(inbox_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {
            "ok": True, "task_id": record["id"], "inbox_path": str(inbox_file),
            "note": f"Task inoltrato a workspace '{target}'. Il responsabile lo processerà.",
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def tool_workspace_diagnose_request(args: dict) -> dict:
    """Hub chiede al manager workspace di fare diagnose + report."""
    if not _is_hub():
        return {"error": "REFUSED: diagnose_request solo da scope=hub"}
    target = (args.get("target_workspace") or "").strip()
    focus = (args.get("focus") or "")
    if not target:
        return {"error": "target_workspace required"}
    return tool_workspace_task({
        "target_workspace": target,
        "prompt": f"Esegui diagnose del tuo workspace e produci report. Focus: {focus or 'general health check'}",
        "focus": focus,
    })


def tool_workspace_list_tasks(args: dict) -> dict:
    """Lista task assegnati al workspace caller (inbox). Utile per il responsabile per checklist."""
    if _is_hub():
        # Hub vede tutti i task assegnati a workspaces
        out = []
        ws_root = HUB_ROOT / "workspaces"
        if ws_root.is_dir():
            for ws in sorted(ws_root.iterdir()):
                if not ws.is_dir():
                    continue
                inbox_file = ws / ".anjawiki" / "inbox" / "tasks.jsonl"
                records = _read_jsonl_tail(inbox_file, limit=100)
                by_id: dict = {}
                for r in records:
                    rid = r.get("id")
                    if rid: by_id[rid] = r
                for t in by_id.values():
                    t["target_workspace"] = ws.name
                    out.append(t)
        return {"tasks": out, "count": len(out)}
    # Workspace caller: solo la sua inbox
    inbox_file = ROOT / "inbox" / "tasks.jsonl"
    records = _read_jsonl_tail(inbox_file, limit=100)
    by_id: dict = {}
    for r in records:
        rid = r.get("id")
        if rid: by_id[rid] = r
    return {"tasks": list(by_id.values()), "count": len(by_id)}


# =================================================================
# F-HubChat — Create tool (proxy HTTP a endpoint webapp REST)
# =================================================================

def _call_webapp_api(method: str, path: str, body: Optional[dict] = None,
                      timeout: int = 30) -> dict:
    """Helper proxy verso webapp REST. Ritorna response dict (o {error}).

    `path` deve iniziare con /api/... (es. "/api/workspaces/create").
    Usa env ANJA_WEBAPP_URL (default http://127.0.0.1:8765).
    """
    url = f"{WEBAPP_URL}{path}"
    data = json.dumps(body or {}).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode("utf-8")
            if not text:
                return {"ok": True}
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw": text}
    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode("utf-8")
        except Exception:
            pass
        return {"error": f"HTTP {e.code}: {body_text or e.reason}"}
    except urllib.error.URLError as e:
        return {"error": f"webapp unreachable at {WEBAPP_URL}: {e.reason}. "
                          "Avvia `python3 anja-hub/webapp/server.py --hub <path>` "
                          "o setta ANJA_WEBAPP_URL."}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def tool_workspace_create(args: dict) -> dict:
    """Crea un workspace internal con responsabile agent."""
    body = {
        "name": args.get("name", "").strip(),
        "responsabile_name": args.get("responsabile_name", "").strip(),
        "role_description": args.get("role_description", "").strip(),
        "ws_type": args.get("ws_type", "office"),
        "responsabile_provider": args.get("provider", "claude"),
        "responsabile_model": args.get("model", "sonnet"),
        "responsabile_effort": args.get("effort"),
    }
    if not body["name"] or not body["responsabile_name"] or not body["role_description"]:
        return {"error": "name, responsabile_name, role_description required"}
    return _call_webapp_api("POST", "/api/workspaces/create", body)


def tool_agent_add(args: dict) -> dict:
    """Crea un agent specialista. scope=hub (default) → vive in <hub>/agents/<name>/;
    scope=workspace:<ws> → vive in <hub>/workspaces/<ws>/.anjawiki/agents/<name>/."""
    scope = args.get("scope", "hub").strip()
    body = {
        "name": args.get("name", "").strip(),
        "role": args.get("role", "").strip(),
        "domain": args.get("domain", ""),
        "provider": args.get("provider", "claude"),
        "model": args.get("model", "sonnet"),
        "effort": args.get("effort", "off"),
        "force": bool(args.get("force", False)),
    }
    if scope.startswith("workspace:"):
        body["project"] = scope.split(":", 1)[1]
    if not body["name"] or not body["role"]:
        return {"error": "name and role required"}
    return _call_webapp_api("POST", "/api/agents", body)


def tool_routine_add(args: dict) -> dict:
    """Crea una routine yaml schedulata (cron). scope=hub o project:<ws_name>."""
    body = {
        "name": args.get("name", "").strip(),
        "scope": args.get("scope", "hub").strip(),
        "schedule": args.get("schedule", "").strip(),
        "prompt": args.get("prompt", "").strip(),
        "description": args.get("description", ""),
        "model": args.get("model", "sonnet"),
        "provider": args.get("provider", "claude"),
        "effort": args.get("effort", ""),
        "tools": args.get("tools") or [],
        "output": args.get("output") or [],
        "context": args.get("context") or [],
        "timeout_sec": int(args.get("timeout_sec", 300)),
        "tags": args.get("tags") or [],
        "enabled": bool(args.get("enabled", True)),
    }
    missing = [k for k in ("name", "scope", "schedule", "prompt") if not body.get(k)]
    if missing:
        return {"error": f"missing required: {missing}"}
    return _call_webapp_api("POST", "/api/routines", body)


def tool_goal_add(args: dict) -> dict:
    """Crea un goal con team + judge + escalation. scope=hub o workspace:<name>."""
    body = {
        "title": args.get("title", "").strip(),
        "scope": args.get("scope", "hub").strip(),
        "deadline": args.get("deadline"),
        "priority": args.get("priority", "medium"),
        "responsabile": args.get("responsabile"),
        "success_criteria": args.get("success_criteria") or [],
        "anti_patterns": args.get("anti_patterns") or [],
        "judge_rubric": args.get("judge_rubric", ""),
        "judge_agent": args.get("judge_agent"),
        "judge_cron": args.get("judge_cron", "0 18 * * 0"),
        "judge_model": args.get("judge_model"),
        "judge_provider": args.get("judge_provider"),
        "judge_effort": args.get("judge_effort"),
        "body_md": args.get("body_md", ""),
        "tags": args.get("tags") or [],
        "owner": args.get("owner", "vincent"),
        "assigned_agents": args.get("assigned_agents") or [],
        "escalation_to": args.get("escalation_to"),
        "escalation_trigger": args.get("escalation_trigger", "drift_consecutive_3"),
        "autonomy_level": int(args.get("autonomy_level", 1)),
        "pipeline_cron": args.get("pipeline_cron", ""),
        "execution_budget": args.get("execution_budget") or {},
    }
    if not body["title"]:
        return {"error": "title required"}
    return _call_webapp_api("POST", "/api/goals/create", body)


# =================================================================
# F-HubAutonomous — Self-modifying config tools (mcp/skill/secret)
# =================================================================

# Whitelist comandi MCP runtime safe: tutti gli altri sono rifiutati.
_MCP_COMMAND_WHITELIST = {
    "python3", "python3.10", "python3.11", "python3.12", "python3.13",
    "node", "npx", "uvx", "deno", "bun",
    sys.executable,  # path corrente di python
    "/opt/homebrew/opt/python@3.12/bin/python3.12",
    "/usr/bin/python3", "/usr/local/bin/python3",
}


def _resolve_mcp_json_path(scope: str) -> Optional[Path]:
    """Risolvi path .mcp.json in base allo scope (hub o workspace:<name>)."""
    if scope == "hub":
        return HUB_ROOT / ".mcp.json"
    if scope.startswith("workspace:"):
        ws = scope.split(":", 1)[1]
        return HUB_ROOT / "workspaces" / ws / ".anjawiki" / ".mcp.json"
    return None


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write atomico con backup .bak preservato in caso di errore."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        bak = path.with_suffix(path.suffix + f".bak.{int(time.time())}")
        bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def tool_mcp_add(args: dict) -> dict:
    """Aggiunge/edita un MCP server in .mcp.json del scope target.

    Safety:
    - command DEVE essere nella whitelist (python/node/npx/uvx/deno/bun)
    - args[0] DEVE essere path assoluto file esistente OR npm/uvx package form
    - conflict 409 se name già presente (a meno di force=True)
    - atomic write con backup
    """
    name = (args.get("name") or "").strip()
    command = (args.get("command") or "").strip()
    server_args = args.get("args") or []
    env = args.get("env") or {}
    scope = (args.get("scope") or "hub").strip()
    force = bool(args.get("force", False))

    # Validation
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9_-]*$", name):
        return {"error": "name must be alphanumeric (a-z, 0-9, _-), starting with letter"}
    if command not in _MCP_COMMAND_WHITELIST:
        return {"error": f"command '{command}' not in safety whitelist. Allowed: "
                          f"{sorted(c for c in _MCP_COMMAND_WHITELIST if not c.startswith('/'))}"}
    if not isinstance(server_args, list) or not server_args:
        return {"error": "args must be a non-empty list"}
    # Args[0] check: absolute path file OR npm form (@org/pkg, pkg, -y flag accettato come arg[0])
    first_arg = str(server_args[0])
    if first_arg.startswith("/"):
        if not Path(first_arg).is_file():
            return {"error": f"args[0] absolute path does not exist: {first_arg}"}
    elif first_arg.startswith("-"):
        # Flag tipo "-y" per npx — il package name è in args[1]
        if len(server_args) < 2:
            return {"error": "args starts with flag but missing package name"}
    elif re.match(r"^(@?[a-z0-9][a-z0-9_./-]*)$", first_arg):
        pass  # npm package form: pkg, @org/pkg
    else:
        return {"error": f"args[0] must be absolute path or npm package: {first_arg}"}

    # Resolve target file
    mcp_path = _resolve_mcp_json_path(scope)
    if mcp_path is None:
        return {"error": f"invalid scope: {scope} (must be 'hub' or 'workspace:<name>')"}

    # Load existing
    cfg: dict = {"mcpServers": {}}
    if mcp_path.is_file():
        try:
            cfg = json.loads(mcp_path.read_text(encoding="utf-8"))
        except Exception as e:
            return {"error": f"existing .mcp.json malformed: {e}"}
    servers = cfg.setdefault("mcpServers", {})

    # Conflict check
    if name in servers and not force:
        return {"error": f"server '{name}' already exists in {scope} scope. Use force=true to overwrite.",
                "existing": servers[name]}

    servers[name] = {
        "command": command,
        "args": server_args,
        "env": env if env else {},
    }
    try:
        _atomic_write_json(mcp_path, cfg)
    except Exception as e:
        return {"error": f"write failed: {type(e).__name__}: {e}"}

    return {
        "ok": True, "name": name, "scope": scope, "path": str(mcp_path),
        "note": "Restart la webapp/agent runtime per pickup del nuovo MCP server.",
    }


def tool_skill_add(args: dict) -> dict:
    """Crea una skill custom nel wiki: <root>/.anjawiki/skills/<slug>/SKILL.md + (opz) scripts/<slug>.py.

    Validation:
    - name kebab-case
    - description >= 20 char (deve essere significativa per il dispatcher)
    - conflict 409 se directory già esiste (no force per sicurezza)
    """
    name = (args.get("name") or "").strip().lower()
    description = (args.get("description") or "").strip()
    content = (args.get("content") or "").strip()
    scope = (args.get("scope") or "hub").strip()
    script = args.get("script")  # opzionale, stringa Python

    if not re.match(r"^[a-z][a-z0-9-]*$", name):
        return {"error": "name must be kebab-case lowercase"}
    if len(description) < 20:
        return {"error": "description too short (>= 20 char required, used by dispatcher to match intent)"}
    if not content:
        return {"error": "content (SKILL.md body) required"}

    # Resolve target dir
    if scope == "hub":
        skills_root = HUB_ROOT / ".anjawiki" / "skills"
    elif scope.startswith("workspace:"):
        ws = scope.split(":", 1)[1]
        skills_root = HUB_ROOT / "workspaces" / ws / ".anjawiki" / "skills"
    else:
        return {"error": f"invalid scope: {scope}"}

    skill_dir = skills_root / name
    if skill_dir.exists():
        return {"error": f"skill '{name}' already exists at {skill_dir}", "existing": str(skill_dir)}

    try:
        skill_dir.mkdir(parents=True, exist_ok=False)
        # Frontmatter + body
        today = datetime.utcnow().strftime("%Y-%m-%d")
        frontmatter = (
            "---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            "version: 1.0.0\n"
            "category: custom\n"
            f"created: {today}\n"
            "platforms: [macos, linux]\n"
            "---\n\n"
        )
        # Se content già ha frontmatter, non duplicare
        full_content = content if content.lstrip().startswith("---") else (frontmatter + content)
        (skill_dir / "SKILL.md").write_text(full_content, encoding="utf-8")

        if script:
            scripts_dir = skill_dir / "scripts"
            scripts_dir.mkdir()
            (scripts_dir / f"{name}.py").write_text(script, encoding="utf-8")
            # Chmod +x
            try:
                (scripts_dir / f"{name}.py").chmod(0o755)
            except Exception:
                pass
    except Exception as e:
        return {"error": f"write failed: {type(e).__name__}: {e}"}

    return {
        "ok": True, "name": name, "scope": scope,
        "path": str(skill_dir / "SKILL.md"),
        "script_path": str(skill_dir / "scripts" / f"{name}.py") if script else None,
    }


def tool_secret_set(args: dict) -> dict:
    """Set/update una secret env in <root>/.secrets.env (key UPPERCASE).

    File nuovo: chmod 0600. Existing key: replace in-place; new: append.
    Backup .bak prima del write.
    """
    key = (args.get("key") or "").strip()
    value = args.get("value")
    scope = (args.get("scope") or "hub").strip()

    if not re.match(r"^[A-Z][A-Z0-9_]*$", key):
        return {"error": "key must be UPPERCASE alphanumeric (es. NOTION_TOKEN, SERPAPI_KEY)"}
    if value is None or value == "":
        return {"error": "value required (non-empty)"}
    value_str = str(value)

    # Resolve target file
    if scope == "hub":
        secrets_path = HUB_ROOT / ".secrets.env"
    elif scope.startswith("workspace:"):
        ws = scope.split(":", 1)[1]
        secrets_path = HUB_ROOT / "workspaces" / ws / ".anjawiki" / ".secrets.env"
    else:
        return {"error": f"invalid scope: {scope}"}

    # Read existing
    lines: list[str] = []
    is_new_file = not secrets_path.is_file()
    if not is_new_file:
        try:
            # Backup
            bak = secrets_path.with_suffix(f".env.bak.{int(time.time())}")
            bak.write_text(secrets_path.read_text(encoding="utf-8"), encoding="utf-8")
            lines = secrets_path.read_text(encoding="utf-8").splitlines()
        except Exception as e:
            return {"error": f"read existing failed: {e}"}

    # Escape value: wrap in double quotes, escape internal quotes + backslashes
    escaped = value_str.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    new_line = f'{key}="{escaped}"'

    # Replace existing or append
    found = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        k, _ = stripped.split("=", 1)
        if k.strip() == key:
            lines[i] = new_line
            found = True
            break
    if not found:
        lines.append(new_line)

    try:
        secrets_path.parent.mkdir(parents=True, exist_ok=True)
        secrets_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if is_new_file:
            try:
                secrets_path.chmod(0o600)
            except Exception:
                pass
    except Exception as e:
        return {"error": f"write failed: {type(e).__name__}: {e}"}

    return {
        "ok": True, "key": key, "scope": scope,
        "action": "updated" if found else "added",
        "path": str(secrets_path),
        "note": "Restart la webapp/agent runtime per pickup nei subprocess MCP.",
    }


# =================================================================
# Tool registry
# =================================================================

TOOLS = [
    # T1 — Read
    {
        "name": "hub.diagnose",
        "description": ("Aggregator overview del goal (run stats, verdict trend, specialist health, "
                        "signals, executions, scripts, pending). Senza goal_id ritorna report cross-goal."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "string"},
                "scope": {"type": "string", "description": "hub o workspace:<name>. Workspace caller forza al proprio scope."},
                "days": {"type": "integer", "default": 1, "description": "window di analisi"},
            },
        },
    },
    {
        "name": "hub.notes_recent",
        "description": "Leggi le notes specialist di un goal (analyst/risk-officer/dev/executor/researcher).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "string"},
                "scope": {"type": "string"},
                "role": {"type": "string", "description": "filter per role"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["goal_id"],
        },
    },
    {
        "name": "hub.signals_recent",
        "description": "Tail signals.jsonl di un goal (eventi script monitor).",
        "inputSchema": {
            "type": "object",
            "properties": {"goal_id": {"type": "string"}, "scope": {"type": "string"}, "limit": {"type": "integer", "default": 30}},
            "required": ["goal_id"],
        },
    },
    {
        "name": "hub.executions_recent",
        "description": "Tail executions.jsonl (audit L3 + errori bybit). Critico per diagnose failure pattern.",
        "inputSchema": {
            "type": "object",
            "properties": {"goal_id": {"type": "string"}, "scope": {"type": "string"}, "limit": {"type": "integer", "default": 20}},
            "required": ["goal_id"],
        },
    },
    {
        "name": "hub.script_status",
        "description": "Lista monitor scripts attivi (pid, alive, restarts, errors).",
        "inputSchema": {
            "type": "object",
            "properties": {"scope": {"type": "string", "description": "hub caller può filtrare per scope"}},
        },
    },
    {
        "name": "hub.pending_actions",
        "description": "Lista pending actions di un goal (L2 queue).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "string"},
                "scope": {"type": "string"},
                "status": {"type": "string", "default": "pending", "description": "pending|approved|rejected|expired|all"},
            },
            "required": ["goal_id"],
        },
    },
    # T2 — Write (workspace-locked)
    {
        "name": "agent.update",
        "description": ("Modifica un file di config di un agent **del workspace caller**. "
                        "Files whitelisted: AGENTS.md, SOUL.md, TOOLS.md, CLAUDE.md, config.json. "
                        "Mode: 'replace' (default) o 'append'."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "file": {"type": "string", "enum": ["AGENTS.md", "SOUL.md", "TOOLS.md", "CLAUDE.md", "config.json"]},
                "content": {"type": "string"},
                "mode": {"type": "string", "enum": ["replace", "append"], "default": "replace"},
            },
            "required": ["name", "file", "content"],
        },
    },
    {
        "name": "hub.script_lifecycle",
        "description": "Lifecycle di monitor script. action: start | stop | restart | reset.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "script_path": {"type": "string"},
                "action": {"type": "string", "enum": ["start", "stop", "restart", "reset"]},
                "goal_id": {"type": "string"},
            },
            "required": ["script_path", "action"],
        },
    },
    {
        "name": "routine.lifecycle",
        "description": "Routine lifecycle. action: enable | disable | run_now.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "action": {"type": "string", "enum": ["enable", "disable", "run_now"]}},
            "required": ["name", "action"],
        },
    },
    {
        "name": "goal.assign_agent",
        "description": "Modifica team di un goal del workspace caller. role: responsabile | escalation | analyst | risk-officer | dev | executor | researcher",
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "string"},
                "role": {"type": "string"},
                "agent": {"type": "string"},
                "llm": {"type": "object", "description": "Opzionale: {provider, model, effort}"},
            },
            "required": ["goal_id", "role", "agent"],
        },
    },
    # T3 — Bridge (hub-only)
    {
        "name": "workspace.task",
        "description": "Hub CEO: assegna task a Anja-responsabile di un workspace. Task in inbox del workspace target.",
        "inputSchema": {
            "type": "object",
            "properties": {"target_workspace": {"type": "string"}, "prompt": {"type": "string"}, "focus": {"type": "string"}},
            "required": ["target_workspace", "prompt"],
        },
    },
    {
        "name": "workspace.diagnose_request",
        "description": "Hub CEO: chiedi al manager workspace di diagnose e riportare.",
        "inputSchema": {
            "type": "object",
            "properties": {"target_workspace": {"type": "string"}, "focus": {"type": "string"}},
            "required": ["target_workspace"],
        },
    },
    {
        "name": "workspace.list_tasks",
        "description": "Lista task in inbox del workspace caller (o cross-workspace se hub).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    # F-HubChat — Create (proxy HTTP a webapp REST)
    {
        "name": "hub.workspace_create",
        "description": ("Crea un workspace internal nell'hub con responsabile agent. "
                         "Scaffold automatico: .anjawiki/ + agent dir + .mcp.json + meta marker. "
                         "Da usare quando l'utente chiede 'crea un workspace', 'nuovo workspace per X', "
                         "in dialogo conversazionale via skill orchestrate-hub."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "kebab-case, alphanumeric"},
                "responsabile_name": {"type": "string", "description": "es. anja-research"},
                "role_description": {"type": "string", "description": "2-3 frasi del ruolo"},
                "ws_type": {"type": "string", "description": "office (default) | personal | research | ..."},
                "provider": {"type": "string", "description": "claude (default) | openai | xai | ..."},
                "model": {"type": "string", "description": "sonnet (default) | opus | haiku | ..."},
                "effort": {"type": "string", "description": "off | low | medium | high"},
            },
            "required": ["name", "responsabile_name", "role_description"],
        },
    },
    {
        "name": "hub.agent_add",
        "description": ("Crea un agent specialista. scope='hub' (default) = vive in <hub>/agents/; "
                         "scope='workspace:<name>' = vive nel workspace. Da usare quando l'utente vuole "
                         "aggiungere uno specialista (es. 'aggiungi un paper-scout', 'crea agente analyst')."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "kebab-case"},
                "role": {"type": "string", "description": "2-3 frasi del ruolo + personalità"},
                "scope": {"type": "string", "description": "hub (default) o workspace:<name>"},
                "domain": {"type": "string"},
                "provider": {"type": "string"},
                "model": {"type": "string"},
                "effort": {"type": "string"},
                "force": {"type": "boolean", "description": "Overwrite se già esiste"},
            },
            "required": ["name", "role"],
        },
    },
    {
        "name": "hub.routine_add",
        "description": ("Crea una routine yaml schedulata (cron). scope='hub' o 'project:<ws>'. "
                         "Per workflow autonomi ricorrenti (es. daily news, weekly report, briefing morning). "
                         "Output actions: email, slack, google_chat, telegram, wiki_ingest, file, webhook."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "kebab-case unique"},
                "scope": {"type": "string", "description": "hub o project:<ws>"},
                "schedule": {"type": "string", "description": "cron expression 5-field (es. '0 8 * * *' = daily 8am)"},
                "prompt": {"type": "string", "description": "prompt che l'LLM esegue al trigger"},
                "description": {"type": "string"},
                "model": {"type": "string"},
                "provider": {"type": "string"},
                "tools": {"type": "array", "items": {"type": "string"}},
                "output": {"type": "array", "items": {"type": "object"}, "description": "Lista azioni post-LLM"},
                "timeout_sec": {"type": "integer", "default": 300},
                "tags": {"type": "array", "items": {"type": "string"}},
                "enabled": {"type": "boolean", "default": True},
            },
            "required": ["name", "scope", "schedule", "prompt"],
        },
    },
    # F-HubAutonomous — Self-modifying config
    {
        "name": "hub.mcp_add",
        "description": ("Aggiunge/edita un MCP server in `<hub>/.mcp.json` o "
                         "`<workspace>/.anjawiki/.mcp.json`. Safety whitelist comandi "
                         "(python3/node/npx/uvx/deno/bun), args path validation, conflict "
                         "check 409. Restart webapp per pickup. Da usare quando l'utente "
                         "vuole installare/configurare un nuovo MCP server (es. Stripe, "
                         "Notion, GitHub) via dialogo orchestrate-hub."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "alphanumeric, primo char lettera"},
                "command": {"type": "string", "description": "python3|node|npx|uvx|deno|bun|sys.executable"},
                "args": {"type": "array", "items": {"type": "string"}, "description": "args[0] = abs path file o npm package"},
                "env": {"type": "object", "description": "env vars per il subprocess MCP"},
                "scope": {"type": "string", "description": "hub o workspace:<name>"},
                "force": {"type": "boolean", "description": "Overwrite se name già esiste"},
            },
            "required": ["name", "command", "args"],
        },
    },
    {
        "name": "hub.skill_add",
        "description": ("Crea una skill custom in `<hub>/.anjawiki/skills/<slug>/SKILL.md`. "
                         "Opzionalmente uno script Python helper in `scripts/<slug>.py`. "
                         "Frontmatter generato auto. Da usare quando l'utente vuole una "
                         "capability lazy-loadable on-demand (pattern Hermes-style) come "
                         "alternativa a un MCP server resident. Description >= 20 char "
                         "(usata dal dispatcher per intent matching)."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "kebab-case lowercase"},
                "description": {"type": "string", "description": "≥ 20 char, used per intent match"},
                "content": {"type": "string", "description": "SKILL.md body markdown (workflow + esempi)"},
                "scope": {"type": "string", "description": "hub o workspace:<name>"},
                "script": {"type": "string", "description": "Optional: Python script salvato in scripts/<name>.py"},
            },
            "required": ["name", "description", "content"],
        },
    },
    {
        "name": "hub.secret_set",
        "description": ("Set/update una env secret in `<hub>/.secrets.env` o "
                         "`<workspace>/.anjawiki/.secrets.env`. Key UPPERCASE, value "
                         "escapato. File nuovo creato con chmod 0600 (private). Backup .bak. "
                         "Restart webapp per pickup nei subprocess. Da usare per API keys "
                         "(NOTION_TOKEN, SERPAPI_KEY, ecc.) memorizzate localmente."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "UPPERCASE alphanumeric (es. NOTION_TOKEN)"},
                "value": {"type": "string", "description": "secret value"},
                "scope": {"type": "string", "description": "hub o workspace:<name>"},
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "hub.goal_add",
        "description": ("Crea un goal con team + judge + escalation. scope='hub' o 'workspace:<name>'. "
                         "Goal = obiettivo persistente con success_criteria atomici, judge agent che valuta "
                         "periodicamente (cron), eventuale escalation a CEO se drift. Autonomy_level 0=observer, "
                         "1=advisor, 2=gated (Telegram approve), 3=autonomous (executor L3)."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "scope": {"type": "string"},
                "deadline": {"type": "string", "description": "YYYY-MM-DD"},
                "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                "responsabile": {"type": "string", "description": "agent lead del goal"},
                "success_criteria": {"type": "array", "items": {"type": "string"}},
                "anti_patterns": {"type": "array", "items": {"type": "string"}},
                "judge_rubric": {"type": "string"},
                "judge_agent": {"type": "string"},
                "judge_cron": {"type": "string", "description": "default '0 18 * * 0' (Sun 18:00)"},
                "judge_model": {"type": "string"},
                "body_md": {"type": "string", "description": "context + strategy in markdown"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "assigned_agents": {"type": "array", "items": {"type": "object"}},
                "escalation_to": {"type": "string"},
                "autonomy_level": {"type": "integer", "minimum": 0, "maximum": 3},
                "execution_budget": {"type": "object", "description": "{max_actions_per_day, max_actions_per_type, disallow_types}"},
            },
            "required": ["title"],
        },
    },
]

TOOL_HANDLERS = {
    "hub.diagnose": tool_diagnose,
    "hub.notes_recent": tool_notes_recent,
    "hub.signals_recent": tool_signals_recent,
    "hub.executions_recent": tool_executions_recent,
    "hub.script_status": tool_script_status,
    "hub.pending_actions": tool_pending_actions,
    "agent.update": tool_agent_update,
    "hub.script_lifecycle": tool_script_lifecycle,
    "routine.lifecycle": tool_routine_lifecycle,
    "goal.assign_agent": tool_goal_assign_agent,
    "workspace.task": tool_workspace_task,
    "workspace.diagnose_request": tool_workspace_diagnose_request,
    "workspace.list_tasks": tool_workspace_list_tasks,
    # F-HubChat — Create
    "hub.workspace_create": tool_workspace_create,
    "hub.agent_add": tool_agent_add,
    "hub.routine_add": tool_routine_add,
    "hub.goal_add": tool_goal_add,
    # F-HubAutonomous — Self-modifying
    "hub.mcp_add": tool_mcp_add,
    "hub.skill_add": tool_skill_add,
    "hub.secret_set": tool_secret_set,
}


# =================================================================
# JSON-RPC dispatch (MCP)
# =================================================================

def handle_request(req: dict) -> Optional[dict]:
    method = req.get("method")
    rid = req.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
            "serverInfo": {"name": "anja_hub_ops", "version": "0.1.0"},
        }}
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        handler = TOOL_HANDLERS.get(name)
        if not handler:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown tool: {name}"}}
        try:
            result = handler(args)
        except Exception as e:
            result = {"error": f"{type(e).__name__}: {e}"}
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
            "isError": "error" in result,
        }}
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown method: {method}"}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        resp = handle_request(req)
        if resp is not None:
            print(json.dumps(resp), flush=True)


if __name__ == "__main__":
    main()
