---
description: Registra un progetto anja nel hub corrente
argument-hint: --kind local --path <project-path>
allowed-tools: Bash, AskUserQuestion
---

# /anja-register

Registra un progetto al hub anja. Il progetto deve avere un `.anjawiki/` (creato da `/anja-init`).

Argomenti: `$ARGUMENTS`

## Pre-flight

Verifica che la cwd sia un hub anja:

```bash
test -f config/projects.json && echo "ok" || echo "not hub"
```

Se `not hub`: errore → "Non sei in un hub anja. Lancia `/anja-hub-init` prima, oppure `cd` nel hub."

## Workflow

### Step 1: Determina kind

In MVP solo `local` è supportato.

Se `--kind` non passato, default `local`. Se l'utente passa `--kind ssh`: errore con messaggio "SSH non implementato in MVP. Usa `--kind local`".

### Step 2: Determina path

Se `--path <path>` è passato, usalo. Altrimenti via `AskUserQuestion`: "Path del progetto da registrare? (deve avere `.anjawiki/`)"

### Step 3: Esegui register

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/register.py" \
  --hub "$(pwd)" \
  --kind "<KIND>" \
  --path "<PATH>"
```

Lo script:
- Legge `<path>/.anjawiki/meta.yaml`
- Verifica: token non duplicato, name non collidente
- Aggiunge entry a `config/projects.json`
- Crea symlink `projects/<name>` → `<path>/.anjawiki`
- Append entry a `cross/log.md`

### Step 4: Output

Riporta a video l'output dello script (id, type, path, symlink). Suggerisci `/anja-list` per verificare e `/anja-sync --all` per riconciliare.

## Errori da gestire

- `ERROR: .anjawiki/ not found` → suggerisci all'utente di lanciare prima `/anja-init` nel progetto target
- `ERROR: project ... already registered` → suggerisci `/anja-unregister` (futuro) o cambio path
- `ERROR: name ... already in use` → suggerisci di rinominare il progetto in `meta.yaml`
