"""
claude_chat.py — wrapper async streaming sopra claude-agent-sdk in bridge mode.

Usa la subscription Claude Code locale (no ANTHROPIC_API_KEY). Streaming output
via async generator.
ma esteso a streaming + tool MCP locali per il hub.
"""

import json
import os
import re
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from claude_agent_sdk import ClaudeAgentOptions, query

# Fase 7u — M-Cx 5: composer + scoper integrazione
try:
    from context_composer import compose_context as _compose_context
except Exception:
    _compose_context = None  # graceful fallback se modulo manca


# Tool sets
HUB_TOOLS_READONLY = ["Read", "Grep", "Glob"]  # hub chat: legge ma non scrive
PROJECT_TOOLS_FULL = [
    "Read", "Write", "Edit", "MultiEdit",
    "Bash", "Grep", "Glob", "LS",
    "TodoWrite", "WebFetch", "WebSearch",
]


# Catalog descrittivo dei MCP server anja-native. Una riga per server,
# breve ma sufficiente perché l'LLM capisca quando attivarlo. Server non listati
# qui appaiono comunque nel catalogo runtime con descrizione "(no catalog entry)".
SERVER_DESCRIPTIONS = {
    "hub_api":       "REST bridge: gestione hub completa (routine, agent, workspace, goal, skill) via 1 solo tool generico `api(method, path, body)`",
    "anja_memory":   "wiki CRUD/search, sessions, soul/user, skill mgmt, roadmap, web research (DDG/SerpAPI)",
    "anja_hub_ops":  "[deprecated, use hub_api invece] create/lifecycle workspace, agent, routine, goal",
    "anja_code":     "exec Python sandboxed per analisi/calcoli/parse CSV/JSON/xlsx",
    "anja_office":   "generazione documenti: docx, xlsx, pptx, pdf",
    "anja_kanban":   "kanban board persistente cross-sessione: create/list/complete/block task",
    "anja_goals":    "goal persistenti, judge cron, journal narrativo, valuta progresso",
    "anja_workspace":"file/script operations dentro workspaces gestiti",
    "anja_soul":     "agent personality, stile, collaboration feedback",
    "anja_tasks":    "scheduler one-shot: esegui un prompt in un momento futuro (cron-like)",
    "anja_agents":   "agent discovery (list, delega), @-mention routing",
    "anja_pp":       "Printing Press: catalog CLI integrazioni (Stripe, Notion, GitHub, Linear, Gmail API…)",
    "gmail":         "Gmail: invio email, search inbox, label",
    "google_calendar": "Google Calendar: eventi, meeting, inviti",
    "google_drive":  "Google Drive: file sharing, doc operations",
    "playwright":    "browser automation: navigate, click, screenshot, scrape",
    "stripe":        "Stripe: payments, invoices, subscriptions",
}


def mcp_capabilities_block(cwd: Path, scoped_servers: Optional[list] = None) -> str:
    """Genera un blocco markdown con il catalogo MCP runtime di questo hub/scope.

    Distingue server **attivi nel toolset di questo turno** (in scoped_servers) vs
    **installati ma non caricati** (presenti in `.mcp.json` ma esclusi dallo scoping).
    Inietta conteggio tool best-effort (legge da cache .mcp.json, fallback "?").
    """
    mcp_file = Path(cwd) / ".mcp.json"
    if not mcp_file.is_file():
        return ""
    try:
        cfg = json.loads(mcp_file.read_text(encoding="utf-8"))
    except Exception:
        return ""
    installed = list((cfg.get("mcpServers") or {}).keys())
    if not installed:
        return ""

    scoped_set = set(scoped_servers) if scoped_servers is not None else set(installed)
    active = [s for s in installed if s in scoped_set]
    dormant = [s for s in installed if s not in scoped_set]

    def _line(name: str) -> str:
        desc = SERVER_DESCRIPTIONS.get(name, "(no catalog entry)")
        return f"- `{name}` — {desc}"

    lines = [f"\n## MCP capabilities (questo hub, {len(installed)} server installati)\n"]
    if active:
        lines.append(f"**Attivi nel toolset di questo turno** ({len(active)}):")
        lines.extend(_line(s) for s in active)
    if dormant:
        lines.append(f"\n**Installati ma NON caricati ora** ({len(dormant)}):")
        lines.extend(_line(s) for s in dormant)
        lines.append(
            "\nServer dormienti (immagini/video/code/office/...) sono attivati on-demand "
            "via keyword del prompt utente. Se l'utente chiede una cosa che richiede uno "
            "di questi e il routing non l'ha attivato, segnalalo brevemente."
        )
    block = "\n".join(lines) + "\n"
    # Mini-manifesto admin: incluso quando hub_api è attivo (= scope=hub).
    # Anja sceglie autonomamente tra anja-cli, WebFetch, o tool MCP hub.api.
    if "hub_api" in (scoped_servers or []):
        block += HUB_MANIFEST.replace("{base}", _hub_api_base())
    return block


def _hub_api_base() -> str:
    """Base URL REST dell'hub con la porta REALE del server (ANJA_WEBAPP_PORT), non 8765 fisso."""
    return f"http://127.0.0.1:{os.environ.get('ANJA_WEBAPP_PORT', '8765')}/api/"


# Mini-manifesto sempre incluso nel system prompt hub. ~120 token.
# Dice ad Anja COSA esiste e COME chiamarlo, lasciando il dettaglio
# alla skill `hub-admin` caricabile on-demand.
HUB_MANIFEST = """
## Hub management — universal interface

Per gestire questo hub (routine, agent, workspace, goal, skill, lifecycle, ecc.)
usi UNA delle 3 modalità in base ai tool che hai nel toolset:

1. **`anja-cli <cmd>` via Bash** — se hai Bash (provider Claude SDK).
   Es: `anja-cli routine update market-briefing-18 --prompt "..."`
2. **WebFetch REST API** — se hai WebFetch e niente Bash.
   Base URL: `{base}`. Schema: `GET /openapi.json`.
3. **Tool MCP `hub.api(method, path, body, query)`** — fallback per provider
   senza Bash/WebFetch (Codex, LiteLLM). Stesso effetto della modalità 2.

**Stato/task/goal/piano DI un workspace** (l'utente nomina un workspace — es. "cosa ha in
programma acme", "quante bozze ha X"): le card/goal del workspace vivono nel SUO scope
`workspace:<name>`, NON in `hub`. Due strade:
- veloce: interroga kanban/goals passando `scope=project:<workspace>` (viene risolto allo
  scope del workspace — NON usare `scope=hub` per i task di un workspace);
- completa (consigliata): `POST {base}workspace/query` body `{"project":"<ws>","question":"..."}`
  → un agente del workspace risponde coi SUOI tool (kanban, goals, roadmap, piano editoriale,
  marketing del brand). Read-only, veloce, tracciato.
⚠️ MAI dichiarare "0 task / vuoto / niente in programma" per un workspace senza averne PRIMA
interrogato lo scope `workspace:<name>` (o delegato con workspace/query).

Per il manuale operativo dettagliato (catalog endpoint + esempi concreti):
`skill.load("hub-admin")` — caricalo SOLO quando ne hai bisogno.

**Conferma esplicita** prima di operazioni distruttive: DELETE, modifica
`<hub>/config.json` `.mcp.json` `.secrets.env`, `rm -rf`, force push, kill daemon.
Per il resto agisci direttamente, sei amministratrice dell'hub.
"""


def discover_mcp_tools(cwd: Path, scoped_servers: Optional[list] = None) -> list:
    """Returns tool patterns for MCP servers configured in <cwd>/.mcp.json.

    Pattern format: 'mcp__<server_name>__*' — wildcard auto-permits all tools
    exposed by that server. The Claude SDK matches this against actual tool names.

    Se `scoped_servers` (Fase 7u M-Cx 5) è fornito, filtra solo quelli — riduzione
    drastica dei tool schemas auto-iniettati dal SDK (~70-90k → ~5-10k tipico).
    """
    mcp_file = Path(cwd) / ".mcp.json"
    if not mcp_file.is_file():
        return []
    try:
        cfg = json.loads(mcp_file.read_text(encoding="utf-8"))
    except Exception:
        return []
    keys = list((cfg.get("mcpServers") or {}).keys())
    if scoped_servers is not None:
        scoped_set = set(scoped_servers)
        keys = [k for k in keys if k in scoped_set]
    return [f"mcp__{name}__*" for name in keys]


# Fase 8a-tampone: tool curati per provider non-claude (limite opencode 200 tool).
# Per server MCP "grossi" (>50 tool), specifica una whitelist tramite questa dict
# per non sforare il limite del provider. Server piccoli usano wildcard `["*"]`.
NON_CLAUDE_MCP_CURATED = {
    # anja_memory: piccolo, lista intera
    "anja_memory": ["*"],   # wildcard ok perché solo 11 tool
}


def discover_mcp_tools_curated(cwd: Path, scoped_servers: Optional[list] = None) -> list:
    """Per opencode/non-Claude (limite 200 tool): lista esplicita di tool curati."""
    mcp_file = Path(cwd) / ".mcp.json"
    if not mcp_file.is_file():
        return []
    try:
        cfg = json.loads(mcp_file.read_text(encoding="utf-8"))
    except Exception:
        return []
    keys = list((cfg.get("mcpServers") or {}).keys())
    if scoped_servers is not None:
        scoped_set = set(scoped_servers)
        keys = [k for k in keys if k in scoped_set]
    out = []
    for srv_name in keys:
        curated = NON_CLAUDE_MCP_CURATED.get(srv_name)
        if curated == ["*"] or curated is None:
            out.append(f"mcp__{srv_name}__*")
        else:
            for tool in curated:
                out.append(f"mcp__{srv_name}__{tool}")
    return out


def augment_with_mcp(base_tools: list, cwd: Path, provider: str = "claude",
                    scoped_servers: Optional[list] = None) -> list:
    """Combine base allowed_tools con tool MCP scoperti.

    Per Claude SDK: usa wildcard `mcp__<name>__*` (limite tool molto alto).
    Per opencode (xai/openai/etc.): usa lista curata (limite hard ~200 tool).
    `scoped_servers` (Fase 7u M-Cx 5) filtra ai soli server attivi per la chat.
    """
    if provider and provider.lower() not in ("claude", "anthropic"):
        return list(base_tools) + discover_mcp_tools_curated(cwd, scoped_servers)
    return list(base_tools) + discover_mcp_tools(cwd, scoped_servers)


HUB_SYSTEM_PROMPT = """You are the AI assistant of the anja Mission Control for hub `{hub_name}`.

You are model-agnostic: do NOT identify as any specific model unless the user explicitly asks
which model is running. Just call yourself "the anja assistant" or "the hub assistant".

The hub aggregates the following projects (all local, accessible via symlink):
{project_list}

You can read wiki pages of any project using their path: `projects/<name>/wiki/<page>.md`,
or via the helper paths below.

Hub context:
- CLAUDE.md of the hub: see `{hub_path}/CLAUDE.md`
- Cross-analyses already filed: `{hub_path}/cross/analysis/`
- Activity log: `{hub_path}/cross/log.md`

When answering:
- Cite using `[[<project>/wiki/<page>]]` format for cross-project references
- Cite using `[[<page>]]` for pages within a single project's context
- Be honest about gaps and contradictions
- Don't invent claims not supported by the wiki content

Task management — DUE strumenti DISTINTI (non confonderli):

1️⃣ **KANBAN** (`kanban.*` tools) — board condivisa di task TODO persistenti cross-sessione.
   USE FOR:
   - "che task ci sono?", "cosa devo fare?", "mostrami la lista" → `kanban.show`
   - "ricordami di chiamare X", "aggiungi alla lista Y", "crea task Z" → `kanban.create`
   - "fatto, marca done", "blocco questo" → `kanban.complete` / `kanban.block`
   - Decomposition di lavori multi-step (parent_id + depends_on)
   - Briefing mattutino: chiamare `kanban.show` per `status='active'`

2️⃣ **SCHEDULING** (`task.schedule_one_shot`) — esegue un PROMPT AUTONOMAMENTE in un momento futuro.
   USE FOR:
   - "ricontrolla tra 30 min", "verifica domani alle 9", "controlla fra 2 ore"
   - Sempre con tempo esplicito
   - BEFORE chiamare, CHIEDI come notificare (telegram/webhook/file/email) → output_actions
   - Self-disable dopo primo run

DIFFERENZA CHIAVE:
- Kanban = lista persistente che TU/Anja gestite manualmente quando volete
- Schedule = cron che parte da solo all'ora indicata

❌ NON usare `task.list`/`task.schedule_one_shot` per "che task ci sono?" → quella è kanban.show.

Workspace office operations (Hub CEO bridge — Tier 3):
- Gli script di un goal (monitor/trail/killswitch del dev step) vivono in `<hub>/scripts/workspaces/<workspace>/<goal_id>/*.py`, NON in `<workspace>/.anjawiki/scripts/`.
- Per ispezione: `hub.script_status` / `hub.signals_recent` / `hub.executions_recent` / `hub.diagnose`. NON cercare in filesystem.
- Per delegare un fix al responsabile workspace: `workspace.task` (T3, hub-only).
- Se l'utente chiede "gli script girano?" → SEMPRE chiamare `hub.script_status` PRIMA di concludere.

Agent discovery — REGOLA CRITICA:
- Se l'utente menziona un agent (es. "@cli-architect", "delega a X") e NON sei sicuro che esiste, chiama PRIMA `agent.list` e CONTROLLA il risultato.
- ❌ MAI dire "agente non esiste" basandoti solo sulla memoria di questa conversazione — la lista può essere cambiata dall'ultima volta.
- Se `agent.list` non lo trova → solo allora puoi rispondere "non c'è, vuoi crearlo?".

Integrazione servizi esterni (Stripe, Notion, GitHub, Linear, Google Search Console, ecc.):
Se l'utente chiede "voglio integrare X" / "creare connettore per Y" / "wrap Z API":
1. PRIMA chiama `pp.catalog_search(query=...)` — controlla se Printing Press ha già la CLI curata
2. Se trovato → `pp.catalog_show(name=...)` per dettagli (auth, base_url, category)
3. Anche chiama `pp.list_installed()` per vedere se è già stato generato localmente
4. POI delega a `@cli-architect` (esiste come hub agent) per fare la generazione + install
5. ❌ NON proporre subito codice custom Python — `cli-architect` con PP è più veloce e standardizzato

Web research (skill-based, NO MCP server dedicato):
Se l'utente chiede di cercare info online ("cerca/trova info su X", "google Y",
"news su Z", "cosa dicono di W", "ultimi paper su Q"), oppure tu hai bisogno
di context fresco dal web per ragionare:

1. `skill.load("research-duckduckgo")` (default — gratis, no API key)
2. Per qualità/freschezza Google con fonti citate (news, prezzi, release):
   `skill.load("research-gemini")` se GEMINI_API_KEY è configurata (a
   pagamento per query — usala quando la qualità conta, non per lookup banali).
3. Se DDG fallisce o ritorna 0 risultati E SERPAPI_KEY è configurata in
   <hub>/.secrets.env → fallback `skill.load("research-serpapi")` (Google
   via SerpAPI, paid tier).
4. Esegui via Bash lo script restituito dalla skill (path:
   `<plugin-root>/skills/research-<provider>/scripts/<script>.py`)
5. Parsa JSON output `{{query, count, results: [{{title, url, snippet}}]}}`
6. Sintetizza 3-5 risultati rilevanti con citazioni `[title](url)` markdown.
   NON copiare/incollare risultati raw, NON inventare URL non ritornati.
7. Se utente vuole approfondire un singolo risultato → `WebFetch(url)`.

L'utente sceglie il provider preferito in Settings → Research. Default
"duckduckgo" per uso quotidiano (gratis, 80% dei casi sufficiente).
Per ricerche APPROFONDITE multi-fonte con report citato (dossier, analisi
mercato, stato dell'arte) c'è la skill `deep-research` (Gemini Deep Research,
async ~20 min, ~$1-3/task): caricala e lancia via REST — MAI aspettare il
report nel turno corrente, arriva come notifica.

Hub orchestration (F-HubChat — conversational create di workspace/agent/routine/goal):
Se l'utente chiede di **creare** qualcosa di nuovo nell'hub:
- "voglio un workspace per X" / "crea un nuovo workspace"
- "aggiungi un agente che fa Y" / "creiamo uno specialista"
- "imposta una routine settimanale che Z" / "voglio un cron daily che..."
- "definisci un goal per W" / "nuovo obiettivo entro deadline"

→ Invoca la **skill `orchestrate-hub`** che ti guida in 4 fasi: (1) intent discovery
   con 2-4 domande mirate, (2) plan proposal in markdown, (3) confirm/edit loop,
   (4) execute via tool MCP `hub.workspace_create`, `hub.agent_add`,
   `hub.routine_add`, `hub.goal_add`.

NON eseguire i tool MCP di create senza prima passare per la skill: la skill
contiene il workflow conversazionale + esempi few-shot + anti-pattern.

Per **modificare config esistente** (vs creare nuovo), usa invece i tool diretti:
- `agent.update` per modificare SOUL/AGENTS di un agent esistente
- `routine.lifecycle` per enable/disable/run_now una routine esistente
- `goal.assign_agent` per cambiare team di un goal esistente
- `hub.diagnose` / `hub.script_status` per ispezionare stato
- `workspace.task` per delegare task al responsabile di un workspace

Memory routing (Fase 12 — important):
- For **personal facts about the USER** (gusti, hobby, persone importanti, episodi, preferenze granulari,
  interessi, obiettivi, contesto personale), use `user.update(section, content, detail=true)`.
  Examples: "mi piace il jazz", "lavoro per Acme Srl", "mio fratello si chiama Marco", "vorrei imparare il giapponese".
- For **rolling profile changes** (lingua, tono, contesto operativo core), use `user.update(detail=false)`.
- `soul.update` is for **agent personality / collaboration feedback** (come l'agent si comporta), NOT for user facts.
  Wrong: `soul.update(type=fact, content="all'utente piace il jazz")` ← deprecated pattern.
  Right: `user.update(section="Gusti e preferenze", content="Musica: jazz", detail=true)`.
- Read user profile via `user.read()` (HOT) or `user.read(detail=true)` (DETAIL) when relevant.

Wiki trust (schema 1.1 — important):
- Quando l'utente CONFERMA che una pagina wiki è corretta/aggiornata ("confermo",
  "sì è giusta", "ok è corretta", "review fatta") → chiama SEMPRE `wiki.verify(slug)`.
  Registra `verified` nel frontmatter e promuove il trust tier a human-reviewed.
  ❌ NON limitarti a rispondere a parole: senza `wiki.verify` la conferma va persa.
- Il blocco `generated` (by/at) nel frontmatter lo scrive AUTOMATICAMENTE il tool
  di upsert: non commentarlo, non attribuirlo a linter o processi esterni.
- Se scrivi una pagina che invecchia (dati datati, stati temporanei), valuta
  `stale_after: YYYY-MM-DD` e `status: draft` negli args dell'upsert.

Reply in Italian unless explicitly asked otherwise.
"""


PROJECT_SYSTEM_PROMPT = """You are the AI assistant operating remotely inside the anja project `{project_name}` at `{project_path}`.

You are model-agnostic: do NOT identify as any specific model unless the user explicitly asks.
Just call yourself "the project assistant".

You have **FULL TOOL ACCESS** to this project (the user is using a webapp to control you remotely):
- **Read/Write/Edit/MultiEdit**: read and modify files of the project
- **Bash**: run shell commands (cwd = project root)
- **Grep/Glob/LS**: search and explore filesystem
- **WebFetch/WebSearch**: external info if needed
- **TodoWrite**: track multi-step tasks
- The project's **MCP servers** (from `.mcp.json`) are auto-available
- The project's **Claude Code skills** (from `.claude/skills/`) are auto-available

Project layout:
- `CLAUDE.md` at project root — read it for conventions and current state
- `.anjawiki/wiki/index.md` — wiki catalog (read first to navigate)
- `.anjawiki/wiki/entities/`, `concepts/`, `sources/`, `analysis/`, `sessions/`
- `.anjawiki/wiki/log.md` — operations history
- The actual source code/files of the project

Recent activity in this project (last operations):
{recent_activity}

Behavior:
- For destructive/major operations (Write/Edit on critical files, dangerous Bash), confirm with user first
- Read existing files before proposing changes
- Cite wiki pages using `[[<page-slug>]]` (no project prefix needed — you ARE in this project)
- Be conservative with modifications: prefer suggesting over executing without confirmation
- When using Bash, prefer non-destructive commands (no `rm -rf`, no force-push, etc.) unless explicitly asked

# Workspace office operations (PRIORITY over Bash for managed scripts)

Se questo è un workspace anja con goal attivi (file `goals/*.md`) e script gestiti
(`<hub>/scripts/workspaces/<this>/<goal_id>/*.py`), per gestire script/agent/routine
USA SEMPRE i tool MCP `anja_hub_ops`, NON Bash/kill/pkill:

| Cosa serve fare | Tool MCP da usare | NON usare |
|---|---|---|
| Vedere stato script (pid, log, restart) | `hub.script_status` | `ps aux \\| grep`, `Bash` |
| Stop / start / restart script | `hub.script_lifecycle(action, script)` | `kill -STOP`, `pkill`, `SIGSTOP/SIGCONT` |
| Diagnose generale workspace | `hub.diagnose` | grep filesystem |
| Aggiornare SOUL/AGENTS di un agent | `agent.update(file, section, content)` | Edit/Write su `.md` |
| Stop/start routine | `routine.lifecycle(action, routine)` | toggle yaml manuale |
| Cambiare LLM di un role nel goal | `goal.assign_agent(role, llm)` | Edit goal.md |
| Lista signal/execution/note recenti | `hub.signals_recent`, `hub.executions_recent`, `hub.notes_recent` | grep file |

REGOLA CRITICA: se l'utente chiede "stoppa il monitor X", "metti in pausa lo script Y",
"riavvia trail_sl", "killa lo script Z" → SEMPRE `hub.script_lifecycle`, MAI Bash.
Bash è OK solo per shell tasks generici NON gestiti dal supervisor del goal.

Se i tool `hub.*` non sono disponibili in questa chat, dichiaralo chiaramente
("non vedo i tool anja_hub_ops attivi") invece di fallback su Bash silently
o di allucinare un'azione fatta. Mai dire "ho stoppato lo script" senza aver chiamato il tool.

Reply in Italian unless explicitly asked otherwise.
"""


def _project_list_md(projects: list) -> str:
    """Format a list of projects for the system prompt."""
    if not projects:
        return "  (no projects registered yet)"
    lines = []
    for p in projects:
        name = p.get("name", "?")
        ptype = p.get("type", "?")
        desc = p.get("description", "")
        line = f"  - **{name}** (type: {ptype})"
        if desc:
            line += f" — {desc}"
        lines.append(line)
    return "\n".join(lines)


IMAGE_GEN_INSTRUCTIONS = """

# Image generation enabled

L'utente ha attivato il toggle "image" — vuole che tu generi con la CLI `giv`
via Bash (vedi la sezione "Media generation" per comandi e output dir) quando
chiede esplicitamente di generare/creare/disegnare qualcosa di visuale.

Linee guida:
- Quando la CLI è disponibile, **usala** invece di dire "non posso".
- Per richieste con accuratezza scientifica/tecnica (es. proteine 3D, cromatogrammi DNA reali,
  diagrammi medici precisi): genera comunque un'**illustrazione artistica/educational** e
  PREMETTI esplicitamente "Questa è una rappresentazione illustrativa, non scientificamente
  accurata. Per dati reali consulta [PyMOL/AlphaFold/IGV/...]."
- Adatta il prompt all'image generator: descrizione visuale concreta (colori, stile, composizione),
  non termini scientifici puri.
- Per più immagini diverse: `-n <num>` o chiamate separate con prompt distinti.
"""


def _bootstrap_block(hub_path: Path) -> str:
    """Fase 12b M-Onb 5 — rituale di primo avvio (BOOTSTRAP.md).

    Iniettato in cima al system prompt SOLO se: il file `<hub>/BOOTSTRAP.md`
    esiste E l'utente non è ancora onboarded (nessun default_user / users/*.md).
    Self-destruct logico: appena l'utente viene salvato, il check fallisce e il
    blocco non viene più iniettato — niente tool dedicato, niente rinomina file.
    """
    bf = hub_path / "BOOTSTRAP.md"
    if not bf.is_file():
        return ""
    try:
        cfg = json.loads((hub_path / "config.json").read_text(encoding="utf-8"))
        if cfg.get("default_user"):
            return ""
    except Exception:
        pass
    users = hub_path / "users"
    if users.is_dir() and any(users.glob("*.md")):
        return ""
    return bf.read_text(encoding="utf-8").strip()


def build_system_prompt(hub_path: Path, projects: list, user_prompt: str = "",
                       image_gen_enabled: bool = False, user_name: str = "user",
                       timezone: str = "") -> str:
    """System prompt per chat scope=hub. Include unified ProjectContext (Fase 7u)
    via context_composer (identity + CLAUDE.md slim + active memory HOT/WARM).
    """
    base = HUB_SYSTEM_PROMPT.format(
        hub_name=hub_path.name,
        hub_path=str(hub_path),
        project_list=_project_list_md(projects),
    )
    if image_gen_enabled:
        base += IMAGE_GEN_INSTRUCTIONS
    bootstrap = _bootstrap_block(hub_path)
    if bootstrap:
        base = bootstrap + "\n\n===\n\n" + base
    composed = _compose_or_legacy(hub_path, "hub", None, hub_path.name, user_prompt, user_name, timezone, hub_path=hub_path)
    if composed:
        return composed + "\n\n===\n\n" + base
    return base


def read_project_recent_activity(project_path: Path, max_entries: int = 10) -> str:
    """Read recent log entries del progetto (da `.anjawiki/wiki/log.md`)."""
    log_path = project_path / ".anjawiki" / "wiki" / "log.md"
    if not log_path.is_file():
        return "(nessun log anja trovato in questo progetto)"
    text = log_path.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})\] (\w[\w-]*) \| (.+?)$", re.M)
    entries = pattern.findall(text)
    last = entries[-max_entries:][::-1]  # latest first
    if not last:
        return "(nessuna entry log)"
    return "\n".join(f"  - {d} {t}: {desc}" for d, t, desc in last)


def build_project_system_prompt(project_name: str, project_path: Path, user_prompt: str = "",
                                image_gen_enabled: bool = False, hub_name: str = "",
                                user_name: str = "user", timezone: str = "",
                                hub_path: Optional[Path] = None) -> str:
    """System prompt per chat scope=project:<name>, unified ProjectContext (Fase 7u + 12)."""
    activity = read_project_recent_activity(project_path)
    base = PROJECT_SYSTEM_PROMPT.format(
        project_name=project_name,
        project_path=str(project_path),
        recent_activity=activity,
    )
    if image_gen_enabled:
        base += IMAGE_GEN_INSTRUCTIONS
    composed = _compose_or_legacy(project_path, "project", project_name,
                                   hub_name or project_name, user_prompt, user_name, timezone,
                                   hub_path=hub_path, project_path=project_path)
    if composed:
        return composed + "\n\n===\n\n" + base
    return base


def _compose_or_legacy(scope_root: Path, scope_kind: str, target_name: Optional[str],
                       hub_name: str, user_prompt: str, user_name: str, timezone: str,
                       hub_path: Optional[Path] = None,
                       project_path: Optional[Path] = None) -> str:
    """Fase 7u M-Cx 5 + Fase 12 M-Id 3+4 + Fase 14 dialectic: usa context_composer
    con identity, project overlay (Fase 14) e dialectic memory injection.
    """
    if _compose_context is not None:
        try:
            composed, meta = _compose_context(
                scope_root=scope_root,
                scope_kind=scope_kind,
                target_name=target_name,
                hub_name=hub_name,
                user_prompt=user_prompt,
                user_name=user_name,
                timezone=timezone,
                include_claude_md=True,
                hub_path=hub_path,
                project_path=project_path,
            )
            return composed
        except Exception as e:
            print(f"[anja-chat] WARNING: context_composer failed, falling back: {e}")
    return _load_active_memory_legacy(scope_root, user_prompt)


def _anjadev_dir() -> Path:
    """Root del plugin anjadev installato (override via ANJADEV_DIR per dev locale)."""
    import os
    env = os.environ.get("ANJADEV_DIR")
    return Path(env) if env else Path.home() / ".claude" / "plugins" / "marketplaces" / "anjadev"


def _load_active_memory_legacy(scope_root: Path, user_prompt: str = "") -> str:
    """Legacy fallback: solo HOT+WARM via context_loader. Best-effort: '' on any error."""
    try:
        ctx_loader_path = _anjadev_dir() / "scripts" / "context_loader.py"
        if not ctx_loader_path.is_file():
            print(f"[anja-chat] WARNING: context_loader.py non trovato: {ctx_loader_path}")
            return ""
        import importlib.util
        spec = importlib.util.spec_from_file_location("context_loader", str(ctx_loader_path))
        cl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cl)
        ctx = cl.build_session_context(scope_root, user_prompt=user_prompt)
        return cl.format_for_prompt(ctx)
    except Exception as e:
        print(f"[anja-chat] WARNING: active memory injection failed: {e}")
        return ""


def resolve_chat_cwd(hub_path: Path, scope: str, registry_projects: list) -> tuple:
    """
    Dato uno scope, ritorna (cwd, kind_target).
    - scope='hub' o vuoto → (hub_path, ('hub', None))
    - scope='project:<name>' → (project_real_path, ('project', name))
    - scope='agent:<name>' → (<hub>/agents/<name>/, ('agent', name))
    """
    if not scope or scope == "hub":
        return hub_path, ("hub", None)
    if scope.startswith("project:"):
        proj_name = scope.split(":", 1)[1]
        for p in registry_projects:
            if p.get("name") == proj_name:
                loc = p.get("location", {})
                if loc.get("kind") == "local":
                    return Path(loc.get("path", str(hub_path))), ("project", proj_name)
        return hub_path, ("hub", None)
    if scope.startswith("agent:"):
        agent_name = scope.split(":", 1)[1]
        # Project-scope agents (cross-workspace lookup) — Fase 13+
        resolved = resolve_agent_dir(hub_path, agent_name)
        if resolved is not None:
            return resolved, ("agent", agent_name)
        return hub_path, ("hub", None)
    return hub_path, ("hub", None)


def resolve_agent_dir(hub_path: Path, agent_name: str) -> Optional[Path]:
    """Trova la directory dell'agent risolvendo hub prima, poi project agents.

    Pattern di ricerca:
    1. `<hub>/agents/<name>/` (hub-scope, identità globale)
    2. `<hub>/workspaces/*/.anjawiki/agents/<name>/` (project-scope, primo match)

    Per agent espliciti project:<proj>/<name> usa resolve_project_agent_dir.
    Restituisce Path se trovato, None altrimenti.
    """
    if not agent_name:
        return None
    hub_dir = hub_path / "agents" / agent_name
    if (hub_dir / "config.json").is_file() or (hub_dir / "AGENTS.md").is_file():
        return hub_dir
    # Cross-project lookup
    workspaces_root = hub_path / "workspaces"
    if workspaces_root.is_dir():
        for ws in workspaces_root.iterdir():
            if not ws.is_dir():
                continue
            cand = ws / ".anjawiki" / "agents" / agent_name
            if (cand / "config.json").is_file() or (cand / "AGENTS.md").is_file():
                return cand
    return None


def load_agent_config(hub_path: Path, agent_name: str) -> dict:
    """Read agent config.json. Cerca prima hub agents, poi project agents (Fase 13+)."""
    agent_dir = resolve_agent_dir(hub_path, agent_name)
    if agent_dir is None:
        return {}
    cfg_path = agent_dir / "config.json"
    if not cfg_path.is_file():
        return {}
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        # Inietta scope hint: rende esplicito al resto del codice se l'agent è project-scoped
        try:
            rel = agent_dir.relative_to(hub_path)
            if str(rel).startswith("workspaces/"):
                # workspaces/<name>/.anjawiki/agents/<agent> → project name
                parts = str(rel).split("/")
                if len(parts) >= 2:
                    cfg["_project_scope"] = parts[1]
        except Exception:
            pass
        cfg["_agent_dir"] = str(agent_dir)
        return cfg
    except Exception:
        return {}


AGENT_SYSTEM_PROMPT = """You are the specialized agent `{agent_name}` inside the anja hub `{hub_name}`.

You are model-agnostic: do NOT identify as any specific underlying model unless the user
explicitly asks. Identify as the agent `{agent_name}`.

# Role

{role}

# Identity

Your full personality, preferences, expertise are in the AGENTS+SOUL+TOOLS files of this agent
(loaded automatically via CLAUDE.md composed). Stay in character — you are NOT the generic hub default.

# Scope

- Cwd: this agent's directory (`<hub>/agents/{agent_name}/`)
- You can access: hub-level files (cross/, sessions/, agents/, projects/ symlinks)
- You should focus on your domain. Out-of-domain tasks: suggest delegation to hub default or another agent.

# Memory

Conversations with you persist in `<hub>/agents/{agent_name}/sessions/<date>/<id>.md`.
Recent context is auto-injected at session start.

# Workspace operations (script/log paths)

Quando diagnostichi un goal o lo stato di un workspace, NON cercare gli script
del dev/executor dentro `<workspace>/.anjawiki/scripts/`. Quella directory è
solo placeholder. Gli script effettivi (monitor/trail/killswitch generati dal
dev step della pipeline) vivono in:

  `<hub>/scripts/workspaces/<workspace_name>/<goal_id>/*.py` + `*.log`

Per ispezionarli usa SEMPRE i tool MCP, NON filesystem read diretto:
- `hub.script_status` → pid/running/last_restart/log_tail per ogni script del goal
- `hub.signals_recent` → ultimi signal emessi (entered_zone, trail_sl, killswitch…)
- `hub.script_lifecycle` → start/stop/restart/disable (T2, solo se sei responsabile workspace)
- `hub.diagnose` → check automatico salute team (hanging specialist, error pattern, drift streak)

Se l'utente chiede "gli script funzionano?" → `hub.script_status` PRIMA di concludere.
Se dici "lo script non esiste" senza aver chiamato `hub.script_status`, stai allucinando.
"""


def build_agent_system_prompt(hub_path: Path, agent_name: str, agent_dir: Path,
                              agent_config: dict, user_prompt: str = "",
                              image_gen_enabled: bool = False, user_name: str = "user",
                              timezone: str = "") -> str:
    """System prompt per chat scope=agent:<name>, unified ProjectContext (Fase 7u)."""
    role = agent_config.get("role", "(no role defined)")
    base = AGENT_SYSTEM_PROMPT.format(
        agent_name=agent_name,
        hub_name=hub_path.name,
        role=role,
    )
    if image_gen_enabled:
        base += IMAGE_GEN_INSTRUCTIONS
    composed = _compose_or_legacy(agent_dir, "agent", agent_name, hub_path.name,
                                  user_prompt, user_name, timezone, hub_path=hub_path)
    if composed:
        return composed + "\n\n===\n\n" + base
    return base


def _extract_text(message: Any) -> str:
    """Estrae text da un message dell'SDK (string content o block list)."""
    content = getattr(message, "content", None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def resolve_skill_invocation(user_msg: str, hub_path: Optional[Path]) -> tuple[str, str]:
    """Parse `/skill <slug> [args...]`. Slash invocation for skill catalog.

    Ritorna (transformed_user_msg, extra_system_block):
      - `/skill foo bar baz`     → ("bar baz", "<body of skill foo as system block>")
      - `/skill foo` (no args)   → ("Apply skill `foo` to current context.", <body>)
      - skill non trovata        → (user_msg invariato, warning block)
      - non è una /skill         → (user_msg invariato, "")
    """
    msg = (user_msg or "").strip()
    if not msg.startswith("/skill "):
        return user_msg, ""

    parts = msg.split(maxsplit=2)
    if len(parts) < 2 or not parts[1].strip():
        return user_msg, (
            "\n\n## Skill invocation\n"
            "User typed `/skill` with no slug. Ask them which skill to load "
            "or list available skills from the Level 0 catalog above.\n"
        )
    slug = parts[1].strip()
    args = parts[2].strip() if len(parts) > 2 else ""

    try:
        import skills_catalog
    except ImportError:
        return user_msg, "\n\n## Skill catalog unavailable\nskills_catalog module missing — invocation ignored.\n"

    data = skills_catalog.load_skill(slug, hub_path)
    if not data or not data.get("content"):
        return user_msg, (
            f"\n\n## Skill invocation failed\n"
            f"User invoked `/skill {slug}` but slug not found in catalog. "
            f"Either suggest a close match or treat the literal text as the user's real request.\n"
        )

    extra = (
        f"\n\n## Active skill: `{slug}`\n"
        "<!-- Auto-injected by /skill invocation. Body below is the working procedure. -->\n\n"
        f"{data['content']}\n"
    )
    transformed = args if args else f"Apply skill `{slug}` to the current conversation context."
    return transformed, extra


def resolve_bundle_invocation(user_msg: str, hub_path: Optional[Path]) -> tuple[str, str]:
    """Parse `/bundle <slug> [args...]`. Concatena instruction + body di N skill.

    Stessa semantica di resolve_skill_invocation. Bundle YAML in <hub>/skill-bundles/<slug>.yaml.
    """
    msg = (user_msg or "").strip()
    if not msg.startswith("/bundle "):
        return user_msg, ""

    parts = msg.split(maxsplit=2)
    if len(parts) < 2 or not parts[1].strip():
        return user_msg, (
            "\n\n## Bundle invocation\n"
            "User typed `/bundle` with no slug. Ask which bundle to load.\n"
        )
    slug = parts[1].strip()
    args = parts[2].strip() if len(parts) > 2 else ""

    try:
        import skills_catalog
    except ImportError:
        return user_msg, "\n\n## Skill catalog unavailable\nskills_catalog module missing — invocation ignored.\n"

    data = skills_catalog.load_bundle(slug, hub_path)
    if not data:
        return user_msg, (
            f"\n\n## Bundle invocation failed\n"
            f"User invoked `/bundle {slug}` but bundle not found.\n"
        )

    sections = [
        f"\n\n## Active bundle: `{slug}`",
        "<!-- Auto-injected by /bundle invocation. -->\n",
    ]
    if data.get("instruction"):
        sections.append(f"\n### Bundle instruction\n{data['instruction']}\n")
    for s in data.get("skills", []):
        sections.append(f"\n### Skill: `{s['name']}`\n\n{s['content']}\n")
    if data.get("missing"):
        miss = ", ".join(f"`{m}`" for m in data["missing"])
        sections.append(
            f"\n### Missing skills\nThe bundle references skills not found: {miss}. "
            f"Apply the rest, and flag the gaps to the user.\n"
        )
    extra = "\n".join(sections)
    transformed = args if args else f"Apply bundle `{slug}` to the current context."
    return transformed, extra


async def stream_response(
    user_prompt: str,
    system_prompt: str,
    cwd: Path,
    model: str = "sonnet",
    allowed_tools: list = None,
    effort: str = None,
    providers_chain: list = None,
    no_fallback: bool = False,
    provider: str = "claude",
    resume_session_id: str = None,
    scoped_servers: Optional[list] = None,
    image_attachments: Optional[list] = None,
) -> AsyncIterator[dict]:
    """
    Async generator che yield-a dict eventi.
    `cwd` determina context Claude Code (.mcp.json, CLAUDE.md, .claude/skills/).
    `allowed_tools` controlla quali tool Claude può usare.
    `effort` opzionale: "low" | "medium" | "high" — extended thinking budget.

    M-PA 6 — Failover chain:
    `providers_chain`: lista [{provider, model, effort?}] tentati in ordine se fallisce primary
    `no_fallback`: se True, errore al primo failure (es. agent qualità-critica che non vuole degrade)
    """
    # Costruisci attempt list: prima primary, poi fallbacks dalla chain (skip duplicati)
    attempts = [{"provider": provider or "claude", "model": model, "effort": effort}]
    if providers_chain and not no_fallback:
        seen = {(a["provider"], a["model"]) for a in attempts}
        for p in providers_chain:
            key = (p.get("provider", "claude"), p.get("model", "sonnet"))
            if key not in seen:
                attempts.append({
                    "provider": p.get("provider", "claude"),
                    "model": p.get("model", "sonnet"),
                    "effort": p.get("effort", "off") if p.get("effort") != "off" else None,
                })
                seen.add(key)

    # Lazy import LLMRouter
    try:
        from llm_router import is_claude_provider, stream_via_litellm, stream_via_opencode
    except ImportError:
        # fallback: claude only
        def is_claude_provider(p): return True
        async def stream_via_litellm(*args, **kw):
            yield {"type": "error", "message": "llm_router module not available"}
        async def stream_via_opencode(*args, **kw):
            yield {"type": "error", "message": "llm_router module not available"}

    last_error = None
    for attempt_idx, attempt in enumerate(attempts):
        att_provider = attempt["provider"]
        attempt_model = attempt["model"]
        attempt_effort = attempt["effort"]

        if attempt_idx > 0:
            # Failover triggered
            yield {
                "type": "tool_use",
                "name": "_failover",
                "input": {
                    "from_attempt": attempt_idx - 1,
                    "to_provider": att_provider,
                    "to_model": attempt_model,
                    "previous_error": last_error,
                },
            }

        # === ROUTE: claude (SDK in-process) vs OpenAI OAuth vs LiteLLM ===
        # Fase 7v: provider=openai_oauth → ChatGPT subscription via Codex backend (no LiteLLM)
        if att_provider == "openai_oauth":
            try:
                from openai_oauth_client import stream_via_openai_oauth
            except ImportError:
                yield {"type": "error", "message": "openai_oauth_client module not available"}
                return
            try:
                got_any_text = False
                async for ev in stream_via_openai_oauth(
                    user_prompt=user_prompt,
                    system_prompt=system_prompt,
                    cwd=cwd,
                    model=attempt_model or "gpt-5.5",
                    timeout_sec=300,
                    allowed_tools=allowed_tools,
                    effort=attempt_effort if attempt_effort in ("low", "medium", "high") else None,
                    image_attachments=image_attachments,
                ):
                    if ev.get("type") == "error":
                        last_error = ev.get("message", "openai_oauth error")
                        if attempt_idx == len(attempts) - 1:
                            yield ev
                            return
                        break
                    if ev.get("type") == "text":
                        got_any_text = True
                    yield ev
                if got_any_text:
                    return
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                if attempt_idx == len(attempts) - 1:
                    yield {"type": "error", "message": last_error}
                    return
            continue

        if not is_claude_provider(att_provider):
            # Multi-provider via LiteLLM (Fase 8a). MCP tool calling integrato.
            try:
                got_any_text = False
                async for ev in stream_via_litellm(
                    user_prompt=user_prompt,
                    system_prompt=system_prompt,
                    cwd=cwd,
                    provider=att_provider,
                    model=attempt_model,
                    timeout_sec=300,
                    allowed_tools=allowed_tools,
                    image_attachments=image_attachments,
                ):
                    if ev.get("type") == "error":
                        last_error = ev.get("message", "litellm error")
                        if attempt_idx == len(attempts) - 1:
                            yield ev
                            return
                        break
                    if ev.get("type") == "text":
                        got_any_text = True
                    yield ev
                if got_any_text:
                    return
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                if attempt_idx == len(attempts) - 1:
                    yield {"type": "error", "message": last_error}
                    return
            continue

        # === ROUTE: claude (in-process SDK) ===
        try:
            # Gli alias della CLI NON seguono la famiglia corrente: verificato su
            # CLI 2.1.219 che 'opus' risolve a claude-opus-4-7 e 'sonnet' a
            # claude-sonnet-4-6, mentre 'fable' non è accettato dal bridge
            # stream-json. Passiamo quindi l'ID pieno del modello scelto.
            # Da aggiornare a mano quando esce una nuova famiglia.
            _SDK_MODEL_IDS = {
                "opus":   "claude-opus-5",
                "sonnet": "claude-sonnet-5",
                "fable":  "claude-fable-5",
                "haiku":  "claude-haiku-4-5",
            }
            _sdk_model = _SDK_MODEL_IDS.get(attempt_model, attempt_model)
            kwargs = {
                "system_prompt": system_prompt,
                "model": _sdk_model,
                "cwd": str(cwd),
                # User authorized MCP servers explicitly via anja UI: skip Claude Code's
                # permission prompt system. Tool whitelisting already enforced via allowed_tools.
                "permission_mode": "bypassPermissions",
            }
            if allowed_tools:
                kwargs["allowed_tools"] = allowed_tools

            # NB token-economy (2026-06-17): NON passare `kwargs["tools"]` per filtrare i nativi.
            # Misurato (token_audit_floor.py) che il param `tools` esplicito — anche lista vuota —
            # forza l'SDK a iniettare EAGER ~97k di catalogo tool (floor 13k → 110k). Il filtering
            # lo fa già `allowed_tools` a costo zero; senza `tools` i nativi restano lazy come nel CLI.
            # Disabilita skills auto-load + setting_sources con LISTE VUOTE
            # (None = default = "carica tutto", [] = "non caricare nulla")
            kwargs["skills"] = []           # No auto-load skill catalog (lo iniettiamo noi via skills_catalog)
            kwargs["setting_sources"] = []  # No CLAUDE.md/agents/hooks/plugin auto-prepend

            # Fase 16 — Filter MCP servers via `mcp_servers` param (dict raw, no classi).
            # setting_sources=[] disabilita .mcp.json auto-load → passo qui esplicitamente solo scoped.
            if scoped_servers is not None:
                try:
                    import json as _json
                    mcp_path = Path(cwd) / ".mcp.json"
                    if mcp_path.is_file():
                        raw = _json.loads(mcp_path.read_text(encoding="utf-8"))
                        all_servers = raw.get("mcpServers") or {}
                        scoped_dict = {}
                        for name in scoped_servers:
                            if name not in all_servers:
                                continue
                            cfg = all_servers[name]
                            cfg_type = (cfg.get("type") or "stdio").lower()
                            # SDK accetta dict raw {type, command, args, env} o {type:'sse', url, headers}
                            entry = {"type": cfg_type}
                            if cfg_type in ("sse", "http"):
                                entry["url"] = cfg.get("url", "")
                                if cfg.get("headers"):
                                    entry["headers"] = cfg["headers"]
                            else:
                                entry["type"] = "stdio"
                                entry["command"] = cfg.get("command", "")
                                entry["args"] = cfg.get("args") or []
                                if cfg.get("env"):
                                    entry["env"] = cfg["env"]
                            scoped_dict[name] = entry
                        kwargs["mcp_servers"] = scoped_dict
                        # senza strict il CLI aggiunge i connettori account
                        # claude.ai che scavalcano il catalogo hub
                        kwargs["strict_mcp_config"] = True
                        print(f"[anja-chat] mcp_servers param: {list(scoped_dict.keys())} (filtered from {len(all_servers)})")
                except Exception as e:
                    print(f"[anja-chat] WARN mcp_servers filter: {e}")
            if attempt_effort and attempt_effort in ("low", "medium", "high"):
                kwargs["effort"] = attempt_effort
            # Fase 7k — resume conversation se passed un session_id pregresso (continuity)
            if resume_session_id:
                kwargs["resume"] = resume_session_id
            options = ClaudeAgentOptions(**kwargs)

            # Determina context window per il modello (Fase 7t).
            # Claude SDK usa alias ('sonnet', 'opus', 'haiku') che LiteLLM non riconosce
            # → mappiamo manualmente. Default 200k se sconosciuto.
            # Famiglia Claude 5 (gli alias della CLI puntano al modello corrente).
            CLAUDE_ALIAS_TO_FULL = {
                "haiku":  "anthropic/claude-haiku-4-5",
                "sonnet": "anthropic/claude-sonnet-5",
                "opus":   "anthropic/claude-opus-5",
                "fable":  "anthropic/claude-fable-5",
                "fast":   "anthropic/claude-haiku-4-5",
            }
            CLAUDE_DEFAULT_CTX = {
                "haiku":  200000,          # Haiku 4.5 è l'unico ancora a 200K
                "sonnet": 1000000,         # Sonnet 5: 1M (era 200K su 4.5)
                "opus":   1000000,
                "fable":  1000000,
                "fast":   200000,
            }
            try:
                import litellm
                full_id = CLAUDE_ALIAS_TO_FULL.get(attempt_model, f"anthropic/{attempt_model}")
                info = litellm.get_model_info(full_id) or {}
                ctx_window_claude = int(info.get("max_input_tokens") or info.get("max_tokens") or 0)
                if not ctx_window_claude:
                    ctx_window_claude = CLAUDE_DEFAULT_CTX.get(attempt_model, 200000)
            except Exception:
                ctx_window_claude = CLAUDE_DEFAULT_CTX.get(attempt_model, 200000)

            # Fase 24.b — Claude SDK vision via async iter prompt + content blocks.
            # Anthropic Messages API: content = list of blocks, each
            #   {"type": "text", "text": "..."} oppure
            #   {"type": "image", "source": {"type":"base64","media_type":"image/png","data":"..."}}
            _effective_prompt = user_prompt
            if image_attachments:
                _names = ", ".join(img.get("filename", "?") for img in image_attachments)
                print(f"[claude_chat] {len(image_attachments)} image(s) -> Claude SDK content blocks: {_names}", flush=True)

                async def _prompt_iter(_text=user_prompt, _imgs=image_attachments):
                    blocks: list[dict] = []
                    if _text:
                        blocks.append({"type": "text", "text": _text})
                    for img in _imgs:
                        b64 = img.get("image_b64")
                        if not b64:
                            continue
                        mime = img.get("mime") or "image/png"
                        blocks.append({
                            "type": "image",
                            "source": {"type": "base64", "media_type": mime, "data": b64},
                        })
                    yield {"type": "user", "message": {"role": "user", "content": blocks}}

                prompt_arg: Any = _prompt_iter()
            else:
                prompt_arg = _effective_prompt

            peak_ctx_in = 0  # token-economy: picco contesto per-chiamata (≠ somma cumulativa)
            async for message in query(prompt=prompt_arg, options=options):
                # Capture session_id
                sid = getattr(message, "session_id", None)
                if sid:
                    yield {"type": "session_id", "session_id": sid}
                data = getattr(message, "data", None)
                if isinstance(data, dict) and data.get("session_id"):
                    yield {"type": "session_id", "session_id": data["session_id"]}

                # Fase 7t — usage extraction da ResultMessage
                mtype = type(message).__name__
                # token-economy: l'usage di ResultMessage è CUMULATIVO sui round-tool (somma
                # i cache_read di ogni chiamata) → NON è il riempimento della finestra. Il picco
                # reale è il max input per-singola-chiamata, esposto da AssistantMessage.usage.
                if mtype == "AssistantMessage":
                    _mu = getattr(message, "usage", None)
                    if isinstance(_mu, dict):
                        _ci = (int(_mu.get("input_tokens", 0) or 0)
                               + int(_mu.get("cache_creation_input_tokens", 0) or 0)
                               + int(_mu.get("cache_read_input_tokens", 0) or 0))
                        if _ci > peak_ctx_in:
                            peak_ctx_in = _ci
                if mtype == "ResultMessage":
                    usage_dict = getattr(message, "usage", None)
                    if isinstance(usage_dict, dict):
                        in_t = int(usage_dict.get("input_tokens", 0) or 0)
                        out_t = int(usage_dict.get("output_tokens", 0) or 0)
                        cache_in = int(usage_dict.get("cache_creation_input_tokens", 0) or 0)
                        cache_read = int(usage_dict.get("cache_read_input_tokens", 0) or 0)
                        total_in = in_t + cache_in + cache_read       # cumulativo (per costo/billing)
                        context_in = peak_ctx_in or total_in          # picco finestra (per il gauge)
                        yield {
                            "type": "usage",
                            "input_tokens": total_in,
                            "context_input_tokens": context_in,
                            "output_tokens": out_t,
                            "total_tokens": total_in + out_t,
                            "context_window": ctx_window_claude,
                            "cache_read_tokens": cache_read,
                            "model": attempt_model,
                        }

                text = _extract_text(message)
                if text:
                    yield {"type": "text", "content": text}

                content = getattr(message, "content", None)
                if isinstance(content, list):
                    for block in content:
                        block_type = type(block).__name__
                        if "Tool" in block_type and "Use" in block_type:
                            tool_name = getattr(block, "name", "?")
                            tool_input = getattr(block, "input", {})
                            yield {
                                "type": "tool_use",
                                "name": tool_name,
                                "input": tool_input if isinstance(tool_input, dict) else str(tool_input),
                            }

            yield {"type": "done"}
            return

        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            if attempt_idx == len(attempts) - 1:
                yield {"type": "error", "message": last_error}
                return

    yield {"type": "error", "message": last_error or "no provider available"}


# ============================================================
# Conversation persistence (semplice, JSON files)
# ============================================================

def save_conversation(webapp_dir: Path, conv_id: str, messages: list, title: str = "", scope: str = "hub",
                      provider: str = "", model: str = "", effort: str = "") -> None:
    """Salva una conversazione su disco con metadata LLM (per restore al riapri)."""
    conv_dir = webapp_dir / "conversations"
    conv_dir.mkdir(parents=True, exist_ok=True)
    path = conv_dir / f"{conv_id}.json"
    payload = {"id": conv_id, "title": title, "scope": scope, "messages": messages}
    # Preserva metadata LLM esistenti se non passati di nuovo
    if path.is_file():
        try:
            with path.open(encoding="utf-8") as f:
                existing = json.load(f)
            for k in ("provider", "model", "effort"):
                if k in existing and existing[k]:
                    payload[k] = existing[k]
        except Exception:
            pass
    if provider: payload["provider"] = provider
    if model:    payload["model"] = model
    if effort:   payload["effort"] = effort
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_conversation(webapp_dir: Path, conv_id: str) -> dict | None:
    path = webapp_dir / "conversations" / f"{conv_id}.json"
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def list_conversations(webapp_dir: Path) -> list:
    conv_dir = webapp_dir / "conversations"
    if not conv_dir.is_dir():
        return []
    out = []
    for f in sorted(conv_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with f.open(encoding="utf-8") as fh:
                data = json.load(fh)
            out.append({
                "id": data.get("id", f.stem),
                "title": data.get("title", "(untitled)"),
                "scope": data.get("scope", "hub"),
                "msg_count": len(data.get("messages", [])),
                "modified": f.stat().st_mtime,
            })
        except Exception:
            continue
    return out
