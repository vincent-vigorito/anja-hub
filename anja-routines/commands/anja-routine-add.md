---
description: Wizard interattivo per creare una nuova routine anja
argument-hint: [routine-name]
allowed-tools: Read, Write, Bash, AskUserQuestion
---

# /anja-routine-add

Wizard guidato per creare una nuova routine yaml in `<hub>/routines/`.

## Behavior

Esegui i passi in ordine, chiedendo all'utente le info mancanti via AskUserQuestion. Sii conciso, una domanda alla volta.

### 1. Nome
Se $ARGUMENTS contiene un nome valido (kebab-case), usalo. Altrimenti chiedi all'utente.

### 2. Scope
Chiedi: `hub` o `project:<name>`?
Se `project`, lista i progetti disponibili leggendo `${ANJA_HUB:-$HOME/anja-hub}/registry/hub.json`.

### 3. Schedule
Chiedi cron expression (5 fields). Mostra esempi comuni:
- `0 8 * * *` → Daily at 08:00
- `*/15 * * * *` → Every 15 minutes
- `0 9 * * 1` → Mondays at 09:00
- `0 0 1 * *` → First day of month, midnight

### 4. Description (1 riga) e Prompt (multi-line)
Chiedi all'utente che cosa la routine deve fare. Costruisci un prompt chiaro.

### 5. Model
Default: `sonnet`. Offri `haiku` per task semplici, `opus` per analisi complesse.

### 6. Output actions
Per ognuna chiedi tipo (email/slack/google_chat/wiki_ingest/file) e config minima.

### 7. Genera yaml
Usa `${CLAUDE_PLUGIN_ROOT}/templates/routine-skeleton.yaml` come base. Riempi i campi raccolti, scrivi in `${ANJA_HUB:-$HOME/anja-hub}/routines/<name>.yaml`.

### 8. Valida
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/routine_validate.py <yaml-path>
```

Se valid, suggerisci all'utente:
- Test in dry-run: `/anja-routine-run <name> --dry-run`
- Trigger reale: `/anja-routine-run <name>`
- Lista: `/anja-routine-list`

Se non valid, mostra gli errori e chiedi se correggere.

## Tip

Mantieni il flow leggero: 6-8 domande totali. Non chiedere campi opzionali se l'utente non li menziona (timeout, max_retries, tags).
