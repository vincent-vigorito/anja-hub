"""pod_orchestrator.py — F-MarketingVertical — delega isolata multi-provider del pod.

Esegue gli specialisti del pod come **sessioni isolate** (una `stream_response` per
ruolo), ognuna:
  - scopizzata sul brand: `cwd = <ws>/.anjawiki/agents/<role>/` → usa il `.mcp.json`
    option-1 (anja_marketing puntato sul vault del brand + group del ruolo);
  - col SUO modello/provider (dal `config.json`) → multi-provider via `llm_router`
    (NON i subagent di Claude Code, che sarebbero Claude-only);
  - in un **contesto separato** → la delega non gonfia il contesto del lead e i token
    restano per-sessione (l'HTML/dati pesanti muoiono nella sessione dello specialista).

Flusso completo (`run_pod_review`):
  1. PLAN  — il lead (no tool) decompone il brief in assegnazioni [{role, task}]
  2. FAN-OUT (`run_pod`) — specialisti in parallelo, isolati → manifesto + handoff file
  3. SYNTH — il lead (no tool) ricompone dal manifesto

Vedi anja-marketing-workspace-design.md §5. Stdlib + claude_chat/mcp_scoper.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Optional

import claude_chat as chat
import mcp_scoper


PLAN_TEMPLATE = (
    "Sei il lead del workspace marketing del brand `{ws}`. Decomponi il BRIEF in "
    "assegnazioni per i tuoi specialisti DISPONIBILI: {roles}.\n"
    "- analyst: dati GSC/GA4/Ads (read-only)\n"
    "- seo-copy: meta SEO, articoli, schede (in bozza)\n"
    "- dev: pagine HTML / struttura\n"
    "- social: post FB/IG, kit social\n"
    "Regole: assegna a ciascuno SOLO ciò che è nel suo dominio; OMETTI gli specialisti "
    "non necessari; ogni task è una frase operativa concreta e autosufficiente.\n"
    "Rispondi SOLO con un array JSON, niente testo prima/dopo: "
    '[{{"role":"<ruolo>","task":"<task>"}}]\n\nBRIEF:\n{brief}'
)

SYNTH_TEMPLATE = (
    "Sei il lead del brand `{ws}`. I tuoi specialisti hanno completato i loro task. "
    "Ecco i deliverable:\n\n{manifest}\n\n"
    "Scrivi UN riepilogo unico, ordinato e conciso per l'utente, integrando i contributi "
    "(cita chi ha prodotto cosa). Non re-inventare dati che non sono nei deliverable.\n\n"
    "BRIEF originale dell'utente:\n{brief}"
)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:50] or "task"


async def run_specialist(hub_path: Path, ws_slug: str, role: str, task: str,
                         tools: bool = True, persist: bool = True) -> dict:
    """Esegue UN agente in sessione isolata, scopizzato sul brand.

    tools=False → niente MCP/tool (passata pura di plan/synth del lead, economica).
    persist=True → scrive il deliverable in files/pod/<role>.md.
    """
    hub_path = Path(hub_path)
    ws_root = hub_path / "workspaces" / ws_slug
    agent_dir = ws_root / ".anjawiki" / "agents" / role
    # Difesa path-traversal: agent_dir deve restare dentro <hub>/workspaces/.
    if not agent_dir.resolve().is_relative_to((hub_path / "workspaces").resolve()):
        return {"role": role, "ok": False, "error": "path fuori da workspaces/"}
    cfg_path = agent_dir / "config.json"
    if not cfg_path.is_file():
        return {"role": role, "ok": False, "error": f"agente '{role}' non trovato in {ws_slug}"}

    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cwd = agent_dir  # legge agent_dir/.mcp.json (scoped sul brand)
    provider = cfg.get("default_provider", "claude")
    model = cfg.get("default_model", "sonnet")

    system_prompt = chat.build_agent_system_prompt(hub_path, role, cwd, cfg, user_prompt=task)
    # Inietta i FATTI del brand (onboarding) → lo specialista lavora sul brand, non a vuoto.
    esperto = ws_root / "data" / "ESPERTO.md"
    if esperto.is_file():
        system_prompt += (
            "\n\n## CONOSCENZA DEL BRAND (data/ESPERTO.md — fonte di verità)\n"
            + esperto.read_text(encoding="utf-8")[:6000]
        )

    if tools:
        scoped, _meta = mcp_scoper.scope_mcps(
            hub_path=hub_path, scope_kind="agent", target_name=role,
            cwd=cwd, user_prompt=task, agent_config=cfg,
        )
        allowed = cfg.get("allowed_tools") or chat.PROJECT_TOOLS_FULL
        allowed = chat.augment_with_mcp(allowed, cwd, provider=provider, scoped_servers=scoped)
    else:
        scoped, allowed = [], []

    text, usage, err = "", None, None
    try:
        async for ev in chat.stream_response(
            user_prompt=task, system_prompt=system_prompt, cwd=cwd,
            model=model, allowed_tools=allowed, provider=provider, scoped_servers=scoped,
        ):
            t = ev.get("type")
            if t == "text":
                text += ev.get("content", "")
            elif t == "usage":
                usage = ev
            elif t == "error":
                err = ev.get("message")
    except Exception as e:
        import traceback
        err = f"{type(e).__name__}: {e}"
        print(f"[pod] {role} crash:\n{traceback.format_exc()}")

    summary = text.strip()
    # Cost per-brand (M-CostObservability): tagga il consumo del pod sul brand
    if usage:
        try:
            import cost_store
            cost_store.record_usage_event(hub_path, usage, feature="marketing-pod",
                                          scope=f"workspace:{ws_slug}", provider=provider)
        except Exception:
            pass
    file_path = None
    if persist and summary:
        pod_dir = ws_root / "files" / "pod"
        pod_dir.mkdir(parents=True, exist_ok=True)
        fp = pod_dir / f"{role}.md"
        fp.write_text(f"# {role} — deliverable\n\n**Task:** {task}\n\n---\n\n{summary}\n", encoding="utf-8")
        file_path = str(fp)

    return {
        "role": role, "ok": err is None, "error": err,
        "model": model, "provider": provider, "scoped_servers": scoped,
        "summary": summary, "file": file_path,
        "usage": {k: usage.get(k) for k in ("input_tokens", "context_input_tokens", "output_tokens")} if usage else None,
    }


def _workspace_live_state(hub_path: Path, ws_slug: str) -> str:
    """Blocco STATO LIVE letto LATO CODICE con lo scope giusto (deterministico, no tool-call).

    Risolve l'inaffidabilità dell'LLM nel passare lo scope corretto al kanban: i dati sono
    già nel prompt, l'agente lean si limita a formattarli.
    """
    scope = f"workspace:{ws_slug}"
    parts = ["## STATO LIVE DEL WORKSPACE (dati autorevoli, già caricati — usali per kanban/goal)"]
    try:
        import kanban_io
        cards = kanban_io.list_tasks(hub_path, scope=scope, status="active")
        if cards:
            rows = []
            for c in sorted(cards, key=lambda t: (t.get("due_at") or "9999")):
                due = (c.get("due_at") or "")[:10]
                rows.append(f"- [{c.get('status', '')}] {c.get('title', '')}" + (f" — due {due}" if due else ""))
            parts.append(f"### Kanban attivo ({len(cards)})\n" + "\n".join(rows))
        else:
            parts.append("### Kanban attivo (0)\n(nessun task attivo)")
    except Exception as e:
        parts.append(f"### Kanban\n(errore lettura: {e})")
    try:
        import goal_io
        goals = goal_io.list_goals(hub_path, scope=scope, status="active")
        if goals:
            parts.append("### Goal attivi\n" + "\n".join(
                f"- {g.get('title') or g.get('name') or g.get('goal_id', '?')}" for g in goals))
        else:
            parts.append("### Goal attivi (0)")
    except Exception:
        pass
    return "\n\n".join(parts)


async def run_workspace_query(hub_path: Path, ws_slug: str, question: str,
                              provider: str = "claude", model: str = "sonnet") -> dict:
    """Delega LEAN: UNA passata d'agente nello scope **project** del workspace
    (cwd = ws-root → tool kanban/goals/roadmap/marketing del brand via .mcp.json),
    ritorna la risposta. Niente persistenza, niente sub-pod: serve all'hub per
    chiedere DI un workspace ("quante bozze ha acme", "cosa ha in programma X")
    senza cambiare scope. Read-only, tracciato in decision_trail.
    """
    hub_path = Path(hub_path)
    ws_root = hub_path / "workspaces" / ws_slug
    if not ws_root.resolve().is_relative_to((hub_path / "workspaces").resolve()):
        return {"ok": False, "error": "path fuori da workspaces/"}
    if not (ws_root / ".mcp.json").is_file():
        return {"ok": False, "error": f"workspace '{ws_slug}' non trovato o senza .mcp.json"}

    system_prompt = (
        f"Sei l'assistente operativo del workspace `{ws_slug}`. Rispondi in modo CONCISO e "
        f"FATTUALE alla domanda usando i tuoi tool (kanban, goals, roadmap, e i tool marketing "
        f"del brand). Per kanban e goal del workspace usa i dati della sezione «STATO LIVE» "
        f"qui sotto (già caricati e autorevoli): NON richiamare quei tool. Per piano editoriale "
        f"e marketing usa i tuoi tool. Se un dato non è verificabile, dillo — non inventare né "
        f"improvvisare chiamate HTTP."
    )
    esperto = ws_root / "data" / "ESPERTO.md"
    if esperto.is_file():
        system_prompt += "\n\n## CONOSCENZA DEL BRAND (data/ESPERTO.md)\n" + esperto.read_text(encoding="utf-8")[:4000]

    # STATO LIVE pre-caricato lato codice (deterministico): niente tool-call che possa sbagliare scope.
    system_prompt += "\n\n" + _workspace_live_state(hub_path, ws_slug)

    scoped, _meta = mcp_scoper.scope_mcps(
        hub_path=hub_path, scope_kind="project", target_name=ws_slug,
        cwd=ws_root, user_prompt=question,
    )
    allowed = chat.augment_with_mcp(list(chat.PROJECT_TOOLS_FULL), ws_root,
                                    provider=provider, scoped_servers=scoped)

    text, usage, err = "", None, None
    try:
        async for ev in chat.stream_response(
            user_prompt=question, system_prompt=system_prompt, cwd=ws_root,
            model=model, allowed_tools=allowed, provider=provider, scoped_servers=scoped,
        ):
            t = ev.get("type")
            if t == "text":
                text += ev.get("content", "")
            elif t == "usage":
                usage = ev
            elif t == "error":
                err = ev.get("message")
    except Exception as e:
        import traceback
        err = f"{type(e).__name__}: {e}"
        print(f"[wsq] {ws_slug} crash:\n{traceback.format_exc()}")

    answer = text.strip()
    if usage:
        try:
            import cost_store
            cost_store.record_usage_event(hub_path, usage, feature="workspace-query",
                                          scope=f"workspace:{ws_slug}", provider=provider)
        except Exception:
            pass
    try:
        import decision_trail
        decision_trail.record(hub_path, actor="workspace-query", scope=f"workspace:{ws_slug}",
                              ref="delegation", trigger=question[:200],
                              decision=(answer[:200] or err or "no answer"))
    except Exception:
        pass

    return {"ok": err is None and bool(answer), "ws": ws_slug, "answer": answer, "error": err}


async def run_pod(hub_path: Path, ws_slug: str, assignments: list[dict]) -> dict:
    """Fan-out PARALLELO isolato. assignments: [{"role": str, "task": str}]."""
    results = await asyncio.gather(
        *[run_specialist(hub_path, ws_slug, a["role"], a["task"]) for a in assignments],
        return_exceptions=True,
    )
    out = []
    for a, r in zip(assignments, results):
        if isinstance(r, Exception):
            out.append({"role": a["role"], "ok": False, "error": f"{type(r).__name__}: {r}"})
        else:
            out.append(r)
    manifest = {r["role"]: (r.get("summary") or r.get("error") or "") for r in out}
    return {"workspace": ws_slug, "results": out, "manifest": manifest}


# ----------------------------------------------------------------------
# Giro completo: plan → fan-out → synth
# ----------------------------------------------------------------------

def _resolve_lead(hub_path: Path, ws_slug: str) -> str:
    meta = hub_path / "workspaces" / ws_slug / ".anjawiki" / "meta.yaml"
    if meta.is_file():
        for line in meta.read_text(encoding="utf-8").splitlines():
            if line.startswith("responsabile:"):
                return line.split(":", 1)[1].strip()
    return f"anja-{ws_slug}"


def _available_roles(hub_path: Path, ws_slug: str, lead: str) -> list[str]:
    adir = hub_path / "workspaces" / ws_slug / ".anjawiki" / "agents"
    if not adir.is_dir():
        return []
    return [p.name for p in sorted(adir.iterdir())
            if p.is_dir() and p.name != lead and (p / "config.json").is_file()]


def _parse_assignments(text: str, valid_roles: list[str]) -> list[dict]:
    m = re.search(r"\[.*\]", text or "", re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return []
    out = []
    for a in arr:
        if isinstance(a, dict) and a.get("role") in valid_roles and a.get("task"):
            out.append({"role": a["role"], "task": str(a["task"])})
    return out


async def run_pod_review(hub_path: Path, ws_slug: str, brief: str,
                         lead_role: Optional[str] = None) -> dict:
    """Giro completo: il lead pianifica, il pod esegue isolato in parallelo, il lead sintetizza."""
    hub_path = Path(hub_path)
    lead = lead_role or _resolve_lead(hub_path, ws_slug)
    roles = _available_roles(hub_path, ws_slug, lead)
    if not roles:
        return {"ok": False, "stage": "setup", "error": f"nessuno specialista in {ws_slug}"}

    # 1) PLAN (lead, no tool)
    plan = await run_specialist(
        hub_path, ws_slug, lead,
        PLAN_TEMPLATE.format(ws=ws_slug, roles=", ".join(roles), brief=brief),
        tools=False, persist=False,
    )
    assignments = _parse_assignments(plan.get("summary", ""), roles)
    if not assignments:
        return {"ok": False, "stage": "plan", "error": "impossibile decomporre il brief",
                "plan_raw": plan.get("summary"), "available_roles": roles}

    # 2) FAN-OUT (specialisti isolati, parallelo)
    pod = await run_pod(hub_path, ws_slug, assignments)

    # 3) SYNTH (lead, no tool)
    manifest = "\n\n".join(
        f"### {r['role']}\n{r.get('summary') or ('ERRORE: ' + str(r.get('error')))}"
        for r in pod["results"]
    )
    synth = await run_specialist(
        hub_path, ws_slug, lead,
        SYNTH_TEMPLATE.format(ws=ws_slug, manifest=manifest, brief=brief),
        tools=False, persist=False,
    )

    # Decision-trail per-brand: cosa ha fatto il pod (audit verso il cliente)
    try:
        import decision_trail
        roles = ", ".join(a["role"] for a in assignments)
        decision_trail.record(
            hub_path, actor=f"pod:{ws_slug}", trigger=brief[:160],
            decision=f"delega a [{roles}] + sintesi del lead",
            scope=f"workspace:{ws_slug}", ref="marketing-pod",
        )
    except Exception:
        pass

    return {
        "ok": True, "lead": lead, "assignments": assignments,
        "results": pod["results"], "manifest": pod["manifest"],
        "synthesis": synth.get("summary"),
        "plan_usage": plan.get("usage"), "synth_usage": synth.get("usage"),
    }


# CLI debug:
#   python pod_orchestrator.py specialist <hub> <ws> <role> "<task>"
#   python pod_orchestrator.py review     <hub> <ws> "<brief>"
if __name__ == "__main__":
    import sys
    mode = sys.argv[1]
    if mode == "specialist":
        res = asyncio.run(run_specialist(Path(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]))
        print(json.dumps({**res, "summary": (res.get("summary") or "")[:500]}, indent=2, ensure_ascii=False))
    elif mode == "review":
        res = asyncio.run(run_pod_review(Path(sys.argv[2]), sys.argv[3], sys.argv[4]))
        print(json.dumps(res, indent=2, ensure_ascii=False)[:3000])
