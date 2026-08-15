---
description: Pausa o riattiva una routine anja (toggle enabled/disabled)
argument-hint: <routine-name> [enable|disable]
allowed-tools: Bash
---

# /anja-routine-disable

Toggle dello stato `enabled` di una routine. Una routine `disabled` non viene più triggerata dal daemon (resta nel filesystem, può essere rieseguita manualmente con `/anja-routine-run`).

## Esecuzione

Per disabilitare:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/routine_registry.py disable "$1"
```

Per riabilitare:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/routine_registry.py enable "$1"
```

## Behavior

1. Da $ARGUMENTS estrai nome routine + azione (default: `disable` se non specificato).
2. Esegui il comando appropriato.
3. Se l'utente vuole vedere lo stato risultante, esegui `/anja-routine-list` dopo.
4. Conferma all'utente: "Routine X ora è enabled/disabled. Il daemon onorerà la modifica al prossimo polling (≤30s)."

## Esempi

```
/anja-routine-disable news-arxiv
/anja-routine-disable news-arxiv enable
```
