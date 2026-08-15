---
description: Esegue una routine anja immediatamente (trigger now)
argument-hint: <routine-name> [--dry-run]
allowed-tools: Bash
---

# /anja-routine-run

Esegue una singola routine immediatamente, indipendentemente dallo schedule. Utile per test, demo, o force-run di una task urgente.

## Argomenti

- `<routine-name>` (obbligatorio) — nome della routine da eseguire (deve esistere in `<hub>/routines/`)
- `--dry-run` (opzionale) — esegue il prompt LLM ma salta le output actions (email/slack/wiki_ingest non vengono inviate)

## Esecuzione

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/runner.py --name "$1" $2
```

Sostituisci `$1` con il nome routine e `$2` con `--dry-run` se l'utente lo richiede.

## Behavior

1. Identifica la routine da $ARGUMENTS.
2. Se l'utente specifica `--dry-run` o "in dry mode", aggiungi il flag.
3. Esegui il runner. Lo stdout mostra:
   - prompt scope/cwd/model
   - output Claude (preview prime 120 char)
   - dispatch actions (con status)
   - path del log markdown
4. Riassumi all'utente: routine eseguita, status (success/failed), durata, dove leggere il log completo.
5. Se errori: mostra il messaggio completo e suggerisci fix (controllare yaml, secrets, network).

## Esempi

```
/anja-routine-run news-arxiv
/anja-routine-run news-arxiv --dry-run
```
