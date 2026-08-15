---
description: Interroga cross-progetto il hub anja e fila la sintesi come analysis page
argument-hint: <domanda> [--no-file] [--projects p1,p2,...]
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, AskUserQuestion, Task
---

# /anja-cross-query

Interroga il hub anja cross-progetto. Sintetizza con citazioni `[[<project>/wiki/<page>]]` e fila la risposta come `cross/analysis/<slug>.md` per accumulare conoscenza riusabile.

Argomenti: `$ARGUMENTS`

## Pre-flight

Verifica che la cwd sia un hub anja:

```bash
test -f config/projects.json && echo "ok" || echo "not hub"
```

Se `not hub`: errore "Non sei in un hub anja. `cd` nell'hub o lancia `/anja-hub-init` prima."

Leggi `CLAUDE.md` del hub corrente per le convenzioni cross-progetto (link `[[<project>/wiki/<page>]]`, log format).

## Workflow

Esegui il workflow `cross-query` definito in `${CLAUDE_PLUGIN_ROOT}/skills/cross-query/SKILL.md`. Sintesi degli step:

1. **Carica registry**:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/list_projects.py" --hub "$(pwd)" --json
   ```
2. **Identifica progetti rilevanti**:
   - Se `--projects p1,p2,...` passato: usa quelli (filtra il registry)
   - Altrimenti: tutti i progetti registrati. Se >5 progetti: AskUserQuestion per restringere.
3. **Per ogni progetto rilevante**: `Read` di `projects/<name>/wiki/index.md` (regola d'oro)
4. **Identifica pagine candidate cross-progetto** dai vari index + grep su termini chiave della domanda
5. **Limite di prudenza**: se >20 pagine totali tra tutti i progetti → AskUserQuestion per restringere scope
6. **Lettura + sintesi**:
   - Se ≤5 pagine: main agent legge e sintetizza inline
   - Se >5 pagine: **delega al subagent `cross-project-brainstorm`** via Task tool
7. **Decidi se filare** (default sì, skip se `--no-file`)
8. **Slug del tema**:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT_ANJA:-$HOME/.claude/plugins/marketplaces/anjadev}/scripts/slugify.py" "<tema della query>"
   ```
   *Nota: lo slugify è del plugin anja, non anja-hub. Path absoluto se necessario.*
9. **Scrivi `cross/analysis/<slug>.md`** con frontmatter `type: analysis`, `projects: [...]`, `question: "..."`
10. **Aggiorna `cross/index.md`**: entry sotto Analysis
11. **Append log**: `## [YYYY-MM-DD] cross-query | <domanda riassunta>`

## Output finale

```
<risposta sintetica con [[<project>/wiki/<page>]] citations>

---
Progetti coinvolti: <list>
Pagine consultate: <N>
Pattern identificati: <K>
Contrasti identificati: <M>
Filata come: cross/analysis/<slug>.md  (se filata)
Log entry: aggiunta
```

## Edge case

| Caso | Cosa fare |
|---|---|
| Hub vuoto (nessun progetto registrato) | Errore: "Hub vuoto. `/anja-register --kind local --path <project>` prima." |
| 1 progetto solo | Avviso: "1 solo progetto registrato — è meglio usare `/anja-query` direttamente nel progetto. Procedo comunque." |
| Nessuna pagina rilevante trovata | Risposta onesta: "Nessun materiale rilevante nei progetti registrati. Suggerimenti: (a) raffinare la domanda, (b) ingerire fonti rilevanti, (c) aggiungere progetti con `/anja-register`." |
| Domanda triviale (count, navigazione) | Rispondi e stop senza filare (come per `/anja-query`) |
