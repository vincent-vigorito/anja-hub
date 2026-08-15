---
name: blueprint-authoring
description: Progettare e creare NUOVI workspace blueprint per l'hub (F-BlueprintForge). Caricala quando l'utente chiede un nuovo tipo di workspace verticale ("blueprint per uno studio legale", "workspace per un ristorante") o di modificare/validare un blueprint esistente. Documenta il formato completo, il workflow scrivi→valida→istanzia e le regole del pod.
version: 1.0.0
category: hub-management
tags: [blueprint, workspace, scaffold, forge, marketplace]
platforms: [macos, linux]
requires_tools: [Bash]
---

# Skill: blueprint-authoring — creare nuovi blueprint

Un **blueprint** è la ricetta di un tipo di workspace: pod di agenti + template
di contenuto + routine + schema secret. Tu (agent hub) puoi crearne di nuovi
scrivendo file in **`<hub>/blueprints/<nome>/`** — hanno precedenza sui
built-in del repo e compaiono subito nel Marketplace con badge `hub`.

## Workflow obbligatorio

1. **Progetta**: quali ruoli servono nel pod? Quale backend? Quali routine
   hanno senso per il dominio? Discuti la struttura con l'utente PRIMA di scrivere.
2. **Scrivi** la dir in `<hub>/blueprints/<nome>/` (struttura sotto).
3. **Valida** — SEMPRE, prima di dichiarare finito:
   `curl -s -X POST http://127.0.0.1:8765/api/blueprints/<nome>/validate`
   → `{ok, errors, warnings}`. Correggi gli `errors` (bloccanti); valuta i warnings.
4. **Collauda**: istanzia un workspace di prova
   (`POST /api/workspaces/from-blueprint {"brand_name": "test-<nome>", "blueprint": "<nome>"}`),
   verifica il risultato, poi eliminalo (`POST /api/workspaces/test-<nome>/delete` — chiedi conferma).
5. Mostra all'utente la card nel Marketplace.

## Struttura della directory

```
<hub>/blueprints/<nome>/
├── blueprint.json          # manifest (obbligatorio)
├── vault.schema.env        # schema secret (OBBLIGATORIO: senza, lo scaffold crasha)
├── agents/<ruolo>.json     # UN file per OGNI ruolo del pod
├── content/                # opzionale ma consigliato
│   ├── ESPERTO.md          # expertise di dominio del brand
│   ├── BRAND.md            # identità brand (da compilare)
│   ├── PIANO.md            # piano operativo/editoriale
│   └── catalogo/*.md       # skeleton catalogo (prodotti.md solo se ecommerce)
└── routines/*.yaml         # routine pre-configurate (opzionale)
```

## Placeholder (sostituiti allo scaffold, in TUTTI i file)

- `{WS}` → slug del workspace (es. `acme-shop`)
- `{BRAND}` → nome brand (es. `Acme Shop`)
- `{LEAD}` → slug dell'agente lead (es. `anja-acme-shop`)

## blueprint.json — campi

```json
{
  "name": "<nome>",                    // = nome directory
  "version": "0.1.0",
  "description": "una riga per la card del Marketplace",
  "workspace_type": "marketing",       // tipo logico del workspace
  "backends": ["wp", "woo", "swerpi"], // backend supportati (≥1)
  "default_backend": "wp",             // DEVE essere in backends
  "ecommerce_optional": true,          // abilita il flag ecommerce allo scaffold
  "shared_skill": "seo-manager",       // skill metodologica condivisa dal pod
  "mcp_server": "anja_marketing",      // server MCP di dominio riusato
  "tool_groups_by_backend": {          // ANJA_TOOL_GROUPS per backend
    "wp": "cms,analytics,social", "swerpi": "analytics,social"
  },
  "pod": ["lead", "analyst", "social"],// ruoli — un agents/<ruolo>.json ciascuno
  "lead_role": "lead",                 // DEVE essere nel pod
  "placeholders": { "WS": "...", "BRAND": "...", "LEAD": "..." }  // doc, non funzionale
}
```

## agents/<ruolo>.json — campi

```json
{
  "name": "{LEAD}",                    // lead: {LEAD}; specialisti: nome fisso (es. "analyst")
  "display_name": "{BRAND} — lead",
  "role": "cosa fa, con chi parla, cosa delega (2-4 frasi)",
  "persona": "carattere e stile (1-2 frasi)",
  "scope": "agent",
  "default_provider": "claude", "default_model": "sonnet", "default_effort": "off",
  "workspace_name": "{WS}",
  "workspace_lead": true,              // solo sul lead
  "mcp_servers": ["anja_agents"],      // server hub EXTRA da montare (oltre marketing+memory)
  "allowed_tools": ["mcp__anja_marketing__gsc_query", "Bash"],
  "skill_modules": ["wordpress"],      // moduli della shared_skill che carica
  "auto_route": ["seo", "posizionamento"]  // keyword per il routing @mention
}
```

Regole:
- Il **lead** orchestra e delega: pochi tool (status/read), `workspace_lead: true`.
- Gli **specialisti** hanno i tool del loro mestiere. `ANJA_TOOL_GROUPS` viene
  derivato dai tool (`gsc_/ga_/merchant_` → analytics; `wp_*` → cms; `meta_/social_kit` → social).
- Backend `swerpi`: i tool `wp_*` vengono RIMOSSI automaticamente e sostituiti
  da Bash + modulo `swerpicommerce` per chi scrive sul CMS — progetta per `wp`
  e lascia fare all'adattatore.

## vault.schema.env

Un `KEY=` per riga (valori VUOTI — è uno schema), commenti per sezione:

```
# --- CMS ---
WP_BASE_URL=
WP_APP_PASSWORD=
# --- Analytics ---
GSC_SITE=
```

## routines/*.yaml

Routine standard del daemon (name/schedule/scope/prompt/actions); usa i
placeholder nel nome file e nel body: `weekly-report-{WS}.yaml` con
`scope: project:{WS}`.

## Errori tipici (che la validate intercetta)

- ruolo nel `pod` senza `agents/<ruolo>.json` → il ruolo verrebbe **saltato in silenzio**
- `default_backend` fuori da `backends`; `lead_role` fuori dal `pod`
- `vault.schema.env` mancante → crash allo scaffold
- JSON/YAML malformati DOPO la sostituzione placeholder
