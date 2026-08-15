---
description: Lista tutte le routine anja registrate nel hub corrente
allowed-tools: Bash
---

# /anja-routine-list

Mostra tabella di tutte le routine yaml in `<hub>/routines/`, con stato (enabled/disabled), schedule cron, ultimo run, validità.

## Esecuzione

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/routine_registry.py list
```

Se l'hub non è auto-rilevato, esporta `ANJA_HUB=/path/to/hub` prima.

## Output atteso

Tabella con colonne: `EN | NAME | SCOPE | SCHEDULE | LAST RUN | STATUS | VALID`.

## Behavior

1. Esegui il comando sopra.
2. Se nessuna routine, segnala "Nessuna routine registrata. Crea con /anja-routine-add."
3. Se ci sono routine non valide (`VALID=INVALID`), evidenziale e suggerisci `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/routine_validate.py <file>` per dettagli.
