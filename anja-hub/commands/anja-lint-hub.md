---
description: Health check cross-progetto del hub anja (link rotti, frontmatter, index alignment, tag inconsistenti)
argument-hint: [--no-file]
allowed-tools: Bash, Read, Write
---

# /anja-lint-hub

Esegui health check **cross-progetto** del hub. Combina check meccanici (script Python) con eventuale review semantica (Claude). Output: report `cross/analysis/lint-hub-<YYYY-MM-DD>.md` (transient).

Argomenti: `$ARGUMENTS`

> Diverso da `/anja-lint` (di `anja`): quello è progetto-locale, questo è cross-progetto del hub.

## Pre-flight

Verifica `config/projects.json` nella cwd. Se manca: errore "Non sei in un hub anja".

## Workflow

### Step 1: check meccanici (Python)

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lint_hub.py" --hub "$(pwd)"
```

Output JSON con campi:
- `projects_count`, `issues_total`
- `by_severity` (counts)
- `issues[]` con per ogni issue: `severity`, `type`, `page`/`slug`/`tags`, `message`

**Tipi rilevati**:
| Tipo | Severity | Cosa significa |
|---|---|---|
| `cross-link-unknown-project` | error | `[[<project>/wiki/<page>]]` referenzia project NON nel registry |
| `cross-link-broken` | error | `[[<project>/wiki/<page>]]` referenzia page inesistente nel wiki del progetto |
| `frontmatter-unknown-project` | warning | `cross/analysis/*.md` ha `projects: [..., X, ...]` con X fuori dal registry |
| `index-missing-entry` | warning | `cross/analysis/*.md` non listato in `cross/index.md` |
| `tag-variant` | suggestion | Heuristica: tag che differiscono solo per hyphen/underscore/case tra progetti |

Parsa il JSON e tieni in memoria.

### Step 2: compila report

Se `--no-file` passato: mostra a video il summary e ferma.

Altrimenti scrivi `cross/analysis/lint-hub-<YYYY-MM-DD>.md`:

```markdown
---
title: Lint Hub report YYYY-MM-DD
type: analysis
created: YYYY-MM-DD
updated: YYYY-MM-DD
projects: []
tags: [lint, hub]
transient: true
---

# Lint Hub report del YYYY-MM-DD

## Summary

- Progetti registrati: <N>
- Issue totali: <N> (E errors, W warnings, S suggestions)

## Errors (<E>)

### cross-link-unknown-project / cross-link-broken in `<page>`

> <message>

**Fix suggerito**: <specifico per tipo>

(...una sezione per ogni error...)

## Warnings (<W>)

(...una sezione per ogni warning...)

## Suggestions (<S>)

### tag-variant: '<a>' vs '<b>'

> <message>

**Fix suggerito**: standardizzare il tag in tutti i progetti coinvolti.

(...una sezione per ogni suggestion...)

## Note

Report transient — può essere cancellato dopo aver applicato i fix. Il prossimo `/anja-lint-hub` ne genera uno nuovo.
```

### Step 3: aggiorna `cross/index.md`

Append entry sotto Analisi:

```
- [[lint-hub-YYYY-MM-DD]] — health check cross-progetto (E errors, W warnings, S suggestions)
```

### Step 4: append log

In `cross/log.md`:

```
## [YYYY-MM-DD] lint-hub | E errors, W warnings, S suggestions
```

Se errors > 0:

```
## [YYYY-MM-DD] lint-hub | E errors, W warnings, S suggestions
- Errori da risolvere subito (vedi report)
```

## Output finale

```
✓ Lint hub completato.
  Progetti registrati: <N>
  Issue totali:        <N>  (E errors, W warnings, S suggestions)
  Report:              cross/analysis/lint-hub-<YYYY-MM-DD>.md
  Log entry:           aggiunta
```

Se errors > 0, evidenziali in cima:

```
⚠️  <E> errors da risolvere subito:
  - cross-link-broken: [[X/wiki/Y]] in cross/analysis/Z.md
  ...
```
