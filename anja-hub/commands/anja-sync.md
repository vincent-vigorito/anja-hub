---
description: Sincronizza i symlink (locale) e mirror (SSH) del hub anja
argument-hint: [--name <project>] [--all]
allowed-tools: Bash
---

# /anja-sync

Riconcilia i symlink locali (e in futuro mirror rsync SSH) del hub corrente. Aggiorna `last_sync` nel registry.

Argomenti: `$ARGUMENTS`

## Pre-flight

Verifica `config/projects.json` nella cwd.

## Workflow

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sync.py" --hub "$(pwd)" $ARGUMENTS
```

Logica:
- Per `kind: local`: ricostruisce il symlink `projects/<name>` → `<path>/.anjawiki/` (anche se esisteva, lo ricrea per garantire coerenza)
- Per `kind: ssh`: rsync mirror — **non implementato in MVP**, segnalato come failed
- Aggiorna `last_sync` nel registry per ogni progetto syncato con successo
- Append entry a `cross/log.md`: `## [date] sync | N progetti (M ok, K failed)`

## Output

Lo script stampa una tabella per progetto con status (✓ / ✗) e messaggio. Riporta a video.

## Argomenti

- Nessun argomento o `--all` → sync tutti i progetti
- `--name <project>` → sync solo quel progetto
