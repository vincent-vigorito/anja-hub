---
description: Elenca i progetti registrati nel hub anja corrente
argument-hint: [--json]
allowed-tools: Bash
---

# /anja-list

Elenca i progetti del registry hub. Sola lettura.

Argomenti: `$ARGUMENTS`

## Pre-flight

Verifica `config/projects.json` nella cwd. Se manca: errore "Non sei in un hub anja".

## Workflow

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/list_projects.py" --hub "$(pwd)" $ARGUMENTS
```

Lo script gestisce sia output tabellare (default) sia JSON (con `--json`). Riporta l'output a video.
