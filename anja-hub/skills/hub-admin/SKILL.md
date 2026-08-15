---
name: hub-admin
description: Manuale operativo per amministrare l'hub AnjaHub via REST API. Catalog completo degli endpoint + esempi concreti per modalità CLI (anja-cli via Bash), WebFetch nativo, e MCP tool hub.api. Caricala quando devi creare/modificare/eliminare routine, agent, workspace, goal, skill, o gestire lifecycle (start/stop/restart/toggle). Cross-provider: funziona uniforme su Claude SDK, openai_oauth (Codex), LiteLLM.
version: 1.0.0
category: hub-management
tags: [admin, rest-api, routine, agent, workspace, goal, skill, cli]
platforms: [macos, linux]
requires_tools: [Bash, WebFetch]
requires_mcp: [hub_api]
---

# Skill: hub-admin

Operare sull'hub AnjaHub (CRUD + lifecycle di tutte le entità) via API REST locale.

## Architettura

L'hub espone tutto su FastAPI `http://127.0.0.1:8765/api/*`. Schema OpenAPI completo
in `http://127.0.0.1:8765/openapi.json` (fetchable on-demand).

## Sceglie il transport in base ai tool che hai

| Hai... | Usa... | Esempio |
|--------|--------|---------|
| `Bash` (Claude SDK) | `anja-cli` | `anja-cli routine update market-briefing-18 --prompt "..."` |
| `WebFetch` (Claude SDK) | URL diretto | `WebFetch(method="PATCH", url="http://127.0.0.1:8765/api/routines/market-briefing-18", body={"prompt": "..."})` |
| MCP tool `hub.api` | Codex, LiteLLM | `hub.api(method="PATCH", path="/api/routines/market-briefing-18", body={"prompt": "..."})` |

Tutti e 3 colpiscono lo stesso backend. Stesso effetto. Scegli quello che hai disponibile.

## Catalog endpoint per entità

### Routine

| Cosa fai | Method + Path | Body / Args |
|----------|---------------|-------------|
| Lista routine | `GET /api/routines` | — |
| Dettaglio routine | `GET /api/routines/{name}` | — |
| Crea routine | `POST /api/routines` | `{name, scope, schedule, prompt, provider, model, tools, output, ...}` |
| Modifica campo (prompt/schedule/output/tools/research/...) | `PATCH /api/routines/{name}` — merge-style, solo campi nel body vengono toccati | `{schedule: "30 19 * * *"}` (cambia solo orario). Passa `null` per rimuovere un campo. |
| Toggle enabled | `POST /api/routines/{name}/toggle` | `{enabled: true|false}` |
| Run on-demand | `POST /api/routines/{name}/run` | — |
| Status (last run, pid, log) | `GET /api/routines/{name}/status` | — |
| Lista runs storici | `GET /api/routines/{name}/runs/{filename}` | — |
| Elimina routine | `DELETE /api/routines/{name}` ⚠️ confirm | — |

**Esempi modifica routine** (PATCH = merge-style, solo i campi nel body cambiano):

```bash
# CLI
anja-cli routine update market-briefing-18 --schedule "30 19 * * *"
anja-cli routine update market-briefing-18 --prompt-file /tmp/new_prompt.txt
anja-cli routine toggle market-briefing-18 --off

# WebFetch (Claude SDK) — cambio solo orario
WebFetch(
  method="PATCH",
  url="http://127.0.0.1:8765/api/routines/market-briefing-18",
  body={"schedule": "30 19 * * *"}
)

# MCP hub.api (Codex/LiteLLM) — toggle off
hub.api(method="POST", path="/api/routines/market-briefing-18/toggle", body={"enabled": false})

# MCP hub.api — patch multiple fields atomic
hub.api(method="PATCH", path="/api/routines/market-briefing-18",
        body={"schedule": "30 19 * * *", "model": "gpt-5.5"})
```

### Agent

| Cosa | Method + Path | Body / Args |
|------|---------------|-------------|
| Lista agent | `GET /api/agents` | — |
| Dettaglio | `GET /api/agents/{name}` | — |
| Crea | `POST /api/agents` | `{name, role, domain?, provider?="claude", model?="sonnet", effort?="off", project?, force?=false}` — genera AGENTS.md/SOUL.md di default. Per personalizzare SOUL/AGENTS subito dopo, usa PATCH. |
| Modifica file (AGENTS.md/SOUL/config) | `PATCH /api/agents/{name}` | `{agents_md?: str, soul_md?: str, tools_md?: str, config_patch?: dict}` — solo i campi forniti vengono scritti. `config_patch` fa merge sul config.json (null per rimuovere chiave). |
| Clona | `POST /api/agents/clone` | `{source_name, target_name, source_project?, target_project?, include_config?=true}` |
| AI suggest config | `POST /api/agents/ai-suggest` | — |
| Lista sessions | `GET /api/agents/{name}/sessions` | — |
| Read session | `GET /api/agents/{name}/session/{session_id}` | — |
| Read raw file (AGENTS/SOUL/TOOLS) | `GET /api/agents/{name}/file?which=agents|soul|tools` | — |
| Elimina | `DELETE /api/agents/{name}` ⚠️ confirm | — |

**Esempio: crea agent + personalizza SOUL in 2 call**:
```python
# 1. Crea con default
api("POST", "/api/agents", body={"name": "fin-analyst", "role": "Analista finanziario pragmatico"})
# 2. Sovrascrivi SOUL.md con personalità custom
api("PATCH", "/api/agents/fin-analyst", body={
    "soul_md": "# Soul: fin-analyst\n\n## Personality\nAnalista pragmatico e diretto...\n",
    "config_patch": {"default_provider": "claude", "default_model": "sonnet"}
})
```

### Workspace

| Cosa | Method + Path |
|------|---------------|
| Lista | `GET /api/workspaces` |
| Dettaglio (path, agents/routines/goals interni, config) | `GET /api/workspaces/{name}` |
| Crea (base) | `POST /api/workspaces/create` |
| Crea da blueprint (pod completo) | `POST /api/workspaces/from-blueprint` body `{brand_name, blueprint?, backend?, ecommerce?}` |
| Blueprint disponibili | `GET /api/blueprints` |
| Valida un blueprint (prima di istanziare o dopo averne creato uno) | `POST /api/blueprints/{name}/validate` |
| Archivia | `POST /api/workspaces/{name}/archive` |
| Elimina | `POST /api/workspaces/{name}/delete` ⚠️ confirm |

Per PROGETTARE un blueprint nuovo (formato, pod, workflow): `skill.load("blueprint-authoring")`.

### Goal

| Cosa | Method + Path |
|------|---------------|
| Matrix | `GET /api/goals/matrix` |
| Lista | `GET /api/goals` |
| Detail | `GET /api/goals/{scope_kind}/{scope_target}/{goal_id}` |
| Linked tasks | `GET /api/goals/{scope_kind}/{scope_target}/{goal_id}/linked-tasks` |
| Create | `POST /api/goals/create` |
| Update | `POST /api/goals/{scope_kind}/{scope_target}/{goal_id}/update` |
| Run judge | `POST /api/goals/{scope_kind}/{scope_target}/{goal_id}/judge` |
| Run pipeline | `POST /api/goals/{scope_kind}/{scope_target}/{goal_id}/pipeline` |
| Pending actions | `GET /api/goals/{...}/pending-actions` |
| Resolve action | `POST /api/goals/{...}/pending-actions/{action_id}/resolve` |
| Start script | `POST /api/goals/{...}/scripts/start` |
| Stop script | `POST /api/goals/{...}/scripts/stop` |

### Sources (raw/ ingest → wiki)

Fonti raw scaricate/uploadate in `<scope-root>/.anjawiki/raw/<topic>/`, poi
**ingerite** (sintesi LLM) come source page del wiki in `<scope-root>/.anjawiki/wiki/sources/`.
UI: tab "Sources" (project/workspace) e "Hub Knowledge" (hub).

**Scope** (chiave `scope` nei body/query):
- `hub` → knowledge di dominio dell'**hub** (`<hub>/.anjawiki/`). **target vuoto** (`""`). È la conoscenza con cui ragioni e analizzi i progetti.
- `workspace` → `target` = nome workspace interno
- `project` → `target` = nome progetto esterno

| Cosa | Method + Path | Body / Query |
|------|---------------|--------------|
| Lista topic + file raw | `GET /api/sources/list?scope={hub\|project\|workspace}&target={name}` | target vuoto per hub |
| Add URL (fetch **server-side**) | `POST /api/sources/add` | `{scope, target, topic, mode:"url", url, filename?}` → ritorna `{filename}` |
| Upload inline | `POST /api/sources/add` | `{scope, target, topic, mode:"inline", filename, content_b64\|content_text}` |
| **Crawl doc multi-pagina** | `POST /api/sources/add-crawl` | `{scope, target, topic, url, max_pages?:25, ingest?:false}` → scarica seed + sotto-pagine interne (per doc senza sitemap: Sphinx/MkDocs/RTD). Background, `{status:"started"}` |
| Stato crawl (polling) | `GET /api/sources/crawl-status?scope&target` | `{status, seed, total, fetched, ingested, error}` |
| **Ingest reale** (LLM → source page) | `POST /api/sources/ingest-now` | `{scope, target, topic, filename}` → spawna sintesi in background, ritorna `{status:"started"}` |
| Stato ingest (polling) | `GET /api/sources/ingest-status?scope&target` | mappa `topic/filename → {status: ingesting\|done\|error, source, error}` |
| Pagine wiki generate | `GET /api/wiki/pages?scope&target` | lista `{kind, slug, path, title}` (sources/entities/concepts/analysis) |
| Leggi pagina wiki | `GET /api/wiki/page?scope&target&path=sources/x.md` | markdown |
| Serve/elimina file raw | `GET\|DELETE /api/sources/file?scope&target&topic&filename` | — |

**Workflow — creare conoscenza nel wiki da un link** (es. nel wiki dell'hub):
```python
# 1. scarica il link (il fetch avviene server-side — NON serve WebFetch/Bash)
r = api("POST", "/api/sources/add", body={
    "scope": "hub", "topic": "incus", "mode": "url",
    "url": "https://linuxcontainers.org/incus/docs/main/"})
fname = r["filename"]                       # es. "index.html"
# 2. ingerisci: l'LLM sintetizza una source page (TL;DR + punti chiave + [[entità]])
api("POST", "/api/sources/ingest-now", body={
    "scope": "hub", "topic": "incus", "filename": fname})
# 3. (opz) polling: GET /api/sources/ingest-status?scope=hub fino a status "done".
#    La source page finisce in wiki/sources/ e nell'index → entra nel tuo context.
```
**Workflow — ingerire una documentazione COMPLETA (multi-pagina)**, non una sola pagina:
```python
# crawl shallow: scarica la index + tutte le sotto-pagine interne, poi ingerisce ciascuna
api("POST", "/api/sources/add-crawl", body={
    "scope": "hub", "topic": "incus-docs",
    "url": "https://linuxcontainers.org/incus/docs/main/",
    "max_pages": 40, "ingest": True})
# polling: GET /api/sources/crawl-status?scope=hub → {fetched, ingested, status:"done"}
```
Usa `ingest: false` se vuoi prima scaricare e poi ingerire selettivamente. `max_pages`
cap a 100. Funziona per doc Sphinx/MkDocs/ReadTheDocs (senza sitemap): segue i link
interni sotto il path del seed (esclude `_static`, asset, `genindex`, link esterni).

Per **granularità maggiore** (entity/concept separate, non solo una source page)
puoi scrivere direttamente via i wiki tool di `anja_memory`
(`wiki.upsert_entity`, `wiki.upsert_concept`) leggendo il raw con `GET /api/sources/file`.

**Topic convention**: tematico, kebab-case (`llm-research`, `incus`, `linux`, `finanza`).

### Skill

| Cosa | Method + Path | Body / Args |
|------|---------------|-------------|
| Lista | `GET /api/skills` o `GET /api/resources/skills` | — |
| Detail | `GET /api/skills/{name}` | — |
| Read file (SKILL.md o script) | `GET /api/skills/{name}/file?path=...` | — |
| Crea | `POST /api/resources/skills` | `{scope, name, description?, content, ...}` |
| Modifica body SKILL.md | `PATCH /api/skills/{name}` | `{content: str, scope?: "hub"|"user-global"}` — sovrascrive SKILL.md con backup `.md.bak` |
| Setup config (env vars) | `GET/POST /api/skills/{name}/setup` | — |
| Import | `POST /api/resources/skills/import` | — |
| Elimina | `DELETE /api/resources/skill` ⚠️ confirm | — |

**Workflow — creare una skill da una documentazione**:
```python
# 1. ingerisci la doc (vedi Sources sopra): add URL + ingest-now → source page
# 2. leggi la conoscenza sintetizzata
pages = api("GET", "/api/wiki/pages?scope=hub")["pages"]
doc = api("GET", f"/api/wiki/page?scope=hub&path={pages[0]['path']}")  # markdown
# 3. componi il body SKILL.md (frontmatter name+description + workflow operativo
#    derivato dalla doc) e crealo
api("POST", "/api/resources/skills", body={
    "scope": "hub", "name": "incus-ops",
    "description": "Operazioni Incus: container/VM lifecycle, storage, networking. Usala quando l'utente chiede di gestire Incus.",
    "content": "<SKILL.md markdown completo>"})
```
Due livelli: **knowledge skill** (riassume la doc per consultazione) sempre fattibile;
**operational skill** (workflow eseguibile) richiede di tradurre la doc in passi concreti —
falla rivedere all'utente prima di darla per buona.

### Action triggers

| Cosa | Method + Path |
|------|---------------|
| Lint hub | `POST /api/action/lint-hub` |

## Discovery dinamica

Se non trovi nel catalog sopra cosa cerchi:

```bash
# Schema completo OpenAPI (FastAPI auto-gen)
curl -s http://127.0.0.1:8765/openapi.json | jq '.paths | keys'

# Oppure via WebFetch / hub.api
WebFetch(url="http://127.0.0.1:8765/openapi.json")
```

Da lì leggi `paths.<endpoint>.{get,post,patch,delete}.summary` per descrizione e
`requestBody.content."application/json".schema` per il body schema.

## Regole operative

### Routine: modifica via PATCH (merge-style)

`PATCH /api/routines/{name}` accetta SOLO i campi che vuoi cambiare. Quelli
non menzionati restano invariati. Per RIMUOVERE un campo, passalo con `null`.

```python
# Cambia solo orario (resto invariato)
api("PATCH", "/api/routines/market-briefing-18", body={"schedule": "30 19 * * *"})

# Cambia prompt + tools insieme (atomic)
api("PATCH", "/api/routines/market-briefing-18", body={
    "prompt": "...nuovo prompt...",
    "tools": ["research-serpapi"]
})

# Rimuove un campo opzionale (es. timeout_sec custom)
api("PATCH", "/api/routines/market-briefing-18", body={"timeout_sec": null})
```

Il backend valida lo yaml dopo il patch. Se validation fail, rollback automatico.

### Confirm prima di:

- `DELETE` di qualunque entità (workspace, agent, routine, skill, goal)
- Modifiche a `<hub>/config.json` (default provider, telegram chat_ids, audio)
- Modifiche a `<hub>/.mcp.json` (server core)
- Modifiche a `<hub>/.secrets.env`

Format conferma: "Sto per [azione]. [Dettaglio breve]. Procedo? (sì/no)"

### Routine provider/model validi

Il runner supporta solo questi provider+model:

- `claude`: model = `haiku` | `sonnet` | `opus` | `fast`
- `openai_oauth` (ChatGPT subscription): model = `gpt-5.5` UNICO supportato
- `openai`: model = id LiteLLM (es. `gpt-4o`, `gpt-5`) — richiede `OPENAI_API_KEY` nei secrets
- `xai`: `grok-4`, `grok-4-fast` — richiede `XAI_API_KEY`
- `openrouter`: id OpenRouter — richiede `OPENROUTER_API_KEY`

**Output action types** (`output: [{type: ...}]`):
- `telegram` (chat_id opzionale, default hub config), `email`, `slack`, `google_chat`,
  `webhook`, `file`, `wiki_page_hub`, `wiki_ingest`

**Web research pre-fetch** (provider senza Bash come openai_oauth): se
`tools: [research-duckduckgo]` o `[research-serpapi]`, AGGIUNGI campo
`research: [lista query]` nel yaml — il runner pre-esegue lo script e inietta
risultati nel context.

## Esempi end-to-end

### "Modifica il prompt della routine market-briefing-18 aggiungendo Investing.com"

```python
# 1. Leggi routine corrente
r = api("GET", "/api/routines/market-briefing-18")

# 2. Modifica il prompt
r["prompt"] = r["prompt"].replace(
    "Usa ricerca web fresca tramite gli skill disponibili",
    "Usa Investing.com come fonte preferita per dati mercato + skill research come fallback"
)

# 3. Salva con overwrite
api("POST", "/api/routines", body={**r, "overwrite": True})
# → conferma all'utente
```

### "Crea una routine giornaliera che mi manda su Telegram il meteo di Milano alle 7"

```python
api("POST", "/api/routines", body={
    "name": "meteo-milano-7",
    "scope": "hub",
    "schedule": "0 7 * * *",
    "enabled": True,
    "provider": "openai_oauth",
    "model": "gpt-5.5",
    "tools": ["research-duckduckgo"],
    "research": ["meteo Milano oggi previsioni"],
    "prompt": "Briefing meteo Milano per oggi in italiano: temperatura min/max, pioggia, vento, indicazioni vestizione. Sintetico, max 5 righe.",
    "output": [{"type": "telegram"}],
    "timeout_sec": 120,
})
```

### "Crea un agent specializzato in finanza chiamato 'fin-analyst' con SOUL 'analista pragmatico'"

```python
api("POST", "/api/agents", body={
    "name": "fin-analyst",
    "role": "Analista finanziario pragmatico",
    "soul": "Analista pragmatico e diretto. Cita sempre le fonti. Non dà consigli finanziari personalizzati...",
    "default_provider": "claude",
    "default_model": "sonnet",
})
```
