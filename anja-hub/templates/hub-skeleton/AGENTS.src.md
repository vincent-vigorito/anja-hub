---
type: hub
created: {DATE}
updated: {DATE}
---

# {HUB_NAME}

> Hub aggregator anja: collega progetti registrati, ragiona cross-progetto, ospita agents personalizzati.

<!--
  AGENTS.md hub-level è always-loaded per qualsiasi sessione/routine sul hub.
  Token budget HOT: ~600.

  IMPORTANTE: i marker @ qui sotto includono inline SOUL.md (preferenze + identità)
  e TOOLS.md (capabilities) — così la triade è sempre caricata anche da CLI puro nel hub.
-->

@SOUL.md
@TOOLS.md

## Stato corrente

<una frase + data>

## Progetti registrati

<lista compatta dei progetti registrati nel hub. Auto-aggiornata da /anja-list o sync.>

## Convenzioni cross-progetto

- Wiki link cross: `[[<project>/wiki/<page>]]` (path-extended)
- Cross-analyses vivono in `cross/analysis/<slug>.md`
- Sessions aggregate in `sessions/index.md`
- Routine in `routines/<name>.yaml`

## Workflow tipici

- Cross-query su tema X: `/anja-cross-query "..."`
- Aggregate sessions: `/anja-aggregate-sessions`
- Lint hub: `/anja-lint-hub`

## Note operative

- Default provider/model: vedi `config.json`
- Secrets: `routines/.secrets.env` (gitignored)
- Agents: `agents/<name>/` (Fase 9)

## Memoria collegata

- `SOUL.md` — identità agent default + preferenze user a livello hub
- `TOOLS.md` — capabilities cross-progetto + tool quotidiani (Fase 11)
- `cross/`, `sessions/`, `routines/` — episodic + scheduled actions
