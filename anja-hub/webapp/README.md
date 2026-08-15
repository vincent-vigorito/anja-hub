# anja Mission Control — webapp

Frontend desktop + backend del hub `anja-hub`. FastAPI + HTML/Alpine + CSS puro, zero-dep frontend.

## Status

✅ **In produzione (dogfooding).** SPA Mission Control completa con backend FastAPI (~200 route REST + WebSocket).

Capability principali:
- **Chat multi-LLM** (WebSocket streaming) su scope hub / project / agent, provider-aware (Claude, OpenAI, xAI, Ollama) via `llm_router`
- **Workspace + agent** switcher, scaffold e CRUD
- **Goals** gerarchici (scheduler + judge + executor L3) e **Kanban** (dispatcher steward)
- **Routines** (daemon cron-like) e **Proactive engine** (heartbeat, commitments, scoring adattivo)
- **Notifications bus**, **Telegram** inbound/outbound, **Realtime voice** (OpenAI WebRTC + xAI WS)
- **Memory Inspector** (triade + tier estimates via context_loader del plugin anjadev)
- **Resources** + **Sources** (ingest URL/crawl/file in background) + **Hub Knowledge**
- **Onboarding wizard**, **Dialectic** memory passes, **git-shadow checkpoints**

## Setup (una volta sola)

```bash
pip3 install --break-system-packages -r requirements.txt
```

## Avvio

```bash
python3 server.py --hub <path-hub> --port 8765
```

Apre su `http://127.0.0.1:8765`. Per il dev locale degli script del plugin (context_loader,
tools_md, ecc.) si può puntare `ANJADEV_DIR=~/Documents/anjadev` invece dell'install marketplace.

## Struttura

```
webapp/
├── server.py              FastAPI app + ~200 route (monolite — split in router pianificato, vedi anja-techdebt.md)
├── llm_router.py          routing multi-provider (Claude/OpenAI/xAI/Ollama)
├── claude_chat.py         chat SDK + active memory injection
├── context_composer.py    composizione contesto (HOT/WARM + memory tiers)
├── goal_*.py              goals: scheduler / judge / executor / office / io
├── kanban_*.py            kanban: dispatcher / io
├── proactive_scoring.py   proactive engine (soglia adattiva)
├── commitment_*.py        commitments inbox + sensor
├── notification_bus.py    notifiche
├── telegram_*.py          bot Telegram (daemon + action notifier)
├── *_oauth*.py            OAuth Claude / OpenAI
├── dialectic_*.py         memory dialectic passes
├── workspace_scaffold.py  scaffold workspace/agent
└── static/                SPA Alpine (index.html + app.js + style.css)
```

## API

~200 route sotto `/api/*`, raggruppate per dominio: `goals`, `project`, `resources`, `settings`,
`agents`, `sources`, `routines`, `kanban`, `notifications`, `memory`, `dialectic`, `skills`,
`hub`, `workspaces`, `telegram`, `chat` (+ WS), `realtime`, `media`, `conversations`,
`checkpoints`, `pp` (Printing Press), `wiki`. Più `WS /api/chat` per lo streaming.

Il self-management hub (workspace/agent/routine/goal/kanban/skill/secret CRUD) è esposto via
queste REST `:8765/api/*` — consumabili da anja-cli/Bash, WebFetch, o dal thin MCP `hub_api`.

## Architettura

- Legge il filesystem del hub (`.anjawiki/`, `workspaces/`, `agents/`, `cross/`) e proxa gli
  script del plugin **anjadev** installato (`context_loader`, `tools_md`, `lint_checks`,
  `compose_claude_md`) — vedi `ANJADEV_DIR` in `server.py`.
- Chat via SDK/provider con active memory injection (triade + sessioni passate).
- Nessun database: stato su filesystem + in-memory.

## Sicurezza

- Solo localhost (`127.0.0.1`) di default, no auth (uso personale)
- **Webhook inbound** (`POST /hooks/wake|agent|signal`): unico canale con auth — bearer
  token `ANJA_WEBHOOK_TOKEN` in `.secrets.env` (assente → `503`, disabilitati by default)
- Path traversal protection sui parametri dinamici
- `injection_guard.py`: difesa prompt-injection su ingest + dialectic
- Secrets in `<hub>/.secrets.env` (gitignored, mai committare)

## Sviluppo

Modifica `static/*` → ricarica browser (no rebuild). Modifica `server.py`/moduli Python → restart.

## Vedi anche

- `anja-mission-control-design.md` — design webapp
- `anja-proactivity-design.md` — proactive engine
- `MAINTAINERS.md` — stato dev env completo
