"""goal_judge.py — judge engine per Goals (Fase 18.A MVP).

Pattern: dato un goal_id + scope, costruisce prompt con context, chiama LLM
configurato (per default: hub defaults), parsa risposta strutturata, appende
al journal del goal.

Standalone callable: `python goal_judge.py --hub <path> --goal-id <id> --scope <s>`
Usato da routine cron auto-registrate per il `judge_cron` di ogni goal.

Async API per webapp endpoint `/api/goals/{id}/judge`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# Import locali (stessa dir)
_THIS_DIR = Path(__file__).parent.resolve()
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))


JUDGE_SYSTEM_PROMPT = """Sei {judge_agent}, judge del goal "{goal_title}".

Stato: {status} · deadline: {deadline} ({days_remaining} giorni rimanenti)
Scope: {scope}

Success criteria:
{success_criteria}

Strategia / contesto del goal:
{goal_body}

Ultimi verdict (max 3):
{recent_journal}

Compito:
1. Valuta lo stato del goal contro i success criteria. Sii concreto: numeri, fatti, evidenze.
2. Determina UN verdict tra: on_track, drift, blocked, achieved, failed.
3. Identifica pattern (cosa funziona, cosa no, cosa serve cambiare).
4. (Opzionale) proponi 1-3 azioni concrete.

Output strettamente JSON, nessun testo prima/dopo:
{{
  "verdict": "on_track|drift|blocked|achieved|failed",
  "summary": "1-2 frasi di sintesi",
  "observations": ["bullet 1", "bullet 2", "..."],
  "metrics": {{"key": "value", ...}},
  "suggested_actions": ["action 1", "..."],
  "auto_kanban_tasks": [
    {{"title": "...", "body": "...", "priority": "low|medium|high"}}
  ],
  "proposed_goal_edits": {{
    "deadline": "YYYY-MM-DD",
    "priority": "low|medium|high",
    "success_criteria": ["..."],
    "judge_cron": "..."
  }}
}}

`auto_kanban_tasks`: task da creare ora (es. azioni di recovery in caso drift).
`proposed_goal_edits`: cambiamenti suggeriti al goal stesso (NON applicati automaticamente, finiscono in inbox per approvazione utente).
Lascia vuoti i campi opzionali se non hai suggerimenti.

## Baseline persistente (importante)
Se il BRIEFING qui sotto indica "Baseline (non ancora settata)", al primo run ricava
la baseline iniziale del dominio (es. valore di partenza, snapshot dello stato) e
includila in `metrics`, ad esempio `metrics.start_value`, `metrics.start_ts_ms`, ecc.
Il sistema la persiste automaticamente in reflections.md — write-once, mai sovrascritta.

## CRITICO — filtro storico
Se i tool MCP che chiami restituiscono dati storici (eventi/transazioni/stati),
DEVI filtrare in base a `start_ts_ms` (dal BRIEFING qui sotto): tieni solo
record `ts >= start_ts_ms`. IGNORA dati pre-goal: NON contribuiscono allo scoring.

## Kanban closure (accountability)
Nel JSON output puoi includere `kanban_to_close: [task_id1, task_id2]` per task
che ritieni COMPLETATI (es. "Daily journal review" se hai trovato l'entry oggi).
Il sistema li chiude automaticamente.

CRITICO — REGOLE OUTPUT (le RIPETO perché sono importanti):
- Dopo aver chiamato i tool MCP che ti servono, l'ULTIMA cosa che scrivi DEVE essere il JSON.
- NIENTE testo prima o dopo il JSON. Niente "Ecco il risultato:", niente markdown fences ```json.
- SOLO l'oggetto JSON, deve iniziare con `{{` e finire con `}}`.
- Se non hai abbastanza dati, restituisci comunque JSON con verdict="blocked" e summary che spiega cosa manca.
- NON scrivere mai una risposta conversational. Tu sei un judge automatizzato, non un chatbot."""


def _build_prompt(goal: dict, judge_agent: str, hub_path: Optional[Path] = None) -> tuple[str, str]:
    """Build (system_prompt, user_prompt) per il judge call."""
    meta = goal["meta"]
    # F1 — passa hub_path al briefing builder via meta-key opaco
    if hub_path is not None:
        meta = dict(meta)
        meta["_hub_path"] = str(hub_path)
    title = meta.get("title", "")
    status = meta.get("status", "active")
    deadline = meta.get("deadline", "")
    success_criteria = meta.get("success_criteria") or []
    sc_block = "\n".join(f"- {c}" for c in success_criteria) if success_criteria else "(nessun success criterion definito)"
    body = goal.get("body", "").strip()[:2000]
    entries = goal.get("journal_entries", []) or []
    recent = entries[-3:]
    recent_block = "(nessun verdict precedente)"
    if recent:
        chunks = []
        for e in recent:
            chunks.append(f"[{e['ts']}] {e['verdict']} — {e['body'][:400]}")
        recent_block = "\n".join(chunks)
    # Days remaining
    days_remaining = "?"
    if deadline:
        try:
            d = datetime.strptime(deadline, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            days_remaining = str((d - datetime.now(timezone.utc)).days)
        except Exception:
            pass
    # M1 — Anti-patterns + judge rubric injection
    ap_block = ""
    anti_patterns = meta.get("anti_patterns") or []
    if anti_patterns:
        ap_block = "\n\n## Anti-pattern (cose che invalidano il goal — marca `blocked` se rilevi una)\n" + \
                   "\n".join(f"- {x}" for x in anti_patterns)
    rubric_block = ""
    judge_rubric = (meta.get("judge_rubric") or "").strip()
    if judge_rubric:
        rubric_block = f"\n\n## Judge rubric — workflow di valutazione (segui questi step esatti)\n{judge_rubric}"

    # F1+F2+F3 — Iniezione BRIEFING (la "lavagna" condivisa): baseline + trend + kanban + reflections
    briefing_block = ""
    if meta.get("_hub_path"):
        try:
            import goal_io as _gio_briefing
            briefing_block = _gio_briefing.build_briefing_block(
                hub_path=Path(meta["_hub_path"]),
                scope=meta.get("scope", "hub"),
                goal_id=meta.get("id", ""),
                goal_meta=meta,
                journal_entries=entries,
            )
        except Exception as _e:
            briefing_block = f"\n\n_(briefing unavailable: {_e})_\n"

    sys_prompt = JUDGE_SYSTEM_PROMPT.format(
        judge_agent=judge_agent,
        goal_title=title,
        status=status,
        deadline=deadline or "n/a",
        days_remaining=days_remaining,
        scope=meta.get("scope", "hub"),
        success_criteria=sc_block,
        goal_body=body or "(nessuna strategia documentata)",
        recent_journal=recent_block,
    ) + ap_block + rubric_block + briefing_block
    user_prompt = f"Esegui il judging del goal '{title}' ora ({datetime.utcnow().strftime('%Y-%m-%d')}) e produci JSON come da spec. Usa i tool MCP a tua disposizione (es. memory.recall, eventuali tool di dominio configurati nel workspace) per ricavare dati reali invece di dichiarare 'no access'."
    return sys_prompt, user_prompt


def _parse_judge_output(text: str) -> dict:
    """Estrai JSON dall'output del judge. Robusto a wrap markdown / prefissi / fence multipli."""
    if not text or not text.strip():
        return {"verdict": "blocked", "summary": "(empty output)", "raw": ""}
    cleaned = text.strip()

    # 1) Prova fenced ```json...```  (più affidabile)
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except Exception:
            pass

    # 2) Brace matching balanciato (trova il PIU' LUNGO blocco { ... } valido)
    # Utile se c'è prima testo conversational seguito da JSON
    best = None
    depth = 0
    start = -1
    for i, ch in enumerate(cleaned):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = cleaned[start:i+1]
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, dict) and parsed.get("verdict"):
                        return parsed
                    # Tieni come fallback se ha qualche campo utile
                    if best is None and isinstance(parsed, dict):
                        best = parsed
                except Exception:
                    pass
                start = -1
    if best:
        return best

    # 3) Fallback: nessun JSON trovato — preserva preview del testo per debug
    return {
        "verdict": "blocked",
        "summary": f"(no JSON in output) preview: {text[:300]}",
        "raw": text[:1000],
    }


def _format_verdict_md(parsed: dict) -> str:
    """JSON parsed → markdown body per journal."""
    lines = []
    summary = parsed.get("summary") or ""
    if summary:
        lines.append(summary)
        lines.append("")
    obs = parsed.get("observations") or []
    if obs:
        lines.append("**Observations**:")
        for o in obs:
            lines.append(f"- {o}")
        lines.append("")
    metrics = parsed.get("metrics") or {}
    if metrics:
        lines.append("**Metrics**:")
        for k, v in metrics.items():
            lines.append(f"- {k}: {v}")
        lines.append("")
    actions = parsed.get("suggested_actions") or []
    if actions:
        lines.append("**Suggested actions**:")
        for a in actions:
            lines.append(f"- {a}")
    return "\n".join(lines).strip()


async def run_judge_async(hub_path: Path, scope: str, goal_id: str,
                          provider_override: Optional[str] = None,
                          model_override: Optional[str] = None,
                          agent_override: Optional[str] = None) -> dict:
    """Esegue judge per goal. Ritorna {verdict, summary, journal_appended}.

    agent_override: M4 — invoca un agent specifico (es. specialist) invece del judge default.
    """
    import goal_io
    g = goal_io.read_goal(hub_path, scope, goal_id)
    if not g:
        return {"error": f"goal '{goal_id}' not found in scope '{scope}'"}

    meta = g["meta"]
    # M1 — Judge agent può essere il responsabile O un judge_agent dedicato
    judge_agent = agent_override or meta.get("judge_agent") or meta.get("responsabile") or "anja"
    judge_model = model_override or meta.get("judge_model") or ""
    judge_provider = provider_override or meta.get("judge_provider") or ""
    judge_effort = meta.get("judge_effort") or ""

    # M3 — emit start event
    def _emit(event_type: str, msg: str, level: str = "info", payload: Optional[dict] = None, agent: Optional[str] = None):
        try:
            goal_io.append_activity(hub_path, scope, goal_id, {
                "agent": agent or judge_agent,
                "level": level,
                "event_type": event_type,
                "msg": msg,
                **({"payload": payload} if payload else {}),
            })
        except Exception:
            pass

    _emit("judge_run_start", f"judge_run started (model: {judge_provider or 'default'}/{judge_model or 'default'})")

    # Hub defaults se non specificato sul goal
    if not judge_provider or not judge_model:
        try:
            cfg_path = hub_path / "config.json"
            if cfg_path.is_file():
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                judge_provider = judge_provider or cfg.get("default_provider", "claude")
                judge_model = judge_model or cfg.get("default_model", "haiku")
        except Exception:
            judge_provider = judge_provider or "claude"
            judge_model = judge_model or "haiku"

    sys_prompt, user_prompt = _build_prompt(g, judge_agent, hub_path=hub_path)

    # Call appropriate LLM router based on provider
    text_chunks: list[str] = []
    try:
        if judge_provider == "openai_oauth":
            from openai_oauth_client import stream_via_openai_oauth
            async for ev in stream_via_openai_oauth(
                user_prompt=user_prompt,
                system_prompt=sys_prompt,
                cwd=hub_path,
                model=judge_model or "gpt-5.5",
                timeout_sec=180,
                allowed_tools=[],  # no tools, judge è pure reasoning
            ):
                if ev.get("type") == "text":
                    text_chunks.append(ev.get("content", ""))
                elif ev.get("type") == "error":
                    return {"error": f"judge stream error: {ev.get('message')}"}
        elif judge_provider == "claude":
            # M4 — Passa per MCPScoper come una chat normale: il judge eredita tool MCP scoped
            # (memory.recall, kanban, eventuali tool di dominio) invece di essere cieco senza tool.
            from claude_chat import stream_response
            from mcp_scoper import scope_mcps as _scope_mcps
            # M4 fix — alimenta MCPScoper col contesto goal completo (title + criteria + body + tags)
            # così il keyword router può matchare termini di dominio e includere i tool MCP corretti.
            scoper_prompt = " ".join([
                meta.get("title", ""),
                " ".join(meta.get("success_criteria") or []),
                " ".join(meta.get("anti_patterns") or []),
                " ".join(meta.get("tags") or []),
                (meta.get("judge_rubric") or "")[:500],
                (g.get("body") or "")[:1000],
                user_prompt,
            ])
            try:
                scoped, scope_meta = _scope_mcps(
                    hub_path=hub_path,
                    scope_kind=("hub" if scope == "hub" else "workspace"),
                    target_name=(scope.split(":", 1)[1] if scope.startswith("workspace:") else None),
                    cwd=hub_path,
                    user_prompt=scoper_prompt,
                    active_mcps=[],
                    agent_config=None,
                )
                # M4 fix — Strip server di distrazione per il judge:
                # - anja_goals: il judge lavora SUL goal, non ha senso che si chiami
                #   goal.show o goal.list, riceve già il context nel system prompt
                # - anja_memory_core: il judge è stateless, non deve cercare sessions
                # - anja_skills: pure overhead, niente skills per un task così focalizzato
                # Lasciamo: domain MCPs installati nel workspace + anja_code (per calcoli)
                JUDGE_BLOCKED = {"anja_goals", "anja_memory_core", "anja_skills"}
                if scoped:
                    scoped = [s for s in scoped if s not in JUDGE_BLOCKED]
                _emit("mcp_scope", f"MCP scoped: {scoped}", level="tool", payload={"servers": scoped})
            except Exception as e:
                _emit("mcp_scope_error", f"MCPScoper failed: {e}", level="warn")
                scoped = None
            # Allowed tools: native Read/Grep + tutti i mcp__* dei server scoped
            allowed_judge_tools = ["Read", "Grep", "Glob"]
            if scoped:
                allowed_judge_tools += [f"mcp__{s}__*" for s in scoped]
            async for ev in stream_response(
                user_prompt=user_prompt,
                system_prompt=sys_prompt,
                provider="claude",
                model=judge_model or "haiku",
                effort=judge_effort if judge_effort in ("low", "medium", "high") else None,
                cwd=str(hub_path),
                allowed_tools=allowed_judge_tools,
                scoped_servers=scoped,
            ):
                if ev.get("type") == "text":
                    text_chunks.append(ev.get("content", ""))
                elif ev.get("type") == "usage":
                    try:
                        import cost_store
                        cost_store.record_usage_event(hub_path, ev, feature="judge")
                    except Exception:
                        pass
                elif ev.get("type") == "tool_use":
                    _emit("tool_call", f"→ {ev.get('name', '?')}", level="tool", payload={"input_preview": str(ev.get('input', ''))[:200]})
                elif ev.get("type") == "error":
                    _emit("judge_error", f"stream error: {ev.get('message')}", level="error")
                    return {"error": f"judge stream error: {ev.get('message')}"}
        else:
            # Fallback LiteLLM (incluso Ollama)
            from llm_router import stream_via_litellm
            async for ev in stream_via_litellm(
                user_prompt=user_prompt,
                system_prompt=sys_prompt,
                cwd=hub_path,
                provider=judge_provider,
                model=judge_model,
                timeout_sec=180,
                allowed_tools=[],
            ):
                if ev.get("type") == "text":
                    text_chunks.append(ev.get("content", ""))
                elif ev.get("type") == "error":
                    return {"error": f"judge stream error: {ev.get('message')}"}
    except Exception as e:
        _emit("judge_error", f"judge call failed: {type(e).__name__}: {e}", level="error")
        return {"error": f"judge call failed: {type(e).__name__}: {e}"}

    raw_text = "".join(text_chunks)
    parsed = _parse_judge_output(raw_text)
    verdict = parsed.get("verdict") or "blocked"
    if verdict not in goal_io.VALID_VERDICTS:
        verdict = "blocked"
    body_md = _format_verdict_md(parsed)
    if not body_md:
        body_md = raw_text[:1000].strip() or "(empty body)"

    verdict_level = {"on_track": "success", "achieved": "success", "drift": "warn", "blocked": "error", "failed": "error"}.get(verdict, "info")
    _emit("verdict", f"verdict: {verdict}", level=verdict_level, payload={"summary": parsed.get("summary", "")[:200]})
    try:
        import decision_trail
        decision_trail.record(hub_path, actor="judge",
                              trigger=f"judge goal {goal_id} (scope {scope})",
                              decision=verdict, rationale=(parsed.get("summary") or "")[:500],
                              confidence=parsed.get("confidence"), scope=scope, ref=goal_id)
    except Exception:
        pass

    ok = goal_io.append_journal(hub_path, scope, goal_id, verdict, judge_agent, body_md)
    if ok:
        _emit("journal_appended", "wrote journal entry")

    # Phase A — L0 (Observer) gate: skip auto-kanban + proposed_edits writes
    autonomy = int(meta.get("autonomy_level", 1) or 1)
    is_observer = (autonomy == 0)
    if is_observer:
        _emit("autonomy_gate", "L0 Observer mode: auto-kanban + proposed_edits skipped", "info")

    # Fase 18.C — Self-improvement loop: auto-create kanban tasks + queue proposed edits
    created_tasks = []
    auto_tasks = [] if is_observer else (parsed.get("auto_kanban_tasks") or [])
    if auto_tasks:
        try:
            import sys as _sys
            _sys.path.insert(0, str(hub_path / "..")) if hub_path.parent.exists() else None
            from kanban_io import create_task as _kanban_create, find_similar_active as _kanban_similar, add_comment as _kanban_comment
            prio_map = {"low": 0, "medium": 1, "high": 2}
            for t in auto_tasks[:5]:  # cap a 5 per safety
                if not isinstance(t, dict) or not t.get("title"):
                    continue
                try:
                    # Fase B2 — dedup
                    try:
                        similar = _kanban_similar(hub_path, scope=scope, title=t["title"],
                                                   linked_goal=goal_id, threshold=0.75)
                    except Exception:
                        similar = []
                    if similar:
                        top = similar[0]
                        try:
                            _kanban_comment(hub_path, top["id"],
                                            f"[dedup] judge re-emitted similar task ({top['ratio']:.2f}): {t['title'][:120]}",
                                            author=judge_agent)
                        except Exception:
                            pass
                        _emit("auto_kanban_dedup",
                              f"skip duplicate (#{top['id']} ratio={top['ratio']:.2f}): {t['title'][:80]}",
                              level="info", payload={"existing_id": top["id"], "ratio": top["ratio"]})
                        continue
                    task = _kanban_create(
                        hub_path,
                        title=t["title"][:200],
                        body=(t.get("body") or "")[:1000],
                        scope=scope,
                        priority=prio_map.get(t.get("priority", "medium"), 1),
                        assignee=judge_agent,
                        tags=["auto:judge"],
                        metadata={"linked_goal": goal_id},
                    )
                    created_tasks.append({"id": task.get("id"), "title": t["title"]})
                    _emit("auto_kanban", f"auto-kanban created: {t['title'][:80]}", level="tool", payload={"task_id": task.get("id")})
                    # Also append to goal linked_tasks
                    try:
                        cur = goal_io.read_goal(hub_path, scope, goal_id)
                        if cur:
                            linked = cur["meta"].get("linked_tasks") or []
                            linked.append(task.get("id"))
                            goal_io.update_goal(hub_path, scope, goal_id, {"linked_tasks": linked})
                    except Exception:
                        pass
                except Exception as e:
                    print(f"[goal_judge] kanban auto-task error: {e}", flush=True)
        except ImportError:
            pass

    proposed_edits = {} if is_observer else (parsed.get("proposed_goal_edits") or {})
    queued_suggestion = None
    if proposed_edits and isinstance(proposed_edits, dict):
        # Cleanup empty/null
        clean_edits = {k: v for k, v in proposed_edits.items() if v not in (None, "", [])}
        if clean_edits:
            queued_suggestion = _append_suggestion(hub_path, scope, goal_id, judge_agent, clean_edits, parsed.get("summary", ""))
            _emit("proposed_edit", f"proposed_edit queued: {list(clean_edits.keys())}", level="warn", payload={"edits": clean_edits})

    # F2 — Auto-persist baseline emessa nei metrics (write-once)
    metrics = parsed.get("metrics") or {}
    if isinstance(metrics, dict):
        baseline_candidates = {}
        for k in ("equity_start_usdt", "equity_start_usd", "start_ts_ms", "pnl_baseline_usdt"):
            if k in metrics and metrics[k] not in (None, "", "N/D"):
                try:
                    baseline_candidates[k] = float(metrics[k])
                except Exception:
                    pass
        if baseline_candidates:
            saved = goal_io.save_baseline_to_reflections(hub_path, scope, goal_id, baseline_candidates)
            if saved:
                _emit("baseline_saved", f"baseline persisted to reflections.md: {list(baseline_candidates.keys())}", level="success")

    # F3 — Kanban closure loop: chiudi i task che il judge ha marcato come done
    closed_tasks = []
    to_close = parsed.get("kanban_to_close") or []
    if isinstance(to_close, list) and to_close:
        try:
            import kanban_io as _ki
            for tid in to_close:
                try:
                    tid_int = int(tid)
                    # Verifica che il task sia tra i linked di questo goal (safety)
                    linked = (meta.get("linked_tasks") or [])
                    if tid_int not in linked:
                        continue
                    res = _ki.update_status(hub_path, tid_int, "done", note=f"auto-closed by judge ({judge_agent})")
                    if res:
                        closed_tasks.append(tid_int)
                        _emit("kanban_closed", f"kanban #{tid_int} auto-closed", level="success", payload={"task_id": tid_int})
                except Exception as e:
                    print(f"[goal_judge] kanban close error #{tid}: {e}", flush=True)
        except ImportError:
            pass

    _emit("judge_run_end", f"judge_run completed: {verdict}", level=verdict_level)

    return {
        "id": goal_id,
        "scope": scope,
        "verdict": verdict,
        "agent": judge_agent,
        "model": f"{judge_provider}/{judge_model}",
        "journal_appended": ok,
        "summary": parsed.get("summary", ""),
        "suggested_actions": parsed.get("suggested_actions", []),
        "auto_kanban_tasks_created": created_tasks,
        "queued_suggestion": queued_suggestion,
    }


# ============================================================
# Suggested actions inbox (Fase 18.C)
# ============================================================

def _suggestions_path(hub_path: Path) -> Path:
    return hub_path / "goals" / ".suggestions_inbox.json"


def _append_suggestion(hub_path: Path, scope: str, goal_id: str, agent: str,
                        edits: dict, summary: str) -> dict:
    """Append a suggestion to <hub>/goals/.suggestions_inbox.json."""
    p = _suggestions_path(hub_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    inbox = []
    if p.is_file():
        try:
            inbox = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            inbox = []
    suggestion = {
        "id": f"sug_{int(time.time())}_{goal_id[:12]}",
        "goal_id": goal_id,
        "scope": scope,
        "proposed_by": agent,
        "ts": datetime.utcnow().isoformat(),
        "edits": edits,
        "rationale": summary[:500],
        "status": "pending",  # pending | approved | rejected
    }
    inbox.append(suggestion)
    p.write_text(json.dumps(inbox, indent=2), encoding="utf-8")
    return suggestion


def list_suggestions(hub_path: Path, status: Optional[str] = None) -> list:
    p = _suggestions_path(hub_path)
    if not p.is_file():
        return []
    try:
        inbox = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    if status:
        return [s for s in inbox if s.get("status") == status]
    return inbox


def resolve_suggestion(hub_path: Path, suggestion_id: str, action: str,
                        note: str = "") -> dict:
    """action: 'approve' | 'reject'. Applica edits se approve."""
    if action not in ("approve", "reject"):
        return {"error": "action must be 'approve' or 'reject'"}
    p = _suggestions_path(hub_path)
    if not p.is_file():
        return {"error": "inbox empty"}
    try:
        inbox = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"error": "inbox malformed"}
    target = None
    for s in inbox:
        if s.get("id") == suggestion_id:
            target = s
            break
    if not target:
        return {"error": f"suggestion '{suggestion_id}' not found"}
    if target.get("status") != "pending":
        return {"error": f"already {target.get('status')}"}
    target["status"] = "approved" if action == "approve" else "rejected"
    target["resolved_ts"] = datetime.utcnow().isoformat()
    target["resolution_note"] = note
    if action == "approve":
        import goal_io
        goal_io.update_goal(hub_path, target["scope"], target["goal_id"], target["edits"])
        # Log to reflections
        goal_io.append_reflection(
            hub_path, target["scope"], target["goal_id"],
            f"✓ Applied judge suggestion ({target['proposed_by']}): {json.dumps(target['edits'], ensure_ascii=False)}\n\nNote: {note}"
        )
    else:
        import goal_io
        goal_io.append_reflection(
            hub_path, target["scope"], target["goal_id"],
            f"✗ Rejected suggestion ({target['proposed_by']}): {json.dumps(target['edits'], ensure_ascii=False)}\n\nNote: {note}"
        )
    p.write_text(json.dumps(inbox, indent=2), encoding="utf-8")
    return target


def run_judge_sync(hub_path: Path, scope: str, goal_id: str,
                   provider_override: Optional[str] = None,
                   model_override: Optional[str] = None) -> dict:
    """Sync wrapper per CLI / routine runner."""
    return asyncio.run(run_judge_async(hub_path, scope, goal_id, provider_override, model_override))


# ============================================================
# CLI entry — invocato da routine cron
# ============================================================

def _main():
    ap = argparse.ArgumentParser(description="Run judge per un goal anja.")
    ap.add_argument("--hub", required=True, help="Hub path")
    ap.add_argument("--goal-id", required=True)
    ap.add_argument("--scope", default="hub", help="'hub' o 'workspace:<name>'")
    ap.add_argument("--provider", default=None)
    ap.add_argument("--model", default=None)
    args = ap.parse_args()
    res = run_judge_sync(Path(args.hub), args.scope, args.goal_id, args.provider, args.model)
    print(json.dumps(res, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    _main()
