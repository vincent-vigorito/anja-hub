#!/usr/bin/env python3
"""
mcp_hub_runtime.py — MCP server "anja_hub_runtime": il piano di lavoro degli agent
di AnjaHub (agents / tasks / workspace / kanban / goals / pp).

F-AnjadevCoreSplit (2026-08-19): questi tool vivevano in anjadev
`mcp_memory_server.py` e importavano la webapp con un path indovinato
(`ANJA_HUB_WEBAPP`). Ora stanno QUI, dentro anja-hub, e importano la webapp
per posizione (`<repo>/anja-hub/webapp`, via `Path(__file__)`). anjadev resta il
plugin CLI puro (memory/sessions/soul/user/skills/wiki/roadmap/code/graph).

Nomi tool INVARIATI (`kanban.create`, `agent.delegate`, ...): gli agent non si
accorgono dello spostamento; cambia solo quale script c'è nel `.mcp.json`.

    {
      "mcpServers": {
        "anja_hub_runtime": {
          "command": "python3",
          "args": ["/abs/path/anja-hub/scripts/mcp_hub_runtime.py"],
          "env": {
            "ANJA_SCOPE": "hub",                 // o "project" (workspace) con ANJA_HUB
            "ANJA_ROOT": "/abs/path/hub",         // hub root, o root del workspace
            "ANJA_HUB":  "/abs/path/hub",         // richiesto in scope=project
            "ANJA_TOOL_GROUPS": "kanban,goals"    // default: tutti e 6 i gruppi
          }
        }
      }
    }

Non è un secondo `mcp_hub_ops.py` (diagnostica/lifecycle/bridge REST): qui c'è il
piano di lavoro degli agent. Due responsabilità, due server.

Stdlib pure + moduli della webapp anja-hub (kanban_io, goal_io, workspace_scaffold,
pp_integration) importati lazy dalla dir sorella.
"""

import importlib
import json
import os
import re
import secrets
import sys
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import re as _re
import secrets as _secrets


# ============================================================
# config
# ============================================================

PROTO_VERSION = "2024-11-05"
SERVER_NAME = "anja_hub_runtime"
SERVER_VERSION = "1.0.0"

SCOPE = os.environ.get("ANJA_SCOPE", "hub")  # hub | project | agent
ROOT = Path(os.environ.get("ANJA_ROOT", os.getcwd())).resolve()

# La webapp è la dir sorella di scripts/: nessun path indovinato, nessun env.
WEBAPP_DIR = Path(__file__).resolve().parent.parent / "webapp"

_WEBAPP_MODULES: dict = {}


def _load_webapp_module(module_name: str):
    """Import lazy (cached) di un modulo della webapp anja-hub. None se manca:
    i tool rispondono con errore graceful, la causa vera va su stderr."""
    if module_name in _WEBAPP_MODULES:
        return _WEBAPP_MODULES[module_name]
    sp = str(WEBAPP_DIR)
    if sp not in sys.path:
        sys.path.insert(0, sp)
    try:
        mod = importlib.import_module(module_name)
    except Exception:
        print(f"[{SERVER_NAME}] WARN import {module_name} da {WEBAPP_DIR} fallito:\n"
              f"{traceback.format_exc()}", file=sys.stderr, flush=True)
        mod = None
    _WEBAPP_MODULES[module_name] = mod
    return mod


# ============================================================
# Hub root
# ============================================================

def _hub_root_from_scope() -> Optional[Path]:
    """Risale alla hub root partendo da ANJA_ROOT.

    - SCOPE=hub:    ROOT è già il hub
    - SCOPE=agent:  ROOT è <hub>/agents/<name>/, quindi parent.parent = hub
    - SCOPE=project: ROOT è la project root, hub non determinabile direttamente
                     → prova ANJA_HUB env, altrimenti None
    """
    if SCOPE == "hub":
        return ROOT
    if SCOPE == "agent":
        # Resolve agent dir → parent (agents/) → parent (hub)
        if ROOT.parent.name == "agents":
            return ROOT.parent.parent
    # project: try env or fallback
    env_hub = os.environ.get("ANJA_HUB")
    if env_hub:
        return Path(env_hub).expanduser().resolve()
    return None


# ============================================================
# Agent tools (M-PA 5)
# ============================================================

def _find_agent_dirs(hub: Path, name: str) -> list:
    """Tutte le dir agent che matchano il nome: lista di (workspace, dir),
    workspace='' per hub-level. I nomi sono duplicati tra i pod dei workspace
    (ogni brand ha il suo seo-copy/dev/social): il chiamante DEVE disambiguare,
    mai prendere il primo match — il target sbagliato pubblica sul brand sbagliato."""
    out = []
    d = hub / "agents" / name
    if (d / "config.json").is_file() or (d / "AGENTS.md").is_file():
        out.append(("", d))
    ws_root = hub / "workspaces"
    if ws_root.is_dir():
        for ws in sorted(ws_root.iterdir()):
            cand = ws / ".anjawiki" / "agents" / name
            if (cand / "config.json").is_file() or (cand / "AGENTS.md").is_file():
                out.append((ws.name, cand))
    return out


def tool_agent_list(args: dict) -> dict:
    """Lista agent disponibili: hub-level + i team dei workspace (roster completo)."""
    hub = _hub_root_from_scope()
    if not hub:
        return {"error": "hub root not determinable. Set ANJA_HUB env or run from hub/agent scope."}

    def _mk(sub: Path, ws: str = "") -> Optional[dict]:
        cfg_path = sub / "config.json"
        if ws and not cfg_path.is_file() and not (sub / "AGENTS.md").is_file():
            return None   # nei workspace conta solo un agent vero, non dir spurie
        info = {"name": sub.name, "role": "", "model": "?", "auto_route_keywords": []}
        if ws:
            info["workspace"] = ws
        if cfg_path.is_file():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                info["role"] = cfg.get("role", "")
                info["model"] = cfg.get("default_model", "?")
                info["provider"] = cfg.get("default_provider", "claude")
                info["auto_route_keywords"] = cfg.get("auto_route_keywords", [])
                info["scope"] = cfg.get("scope", "hub")
                info["_has_config"] = True
            except Exception:
                pass
        return info

    # NB: workspace diversi possono avere agent omonimi (pod: dev/analyst/…) —
    # sono agent DIVERSI, tutti in lista. Si dedup-a solo il mirror hub
    # (dir sessions/ senza config) rispetto all'agent vero di un workspace.
    hub_entries: dict = {}
    agents_dir = hub / "agents"
    if agents_dir.is_dir():
        for sub in sorted(agents_dir.iterdir()):
            if sub.is_dir():
                info = _mk(sub)
                if info:
                    hub_entries[sub.name] = info
    replaced = set()
    ws_out = []
    ws_root = hub / "workspaces"
    if ws_root.is_dir():
        for ws in sorted(ws_root.iterdir()):
            adir = ws / ".anjawiki" / "agents"
            if not adir.is_dir():
                continue
            for sub in sorted(adir.iterdir()):
                if not sub.is_dir():
                    continue
                info = _mk(sub, ws=ws.name)
                if not info:
                    continue
                cur = hub_entries.get(sub.name)
                if cur is not None and not cur.get("_has_config") and info.get("_has_config"):
                    replaced.add(sub.name)
                ws_out.append(info)
    out = [a for n, a in hub_entries.items() if n not in replaced] + ws_out
    out = [{k: v for k, v in i.items() if k != "_has_config"} for i in out]
    return {"agents": out, "count": len(out)}


def _iter_agent_dirs(hub: Path):
    """(name, workspace, dir) per gli agent con config: hub-level + team dei workspace."""
    d = hub / "agents"
    if d.is_dir():
        for sub in sorted(d.iterdir()):
            if sub.is_dir() and (sub / "config.json").is_file():
                yield sub.name, "", sub
    wsr = hub / "workspaces"
    if wsr.is_dir():
        for ws in sorted(wsr.iterdir()):
            ad = ws / ".anjawiki" / "agents"
            if ad.is_dir():
                for sub in sorted(ad.iterdir()):
                    if sub.is_dir() and (sub / "config.json").is_file():
                        yield sub.name, ws.name, sub


def _route_delegate_target(hub: Path, prompt: str, workspace: str = "") -> tuple:
    """Sceglie l'agent quando `target` non è dato: match delle `auto_route_keywords`
    sul prompt, ristretto al workspace (esplicito o inferito dal testo).
    Deterministico e osservabile — nessuna chiamata LLM. Ritorna (target, meta)."""
    entries = []
    for name, ws, adir in _iter_agent_dirs(hub):
        try:
            cfg = json.loads((adir / "config.json").read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
        entries.append((name, ws, cfg))
    if not entries:
        return "", {"error": "nessun agent con config"}

    low = " " + prompt.lower() + " "
    ws_sel = (workspace or "").strip()
    if not ws_sel:
        # inferisci il workspace dal prompt (nome completo o primo token dello slug)
        names = sorted({ws for _, ws, _ in entries if ws})
        hits = [n for n in names
                if re.search(r"\b" + re.escape(n.lower()) + r"\b", low)
                or re.search(r"\b" + re.escape(n.split("-")[0].lower()) + r"\b", low)]
        if len(hits) == 1:
            ws_sel = hits[0]
        elif len(hits) > 1:
            return "", {"error": f"workspace ambiguo nel prompt: {hits} — passa `workspace`"}

    cands = [e for e in entries if not ws_sel or e[1] == ws_sel]
    if not cands:
        return "", {"error": f"nessun agent nel workspace '{ws_sel}'"}

    scored = []
    for name, ws, cfg in cands:
        kws = [str(k).lower() for k in (cfg.get("auto_route_keywords") or [])]
        hit = [k for k in kws if re.search(r"\b" + re.escape(k) + r"\b", low)]
        if hit:
            # a parità di keyword vince chi PUÒ eseguire il task (tool di produzione)
            scored.append((len(hit), 1 if cfg.get("delegate_tools") else 0, name, ws, hit))
    if not scored:
        leads = [(n, w) for n, w, c in cands if c.get("workspace_lead")]
        if len(leads) == 1:
            return leads[0][0], {"routed_to": leads[0][0], "workspace": leads[0][1],
                                 "reason": "nessuna keyword matchata → lead del workspace"}
        return "", {"error": "routing fallito: nessuna keyword matchata e lead non univoco — passa `target`"}

    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    best = scored[0]
    # Ambiguità di WORKSPACE: stesso punteggio in brand diversi (gli specialisti hanno
    # nomi uguali nei pod) → mai tirare a sorte, il target sbagliato pubblica sul
    # brand sbagliato. Chiedi `workspace`.
    tied_ws = {s[3] for s in scored if (s[0], s[1]) == (best[0], best[1]) and s[3]}
    if not ws_sel and len(tied_ws) > 1:
        return "", {"error": f"routing ambiguo tra i workspace {sorted(tied_ws)} "
                             f"(agent '{best[2]}', keyword {best[4]}) — passa `workspace`"}
    others = [{"agent": s[2], "keywords": s[4]} for s in scored[1:3]]
    return best[2], {"routed_to": best[2], "workspace": best[3],
                     "matched_keywords": best[4], "runner_up": others,
                     "reason": f"auto-route su keyword {best[4]}"}


def _delegate_mcp_mounts(hub: Path, agent_dir: Path) -> dict:
    """Merge delle definizioni mcpServers di hub/.mcp.json + agent_dir/.mcp.json.

    A parità di nome vince l'agent dir (env con scope workspace più specifico).
    """
    servers: dict = {}
    for src_dir in (hub, agent_dir):
        mcp_file = src_dir / ".mcp.json"
        if mcp_file.is_file():
            try:
                mcp_json = json.loads(mcp_file.read_text(encoding="utf-8"))
                servers.update(mcp_json.get("mcpServers") or {})
            except Exception:
                pass
    return servers


def tool_agent_delegate(args: dict) -> dict:
    """Delega un task a un agent specializzato. Spawn mini-sessione claude-agent-sdk con cwd=agent dir.

    args:
      target: str  — nome agent (es. 'social'), qualificabile con il workspace
                     ('swebby/social'). OPZIONALE: se assente, l'agent viene
                     scelto automaticamente dalle `auto_route_keywords` sul prompt
      workspace: str — vincola il target (esplicito o auto-routato) a un workspace
      prompt: str  — task da delegare
      timeout_sec: int = 120
    """
    target = (args.get("target") or "").strip()
    workspace = (args.get("workspace") or "").strip()
    prompt = (args.get("prompt") or "").strip()
    timeout_sec = int(args.get("timeout_sec", 120))

    if not prompt:
        return {"error": "prompt required"}

    # target qualificato 'workspace/nome' — il qualificatore vince sul param
    if "/" in target:
        ws_q, _, target = target.partition("/")
        workspace = ws_q.strip() or workspace

    hub = _hub_root_from_scope()
    if not hub:
        return {"error": "hub root not determinable"}

    routing = None
    if not target:
        target, routing = _route_delegate_target(hub, prompt, workspace)
        if not target:
            return {"error": routing.get("error", "routing fallito"), "hint":
                    "passa `target` esplicito (agent.list mostra il roster)"}
        # il workspace scelto dal routing DEVE vincolare anche la resolve:
        # i nomi sono duplicati tra i pod e il primo match è il brand sbagliato
        workspace = routing.get("workspace") or workspace

    matches = _find_agent_dirs(hub, target)
    if workspace:
        matches = [m for m in matches if m[0] == workspace]
    if not matches and not workspace and (hub / "agents" / target).is_dir():
        matches = [("", hub / "agents" / target)]   # legacy: dir hub anche senza config
    if not matches:
        where = f"nel workspace '{workspace}'" if workspace else \
                f"(né {hub}/agents/ né workspaces/*/.anjawiki/agents/)"
        return {"error": f"agent '{target}' not found {where}"}
    if len(matches) > 1:
        return {"error": f"agent '{target}' ambiguo: esiste in "
                         f"{[m[0] or 'hub' for m in matches]} — passa `workspace` "
                         f"o un target qualificato '<workspace>/{target}'",
                "candidates": [m[0] or "hub" for m in matches]}
    workspace, agent_dir = matches[0]

    # Load agent config
    cfg_path = agent_dir / "config.json"
    cfg = {}
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    model = cfg.get("default_model", "sonnet")
    role = cfg.get("role", "")
    # F-Sec-Anjadev-DelegateBypass: bypassPermissions solo se l'agent lo dichiara
    # esplicitamente nella sua config (default least-privilege).
    bypass_perms = bool(cfg.get("bypass_permissions", False))
    # Tool NATIVI in delega: read-only di default. Un agent che deve PRODURRE (es. il
    # lead di un workspace marketing: generare kit, convertire immagini) li dichiara
    # in config `delegate_tools`. Filtrati su whitelist: niente nomi arbitrari.
    _NATIVE_TOOLS = {"Read", "Write", "Edit", "MultiEdit", "Bash", "Grep", "Glob",
                     "LS", "TodoWrite", "WebFetch", "WebSearch", "NotebookEdit", "Task"}
    declared = cfg.get("delegate_tools")
    native_tools = ([t for t in declared if t in _NATIVE_TOOLS] if isinstance(declared, list) and declared
                    else ["Read", "Grep", "Glob"])

    # Spawn claude-agent-sdk in-process (timeout protection via asyncio)
    import asyncio
    try:
        from claude_agent_sdk import ClaudeAgentOptions, query
    except ImportError as e:
        return {"error": f"claude-agent-sdk not installed: {e}"}

    system_prompt = (
        f"You are the specialized agent '{target}' in the anja hub.\n"
        f"Role: {role}\n\n"
        f"Stay in character — you are NOT the generic hub default. "
        f"Your full personality + tools are in CLAUDE.md (composed from AGENTS+SOUL+TOOLS).\n\n"
        f"You have been DELEGATED a task from the hub default. "
        f"Respond focused on your domain. Output will be returned to the caller."
    )

    # MCP in delega: le definizioni COMPLETE (command/args/env) di hub + agent dir
    # vengono passate al SDK via `mcp_servers`. Allowlistare solo i nomi non basta:
    # il pattern risulta permesso ma il server non è montato, e l'agent senza tool
    # può "riuscire" confabulando i risultati invece di fallire.
    mcp_servers = _delegate_mcp_mounts(hub, agent_dir)
    mcp_patterns = [f"mcp__{srv}__*" for srv in mcp_servers]

    # Fail-fast: se la config dichiara mcp_servers non montabili, errore esplicito
    # prima dello spawn.
    required = cfg.get("mcp_servers")
    if isinstance(required, list):
        missing = [s for s in required if s not in mcp_servers]
        if missing:
            return {
                "error": (f"agent '{target}' dichiara mcp_servers {missing} ma nessun "
                          f".mcp.json li definisce (cercato in {hub} e {agent_dir})"),
                "mounted": sorted(mcp_servers),
                "hint": "aggiungi la definizione al .mcp.json dell'agent o correggi mcp_servers nella sua config.json",
            }

    # cwd dell'agent SDK: usa hub dir perché lì c'è .mcp.json (l'agent eredita gli MCP).
    sdk_cwd = hub if (hub / ".mcp.json").is_file() else agent_dir

    # Accumulo FUORI dalla coroutine: al timeout `wait_for` la cancella, ma il lavoro
    # già prodotto resta qui e viene restituito+loggato invece di andare perso.
    chunks: list = []

    async def _run():
        # Least-privilege di default: l'agent delegato legge/cerca + usa i suoi MCP,
        # ma NON scrive/esegue bash senza opt-in (delegate_tools + bypass_permissions
        # nella sua config). Con prompt injection non eredita pieni poteri sull'host.
        opts_kwargs = {
            "system_prompt": system_prompt,
            "model": model,
            "cwd": str(sdk_cwd),
            "permission_mode": "bypassPermissions" if bypass_perms else "default",
            "allowed_tools": native_tools + mcp_patterns,
            # Solo i server montati qui: senza strict la sessione delegata eredita
            # gli MCP user-level dell'host (connettori personali: Gmail, Drive,
            # sandbox di esecuzione remota...) che in bypassPermissions sono
            # usabili senza limiti — escalation fuori scope osservata live.
            "strict_mcp_config": True,
        }
        if mcp_servers:
            opts_kwargs["mcp_servers"] = mcp_servers
        options = ClaudeAgentOptions(**opts_kwargs)
        async for msg in query(prompt=prompt, options=options):
            mtype = type(msg).__name__
            if mtype == "AssistantMessage":
                for block in getattr(msg, "content", []):
                    if type(block).__name__ == "TextBlock":
                        chunks.append(getattr(block, "text", ""))
        return "".join(chunks)

    started = datetime.now(timezone.utc)
    timed_out = False
    try:
        response = asyncio.run(asyncio.wait_for(_run(), timeout=timeout_sec))
    except asyncio.TimeoutError:
        # Timeout NON distruttivo: restituiamo il parziale e lo logghiamo (sotto).
        timed_out = True
        response = "".join(chunks)
    except Exception as e:
        partial = "".join(chunks)
        out = {"error": f"delegation failed: {type(e).__name__}: {e}"}
        if partial:
            out["partial_response"] = partial
        return out
    ended = datetime.now(timezone.utc)
    duration = (ended - started).total_seconds()

    # Log session in agents/<target>/sessions/<date>/<id>.md
    try:
        today = ended.strftime("%Y-%m-%d")
        hms = ended.strftime("%H%M%S")
        short = secrets.token_hex(2)
        sid = f"{hms}-delegation-{short}"
        sdir = agent_dir / "sessions" / today
        sdir.mkdir(parents=True, exist_ok=True)
        log = (
            f"---\nid: {sid}\nscope: agent\nagent: delegation\n"
            f"started: {started.isoformat()}\nended: {ended.isoformat()}\n"
            f"duration_sec: {round(duration, 2)}\nsource: agent.delegate\n"
            f"caller_scope: {SCOPE}\nmodel: {model}\n"
            f"timed_out: {str(timed_out).lower()}\n---\n\n"
            f"# Delegation {sid}\n\n## Prompt\n\n{prompt}\n\n"
            f"## Response{' (PARZIALE — timeout)' if timed_out else ''}\n\n{response}\n"
        )
        (sdir / f"{sid}.md").write_text(log, encoding="utf-8")
    except Exception:
        sid = ""   # logging failure non blocca delegation

    out = {
        "agent": target,
        "workspace": workspace or "hub",
        "model": model,
        "duration_sec": round(duration, 2),
        "response": response,
        "native_tools": native_tools,
        "mcp_servers": sorted(mcp_servers),
    }
    if routing:
        out["routing"] = routing   # perché è stato scelto quell'agent (decision-trail)
    if sid:
        out["session"] = sid
    if timed_out:
        out["timed_out"] = True
        out["partial"] = True
        out["hint"] = (f"timeout dopo {timeout_sec}s: sopra c'è il parziale (loggato nella sessione). "
                       f"Rilancia con timeout_sec maggiore, o spezza il task. Se l'agent deve "
                       f"PRODURRE (bash/write) dichiara `delegate_tools` nella sua config: "
                       f"in delega ha {native_tools}.")
    return out


# ============================================================
# Fase 7p — task.schedule_one_shot (delayed task tool)
# ============================================================

def _parse_when(when_str: str) -> Optional[datetime]:
    """Parse 'in 30 min', 'in 2 hours', 'tomorrow 09:00', 'YYYY-MM-DDTHH:MM' → datetime."""
    s = (when_str or "").strip().lower()
    if not s:
        return None
    now = datetime.now(timezone.utc).astimezone()  # local tz

    # ISO datetime
    try:
        return datetime.fromisoformat(when_str.replace("Z", "+00:00"))
    except Exception:
        pass

    # "in N min/minutes/m"
    m = _re.match(r"^in\s+(\d+)\s*(min|m|minute|minutes)\b", s)
    if m:
        return now + timedelta(minutes=int(m.group(1)))
    # "in N h/hour/hours"
    m = _re.match(r"^in\s+(\d+)\s*(h|hour|hours)\b", s)
    if m:
        return now + timedelta(hours=int(m.group(1)))
    # "in N s/sec/seconds"
    m = _re.match(r"^in\s+(\d+)\s*(s|sec|seconds)\b", s)
    if m:
        return now + timedelta(seconds=int(m.group(1)))
    # "tomorrow HH:MM"
    m = _re.match(r"^tomorrow\s+(\d{1,2}):(\d{2})\b", s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        d = (now + timedelta(days=1)).replace(hour=h, minute=mi, second=0, microsecond=0)
        return d
    # "today HH:MM"
    m = _re.match(r"^today\s+(\d{1,2}):(\d{2})\b", s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        d = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if d <= now:
            d += timedelta(days=1)
        return d
    return None


def _datetime_to_cron(dt: datetime) -> str:
    """Convert datetime to a 5-field cron expression that triggers at that exact minute."""
    return f"{dt.minute} {dt.hour} {dt.day} {dt.month} *"


def tool_task_schedule_one_shot(args: dict) -> dict:
    """Schedula un task one-shot come routine anja cron-based.

    args:
      when: str ('in 30 min', 'in 2 hours', 'tomorrow 09:00', ISO datetime)
      prompt: str (task da eseguire alla scadenza)
      output_actions: list[dict] (es. [{type:'telegram', chat_id:'...'}, {type:'file', path:'/tmp/x.md'}])
                                 Se vuoto/omesso, default = [{type:'file'}] in <hub>/routines/runs/
      name?: str (slug routine; auto-generato se omesso)
      tools?: list[str] (allowed tools, default Read/Grep/Glob + tutti mcp__*)
    """
    when_str = (args.get("when") or "").strip()
    prompt = (args.get("prompt") or "").strip()
    output_actions = args.get("output_actions") or []
    custom_name = (args.get("name") or "").strip()
    tools = args.get("tools") or []

    if not when_str:
        return {"error": "when required (es. 'in 30 min', 'tomorrow 09:00', ISO datetime)"}
    if not prompt:
        return {"error": "prompt required"}

    dt = _parse_when(when_str)
    if not dt:
        return {"error": f"unable to parse when='{when_str}'. Try 'in N min', 'in N hours', 'tomorrow HH:MM', or ISO datetime."}
    if dt <= datetime.now(timezone.utc).astimezone():
        return {"error": f"when must be in the future (got {dt.isoformat()})"}

    hub = _hub_root_from_scope()
    if not hub:
        return {"error": "hub root not determinable"}

    # Determine name
    if custom_name:
        if not _re.match(r"^[a-z0-9][a-z0-9_-]*$", custom_name):
            return {"error": "name must be kebab-case"}
        name = custom_name
    else:
        slug = _re.sub(r"[^a-z0-9]+", "-", prompt.lower())[:32].strip("-")
        name = f"oneshot-{slug or 'task'}-{_secrets.token_hex(3)}"

    # Build routine yaml
    routines_dir = hub / "routines"
    routines_dir.mkdir(parents=True, exist_ok=True)
    target = routines_dir / f"{name}.yaml"
    if target.exists():
        return {"error": f"routine '{name}' already exists"}

    cron = _datetime_to_cron(dt)
    yaml_lines = [
        f"name: {name}",
        f"description: One-shot task scheduled by AI (auto-disable after run)",
        f"scope: hub",
        f"schedule: \"{cron}\"",
        f"enabled: true",
        f"auto_disable_after_run: true   # Fase 7p — disabilita dopo prima esecuzione",
        f"tags: [oneshot, ai-scheduled]",
    ]
    if tools:
        yaml_lines.append("tools:")
        for t in tools:
            yaml_lines.append(f"  - {t}")
    yaml_lines.append("prompt: |")
    for line in prompt.split("\n"):
        yaml_lines.append(f"  {line}")
    if not output_actions:
        output_actions = [{"type": "file", "path": f"<hub>/routines/runs/{name}-output.md"}]
    yaml_lines.append("output:")
    for o in output_actions:
        if not isinstance(o, dict) or "type" not in o:
            continue
        # json.dumps produce uno scalare JSON valido anche come YAML, con escape
        # corretto di quote/newline → niente injection di chiavi via type/value.
        yaml_lines.append(f"  - type: {json.dumps(str(o['type']))}")
        for k, v in o.items():
            if k == "type":
                continue
            if not _re.match(r"^[a-zA-Z0-9_-]+$", str(k)):
                continue  # scarta chiavi non-identificatore (anti-injection)
            yaml_lines.append(f"    {k}: {json.dumps(v)}")
    yaml_text = "\n".join(yaml_lines) + "\n"
    target.write_text(yaml_text, encoding="utf-8")

    return {
        "scheduled": True,
        "name": name,
        "fires_at": dt.isoformat(),
        "fires_in_seconds": int((dt - datetime.now(timezone.utc).astimezone()).total_seconds()),
        "cron": cron,
        "yaml_path": str(target),
        "output_actions": output_actions,
    }


def tool_task_list(args: dict) -> dict:
    """Lista routine one-shot pendenti (auto_disable_after_run=true e enabled=true)."""
    hub = _hub_root_from_scope()
    if not hub:
        return {"error": "hub root not determinable"}
    routines_dir = hub / "routines"
    if not routines_dir.is_dir():
        return {"tasks": []}
    tasks = []
    for f in routines_dir.glob("*.yaml"):
        try:
            txt = f.read_text(encoding="utf-8")
            if "auto_disable_after_run: true" not in txt:
                continue
            if "enabled: true" not in txt:
                continue
            # Estrai schedule + name + description
            sched = _re.search(r"^schedule:\s*['\"]?([^'\"\n]+)", txt, _re.MULTILINE)
            desc = _re.search(r"^description:\s*(.+)", txt, _re.MULTILINE)
            tasks.append({
                "name": f.stem,
                "schedule": sched.group(1).strip() if sched else None,
                "description": desc.group(1).strip() if desc else None,
                "path": str(f),
            })
        except Exception:
            continue
    return {"tasks": tasks, "count": len(tasks)}


def tool_task_cancel(args: dict) -> dict:
    """Cancella una routine one-shot pendente."""
    name = (args.get("name") or "").strip()
    if not name or not _re.match(r"^[a-z0-9][a-z0-9_-]*$", name):
        return {"error": "valid name required"}
    hub = _hub_root_from_scope()
    if not hub:
        return {"error": "hub root not determinable"}
    target = hub / "routines" / f"{name}.yaml"
    if not target.is_file():
        return {"error": f"routine '{name}' not found"}
    target.unlink()
    return {"cancelled": True, "name": name}


# ============================================================
# Fase 22 — Workspace tools
# ============================================================

def tool_workspace_create(args: dict) -> dict:
    """Crea un workspace internal con responsabile agent."""
    hub = _hub_root_from_scope()
    if not hub:
        return {"error": "hub root not determinable"}
    name = (args.get("name") or "").strip()
    resp_name = (args.get("responsabile_name") or "").strip()
    role_desc = (args.get("role_description") or "").strip()
    if not name or not resp_name or not role_desc:
        return {"error": "name, responsabile_name, role_description required"}
    ws_type = (args.get("ws_type") or "office").strip()

    workspace_mod = _load_webapp_module("workspace_scaffold")
    if not workspace_mod or not hasattr(workspace_mod, "scaffold_workspace"):
        return {"error": "workspace_scaffold module not available", "hint": f"webapp non importabile da {WEBAPP_DIR} (vedi stderr del server)"}
    scaffold_workspace = workspace_mod.scaffold_workspace

    result = scaffold_workspace(
        hub_path=hub,
        name=name,
        responsabile_name=resp_name,
        role_description=role_desc,
        ws_type=ws_type,
        responsabile_provider=args.get("responsabile_provider") or "claude",
        responsabile_model=args.get("responsabile_model") or "sonnet",
        responsabile_effort=args.get("responsabile_effort") or None,
    )
    return result


def _resolve_workspace_root(scope: str) -> Optional[Path]:
    """Risolve la root di uno scope: 'hub' o 'workspace:<name>'."""
    hub = _hub_root_from_scope()
    if not hub:
        return None
    if scope == "hub" or not scope:
        return hub
    if scope.startswith("workspace:"):
        name = scope.split(":", 1)[1].strip()
        ws_path = hub / "workspaces" / name
        # Se symlink, dereferenzia
        if ws_path.is_symlink():
            return ws_path.resolve()
        if ws_path.is_dir():
            return ws_path
        return None
    return None


_ALLOWED_SUBDIRS = ("files", "data", "scripts")
_WORKSPACE_ROOT_FILES = ("CLAUDE.md", "log.md", "meta.yaml")

def _validate_workspace_path(scope: str, rel_path: str) -> tuple[Optional[Path], Optional[str]]:
    """Path validation: ritorna (resolved_path, error_msg).

    Allowed: {files,data,scripts}/**/* + {CLAUDE.md,log.md,meta.yaml} + wiki/**.

    Layout workspace POST-HOIST (AnjaHub 2026-06): files/data/scripts vivono alla
    RADICE del workspace (<ws>/data/...), mentre wiki e memoria restano in
    <ws>/.anjawiki/. I workspace pre-hoist hanno ancora tutto dentro .anjawiki/
    → dual-layout: prova la radice, fallback su .anjawiki/ (legacy).
    """
    root = _resolve_workspace_root(scope)
    if not root:
        return None, f"scope '{scope}' non risolto"
    rel = (rel_path or "").lstrip("/")
    if ".." in rel or rel.startswith("/"):
        return None, "path traversal not allowed"

    def _resolve_in(base: Path) -> tuple[Optional[Path], Optional[str]]:
        t = (base / rel).resolve()
        try:
            t.relative_to(base.resolve())
        except ValueError:
            return None, "path outside scope"
        return t, None

    # Hub: root è hub_path diretto — <hub>/{files,data,scripts}/... (invariato)
    if scope == "hub" or not scope:
        target, err = _resolve_in(root)
        if err:
            return None, err
        rel_parts = target.relative_to(root.resolve()).parts
        if len(rel_parts) == 0:
            return root, None  # listing root
        first = rel_parts[0]
        if first in _ALLOWED_SUBDIRS:
            return target, None
        if len(rel_parts) == 1 and first in _WORKSPACE_ROOT_FILES:
            return target, None
        if first == "wiki":
            return target, None
        return None, f"path '{first}' not in whitelist (allowed: {_ALLOWED_SUBDIRS} + {_WORKSPACE_ROOT_FILES} + wiki/)"

    # Workspace: dual-layout
    aw = root / ".anjawiki"
    if not aw.is_dir():
        return None, f"workspace .anjawiki not found: {aw}"
    parts = Path(rel).parts
    if len(parts) == 0:
        return root, None  # listing root del workspace (layout nuovo)
    first = parts[0]

    if first == "wiki":
        # wiki vive SEMPRE in .anjawiki/
        return _resolve_in(aw)

    if first in _ALLOWED_SUBDIRS or (len(parts) == 1 and first in _WORKSPACE_ROOT_FILES):
        # radice del workspace (post-hoist) se il path esiste lì o se il legacy
        # non ce l'ha; altrimenti fallback .anjawiki/ (pre-hoist)
        new_t = root / rel
        old_t = aw / rel
        base = root if (new_t.exists() or not old_t.exists()) else aw
        return _resolve_in(base)

    return None, f"path '{first}' not in whitelist (allowed: {_ALLOWED_SUBDIRS} + {_WORKSPACE_ROOT_FILES} + wiki/)"


def tool_workspace_list_files(args: dict) -> dict:
    """Lista file in uno scope workspace, sandboxed."""
    scope = (args.get("scope") or "hub").strip()
    rel_path = (args.get("path") or "").strip()
    target, err = _validate_workspace_path(scope, rel_path)
    if err:
        return {"error": err}
    if not target.exists():
        return {"error": f"path not found: {rel_path or '(root)'}"}

    if target.is_file():
        try:
            size = target.stat().st_size
            return {"type": "file", "path": rel_path, "size": size}
        except Exception as e:
            return {"error": str(e)}

    # Directory listing
    entries = []
    try:
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if child.name.startswith("."):
                continue
            try:
                stat = child.stat()
                entries.append({
                    "name": child.name,
                    "type": "dir" if child.is_dir() else "file",
                    "size": stat.st_size if child.is_file() else 0,
                })
            except Exception:
                continue
    except PermissionError:
        return {"error": "permission denied"}
    return {"type": "dir", "scope": scope, "path": rel_path, "entries": entries}


def tool_workspace_read_file(args: dict) -> dict:
    """Legge un file da uno scope workspace."""
    scope = (args.get("scope") or "hub").strip()
    rel_path = (args.get("path") or "").strip()
    if not rel_path:
        return {"error": "path required"}
    target, err = _validate_workspace_path(scope, rel_path)
    if err:
        return {"error": err}
    if not target.is_file():
        return {"error": f"not a file: {rel_path}"}
    try:
        size = target.stat().st_size
        if size > 500_000:
            return {"error": "file too large (>500KB)", "size": size}
        content = target.read_text(encoding="utf-8", errors="replace")
        return {"scope": scope, "path": rel_path, "size": size, "content": content}
    except Exception as e:
        return {"error": str(e)}


def tool_workspace_write_file(args: dict) -> dict:
    """Scrive un file in uno scope workspace (files/scripts/data)."""
    scope = (args.get("scope") or "hub").strip()
    rel_path = (args.get("path") or "").strip()
    content = args.get("content", "")
    if not rel_path:
        return {"error": "path required"}
    if not isinstance(content, str):
        return {"error": "content must be string"}
    if len(content.encode("utf-8")) > 5 * 1024 * 1024:
        return {"error": "content too large (>5MB)"}
    target, err = _validate_workspace_path(scope, rel_path)
    if err:
        return {"error": err}

    # Per write: solo files/scripts/data, non root files (CLAUDE.md etc.)
    rel_parts = Path(rel_path).parts
    if len(rel_parts) == 0 or rel_parts[0] not in _ALLOWED_SUBDIRS:
        return {"error": f"write allowed only in {_ALLOWED_SUBDIRS} subdirs"}

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        # Auto-log in <scope_root>/log.md (NOT in .anjawiki/wiki/log.md)
        try:
            from datetime import datetime as _dt
            root = _resolve_workspace_root(scope)
            scope_root = root if scope == "hub" else (root / ".anjawiki")
            log_file = scope_root / "log.md"
            ts = _dt.now().strftime("%Y-%m-%d %H:%M")
            entry = f"\n## [{ts}] write | {rel_path} ({target.stat().st_size}B)\n"
            if log_file.is_file():
                with log_file.open("a", encoding="utf-8") as f:
                    f.write(entry)
        except Exception:
            pass
        return {
            "ok": True,
            "scope": scope,
            "path": rel_path,
            "size": target.stat().st_size,
            "absolute": str(target),
        }
    except Exception as e:
        return {"error": f"write failed: {e}"}


def tool_workspace_list(args: dict) -> dict:
    """Lista tutti i workspace registrati nel hub con kind metadata."""
    hub = _hub_root_from_scope()
    if not hub:
        return {"error": "hub root not determinable"}
    registry = hub / "config" / "projects.json"
    if not registry.is_file():
        return {"workspaces": [], "hub": hub.name}
    try:
        with registry.open(encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return {"error": f"registry read error: {e}"}

    workspaces = []
    for p in data.get("projects", []):
        ws_name = p.get("name", "")
        meta_file = hub / "workspaces" / f"{ws_name}.meta.yaml"
        kind = "external"
        responsabile = None
        if meta_file.is_file():
            for line in meta_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("kind:"):
                    kind = line.split(":", 1)[1].strip().strip('"').strip("'")
                if line.startswith("responsabile:"):
                    responsabile = line.split(":", 1)[1].strip().strip('"').strip("'")
        workspaces.append({
            "name": ws_name,
            "kind": kind,
            "responsabile": responsabile,
            "type": p.get("type", "?"),
            "location": p.get("location", {}),
        })
    return {"workspaces": workspaces, "hub": hub.name}


# ============================================================
# Fase 15 — Kanban
# ============================================================

def _kanban_module():
    """Lazy-load kanban_io dalla webapp anja-hub. None se non disponibile."""
    return _load_webapp_module("kanban_io")


def tool_kanban_create(args: dict) -> dict:
    """Crea un task kanban."""
    hub = _hub_root_from_scope()
    if not hub:
        return {"error": "hub root not determinable"}
    kio = _kanban_module()
    if not kio:
        return {"error": "kanban_io not available"}
    title = (args.get("title") or "").strip()
    if not title:
        return {"error": "title required"}
    try:
        task = kio.create_task(
            hub,
            title=title,
            body=args.get("body") or "",
            status=args.get("status") or "todo",
            assignee=args.get("assignee") or "",
            scope=args.get("scope") or "hub",
            parent_id=args.get("parent_id"),
            priority=int(args.get("priority", 1)),
            tags=args.get("tags") or [],
            due_at=args.get("due_at"),
        )
        # Apply deps
        for dep_id in (args.get("depends_on") or []):
            try:
                kio.add_dependency(hub, task["id"], int(dep_id))
            except Exception:
                pass
        return {"ok": True, "task": task}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def tool_kanban_show(args: dict) -> dict:
    """Lista task (filtri) o dettaglio (id)."""
    hub = _hub_root_from_scope()
    if not hub:
        return {"error": "hub root not determinable"}
    kio = _kanban_module()
    if not kio:
        return {"error": "kanban_io not available"}
    if args.get("id"):
        task = kio.get_task(hub, int(args["id"]))
        if not task:
            return {"error": f"task {args['id']} not found"}
        return {"task": task}
    tasks = kio.list_tasks(
        hub,
        scope=args.get("scope"),
        status=args.get("status"),
        assignee=args.get("assignee"),
        parent_id=args.get("parent_id"),
        include_archived=bool(args.get("include_archived")),
        limit=int(args.get("limit", 50)),
    )
    return {"tasks": tasks, "stats": kio.stats(hub)}


def tool_kanban_complete(args: dict) -> dict:
    """Marca task come done con summary opzionale."""
    hub = _hub_root_from_scope()
    if not hub:
        return {"error": "hub root not determinable"}
    kio = _kanban_module()
    if not kio:
        return {"error": "kanban_io not available"}
    task_id = args.get("id")
    if task_id is None:
        return {"error": "id required"}
    summary = args.get("summary") or ""
    if summary:
        kio.add_comment(hub, int(task_id), f"✓ Completed: {summary}", author="agent")
    task = kio.update_status(hub, int(task_id), "done")
    if not task:
        return {"error": f"task {task_id} not found"}
    # Auto-promote dependent
    promoted = kio.auto_promote_ready(hub)
    return {"ok": True, "task": task, "auto_promoted": promoted}


def tool_kanban_block(args: dict) -> dict:
    """Blocca un task con reason."""
    hub = _hub_root_from_scope()
    if not hub:
        return {"error": "hub root not determinable"}
    kio = _kanban_module()
    if not kio:
        return {"error": "kanban_io not available"}
    task_id = args.get("id")
    reason = (args.get("reason") or "").strip()
    if task_id is None or not reason:
        return {"error": "id and reason required"}
    task = kio.update_status(hub, int(task_id), "blocked", block_reason=reason)
    if not task:
        return {"error": f"task {task_id} not found"}
    return {"ok": True, "task": task}


def tool_kanban_unblock(args: dict) -> dict:
    """Sblocca → ricontrolla deps. Default torna a 'ready' se deps OK, altrimenti 'todo'."""
    hub = _hub_root_from_scope()
    if not hub:
        return {"error": "hub root not determinable"}
    kio = _kanban_module()
    if not kio:
        return {"error": "kanban_io not available"}
    task_id = args.get("id")
    if task_id is None:
        return {"error": "id required"}
    new_status = "ready" if kio.deps_satisfied(hub, int(task_id)) else "todo"
    task = kio.update_status(hub, int(task_id), new_status, block_reason=None)
    return {"ok": True, "task": task}


def tool_kanban_comment(args: dict) -> dict:
    hub = _hub_root_from_scope()
    if not hub:
        return {"error": "hub root not determinable"}
    kio = _kanban_module()
    if not kio:
        return {"error": "kanban_io not available"}
    task_id = args.get("id")
    content = (args.get("content") or "").strip()
    if task_id is None or not content:
        return {"error": "id and content required"}
    c = kio.add_comment(hub, int(task_id), content, author=args.get("author") or "")
    return {"ok": True, "comment": c}


def tool_kanban_assign(args: dict) -> dict:
    """Cambia assignee. Es. 'anja', 'anja-finanze', 'human:vincent'."""
    hub = _hub_root_from_scope()
    if not hub:
        return {"error": "hub root not determinable"}
    kio = _kanban_module()
    if not kio:
        return {"error": "kanban_io not available"}
    task_id = args.get("id")
    assignee = (args.get("assignee") or "").strip()
    if task_id is None or not assignee:
        return {"error": "id and assignee required"}
    task = kio.update_task(hub, int(task_id), assignee=assignee)
    return {"ok": True, "task": task}


def tool_kanban_search(args: dict) -> dict:
    hub = _hub_root_from_scope()
    if not hub:
        return {"error": "hub root not determinable"}
    kio = _kanban_module()
    if not kio:
        return {"error": "kanban_io not available"}
    q = (args.get("query") or "").strip()
    if not q:
        return {"error": "query required"}
    return {"results": kio.search_tasks(hub, q, limit=int(args.get("limit", 20)))}


# ============================================================
# Fase 18.A — Goals
# ============================================================

def _goal_module():
    """Lazy-load goal_io dalla webapp anja-hub. None se non disponibile."""
    return _load_webapp_module("goal_io")


def _resolve_goal_scope(args: dict) -> str:
    """Risolvi scope dai args, fallback to env ANJA_WORKSPACE_SCOPE o 'hub'."""
    s = (args.get("scope") or "").strip()
    if s:
        return s
    env_scope = os.environ.get("ANJA_WORKSPACE_SCOPE", "").strip()
    if env_scope:
        return env_scope
    return "hub"


def tool_goal_create(args: dict) -> dict:
    """Crea un nuovo goal. Scope: 'hub' (meta-goals) o 'workspace:<name>'."""
    hub = _hub_root_from_scope()
    if not hub:
        return {"error": "hub root not determinable"}
    gio = _goal_module()
    if not gio:
        return {"error": "goal_io not available"}
    title = (args.get("title") or "").strip()
    if not title:
        return {"error": "title required"}
    scope = _resolve_goal_scope(args)
    try:
        return gio.create_goal(
            hub, scope, title,
            deadline=args.get("deadline") or None,
            priority=args.get("priority") or "medium",
            responsabile=args.get("responsabile") or None,
            success_criteria=args.get("success_criteria") or [],
            judge_cron=args.get("judge_cron") or "0 18 * * 0",
            judge_model=args.get("judge_model") or None,
            judge_provider=args.get("judge_provider") or None,
            body_md=args.get("body_md") or "",
            tags=args.get("tags") or [],
            owner=args.get("owner") or "vincent",
        )
    except Exception as e:
        return {"error": f"create failed: {type(e).__name__}: {e}"}


def tool_goal_list(args: dict) -> dict:
    """Lista goals. Scope opzionale (default: tutti scopes). Status opzionale."""
    hub = _hub_root_from_scope()
    if not hub:
        return {"error": "hub root not determinable"}
    gio = _goal_module()
    if not gio:
        return {"error": "goal_io not available"}
    scope = args.get("scope") or None  # None = tutti scopes
    status = args.get("status") or None
    return {"goals": gio.list_goals(hub, scope=scope, status=status)}


def tool_goal_show(args: dict) -> dict:
    """Dettaglio singolo goal: meta + body + journal entries + reflections."""
    hub = _hub_root_from_scope()
    if not hub:
        return {"error": "hub root not determinable"}
    gio = _goal_module()
    if not gio:
        return {"error": "goal_io not available"}
    gid = (args.get("id") or "").strip()
    if not gid:
        return {"error": "id required"}
    scope = _resolve_goal_scope(args)
    g = gio.read_goal(hub, scope, gid)
    if not g:
        return {"error": f"goal '{gid}' not found in scope '{scope}'"}
    return g


def tool_goal_update(args: dict) -> dict:
    """Update fields del goal (deadline, status, priority, responsabile, success_criteria, judge_cron, judge_model, tags)."""
    hub = _hub_root_from_scope()
    if not hub:
        return {"error": "hub root not determinable"}
    gio = _goal_module()
    if not gio:
        return {"error": "goal_io not available"}
    gid = (args.get("id") or "").strip()
    if not gid:
        return {"error": "id required"}
    scope = _resolve_goal_scope(args)
    updates = {k: v for k, v in args.items() if k not in ("id", "scope")}
    res = gio.update_goal(hub, scope, gid, updates)
    if not res:
        return {"error": f"goal '{gid}' not found"}
    return res


def tool_goal_judge(args: dict) -> dict:
    """Esegue judge: aggiunge verdict al journal. Args: id, verdict, agent, body_md.

    Versione MVP: il caller (LLM o routine) decide verdict e body. Auto-judge logic
    è in webapp/goal_judge.py invocato da routine cron.
    """
    hub = _hub_root_from_scope()
    if not hub:
        return {"error": "hub root not determinable"}
    gio = _goal_module()
    if not gio:
        return {"error": "goal_io not available"}
    gid = (args.get("id") or "").strip()
    verdict = (args.get("verdict") or "").strip()
    agent = (args.get("agent") or "manual").strip()
    body = args.get("body_md") or args.get("notes") or ""
    if not gid or not verdict:
        return {"error": "id and verdict required"}
    if verdict not in gio.VALID_VERDICTS:
        return {"error": f"verdict must be one of {gio.VALID_VERDICTS}"}
    scope = _resolve_goal_scope(args)
    ok = gio.append_journal(hub, scope, gid, verdict, agent, body)
    if not ok:
        return {"error": f"failed to append journal for '{gid}'"}
    return {"id": gid, "scope": scope, "verdict": verdict, "agent": agent}


def tool_goal_reflect(args: dict) -> dict:
    """Append a reflections.md (pivot / post-mortem manuale)."""
    hub = _hub_root_from_scope()
    if not hub:
        return {"error": "hub root not determinable"}
    gio = _goal_module()
    if not gio:
        return {"error": "goal_io not available"}
    gid = (args.get("id") or "").strip()
    text = args.get("text") or ""
    if not gid or not text.strip():
        return {"error": "id and text required"}
    scope = _resolve_goal_scope(args)
    ok = gio.append_reflection(hub, scope, gid, text)
    if not ok:
        return {"error": f"failed to write reflection for '{gid}'"}
    return {"id": gid, "scope": scope}


def tool_goal_archive(args: dict) -> dict:
    """Marca goal come achieved/abandoned/failed + reflection finale opzionale."""
    hub = _hub_root_from_scope()
    if not hub:
        return {"error": "hub root not determinable"}
    gio = _goal_module()
    if not gio:
        return {"error": "goal_io not available"}
    gid = (args.get("id") or "").strip()
    outcome = (args.get("outcome") or "").strip()
    reflection = args.get("reflection") or ""
    if not gid or outcome not in ("achieved", "abandoned", "failed"):
        return {"error": "id required, outcome in [achieved, abandoned, failed]"}
    scope = _resolve_goal_scope(args)
    return gio.archive_goal(hub, scope, gid, outcome, reflection)


# ============================================================
# Fase P-CLI — Printing Press catalog
# ============================================================

def _pp_binary_path() -> Optional[Path]:
    """Trova printing-press binary. Stessa logica di webapp/pp_integration.py ma stdlib only."""
    import shutil
    found = shutil.which("printing-press")
    if found:
        return Path(found)
    home_go = Path.home() / "go" / "bin" / "printing-press"
    if home_go.is_file():
        return home_go
    gopath = os.environ.get("GOPATH")
    if gopath:
        cand = Path(gopath) / "bin" / "printing-press"
        if cand.is_file():
            return cand
    return None


def tool_pp_catalog_search(args: dict) -> dict:
    """Cerca nel catalog Printing Press una API/service per il quale esiste già una CLI curata.

    USE FIRST quando l'utente vuole integrare un servizio (Stripe, Notion, GitHub, ecc.)
    PRIMA di proporre di generare a mano. Se trovato → suggerisci `pp.catalog_show(name)` per dettagli
    e poi delega a `cli-architect` per generare.
    """
    import subprocess
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "query required"}
    pp = _pp_binary_path()
    if not pp:
        return {"error": "printing-press not installed. Install with: brew install go && go install github.com/mvanhorn/cli-printing-press/v4/cmd/printing-press@latest"}
    try:
        r = subprocess.run(
            [str(pp), "catalog", "search", query],
            capture_output=True, text=True, timeout=10,
        )
        text = r.stdout
        items = []
        skip_patterns = ("No entries", "Found ", "matching entries", "----", "====")
        for ln in text.splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("="):
                continue
            if any(p in ln for p in skip_patterns):
                continue
            # PP catalog output format: "name<spaces>category<spaces>description"
            parts = ln.split(None, 2)  # split max 3 on whitespace
            if len(parts) >= 2:
                items.append({
                    "name": parts[0],
                    "category": parts[1] if len(parts) >= 2 else "",
                    "description": parts[2] if len(parts) >= 3 else "",
                })
            else:
                items.append({"name": ln, "category": "", "description": ""})
        return {"results": items, "count": len(items), "query": query}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def tool_pp_catalog_show(args: dict) -> dict:
    """Mostra dettagli completi (description, category, auth, base_url) di una entry del catalog PP.

    USE DOPO pp.catalog_search per inspectare il candidato prima di delegare la generazione.
    """
    import subprocess
    name = (args.get("name") or "").strip()
    if not name:
        return {"error": "name required"}
    pp = _pp_binary_path()
    if not pp:
        return {"error": "printing-press not installed"}
    try:
        r = subprocess.run(
            [str(pp), "catalog", "show", name],
            capture_output=True, text=True, timeout=10,
        )
        return {"name": name, "details": r.stdout[:4000], "ok": r.returncode == 0,
                "error": r.stderr[:500] if r.returncode != 0 else None}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def tool_pp_list_installed(args: dict) -> dict:
    """Lista CLI Printing Press già generate localmente + dove sono installate (hub/workspace).

    USE per capire se un servizio è già stato integrato prima di rifare.
    """
    pp_mod = _load_webapp_module("pp_integration")
    if not pp_mod or not hasattr(pp_mod, "list_installed_pp"):
        return {"error": "pp_integration module not available", "hint": f"webapp non importabile da {WEBAPP_DIR} (vedi stderr del server)"}
    hub = _hub_root_from_scope()
    if not hub:
        return {"error": "hub root not determinable"}
    return pp_mod.list_installed_pp(hub)


# ============================================================
# tool registry — JSON Schema per MCP tools/list
# ============================================================

# Filtro via env ANJA_TOOL_GROUPS (comma-sep). Default: tutti e 6 i gruppi.
TOOL_GROUPS = {
    "agents": ["agent.list", "agent.delegate"],
    "tasks": ["task.schedule_one_shot", "task.list", "task.cancel"],
    "workspace": ["workspace.create", "workspace.list", "workspace.list_files", "workspace.read_file", "workspace.write_file"],
    "kanban": ["kanban.create", "kanban.show", "kanban.complete", "kanban.block", "kanban.unblock", "kanban.comment", "kanban.assign", "kanban.search"],
    "goals": ["goal.create", "goal.list", "goal.show", "goal.update", "goal.judge", "goal.reflect", "goal.archive"],
    "pp": ["pp.catalog_search", "pp.catalog_show", "pp.list_installed"],
}


_ALLOWED_CACHE: Optional[set] = None


def _allowed_tool_names() -> set:
    """Filter set basato su env ANJA_TOOL_GROUPS (calcolato una volta: l'env non cambia
    a processo vivo). Vuoto = tutti. Gruppo ignoto → warning su stderr, ignorato."""
    global _ALLOWED_CACHE
    if _ALLOWED_CACHE is not None:
        return _ALLOWED_CACHE
    raw = os.environ.get("ANJA_TOOL_GROUPS", "").strip()
    if not raw:
        _ALLOWED_CACHE = {t for g in TOOL_GROUPS.values() for t in g}
        return _ALLOWED_CACHE
    names = set()
    for g in [x.strip() for x in raw.split(",") if x.strip()]:
        if g in TOOL_GROUPS:
            names.update(TOOL_GROUPS[g])
        else:
            print(f"[{SERVER_NAME}] WARN ANJA_TOOL_GROUPS: gruppo '{g}' sconosciuto (ignorato); "
                  f"validi: {','.join(TOOL_GROUPS)}", file=sys.stderr, flush=True)
    _ALLOWED_CACHE = names
    return names


TOOLS = [
    {
        "name": "agent.list",
        "description": "Lista agent specializzati disponibili nel hub. Usa quando l'utente chiede di un dominio specifico per capire se delegare a un agent esperto.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "agent.delegate",
        "description": "Delega un task a un agent specializzato (es. social, dev, analyst). L'agent risponde in character secondo SOUL+AGENTS+TOOLS. OMETTI `target` per lasciar scegliere l'agent giusto in automatico dalle sue auto_route_keywords (la risposta include `routing` con il perché). Restituisce la risposta dell'agent come tool result.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Nome agent da invocare. Omettilo per l'auto-routing sulle keyword del prompt"},
                "workspace": {"type": "string", "description": "Restringe l'auto-routing a un workspace (utile se più brand condividono i nomi degli specialisti)"},
                "prompt": {"type": "string", "description": "Task/domanda da delegare all'agent"},
                "timeout_sec": {"type": "integer", "default": 120, "description": "Timeout massimo per la delegation"},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "task.schedule_one_shot",
        "description": (
            "🕐 SCHEDULING tool: schedula un PROMPT da eseguire AUTONOMAMENTE in un momento futuro (cron auto-disable). "
            "USA SOLO per richieste tipo 'ricontrolla tra 30 min', 'verifica domani alle 9', 'controlla fra 2 ore'. "
            "PRIMA di chiamare, CHIEDI all'utente come essere notificato (telegram/webhook/file/email) → output_actions. "
            "❌ NON usarlo per: 'che task ci sono?', 'cosa devo fare?', 'aggiungi alla lista', 'ricorda di...'. "
            "Per task/todo/promemoria usa il kanban (kanban.create / kanban.show)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "when": {"type": "string", "description": "Quando eseguire: 'in 30 min', 'in 2 hours', 'tomorrow 09:00', 'today 17:30', o ISO datetime '2026-05-08T19:17'"},
                "prompt": {"type": "string", "description": "Task da eseguire alla scadenza (testo libero, può richiedere tool MCP)"},
                "output_actions": {
                    "type": "array",
                    "description": "Come notificare il risultato. Es: [{type:'telegram', chat_id:'...'}, {type:'file', path:'/tmp/x.md'}, {type:'webhook', url:'...'}, {type:'email', to:'...'}]",
                    "items": {"type": "object"},
                },
                "name": {"type": "string", "description": "Slug routine kebab-case (auto-generato se omesso)"},
                "tools": {"type": "array", "items": {"type": "string"}, "description": "Allowed tools (default: tutti MCP del hub)"},
            },
            "required": ["when", "prompt"],
        },
    },
    {
        "name": "task.list",
        "description": (
            "🕐 Lista SOLO routine one-shot SCHEDULATE pendenti (cron auto-disable). "
            "❌ NON è il kanban — per la lista task/todo dell'utente usa kanban.show. "
            "Usa solo per 'che cosa è schedulato per dopo?', 'cosa parte automaticamente?'"
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "task.cancel",
        "description": "Cancella una routine one-shot SCHEDULATA prima della sua esecuzione. NON è cancellare un task kanban (per quello usa kanban.delete o cambia status).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Slug della routine one-shot"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "workspace.create",
        "description": "Crea un nuovo workspace internal (es. ufficio finanze, lab analisi) con un responsabile agent. Il workspace ha la sua memoria, files, scripts, wiki. Il responsabile vive dentro il workspace e ha personalità + role dedicati. Usa per richieste tipo 'crea workspace finanze', 'fammi un ufficio per gestire X', 'voglio un agent dedicato a Y'. PRIMA di chiamare CHIEDI all'utente: nome workspace, nome responsabile, role description (cosa farà). ws_type default 'office', alternative 'lab/studio/inbox/custom'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Nome del workspace (es. 'finanze', 'dev-tools')"},
                "responsabile_name": {"type": "string", "description": "Nome del responsabile agent (es. 'anja-finanze', 'anja-dev')"},
                "role_description": {"type": "string", "description": "Descrizione del ruolo/dominio del responsabile (es. 'Gestione report finanziari mensili e P/L analysis')"},
                "ws_type": {"type": "string", "enum": ["office", "lab", "studio", "inbox", "custom"], "description": "Tipo workspace"},
                "responsabile_provider": {"type": "string", "description": "Provider LLM responsabile (default: claude)"},
                "responsabile_model": {"type": "string", "description": "Model responsabile (default: sonnet)"},
                "responsabile_effort": {"type": "string", "description": "Effort (off|low|medium|high)"},
            },
            "required": ["name", "responsabile_name", "role_description"],
        },
    },
    {
        "name": "workspace.list",
        "description": "Lista tutti i workspace registrati nel hub (internal + external) con kind metadata e responsabile.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "workspace.list_files",
        "description": "Lista file in uno scope workspace (sandboxed). Whitelist: files/, data/, scripts/, wiki/ + CLAUDE.md/log.md/meta.yaml. Usa scope='hub' per i file di Anja a hub-level, scope='workspace:<name>' per workspace specifici.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "description": "'hub' o 'workspace:<name>'"},
                "path": {"type": "string", "description": "Path relativo (es. 'files', 'files/report.docx'). Vuoto = root del scope"},
            },
            "required": ["scope"],
        },
    },
    {
        "name": "workspace.read_file",
        "description": "Legge un file da uno scope workspace (max 500KB). Sandboxed con stessa whitelist di list_files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "description": "'hub' o 'workspace:<name>'"},
                "path": {"type": "string", "description": "Path relativo del file"},
            },
            "required": ["scope", "path"],
        },
    },
    {
        "name": "workspace.write_file",
        "description": "Scrive un file in uno scope workspace. SOLO in files/, scripts/, data/ subdirs (non root files come CLAUDE.md). Auto-log in log.md del scope. Path tipici: 'files/report-YYYY-MM-DD.md', 'scripts/util.py', 'data/dataset.csv'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "description": "'hub' o 'workspace:<name>'"},
                "path": {"type": "string", "description": "Path relativo (deve iniziare con files/, scripts/, o data/)"},
                "content": {"type": "string", "description": "Contenuto del file (max 5MB)"},
            },
            "required": ["scope", "path", "content"],
        },
    },
    {
        "name": "kanban.create",
        "description": (
            "📋 KANBAN: crea un task nella board condivisa (lista TODO persistente cross-sessione). "
            "USE FOR: 'ricordami di...', 'aggiungi alla lista', 'crea task...', 'todo: ...', decomposition multi-step. "
            "Status default 'todo' (auto-promote a 'ready' quando deps done). "
            "Scope: 'hub' o 'workspace:<name>'. Assignee tipici: 'anja', 'anja-finanze' (workspace lead), 'human:vincent'. "
            "Sub-task: parent_id. Dependencies (bloccanti): depends_on=[id1,id2]. "
            "❌ NON usarlo per scheduling temporale ('alle 9 domani') → quello è task.schedule_one_shot."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string", "description": "Descrizione/details (opzionale, markdown ok)"},
                "scope": {"type": "string", "description": "'hub' (default) o 'workspace:<name>'"},
                "assignee": {"type": "string", "description": "es. 'anja', 'anja-finanze', 'human:vincent'"},
                "priority": {"type": "integer", "description": "0=low, 1=normal (default), 2=high, 3=urgent"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "due_at": {"type": "string", "description": "ISO datetime (opzionale)"},
                "parent_id": {"type": "integer", "description": "Id task parent se sub-task"},
                "depends_on": {"type": "array", "items": {"type": "integer"}, "description": "Lista id task che devono finire prima"},
                "status": {"type": "string", "enum": ["triage", "todo", "ready", "running", "blocked", "done"]},
            },
            "required": ["title"],
        },
    },
    {
        "name": "kanban.show",
        "description": (
            "📋 KANBAN: lista task della board OR dettaglio singolo (se id). "
            "USE FOR: 'che task ci sono?', 'cosa devo fare oggi?', 'cosa c'è in lista?', "
            "'mostrami i task', 'briefing mattutino', 'che task ci sono in done?'. "
            "DEFAULT (senza filtri): ritorna TUTTI i task non-archived (incluso done) + stats per status. "
            "Per filtrare per status specifico, usa status='triage'|'todo'|'ready'|'running'|'blocked'|'done'. "
            "Shortcut: status='active' = tutti i non-done/non-archived. "
            "Per vedere ANCHE archived: include_archived=true. "
            "Per dettaglio singolo task: passa id (int). "
            "❌ NON è task.list (= scheduling cron)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Se presente, ritorna dettaglio singolo task"},
                "scope": {"type": "string", "description": "es. 'hub' o 'workspace:finanze'"},
                "status": {
                    "type": "string",
                    "enum": ["triage", "todo", "ready", "running", "blocked", "done", "archived", "active"],
                    "description": "Filtra per status. 'active' = shortcut per tutti tranne done/archived. Omit per vedere tutti (incluso done).",
                },
                "assignee": {"type": "string"},
                "parent_id": {"type": "integer"},
                "include_archived": {"type": "boolean", "description": "Se true, include anche archived. Default false."},
                "limit": {"type": "integer", "description": "Max risultati. Default 50."},
            },
        },
    },
    {
        "name": "kanban.complete",
        "description": (
            "Marca task come done con summary opzionale. "
            "Auto-promote dei dependent task (passano a 'ready' se loro deps satisfied). "
            "Aggiungi un summary breve (1-2 frasi) di cosa è stato completato."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "summary": {"type": "string", "description": "Cosa è stato completato (1-2 frasi)"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "kanban.block",
        "description": "Blocca task con reason. Sospende l'esecuzione finché non viene sbloccato.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "reason": {"type": "string", "description": "Perché bloccato (è visibile)"},
            },
            "required": ["id", "reason"],
        },
    },
    {
        "name": "kanban.unblock",
        "description": "Sblocca task. Auto-determine new status: 'ready' se deps OK, altrimenti 'todo'.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
    {
        "name": "kanban.comment",
        "description": "Aggiunge commento a un task (audit trail).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "content": {"type": "string"},
                "author": {"type": "string", "description": "Default = vuoto (sarà inferito)"},
            },
            "required": ["id", "content"],
        },
    },
    {
        "name": "kanban.assign",
        "description": "Cambia assignee. Es. delegare a 'anja-finanze' o richiedere conferma 'human:vincent'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "assignee": {"type": "string"},
            },
            "required": ["id", "assignee"],
        },
    },
    {
        "name": "kanban.search",
        "description": "Ricerca full-text in title+body.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "goal.create",
        "description": (
            "🎯 GOAL: crea un obiettivo persistente di medio/lungo respiro (settimane/mesi). "
            "USE FOR: 'voglio raggiungere X', 'obiettivo trimestrale', 'voglio imparare Y'. "
            "Diverso dal kanban (task brevi): i goal hanno judge cron periodico, success criteria, journal narrativo. "
            "Scope: 'hub' (meta-goals supervisione) o 'workspace:<name>' (obiettivi specifici). "
            "Esempio: goal.create(title='+500 USDT P/L su Bybit demo in 30gg', scope='workspace:finanze', "
            "deadline='2026-06-13', success_criteria=['closed_pnl > 500', 'win_rate > 55%'], judge_cron='0 18 * * 0')."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "scope": {"type": "string", "description": "'hub' o 'workspace:<name>'"},
                "deadline": {"type": "string", "description": "YYYY-MM-DD"},
                "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                "responsabile": {"type": "string", "description": "Agent supervisor (es. anja-finanze)"},
                "success_criteria": {"type": "array", "items": {"type": "string"}},
                "judge_cron": {"type": "string", "description": "Cron expr per judge (default: '0 18 * * 0' = domenica 18:00)"},
                "judge_model": {"type": "string", "description": "Override modello judge (default: hub default)"},
                "judge_provider": {"type": "string"},
                "body_md": {"type": "string", "description": "Contesto / strategia / note libere"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "owner": {"type": "string", "description": "Default: 'vincent' (single-user)"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "goal.list",
        "description": (
            "🎯 GOAL: lista obiettivi persistenti. USE FOR: 'che obiettivi ho?', 'mostrami i goal attivi'. "
            "Default: tutti scopes (hub + workspaces). Filtra per scope='hub' o scope='workspace:<name>'. "
            "Filtra per status: 'active' (default), 'achieved', 'abandoned', 'paused', 'failed'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string"},
                "status": {"type": "string", "enum": ["active", "achieved", "abandoned", "paused", "failed"]},
            },
        },
    },
    {
        "name": "goal.show",
        "description": (
            "🎯 GOAL: dettaglio singolo goal — meta + body + journal entries + reflections. "
            "USE FOR: 'come va il goal X?', 'mostrami il journal di Y', 'stato obiettivo Z'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Goal slug (es. 'bybit-500-usdt-in-30gg')"},
                "scope": {"type": "string"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "goal.update",
        "description": (
            "🎯 GOAL: modifica fields del goal (deadline, status, priority, responsabile, success_criteria, judge_cron, tags). "
            "USE FOR: 'sposta deadline', 'cambia priorità', 'metti in pausa'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "scope": {"type": "string"},
                "title": {"type": "string"},
                "deadline": {"type": "string"},
                "status": {"type": "string", "enum": ["active", "achieved", "abandoned", "paused", "failed"]},
                "priority": {"type": "string"},
                "responsabile": {"type": "string"},
                "success_criteria": {"type": "array", "items": {"type": "string"}},
                "judge_cron": {"type": "string"},
                "judge_model": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["id"],
        },
    },
    {
        "name": "goal.judge",
        "description": (
            "🎯 GOAL: append verdict al journal del goal. "
            "USE FOR: dopo una valutazione del progresso del goal — scrivi il verdict + razionale. "
            "Verdict enum: on_track / drift / blocked / achieved / failed. "
            "Body markdown libero per dettagli (metriche concrete, osservazioni, suggested actions). "
            "Tipicamente chiamato da routine cron schedulata sul goal, ma anche manuale."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "scope": {"type": "string"},
                "verdict": {"type": "string", "enum": ["on_track", "drift", "blocked", "achieved", "failed"]},
                "agent": {"type": "string", "description": "Chi giudica (default 'manual')"},
                "body_md": {"type": "string", "description": "Razionale verdict in markdown"},
            },
            "required": ["id", "verdict"],
        },
    },
    {
        "name": "goal.reflect",
        "description": (
            "🎯 GOAL: append reflection libera al goal (pivot / post-mortem / nota personale). "
            "USE FOR: 'aggiungi nota al goal X', 'rifletti su come sta andando Y'. "
            "Diverso da goal.judge: questo è prosa libera, non un verdict strutturato."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "scope": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["id", "text"],
        },
    },
    {
        "name": "goal.archive",
        "description": (
            "🎯 GOAL: chiude un goal con outcome finale. "
            "USE FOR: 'goal X è raggiunto', 'abbandona Y', 'goal Z fallito'. "
            "Outcome enum: achieved / abandoned / failed. Reflection finale opzionale."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "scope": {"type": "string"},
                "outcome": {"type": "string", "enum": ["achieved", "abandoned", "failed"]},
                "reflection": {"type": "string", "description": "Post-mortem markdown (opzionale)"},
            },
            "required": ["id", "outcome"],
        },
    },
    {
        "name": "pp.catalog_search",
        "description": (
            "🏭 Cerca nel catalog Printing Press se esiste già una CLI curata per un servizio "
            "(Stripe, Notion, GitHub, Linear, ecc.). USE PRIMA di proporre di generare a mano: "
            "se l'utente chiede 'integra X', chiama questo tool per vedere se PP ha già X nel catalog. "
            "Se trovato → suggerisci di delegare a `cli-architect` per installarlo (5 min di generazione "
            "+ auto-registro come MCP tool). Output: lista {name, description}."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Nome o keyword del servizio (es. 'stripe', 'search console', 'github')"}},
            "required": ["query"],
        },
    },
    {
        "name": "pp.catalog_show",
        "description": (
            "🏭 Mostra dettagli completi (auth, base_url, category) di una entry del catalog Printing Press. "
            "USE DOPO pp.catalog_search per inspectare un candidato prima di confermare la generazione."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Nome canonico nel catalog (es. 'stripe')"}},
            "required": ["name"],
        },
    },
    {
        "name": "pp.list_installed",
        "description": (
            "🏭 Lista CLI Printing Press già generate localmente + dove installate (hub/workspace). "
            "USE per capire se un servizio è già stato integrato prima di rigenerarlo da zero. "
            "Tipico flow: utente dice 'integra Stripe' → pp.list_installed → se già presente, "
            "informa l'utente; se no → pp.catalog_search → cli-architect."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


TOOL_HANDLERS = {
    "agent.list": tool_agent_list,
    "agent.delegate": tool_agent_delegate,
    "task.schedule_one_shot": tool_task_schedule_one_shot,
    "task.list": tool_task_list,
    "task.cancel": tool_task_cancel,
    "workspace.create": tool_workspace_create,
    "workspace.list": tool_workspace_list,
    "workspace.list_files": tool_workspace_list_files,
    "workspace.read_file": tool_workspace_read_file,
    "workspace.write_file": tool_workspace_write_file,
    "kanban.create": tool_kanban_create,
    "kanban.show": tool_kanban_show,
    "kanban.complete": tool_kanban_complete,
    "kanban.block": tool_kanban_block,
    "kanban.unblock": tool_kanban_unblock,
    "kanban.comment": tool_kanban_comment,
    "kanban.assign": tool_kanban_assign,
    "kanban.search": tool_kanban_search,
    "goal.create": tool_goal_create,
    "goal.list": tool_goal_list,
    "goal.show": tool_goal_show,
    "goal.update": tool_goal_update,
    "goal.judge": tool_goal_judge,
    "goal.reflect": tool_goal_reflect,
    "goal.archive": tool_goal_archive,
    "pp.catalog_search": tool_pp_catalog_search,
    "pp.catalog_show": tool_pp_catalog_show,
    "pp.list_installed": tool_pp_list_installed,
}


# ============================================================
# JSON-RPC 2.0 dispatcher
# ============================================================

def handle_request(req: dict) -> Optional[dict]:
    method = req.get("method")
    params = req.get("params") or {}
    req_id = req.get("id")

    if method == "initialize":
        return _ok(req_id, {
            "protocolVersion": PROTO_VERSION,
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "capabilities": {"tools": {"listChanged": False}},
        })

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        allowed = _allowed_tool_names()
        return _ok(req_id, {"tools": [t for t in TOOLS if t["name"] in allowed]})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in _allowed_tool_names():
            return _err(req_id, -32601, f"tool '{name}' not available in this server instance")
        handler = TOOL_HANDLERS.get(name)
        if not handler:
            return _err(req_id, -32601, f"unknown tool: {name}")
        try:
            result = handler(args)
            content = [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]
            return _ok(req_id, {"content": content, "isError": "error" in result})
        except Exception as e:
            return _err(req_id, -32603, f"tool '{name}' failed: {type(e).__name__}: {e}")

    if method == "ping":
        return _ok(req_id, {})

    return _err(req_id, -32601, f"method not found: {method}")


def _ok(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


# ============================================================
# stdio loop
# ============================================================

def main():
    groups_env = os.environ.get("ANJA_TOOL_GROUPS", "")
    print(f"[{SERVER_NAME}] starting (scope={SCOPE} root={ROOT} hub={_hub_root_from_scope()} "
          f"groups={groups_env or 'ALL'} tools={len(_allowed_tool_names())} webapp={WEBAPP_DIR})",
          file=sys.stderr, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            sys.stdout.write(json.dumps(_err(None, -32700, f"parse error: {e}")) + "\n")
            sys.stdout.flush()
            continue
        resp = handle_request(req)
        if resp is None:
            continue
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
