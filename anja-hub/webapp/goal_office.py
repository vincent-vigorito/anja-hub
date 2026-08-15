"""goal_office.py — Specialist sequential pipeline (F4).

Orchestrazione "ufficio": per ogni goal con assigned_agents, invoca i ruoli in
sequenza ordinata (analyst → risk-officer → executor → researcher), ognuno con
il suo LLM dedicato + system prompt specifico. Ogni specialist:

1. Legge briefing condiviso + reflections + note degli specialist precedenti del run
2. Produce output strutturato JSON
3. Salva note in `<goal-dir>/notes/<ts>-<role>.md`

Al termine il "executor"/judge integra tutto e produce verdict come al solito,
ma il prompt include esplicitamente le note del team.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# Same import bootstrap pattern as goal_judge.py
_THIS_DIR = Path(__file__).parent.resolve()
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))


# Ordine canonico dei ruoli — solo questi sono invocati nella pipeline.
# 'dev' (D1) sta tra risk-officer e executor: scrive monitor scripts per setup approvati.
# Altri ruoli ('researcher' etc.) sono invocati separatamente (es. solo a 22:00 UTC).
PIPELINE_ROLE_ORDER = ["analyst", "risk-officer", "dev", "executor"]


# ============================================================
# Role-specific prompts
# ============================================================

ANALYST_PROMPT = """Sei `{agent_name}`, ANALYST del team che lavora sul goal "{goal_title}".

# Tuo ruolo specifico
Sei lo specialista che osserva lo stato del dominio e propone 0-N **proposte
concrete** che il risk-officer potrà approvare e l'executor eseguire. Il
dominio specifico è definito dal briefing condiviso (sezione `# Briefing`).

NON valutare il goal nel complesso (lo fa l'executor), NON validare rischio (lo fa
il risk-officer). SOLO osserva + proponi.

# Cosa devi fare (workflow)
1. Raccogli dati live tramite i tool MCP configurati per questo workspace.
2. Sintetizza il context attuale (1-2 frasi).
3. Proponi 0-N proposte concrete e accionabili. Meglio zero che proposte deboli.

# Output JSON strettamente formato
{{
  "context": "1-2 frasi sintesi del context attuale",
  "proposals": [
    {{
      "title": "label breve della proposta",
      "rationale": "1-2 frasi: perché ora",
      "confidence": "low|medium|high",
      "params": {{ /* parametri specifici della proposta — schema deciso dal dominio */ }}
    }}
  ],
  "skipped": [
    {{"label": "...", "reason": "..."}}
  ]
}}

REGOLE OUTPUT:
- `proposals` vuoto `[]` se nessuna proposta di qualità.
- Cap consigliato 3 per evitare rumore.
- SOLO JSON, niente testo prima/dopo, niente fences ```.

# Briefing condiviso e decisioni umane
{briefing_block}
"""


RISK_OFFICER_PROMPT = """Sei `{agent_name}`, RISK-OFFICER del team che lavora sul goal "{goal_title}".

# Tuo ruolo specifico
Veto power sulle proposte dell'analyst. Verifichi ogni proposta contro le discipline
rules. Output: per ogni proposta, APPROVE | REJECT | MODIFY con motivo.

# Discipline rules (anti-pattern)
{anti_patterns}

# Workflow
1. Raccogli context attuale tramite tool MCP del dominio (stato corrente del sistema).
2. Per ogni proposta dell'analyst: valuta contro discipline + context generale.
3. Restituisci verdict + eventuali modifiche.

# Proposte dell'analyst (questo run)
{analyst_notes_summary}

# Output JSON
{{
  "decisions": [
    {{
      "proposal_index": 0,
      "verdict": "APPROVE|REJECT|MODIFY",
      "reason": "1-2 frasi specifiche (es. 'viola anti-pattern X')",
      "modifications": {{ /* solo se MODIFY: dict con i campi da sovrascrivere */ }}
    }}
  ],
  "context_state": {{ /* snapshot rilevante del sistema osservato */ }},
  "general_red_flags": ["..."]
}}

REGOLE: SOLO JSON, niente testo prima/dopo.

# Briefing condiviso
{briefing_block}
"""


DEV_PROMPT = """Sei `{agent_name}`, DEV del team che lavora sul goal "{goal_title}".

# Tuo ruolo specifico
Scrivi script Python di **monitoraggio/automazione** (always-on monitor, trigger
condizionati, helper periodici) che girano FUORI dalla pipeline LLM (più veloci,
più affidabili, più economici).

NON esegui tu le action — produci script che il supervisor avvia in background.
Gli script emettono signal in `<goal-dir>/signals.jsonl` che l'executor legge.

# 🎯 PATH CRITICO — scripts vanno salvati TASSATIVAMENTE in
```
{scripts_target_dir}
```
NON usare altri path. Il supervisor cerca solo qui per lanciarli. Usa `workspace.write_file`
con esattamente questo prefix path. Il nome file finale: `<name>.py`.

# Cosa devi fare (workflow)

1. Leggi le proposte approvate dal risk-officer (notes sotto).
2. Decidi quali beneficiano di un monitor automatico (event-driven o periodico).
3. **Scrivi i file .py** usando `anja_code.execute_python` per validarli SINTATTICAMENTE (NON eseguire side-effect).
4. Poi usa `workspace.write_file` per salvarli al path canonico sopra.

# Template script monitor (esempio domain-agnostic)

```python
#!/usr/bin/env python3
\"\"\"monitor_example.py — emette signal su evento di interesse.\"\"\"
import os, time, json
from pathlib import Path

# Path canonico per signals (passed via env var dal runtime)
SIGNAL_FILE = Path(os.environ['GOAL_SIGNAL_FILE'])
POLL_SEC = 30

def emit_signal(event_type: str, payload: dict):
    rec = {{
        'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'script': Path(__file__).name,
        'event_type': event_type,
        'payload': payload,
    }}
    with open(SIGNAL_FILE, 'a') as f:
        f.write(json.dumps(rec) + '\\n')

# (Add domain-specific polling + emit logic here)
```

# Output JSON
{{
  "scripts_written": [
    {{"path": "{scripts_target_dir}/<name>.py", "purpose": "...", "schedule": "always_on|on_event|periodic"}}
  ],
  "tools_proposed": ["..."],
  "notes": "1 frase: cosa ho fatto e perché"
}}

REGOLE OUTPUT: SOLO JSON.

# Proposte approvate dal risk-officer (questo run)
{risk_notes_summary}

# Briefing condiviso
{briefing_block}
"""


EXECUTOR_PROMPT_AUTONOMY_BLOCK = """
# Autonomy level corrente: L{autonomy_level}
{autonomy_instructions}
"""

EXECUTOR_PROMPT = """Sei `{agent_name}`, EXECUTOR/JUDGE del team che lavora sul goal "{goal_title}".

# Tuo ruolo specifico
Integri il lavoro del team (analyst + risk-officer) + dati live + briefing condiviso →
produci il VERDICT del goal per questo run.

Stato: {status} · deadline: {deadline} ({days_remaining} giorni rimanenti)

# Success criteria
{success_criteria}

# Anti-patterns
{anti_patterns}

# Workflow
1. Raccogli lo stato corrente del sistema tramite tool MCP del dominio.
2. Filtra i dati in base ai timestamp del BRIEFING (tieni solo quanto rilevante dal goal start).
3. Integra con analyst+risk+dev notes (sotto).
4. Calcola metrics di dominio + verdict.

# CRITICO — filtro storico
Lo stato del sistema può contenere eventi precedenti al goal. Filtra in base ai
timestamp del BRIEFING. Non includere nello scoring eventi pre-goal.

# Notes del team (questo run)
## Analyst output
{analyst_notes_summary}

## Risk-officer output
{risk_notes_summary}

## Dev output (monitor scripts scritti questo run)
{dev_notes_summary}

# Kanban open tasks (a te assegnati, da consumare)
{kanban_open_block}

REGOLA DISPATCHER (B1): se uno dei kanban open qui sopra è stato eseguito/risolto
da questo run, includine l'`id` in `kanban_to_close`. Se è ancora valido ma non
eseguibile ora, lascialo aperto. NON ri-emettere lo stesso task in `auto_kanban_tasks`
(il dedup lo bloccherebbe comunque).

{autonomy_block}

# Output JSON
{{
  "verdict": "on_track|drift|blocked|achieved|failed",
  "summary": "1-2 frasi sintesi",
  "observations": ["..."],
  "metrics": {{ /* metriche di dominio rilevanti per il goal */ }},
  "suggested_actions": ["..."],
  "auto_kanban_tasks": [
    {{"title": "...", "priority": "low|medium|high"}}
  ],
  "kanban_to_close": [],
  "proposed_goal_edits": {{}},
  "pending_actions": [
    {{
      "type": "<action-label>",
      "payload": {{
        "mcp_server": "<server-name>",
        "mcp_tool": "<tool-name>",
        "args": {{ /* argomenti del tool */ }}
      }},
      "rationale": "perché ora",
      "expires_in_min": 15
    }}
  ]
}}

REGOLE OUTPUT:
- Se l'analyst ha proposto azioni approvate dal risk-officer, considera **auto-kanban** per
  trackarle (titolo conciso).
- Se risk-officer ha rejected proposte, citalo nelle observations.
- Se analyst ha trovato 0 proposte, observation tipo "no actionable opportunities detected".
- Per `pending_actions`: usa SEMPRE lo schema `{{mcp_server, mcp_tool, args}}` nel payload
  così l'executor L3 può invocare il tool MCP in autonomia.
- SOLO JSON, niente testo prima/dopo, niente fences ```.

# Briefing condiviso
{briefing_block}
"""


# ============================================================
# Pipeline orchestrator
# ============================================================

def _format_analyst_summary(note: Optional[dict]) -> str:
    """Format domain-agnostic dell'output analyst.

    Schema atteso (definito in ANALYST_PROMPT): {context, proposals[], skipped[]}.
    Fallback compat: vecchio schema crypto-trading {market_context, setups[]}.
    """
    if not note:
        return "(no analyst note this run)"
    out = note.get("output") or {}
    proposals = out.get("proposals") or out.get("setups") or []
    ctx = out.get("context") or out.get("market_context", "")
    if not proposals:
        return f"context: {ctx}\nproposals: NONE"
    lines = [f"context: {ctx}", f"proposals: {len(proposals)}"]
    for i, p in enumerate(proposals):
        title = p.get("title") or p.get("symbol") or "?"
        conf = p.get("confidence", "?")
        lines.append(f"  [{i}] {title} (confidence={conf})")
        if p.get("rationale"):
            lines.append(f"      rationale: {p['rationale']}")
        if p.get("params"):
            try:
                lines.append(f"      params: {json.dumps(p['params'], ensure_ascii=False)[:200]}")
            except Exception:
                pass
    return "\n".join(lines)


def _format_dev_summary(note: Optional[dict]) -> str:
    if not note:
        return "(no dev note this run — no scripts written)"
    out = note.get("output") or {}
    scripts = out.get("scripts_written") or []
    notes = out.get("notes", "")
    if not scripts:
        return f"no scripts written. notes: {notes}"
    lines = [f"scripts written: {len(scripts)}"]
    for s in scripts:
        lines.append(f"  - {s.get('path','?')} :: {s.get('purpose','?')} ({s.get('schedule','?')})")
    if notes:
        lines.append(f"notes: {notes}")
    return "\n".join(lines)


def _format_risk_summary(note: Optional[dict]) -> str:
    if not note:
        return "(no risk-officer note this run)"
    out = note.get("output") or {}
    decisions = out.get("decisions") or []
    portfolio = out.get("portfolio_state") or {}
    red = out.get("general_red_flags") or []
    lines = [
        f"portfolio: positions={portfolio.get('positions_open',0)} "
        f"with_sl={portfolio.get('positions_with_sl',0)} "
        f"recent_losses={portfolio.get('recent_losses_count',0)}",
        f"setup decisions: {len(decisions)}",
    ]
    for d in decisions:
        lines.append(f"  setup[{d.get('setup_index','?')}]: {d.get('verdict','?')} — {d.get('reason','')}")
    if red:
        lines.append(f"red flags: {', '.join(red)}")
    return "\n".join(lines)


_ROLE_TIMEOUT_SEC = {
    "analyst": 90,
    "risk-officer": 90,
    "dev": 120,        # tool calls (execute_python + write_file) → più lento
    "executor": 90,
    "researcher": 60,
}


async def _invoke_role(role: str, agent: str, llm: dict, *,
                       system_prompt: str, user_prompt: str,
                       hub_path: Path, scope: str, goal_id: str,
                       emit_fn) -> str:
    """Chiama un singolo specialist con il suo LLM. Ritorna raw text output.

    Hard timeout per ruolo: evita pipeline che si bloccano.
    """
    provider = (llm or {}).get("provider") or "claude"
    model = (llm or {}).get("model") or "haiku"
    effort = (llm or {}).get("effort") or ""
    role_timeout = _ROLE_TIMEOUT_SEC.get(role, 90)

    async def _do_call() -> str:
        return await _invoke_role_impl(
            role=role, agent=agent, llm=llm,
            system_prompt=system_prompt, user_prompt=user_prompt,
            hub_path=hub_path, scope=scope, goal_id=goal_id,
            emit_fn=emit_fn,
            provider=provider, model=model, effort=effort,
        )

    try:
        return await asyncio.wait_for(_do_call(), timeout=role_timeout)
    except asyncio.TimeoutError:
        emit_fn(role, "timeout", f"{role} hard timeout after {role_timeout}s", "error")
        return ""


async def _invoke_role_impl(role: str, agent: str, llm: dict, *,
                            system_prompt: str, user_prompt: str,
                            hub_path: Path, scope: str, goal_id: str,
                            emit_fn,
                            provider: str, model: str, effort: str) -> str:
    text_chunks: list[str] = []
    try:
        if provider == "openai_oauth":
            from openai_oauth_client import stream_via_openai_oauth
            async for ev in stream_via_openai_oauth(
                user_prompt=user_prompt, system_prompt=system_prompt,
                cwd=hub_path, model=model or "gpt-5.5", timeout_sec=180,
                allowed_tools=[],
            ):
                if ev.get("type") == "text":
                    text_chunks.append(ev.get("content", ""))
                elif ev.get("type") == "error":
                    return ""
        elif provider == "claude":
            from claude_chat import stream_response
            from mcp_scoper import scope_mcps as _scope_mcps
            try:
                scoper_prompt = f"{role} {agent} {user_prompt[:300]}"
                scoped, _ = _scope_mcps(
                    hub_path=hub_path,
                    scope_kind=("hub" if scope == "hub" else "workspace"),
                    target_name=(scope.split(":", 1)[1] if scope.startswith("workspace:") else None),
                    cwd=hub_path, user_prompt=scoper_prompt,
                    active_mcps=[], agent_config=None,
                )
                # Strip distractor servers come fa il judge
                JUDGE_BLOCKED = {"anja_goals", "anja_memory_core", "anja_skills"}
                if scoped:
                    scoped = [s for s in scoped if s not in JUDGE_BLOCKED]
            except Exception:
                scoped = None
            allowed = ["Read", "Grep", "Glob"]
            if scoped:
                allowed += [f"mcp__{s}__*" for s in scoped]
            async for ev in stream_response(
                user_prompt=user_prompt, system_prompt=system_prompt,
                provider="claude", model=model or "haiku",
                effort=effort if effort in ("low", "medium", "high") else None,
                cwd=str(hub_path),
                allowed_tools=allowed, scoped_servers=scoped,
            ):
                if ev.get("type") == "text":
                    text_chunks.append(ev.get("content", ""))
                elif ev.get("type") == "tool_use":
                    emit_fn(role, "tool_call", f"→ {ev.get('name', '?')}", "tool")
                elif ev.get("type") == "error":
                    emit_fn(role, "error", f"stream: {ev.get('message')}", "error")
                    return ""
        else:
            from llm_router import stream_via_litellm
            async for ev in stream_via_litellm(
                user_prompt=user_prompt, system_prompt=system_prompt,
                cwd=hub_path, provider=provider, model=model, timeout_sec=180,
                allowed_tools=[],
            ):
                if ev.get("type") == "text":
                    text_chunks.append(ev.get("content", ""))
                elif ev.get("type") == "error":
                    return ""
    except Exception as e:
        emit_fn(role, "error", f"invoke failed: {type(e).__name__}: {e}", "error")
        return ""
    return "".join(text_chunks)


def _parse_specialist_json(text: str) -> dict:
    """Estrai JSON robusto da output specialist (riusa logica goal_judge._parse_judge_output)."""
    if not text or not text.strip():
        return {}
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except Exception:
            pass
    # brace matching
    depth = 0
    start = -1
    best = None
    for i, ch in enumerate(cleaned):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    parsed = json.loads(cleaned[start:i+1])
                    if isinstance(parsed, dict):
                        if best is None or len(parsed) > len(best):
                            best = parsed
                except Exception:
                    pass
                start = -1
    return best or {}


async def run_pipeline_async(hub_path: Path, scope: str, goal_id: str) -> dict:
    """F4 — Esegue la pipeline completa: analyst → risk-officer → executor.

    Per ogni ruolo nella assigned_agents (filtrato e ordinato per PIPELINE_ROLE_ORDER):
    1. Costruisce prompt con briefing + notes precedenti del run
    2. Invoca LLM dedicato
    3. Parsa output JSON, salva nota
    4. Emit eventi activity
    Alla fine restituisce un dict riassuntivo + chiama il flow standard journal/auto-kanban
    riusando logica goal_judge.
    """
    import goal_io
    import goal_judge

    g = goal_io.read_goal(hub_path, scope, goal_id)
    if not g:
        return {"error": f"goal not found: {goal_id} in {scope}"}

    meta = g["meta"]
    title = meta.get("title", "")
    run_id = f"run-{int(time.time())}"

    def _emit(role: str, event_type: str, msg: str, level: str = "info",
              payload: Optional[dict] = None):
        try:
            goal_io.append_activity(hub_path, scope, goal_id, {
                "agent": role,
                "level": level,
                "event_type": event_type,
                "msg": msg,
                **({"payload": payload} if payload else {}),
            })
        except Exception:
            pass
        # F-Notify: publish persistenti per eventi di interesse end-user.
        # Whitelist: verdict (drift/blocked/failed), pending_action, auto_kanban_dedup.
        try:
            # pending_action: publish canonical da goal_io.write_pending_action (no dup qui)
            interesting = {
                "verdict": ("warn" if level == "warn" else ("error" if level == "error" else "success")),
                "auto_kanban_dedup": "info",
                "l3_error": "error",
            }
            cat = interesting.get(event_type)
            if cat is None:
                return
            # Skip verdict on_track/achieved (success) per ridurre rumore
            if event_type == "verdict" and level == "success":
                return
            import notification_bus as _nb  # local import: lazy
            _nb.publish(
                hub_path,
                source="goal",
                category=cat,
                title=f"{title or goal_id}: {event_type}",
                body=msg[:300],
                action={"label": "View goal", "url": f"/goals/{scope}/{goal_id}", "type": "navigate"},
                payload={"goal_id": goal_id, "scope": scope, "role": role, **(payload or {})},
                scope=scope if scope.startswith("workspace:") else "hub",
            )
        except Exception:
            pass

    _emit("system", "pipeline_start", f"office pipeline started (run_id={run_id})", "info",
          payload={"run_id": run_id, "goal_id": goal_id})

    # === Build common context ===
    # Briefing block
    briefing = ""
    try:
        briefing = goal_io.build_briefing_block(
            hub_path=hub_path, scope=scope, goal_id=goal_id,
            goal_meta=meta, journal_entries=g.get("journal_entries", []),
        )
    except Exception:
        pass

    # Anti-patterns formatted
    aps = meta.get("anti_patterns") or []
    aps_block = "\n".join(f"- {x}" for x in aps) if aps else "(nessuno specifico)"

    # Success criteria
    sc = meta.get("success_criteria") or []
    sc_block = "\n".join(f"- {x}" for x in sc) if sc else "(nessuno definito)"

    # Days remaining
    days_remaining = "?"
    deadline = meta.get("deadline", "")
    if deadline:
        try:
            from datetime import datetime as _dt, timezone as _tz
            d = _dt.strptime(deadline, "%Y-%m-%d").replace(tzinfo=_tz.utc)
            days_remaining = str((d - _dt.now(_tz.utc)).days)
        except Exception:
            pass

    # === Build team map ===
    assigned = meta.get("assigned_agents") or []
    # Dedup by role (1 agent per role)
    team_by_role: dict = {}
    for a in assigned:
        r = (a.get("role") or "").lower()
        if r and r not in team_by_role:
            team_by_role[r] = a

    notes_collected: dict = {}  # role → parsed output dict

    # === STEP 1: Analyst ===
    analyst_cfg = team_by_role.get("analyst")
    if analyst_cfg:
        _emit("analyst", "specialist_start", f"analyst starting (agent: {analyst_cfg.get('agent','?')}, llm: {analyst_cfg.get('llm',{})})", "info")
        sys_p = ANALYST_PROMPT.format(
            agent_name=analyst_cfg.get("agent", "?"),
            goal_title=title,
            briefing_block=briefing,
        )
        user_p = "Scansiona il mercato ora e proponi setup come da spec. Output JSON only."
        raw = await _invoke_role(
            "analyst", analyst_cfg.get("agent", "?"), analyst_cfg.get("llm") or {},
            system_prompt=sys_p, user_prompt=user_p,
            hub_path=hub_path, scope=scope, goal_id=goal_id, emit_fn=_emit,
        )
        parsed = _parse_specialist_json(raw)
        if not parsed:
            _emit("analyst", "parse_error", "no JSON in output, falling back to empty", "warn")
            parsed = {"setups": [], "market_context": "(parse failed)", "_raw_preview": raw[:300]}
        np = goal_io.write_specialist_note(
            hub_path, scope, goal_id,
            role="analyst", agent=analyst_cfg.get("agent", "?"),
            llm=analyst_cfg.get("llm") or {}, run_id=run_id,
            output=parsed, body_md=f"# Analyst note\n\n```json\n{json.dumps(parsed, ensure_ascii=False, indent=2)[:2000]}\n```\n",
        )
        notes_collected["analyst"] = parsed
        n_setups = len(parsed.get("setups") or [])
        _emit("analyst", "specialist_end", f"analyst done: {n_setups} setups proposed", "success",
              payload={"setups_count": n_setups, "note_file": str(np) if np else None})

    # === STEP 2: Risk-officer ===
    risk_cfg = team_by_role.get("risk-officer")
    if risk_cfg:
        _emit("risk-officer", "specialist_start", f"risk-officer starting (agent: {risk_cfg.get('agent','?')})", "info")
        sys_p = RISK_OFFICER_PROMPT.format(
            agent_name=risk_cfg.get("agent", "?"),
            goal_title=title,
            anti_patterns=aps_block,
            analyst_notes_summary=_format_analyst_summary({"output": notes_collected.get("analyst")}),
            briefing_block=briefing,
        )
        user_p = "Valuta i setup dell'analyst contro le discipline rules. Output JSON only."
        raw = await _invoke_role(
            "risk-officer", risk_cfg.get("agent", "?"), risk_cfg.get("llm") or {},
            system_prompt=sys_p, user_prompt=user_p,
            hub_path=hub_path, scope=scope, goal_id=goal_id, emit_fn=_emit,
        )
        parsed = _parse_specialist_json(raw)
        if not parsed:
            _emit("risk-officer", "parse_error", "no JSON in output, falling back", "warn")
            parsed = {"decisions": [], "portfolio_state": {}, "general_red_flags": [], "_raw_preview": raw[:300]}
        np = goal_io.write_specialist_note(
            hub_path, scope, goal_id,
            role="risk-officer", agent=risk_cfg.get("agent", "?"),
            llm=risk_cfg.get("llm") or {}, run_id=run_id,
            output=parsed, body_md=f"# Risk-officer note\n\n```json\n{json.dumps(parsed, ensure_ascii=False, indent=2)[:2000]}\n```\n",
        )
        notes_collected["risk-officer"] = parsed
        approved = sum(1 for d in parsed.get("decisions", []) if d.get("verdict") == "APPROVE")
        rejected = sum(1 for d in parsed.get("decisions", []) if d.get("verdict") == "REJECT")
        modified = sum(1 for d in parsed.get("decisions", []) if d.get("verdict") == "MODIFY")
        _emit("risk-officer", "specialist_end", f"risk-officer done: {approved}✓ approve, {modified}~ modify, {rejected}✗ reject", "success",
              payload={"approved": approved, "rejected": rejected, "modified": modified, "note_file": str(np) if np else None})

    # === STEP 2.5: Dev (D1) — scrive monitor scripts per setup approvati ===
    dev_cfg = team_by_role.get("dev")
    if dev_cfg:
        _emit("dev", "specialist_start", f"dev starting (agent: {dev_cfg.get('agent','?')}, llm: {dev_cfg.get('llm',{})})", "info")
        # Path canonico script: <hub>/scripts/<scope_norm>/<goal_id>/
        # scope='hub'→ 'hub' · scope='workspace:<name>'→ 'workspaces/<name>'
        if scope == "hub":
            _scope_path = "hub"
        elif scope.startswith("workspace:"):
            _scope_path = f"workspaces/{scope.split(':', 1)[1]}"
        else:
            _scope_path = "misc"
        scripts_target_dir = f"scripts/{_scope_path}/{goal_id}"
        sys_p = DEV_PROMPT.format(
            agent_name=dev_cfg.get("agent", "?"),
            goal_title=title,
            scripts_target_dir=scripts_target_dir,
            risk_notes_summary=_format_risk_summary({"output": notes_collected.get("risk-officer")}),
            briefing_block=briefing,
        )
        user_p = "Scrivi gli script monitor/automation necessari per i setup approvati. Output JSON only."
        raw = await _invoke_role(
            "dev", dev_cfg.get("agent", "?"), dev_cfg.get("llm") or {},
            system_prompt=sys_p, user_prompt=user_p,
            hub_path=hub_path, scope=scope, goal_id=goal_id, emit_fn=_emit,
        )
        parsed = _parse_specialist_json(raw)
        if not parsed:
            _emit("dev", "parse_error", "no JSON in output, falling back", "warn")
            parsed = {"scripts_written": [], "tools_proposed": [], "notes": "(parse failed)", "_raw_preview": raw[:300]}
        np = goal_io.write_specialist_note(
            hub_path, scope, goal_id,
            role="dev", agent=dev_cfg.get("agent", "?"),
            llm=dev_cfg.get("llm") or {}, run_id=run_id,
            output=parsed, body_md=f"# Dev note\n\n```json\n{json.dumps(parsed, ensure_ascii=False, indent=2)[:2000]}\n```\n",
        )
        notes_collected["dev"] = parsed
        n_scripts = len(parsed.get("scripts_written") or [])
        _emit("dev", "specialist_end", f"dev done: {n_scripts} scripts written", "success",
              payload={"scripts_count": n_scripts, "note_file": str(np) if np else None})

        # D2 — Auto-start nuovi script via script_runtime
        if n_scripts > 0:
            try:
                from script_runtime import start_script, goal_scripts_dir
                target_dir = goal_scripts_dir(hub_path, scope, goal_id)
                started = []
                for s in parsed.get("scripts_written", []):
                    raw_path = s.get("path", "")
                    # Accept paths relativi (scripts/...) o assoluti
                    if raw_path.startswith("scripts/"):
                        candidate = hub_path / raw_path
                    elif raw_path.startswith("/"):
                        candidate = Path(raw_path)
                    else:
                        candidate = target_dir / Path(raw_path).name
                    # Fallback se non trovato lì: cerca per basename in target_dir
                    if not candidate.is_file():
                        alt = target_dir / Path(raw_path).name
                        if alt.is_file():
                            candidate = alt
                    if candidate.is_file():
                        res = start_script(hub_path, scope, goal_id, candidate)
                        if res.get("ok") and not res.get("already_running"):
                            started.append(candidate.name)
                if started:
                    _emit("system", "scripts_started", f"auto-started {len(started)} scripts: {started}", "success",
                          payload={"started": started})
            except Exception as e:
                _emit("system", "scripts_start_error", f"auto-start failed: {e}", "warn")

    # === STEP 3: Executor (the judge) ===
    exec_cfg = team_by_role.get("executor")
    judge_result = None
    if exec_cfg:
        _emit("executor", "specialist_start", f"executor starting (judge integrator)", "info")
        # Phase B — Inject autonomy instructions
        autonomy_level = int(meta.get("autonomy_level", 1) or 1)
        autonomy_instructions = {
            0: "Sei in modalità OBSERVER. NON proporre pending_actions, NON proporre auto_kanban_tasks, NON proporre proposed_goal_edits. Solo verdict + metrics + observations + summary.",
            1: "Sei in modalità ADVISOR. Puoi proporre auto_kanban_tasks (recovery actions) e proposed_goal_edits. NON proporre pending_actions (non eseguibili a questo livello).",
            2: """Sei in modalità GATED EXECUTOR.

REGOLA CRITICA — pending_actions SOLO per execution IMMEDIATA:
- Condizione necessaria: la proposta è APPROVED dal risk-officer + tutte le precondizioni di dominio sono soddisfatte ora.
- Proposte da MONITORARE (precondizioni non ancora met) → vanno in `auto_kanban_tasks`, NON in pending_actions.
- Ogni `pending_action.payload` DEVE contenere `{mcp_server, mcp_tool, args}` perché L3 possa eseguirla in autonomia.
- L'utente approverà via Telegram/UI prima dell'esecuzione (gating L2).

Includi sempre `rationale` specifico per ogni pending_action.""",
            3: """Sei in modalità AUTONOMOUS.

REGOLA CRITICA — pending_actions SOLO per execution IMMEDIATA:
- Stesso schema di L2: `{mcp_server, mcp_tool, args}` nel payload.
- Proposte da monitorare → `auto_kanban_tasks`, NON pending_actions.
- Proposte pronte da ESEGUIRE → pending_action con payload completo + rationale.

Il sistema eseguirà i pending_actions DIRETTAMENTE entro execution_budget del goal.
Sii ULTRA CONSERVATIVE: meglio zero action che una marginale.
Se hai dubbi → auto_kanban_task (manual review), non pending_action.""",
        }.get(autonomy_level, "Modalità sconosciuta — comportati come Advisor (L1).")
        autonomy_block = EXECUTOR_PROMPT_AUTONOMY_BLOCK.format(
            autonomy_level=autonomy_level,
            autonomy_instructions=autonomy_instructions,
        )

        # Fase B1 — Dispatcher: pull open kanban tasks assignati all'executor per questo goal
        kanban_open_block = "(nessun task aperto)"
        try:
            import kanban_io as _ki_open
            executor_name = exec_cfg.get("agent", "?")
            open_tasks = _ki_open.list_tasks(
                hub_path, scope=scope, status="active",
                assignee=executor_name, linked_goal=goal_id, limit=20,
            )
            if open_tasks:
                lines = []
                for t in open_tasks[:15]:
                    title_short = (t.get("title") or "")[:120]
                    prio_label = {0: "low", 1: "med", 2: "high"}.get(t.get("priority", 1), "med")
                    lines.append(f"- #{t['id']} [{t.get('status')}/{prio_label}] {title_short}")
                kanban_open_block = "\n".join(lines)
                _emit("executor", "dispatcher_inject",
                      f"{len(open_tasks)} open kanban task injected as context",
                      "info", payload={"count": len(open_tasks)})
        except Exception as e:
            _emit("executor", "dispatcher_error", f"could not load open tasks: {e}", "warn")

        sys_p = EXECUTOR_PROMPT.format(
            agent_name=exec_cfg.get("agent", "?"),
            goal_title=title,
            status=meta.get("status", "active"),
            deadline=deadline or "n/a",
            days_remaining=days_remaining,
            success_criteria=sc_block,
            anti_patterns=aps_block,
            analyst_notes_summary=_format_analyst_summary({"output": notes_collected.get("analyst")}),
            risk_notes_summary=_format_risk_summary({"output": notes_collected.get("risk-officer")}),
            dev_notes_summary=_format_dev_summary({"output": notes_collected.get("dev")}),
            briefing_block=briefing,
            kanban_open_block=kanban_open_block,
            autonomy_block=autonomy_block,
        )
        user_p = f"Integra il lavoro del team e produci il verdict del goal '{title}' per oggi. Output JSON only."
        # Riusa _invoke_role per consistency, ma poi processiamo come judge_async per journal/kanban
        raw = await _invoke_role(
            "executor", exec_cfg.get("agent", "?"), exec_cfg.get("llm") or {},
            system_prompt=sys_p, user_prompt=user_p,
            hub_path=hub_path, scope=scope, goal_id=goal_id, emit_fn=_emit,
        )
        parsed = _parse_specialist_json(raw)
        if not parsed or not parsed.get("verdict"):
            _emit("executor", "parse_error", "no valid verdict in output", "error")
            parsed = {"verdict": "blocked", "summary": "(executor parse failed)", "_raw_preview": raw[:300]}

        # Save executor note
        goal_io.write_specialist_note(
            hub_path, scope, goal_id,
            role="executor", agent=exec_cfg.get("agent", "?"),
            llm=exec_cfg.get("llm") or {}, run_id=run_id,
            output=parsed, body_md=f"# Executor verdict\n\n```json\n{json.dumps(parsed, ensure_ascii=False, indent=2)[:2000]}\n```\n",
        )
        notes_collected["executor"] = parsed

        # Append journal (riuso goal_judge helpers per consistency)
        verdict = parsed.get("verdict") or "blocked"
        if verdict not in goal_io.VALID_VERDICTS:
            verdict = "blocked"
        body_md = goal_judge._format_verdict_md(parsed)
        if not body_md:
            body_md = parsed.get("summary", "") or "(empty)"
        goal_io.append_journal(hub_path, scope, goal_id, verdict, exec_cfg.get("agent", "?"), body_md)
        _emit("executor", "verdict", f"verdict: {verdict}",
              "success" if verdict in ("on_track", "achieved") else ("warn" if verdict == "drift" else "error"),
              payload={"summary": parsed.get("summary", "")[:200]})

        # Baseline save
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
                if goal_io.save_baseline_to_reflections(hub_path, scope, goal_id, baseline_candidates):
                    _emit("system", "baseline_saved", f"baseline persisted: {list(baseline_candidates.keys())}", "success")

        # Phase A — L0 gate
        autonomy = int(meta.get("autonomy_level", 1) or 1)
        is_observer = (autonomy == 0)
        if is_observer:
            _emit("system", "autonomy_gate", "L0 Observer mode: auto-kanban + proposed_edits skipped", "info")

        # Auto-kanban
        auto_tasks = [] if is_observer else (parsed.get("auto_kanban_tasks") or [])
        if auto_tasks:
            try:
                import kanban_io as _ki
                prio_map = {"low": 0, "medium": 1, "high": 2}
                for t in auto_tasks[:5]:
                    if not isinstance(t, dict) or not t.get("title"):
                        continue
                    try:
                        # Fase B2 — dedup: skip se task simile attivo già esiste
                        try:
                            similar = _ki.find_similar_active(
                                hub_path, scope=scope, title=t["title"],
                                linked_goal=goal_id, threshold=0.75,
                            )
                        except Exception:
                            similar = []
                        if similar:
                            top = similar[0]
                            try:
                                _ki.add_comment(hub_path, top["id"],
                                                f"[dedup] judge re-emitted similar task ({top['ratio']:.2f}): {t['title'][:120]}",
                                                author=exec_cfg.get("agent", "judge"))
                            except Exception:
                                pass
                            _emit("system", "auto_kanban_dedup",
                                  f"skip duplicate (#{top['id']} ratio={top['ratio']:.2f}): {t['title'][:80]}",
                                  "info", payload={"existing_id": top["id"], "ratio": top["ratio"]})
                            continue
                        task = _ki.create_task(
                            hub_path,
                            title=t["title"][:200],
                            body=(t.get("body") or "")[:1000],
                            scope=scope,
                            priority=prio_map.get(t.get("priority", "medium"), 1),
                            assignee=exec_cfg.get("agent", "?"),
                            tags=["auto:judge"],
                            metadata={"linked_goal": goal_id, "run_id": run_id},
                        )
                        # Append to linked_tasks
                        try:
                            cur = goal_io.read_goal(hub_path, scope, goal_id)
                            if cur:
                                linked = cur["meta"].get("linked_tasks") or []
                                linked.append(task.get("id"))
                                goal_io.update_goal(hub_path, scope, goal_id, {"linked_tasks": linked})
                        except Exception:
                            pass
                        _emit("system", "auto_kanban", f"auto-kanban: {t['title'][:80]}", "tool", payload={"task_id": task.get("id")})
                    except Exception:
                        pass
            except ImportError:
                pass

        # Kanban closure
        to_close = parsed.get("kanban_to_close") or []
        if isinstance(to_close, list):
            try:
                import kanban_io as _ki
                for tid in to_close:
                    try:
                        tid_int = int(tid)
                        linked = (meta.get("linked_tasks") or [])
                        if tid_int in linked:
                            if _ki.update_status(hub_path, tid_int, "done", note=f"auto-closed by pipeline ({exec_cfg.get('agent','?')})"):
                                _emit("system", "kanban_closed", f"kanban #{tid_int} auto-closed", "success")
                    except Exception:
                        pass
            except ImportError:
                pass

        # Proposed edits
        proposed = {} if is_observer else (parsed.get("proposed_goal_edits") or {})
        if proposed:
            clean = {k: v for k, v in proposed.items() if v not in (None, "", [])}
            if clean:
                try:
                    goal_judge._append_suggestion(hub_path, scope, goal_id, exec_cfg.get("agent", "?"), clean, parsed.get("summary", ""))
                    _emit("system", "proposed_edit", f"proposed edit queued: {list(clean.keys())}", "warn")
                except Exception:
                    pass

        # Phase B — Pending actions (L2+ only)
        if autonomy >= 2:
            pending_in = parsed.get("pending_actions") or []
            if isinstance(pending_in, list):
                created_ids = []
                for pa in pending_in[:10]:  # cap a 10 per safety
                    if not isinstance(pa, dict) or not pa.get("type"):
                        continue
                    try:
                        rec = goal_io.write_pending_action(
                            hub_path, scope, goal_id,
                            agent=exec_cfg.get("agent", "?"),
                            action_type=str(pa.get("type"))[:60],
                            payload=pa.get("payload") or {},
                            rationale=(pa.get("rationale") or "")[:500],
                            expires_in_min=int(pa.get("expires_in_min", 30) or 30),
                        )
                        if rec:
                            created_ids.append(rec["id"])
                            _emit("system", "pending_action", f"pending action queued: {rec['type']} #{rec['id']}", "warn",
                                  payload={"action_id": rec["id"], "type": rec["type"]})
                            # L2: trigger telegram notification (Phase B3)
                            if autonomy == 2:
                                try:
                                    from telegram_action_notifier import notify_pending_action
                                    asyncio.create_task(notify_pending_action(hub_path, scope, goal_id, rec))
                                except Exception:
                                    pass
                    except Exception as e:
                        print(f"[goal_office] pending action error: {e}", flush=True)
                # Phase C — L3: process actions autonomy
                if autonomy >= 3 and created_ids:
                    try:
                        from goal_executor_l3 import process_l3_actions
                        l3_res = await process_l3_actions(hub_path, scope, goal_id)
                        _emit("system", "l3_processed", f"L3 autonomous: {l3_res}", "tool", payload=l3_res)
                    except Exception as e:
                        _emit("system", "l3_error", f"L3 process failed: {e}", "error")

        judge_result = {"verdict": verdict, "agent": exec_cfg.get("agent"), "summary": parsed.get("summary", "")}

    _emit("system", "pipeline_end", f"office pipeline completed (run_id={run_id})", "success",
          payload={"run_id": run_id, "roles_invoked": list(notes_collected.keys())})

    return {
        "run_id": run_id,
        "goal_id": goal_id,
        "scope": scope,
        "roles_invoked": list(notes_collected.keys()),
        "verdict": (judge_result or {}).get("verdict"),
        "summary": (judge_result or {}).get("summary"),
    }
