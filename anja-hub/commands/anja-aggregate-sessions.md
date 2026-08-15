---
description: Rigenera sessions/index.md aggregando i journal di sessione cross-progetto
argument-hint: (nessun argomento)
allowed-tools: Bash
---

# /anja-aggregate-sessions

Aggrega i `wiki/sessions/<date>.md` di **tutti i progetti registrati** in `sessions/index.md` cronologico.

## Pre-flight

Verifica `config/projects.json` nella cwd. Se manca: errore "Non sei in un hub anja".

## Workflow

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/aggregate_sessions.py" --hub "$(pwd)"
```

Lo script:
- Itera `projects/<name>/wiki/sessions/*.md` per ogni progetto del registry
- Estrae `summary` da ogni file (cerca `Summary:` in body, altrimenti primo paragrafo non-titolo)
- Genera `sessions/index.md` con timeline cronologica raggruppata per data (più recente in alto)
- Preserva `created:` dell'indice esistente
- Append entry `cross-rebuild` in `cross/log.md`

## Quando lanciarlo

- Dopo `/anja-sync` quando hai sessioni recenti su più progetti
- Periodicamente per avere una vista cross delle ultime settimane
- Prima di una review settimanale ("cosa ho fatto ultimamente nei vari progetti?")

## Output finale

Riporta a video l'output dello script: progetti scansionati, sessioni trovate, range date.
