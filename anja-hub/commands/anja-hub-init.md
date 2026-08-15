---
description: Inizializza una directory come hub anja
argument-hint: [--target <path>]
allowed-tools: Bash, AskUserQuestion
---

# /anja-hub-init

Inizializza una directory come **hub anja**: aggregatore di progetti locali (e in futuro SSH).

Argomenti: `$ARGUMENTS`

## Workflow

### Step 1: Determina il target

Se `--target <path>` è passato, usalo. Altrimenti chiedi via `AskUserQuestion`:

> "Inizializzare hub anja nella directory corrente (`<cwd>`)?
> (a) Sì, in cwd
> (b) Specifica altro path
> (c) Annulla"

### Step 2: Esegui init

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/init_hub.py" --target "<TARGET>"
```

Lo script crea:
- `CLAUDE.md` (schema globale del hub)
- `cross/index.md`, `cross/log.md`
- `sessions/index.md`
- `config/projects.json` (registry vuoto)
- `projects/` (cartella vuota, popolata da `/anja-register`)

Lo script fallisce se la directory target non è vuota → mostra errore all'utente.

### Step 3: Output

Riporta a video l'output dello script (path, registry location, prossimi step).
