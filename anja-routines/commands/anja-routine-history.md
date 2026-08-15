---
description: Mostra lo storico dei run di una routine anja
argument-hint: <routine-name> [last N]
allowed-tools: Bash, Read, Glob
---

# /anja-routine-history

Mostra gli ultimi run di una routine. Ogni run è un file markdown in `<hub>/routines/runs/<name>-<timestamp>.md`.

## Esecuzione

```bash
ANJA_HUB="${ANJA_HUB:-$HOME/anja-hub}"
ls -1t "$ANJA_HUB/routines/runs/" 2>/dev/null | grep "^$1-" | head -${2:-10}
```

## Behavior

1. Da $ARGUMENTS estrai nome routine e (opzionale) `last N` (default 10).
2. Lista i file più recenti.
3. Per ogni run mostra: timestamp, status (parsing prima riga del .md), durata.
4. Se l'utente chiede "view <file>" o numero, leggi il file con Read e mostra il contenuto formatato.
5. Se nessun run esiste, segnala "Nessun run per <name>. Esegui con /anja-routine-run <name>."

## Esempi

```
/anja-routine-history news-arxiv
/anja-routine-history news-arxiv last 30
```
