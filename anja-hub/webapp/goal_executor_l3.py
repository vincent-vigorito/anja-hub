"""goal_executor_l3.py — Phase C — Autonomous executor (L3, domain-agnostic).

Quando un goal è autonomy_level=3 e il pipeline executor emette pending_actions:
1. Verifica execution_budget (max actions/day, custom limits per dominio)
2. Verifica killswitch (drift consecutivi → auto-downgrade a L2)
3. Esegue l'action chiamando un MCP tool generico via discovery
4. Audit completo in executions.jsonl

Schema atteso di una pending_action.payload:
    {
      "mcp_server": "<server-name>",  # es. "gmail", "notion", "arxiv-mcp", ...
      "mcp_tool":   "<tool-name>",    # es. "send_message", "place_order", ...
      "args":       { ... }            # dict argomenti del tool
    }

Il dispatcher è **domain-agnostic**: nessun handler hardcoded. Il judge decide
quale tool MCP invocare e con quali argomenti; L3 lo invoca e logga il risultato.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
from pathlib import Path
from typing import Optional


def _emit_activity(hub_path: Path, scope: str, goal_id: str, event_type: str, msg: str,
                  level: str = "info", payload: Optional[dict] = None) -> None:
    try:
        import goal_io
        goal_io.append_activity(hub_path, scope, goal_id, {
            "agent": "executor-l3",
            "level": level,
            "event_type": event_type,
            "msg": msg,
            **({"payload": payload} if payload else {}),
        })
    except Exception:
        pass


def _verdicts_streak_drift(journal_entries: list, n: int = 3) -> bool:
    """True se gli ultimi N verdict sono tutti drift/blocked."""
    if not journal_entries or len(journal_entries) < n:
        return False
    last_n = journal_entries[-n:]
    return all((e.get("verdict") in ("drift", "blocked")) for e in last_n)


async def _call_mcp_tool(hub_path: Path, server_name: str, tool_name: str, args: dict) -> dict:
    """Invoca un MCP tool in-process leggendo lo script dalla `.mcp.json` dell'hub.

    Domain-agnostic: il server MCP deve esporre `TOOL_HANDLERS: dict[str, callable]`
    (convenzione interna del codebase) per essere invocabile via import diretto.
    Per server MCP esterni che non seguono questa convenzione, l'esecuzione
    fallisce con errore esplicito (l'integrazione va wrappata custom).
    """
    try:
        mcp_file = hub_path / ".mcp.json"
        if not mcp_file.is_file():
            return {"error": ".mcp.json not found"}
        cfg = json.loads(mcp_file.read_text(encoding="utf-8"))
        srv = (cfg.get("mcpServers") or {}).get(server_name)
        if not srv:
            return {"error": f"server '{server_name}' not configured in .mcp.json"}
        script = (srv.get("args") or [None])[0]
        env_vars = srv.get("env") or {}
        if not script or not Path(script).is_file():
            return {"error": f"server script not found: {script}"}
        script_dir = str(Path(script).parent)
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        module_name = Path(script).stem
        saved_env = {}
        for k, v in env_vars.items():
            saved_env[k] = os.environ.get(k)
            os.environ[k] = str(v)
        try:
            if module_name in sys.modules:
                mod = sys.modules[module_name]
                importlib.reload(mod)
            else:
                mod = importlib.import_module(module_name)
            handlers = getattr(mod, "TOOL_HANDLERS", None)
            if not isinstance(handlers, dict):
                return {"error": f"server '{server_name}' does not expose TOOL_HANDLERS dict"}
            handler = handlers.get(tool_name)
            if not handler:
                return {"error": f"tool '{tool_name}' not found in server '{server_name}'"}
            res = handler(args)
            if asyncio.iscoroutine(res):
                res = await res
            return res
        finally:
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


async def execute_pending_action(hub_path: Path, scope: str, goal_id: str,
                                action: dict) -> dict:
    """Esegue una pending_action approvata via MCP tool generico.

    payload schema:
        {"mcp_server": "...", "mcp_tool": "...", "args": {...}}

    Restituisce {ok, audit, error?}.
    """
    import goal_io

    a_type = action.get("type", "")
    payload = action.get("payload") or {}

    _emit_activity(hub_path, scope, goal_id, "execution_start",
                   f"executing action {action.get('id')} type={a_type}", "info")

    audit = {
        "action_id": action.get("id"),
        "type": a_type,
        "payload": payload,
        "executed_by": "L3-autonomous",
    }

    mcp_server = payload.get("mcp_server", "").strip()
    mcp_tool = payload.get("mcp_tool", "").strip()
    args = payload.get("args") or {}
    if not mcp_server or not mcp_tool:
        audit["status"] = "failed"
        audit["error"] = "missing mcp_server/mcp_tool in payload"
        goal_io.append_execution(hub_path, scope, goal_id, audit)
        _emit_activity(hub_path, scope, goal_id, "execution_error",
                       audit["error"], "error", payload=audit)
        return {"ok": False, "error": audit["error"], "audit": audit}

    try:
        res = await _call_mcp_tool(hub_path, mcp_server, mcp_tool, args)
    except Exception as e:
        audit["status"] = "failed"
        audit["error"] = f"{type(e).__name__}: {e}"
        goal_io.append_execution(hub_path, scope, goal_id, audit)
        _emit_activity(hub_path, scope, goal_id, "execution_error",
                       f"execution raised: {audit['error']}", "error", payload=audit)
        return {"ok": False, "error": audit["error"], "audit": audit}

    audit["mcp_response"] = res
    if isinstance(res, dict) and res.get("error"):
        audit["status"] = "failed"
        audit["error"] = res["error"]
        goal_io.append_execution(hub_path, scope, goal_id, audit)
        _emit_activity(hub_path, scope, goal_id, "execution_error",
                       f"mcp rejected: {res['error']}", "error", payload=audit)
        return {"ok": False, "error": res["error"], "audit": audit}

    audit["status"] = "ok"
    goal_io.append_execution(hub_path, scope, goal_id, audit)
    _emit_activity(hub_path, scope, goal_id, "execution_success",
                   f"executed: {a_type} via {mcp_server}.{mcp_tool}",
                   "success", payload=audit)

    try:
        goal_io.resolve_pending_action(hub_path, scope, goal_id, action.get("id", ""),
                                       resolution="executed",
                                       note=f"{mcp_server}.{mcp_tool} ok",
                                       by="L3-autonomous")
    except Exception:
        pass

    return {"ok": True, "audit": audit}


async def process_l3_actions(hub_path: Path, scope: str, goal_id: str) -> dict:
    """Per un goal L3: scansiona pending_actions, verifica budget+killswitch, esegui.

    Da chiamare dal pipeline executor dopo aver scritto le pending_actions.
    """
    import goal_io

    g = goal_io.read_goal(hub_path, scope, goal_id)
    if not g:
        return {"error": "goal not found"}

    meta = g["meta"]
    autonomy = int(meta.get("autonomy_level", 1) or 1)
    if autonomy < 3:
        return {"skipped": "autonomy < L3"}

    if _verdicts_streak_drift(g.get("journal_entries", []) or [], n=3):
        _emit_activity(hub_path, scope, goal_id, "killswitch",
                       "3 drift consecutivi: auto-downgrade L3→L2", "error")
        goal_io.update_goal(hub_path, scope, goal_id, {"autonomy_level": 2})
        return {"killswitch": True, "downgraded_to": 2}

    pending = goal_io.list_pending_actions(hub_path, scope, goal_id, status="pending")
    if not pending:
        return {"executed": 0, "note": "no pending actions"}

    budget = meta.get("execution_budget") or {}
    executed = []
    skipped_budget = []
    for action in pending:
        check = goal_io.check_execution_budget(hub_path, scope, goal_id, budget)
        if not check.get("ok"):
            _emit_activity(hub_path, scope, goal_id, "budget_exceeded",
                          f"action {action.get('id')} skipped: {check.get('reason')}",
                          "warn", payload=check)
            goal_io.resolve_pending_action(hub_path, scope, goal_id, action.get("id", ""),
                                          resolution="rejected",
                                          note=f"budget: {check.get('reason')}",
                                          by="L3-budget-check")
            skipped_budget.append(action.get("id"))
            continue
        res = await execute_pending_action(hub_path, scope, goal_id, action)
        if res.get("ok"):
            executed.append(action.get("id"))

    return {
        "autonomy": 3,
        "executed": len(executed),
        "executed_ids": executed,
        "skipped_budget": skipped_budget,
        "total_pending": len(pending),
    }
