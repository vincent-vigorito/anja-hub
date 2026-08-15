"""goal_scheduler.py — periodic scanner che esegue judge sui goals attivi (Fase 18.B).

Architettura:
  - Async task lanciato all'avvio webapp (server.py lifespan)
  - Ogni TICK_SEC (default 60s) rilegge tutti goal attivi cross-scope
  - Per ogni goal: se `judge_cron` matcha l'ora corrente E ultimo judge > tot da matching tick → fire
  - Lock file per scheduling tracking: <hub>/goals/.scheduler_state.json
    Schema: {"<scope_kind>:<scope_target>:<gid>": "<last_fire_ts_iso>"}
  - Chiama `goal_judge.run_judge_async` direttamente (no subprocess overhead)
  - Skip se goal archived/paused

Stdlib only + croniter (già richiesto da anja-routines).
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


TICK_SEC = 60
STATE_FILENAME = ".scheduler_state.json"

# Lazy croniter import (riusato da anja-routines)
def _croniter():
    try:
        from croniter import croniter
        return croniter
    except ImportError:
        return None


def _state_path(hub_path: Path) -> Path:
    return hub_path / "goals" / STATE_FILENAME


def _load_state(hub_path: Path) -> dict:
    p = _state_path(hub_path)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(hub_path: Path, state: dict):
    p = _state_path(hub_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _should_fire(cron_expr: str, last_fire_iso: Optional[str], now: datetime, tick_window_sec: int) -> bool:
    """True se il cron expression match in [last_fire, now] window.

    Se mai fired: True se prossimo match cron è entro tick_window_sec prima di ora.
    """
    # Skip stringy null / empty expressions (YAML serialization "null" o "None")
    if not cron_expr or str(cron_expr).strip().lower() in ("none", "null", "~"):
        return False
    croniter_cls = _croniter()
    if not croniter_cls:
        return False
    # Determina punto di partenza per iter
    if last_fire_iso:
        try:
            last = datetime.fromisoformat(last_fire_iso.replace("Z", "+00:00"))
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
        except Exception:
            last = now - timedelta_safe(seconds=tick_window_sec * 2)
    else:
        last = now - timedelta_safe(seconds=tick_window_sec * 2)
    try:
        it = croniter_cls(cron_expr, last)
        nxt = it.get_next(datetime)
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=timezone.utc)
        # Fire se prossimo cron tick è già passato (>=) e non ancora in futuro lontano
        return last <= nxt <= now
    except Exception as e:
        print(f"[goal_scheduler] cron parse error '{cron_expr}': {e}", flush=True)
        return False


def timedelta_safe(seconds: int):
    """Helper localizzato per evitare import inline."""
    from datetime import timedelta
    return timedelta(seconds=seconds)


async def _scan_and_fire(hub_path: Path):
    """One tick: enumera goal, fire judge dove serve."""
    # Lazy import per evitare cycle con server.py
    if str(Path(__file__).parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).parent))
    import goal_io
    from goal_judge import run_judge_async

    state = _load_state(hub_path)
    now = datetime.now(timezone.utc)
    changed = False

    # Phase A — Routing: pipeline_cron usa run_pipeline_async, judge_cron usa run_judge_async legacy
    try:
        from goal_office import run_pipeline_async
        _has_pipeline = True
    except Exception:
        _has_pipeline = False

    # Lista tutti goal cross-scope active
    goals = goal_io.list_goals(hub_path, scope=None, status="active")
    for g in goals:
        scope = g.get("scope") or "hub"
        gid = g["id"]
        # Salta goal in pausa o L0 osservatori (no firing automatico)
        if g.get("status") == "paused":
            continue

        # === Pipeline cron (preferito se assigned_agents + pipeline_cron set) ===
        pipeline_cron = g.get("pipeline_cron") or ""
        has_team = bool(g.get("assigned_agents"))
        if _has_pipeline and pipeline_cron and has_team:
            state_key = f"{scope}::{gid}::pipeline"
            last = state.get(state_key)
            if _should_fire(pipeline_cron, last, now, TICK_SEC):
                print(f"[goal_scheduler] firing PIPELINE for {state_key} (cron='{pipeline_cron}')", flush=True)
                try:
                    res = await run_pipeline_async(hub_path, scope, gid)
                    if res.get("error"):
                        print(f"[goal_scheduler] pipeline error: {res['error']}", flush=True)
                    else:
                        print(f"[goal_scheduler] pipeline {state_key} → verdict={res.get('verdict')}", flush=True)
                        try:
                            await _check_escalation(hub_path, scope, gid)
                        except Exception as e:
                            print(f"[goal_scheduler] escalation check failed: {e}", flush=True)
                except Exception as e:
                    print(f"[goal_scheduler] pipeline exception: {e}", flush=True)
                state[state_key] = now.isoformat()
                changed = True

        # === Judge cron (legacy single-shot O daily review separato) ===
        judge_cron = g.get("judge_cron") or ""
        if judge_cron:
            state_key = f"{scope}::{gid}"  # backcompat key (no suffix)
            last = state.get(state_key)
            if _should_fire(judge_cron, last, now, TICK_SEC):
                # Se ho già pipelined questo tick, skippa judge (evita doppi run sovrapposti)
                pipeline_key = f"{scope}::{gid}::pipeline"
                if state.get(pipeline_key) != now.isoformat():
                    print(f"[goal_scheduler] firing JUDGE for {state_key} (cron='{judge_cron}')", flush=True)
                    try:
                        res = await run_judge_async(hub_path, scope, gid)
                        if res.get("error"):
                            print(f"[goal_scheduler] judge error: {res['error']}", flush=True)
                        else:
                            print(f"[goal_scheduler] judge {state_key} → verdict={res.get('verdict')}", flush=True)
                            try:
                                await _check_escalation(hub_path, scope, gid)
                            except Exception as e:
                                print(f"[goal_scheduler] escalation check failed: {e}", flush=True)
                    except Exception as e:
                        print(f"[goal_scheduler] judge exception: {e}", flush=True)
                    state[state_key] = now.isoformat()
                    changed = True

    if changed:
        _save_state(hub_path, state)


async def _check_escalation(hub_path: Path, scope: str, goal_id: str):
    """Se ultimi 3 verdict consecutivi sono 'drift' e escalation_to settato → escalate."""
    import goal_io
    g = goal_io.read_goal(hub_path, scope, goal_id)
    if not g:
        return
    meta = g["meta"]
    if meta.get("escalated"):
        return  # Already escalated, skip
    target = meta.get("escalation_to")
    if not target:
        return
    trigger = meta.get("escalation_trigger") or "drift_consecutive_3"
    entries = g.get("journal_entries") or []
    if not entries:
        return
    # Default trigger: 3 consecutive drift
    if trigger == "drift_consecutive_3":
        last3 = entries[-3:]
        if len(last3) >= 3 and all(e.get("verdict") == "drift" for e in last3):
            # Escalate: cambia judge agent + flag escalated
            goal_io.update_goal(hub_path, scope, goal_id, {
                "responsabile": target,
                "judge_provider": "",  # reset to hub defaults
                "judge_model": "",
                "escalated": True,
            })
            # Append journal nota escalation
            goal_io.append_journal(
                hub_path, scope, goal_id, "blocked",
                "escalation_system",
                f"🚨 ESCALATED to {target} dopo 3 verdict 'drift' consecutivi. Nuovo judge prenderà in carico al prossimo cron tick."
            )
            print(f"[goal_scheduler] escalated {scope}::{goal_id} to {target}", flush=True)
            try:
                import notification_bus as _nb
                _nb.publish(
                    hub_path,
                    source="goal",
                    category="action_needed",
                    title=f"Goal escalated → {target}",
                    body=f"{scope}::{goal_id} dopo 3 drift consecutivi. Trigger: {trigger}",
                    action={"label": "View goal", "url": f"/goals/{scope}/{goal_id}", "type": "navigate"},
                    payload={"goal_id": goal_id, "scope": scope, "escalation_to": target, "trigger": trigger},
                    scope=scope if scope.startswith("workspace:") else "hub",
                )
            except Exception:
                pass


async def goal_scheduler_loop(hub_path: Path, tick_sec: int = TICK_SEC):
    """Async loop infinito. Da lanciare nel lifespan FastAPI startup."""
    print(f"[goal_scheduler] starting loop (tick={tick_sec}s) hub={hub_path}", flush=True)
    while True:
        try:
            await _scan_and_fire(hub_path)
        except asyncio.CancelledError:
            print("[goal_scheduler] cancelled", flush=True)
            raise
        except Exception as e:
            print(f"[goal_scheduler] scan error: {type(e).__name__}: {e}", flush=True)
        await asyncio.sleep(tick_sec)


# CLI for manual one-shot scan
def _main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub", required=True)
    ap.add_argument("--one-shot", action="store_true")
    args = ap.parse_args()
    hub = Path(args.hub)
    if args.one_shot:
        asyncio.run(_scan_and_fire(hub))
    else:
        asyncio.run(goal_scheduler_loop(hub))


if __name__ == "__main__":
    _main()
