---
name: cross-query
description: Workflow per interrogare il hub anja cross-progetto. Da usare quando l'utente esegue /anja-cross-query nel hub, o pone una domanda che richiede di correlare materiale da PIÙ progetti registrati ("cosa hanno in comune i progetti X e Y", "il pattern Z di un progetto si applica a un altro?", "contraddizioni cross-progetto su tema W").
version: 1.0.0
category: query
tags: [wiki, cross-project, analysis, hub]
platforms: [macos, linux]
requires_tools: []
---

# Skill: cross-query

Workflow di interrogazione cross-progetto del hub anja. Sola lettura cross-progetti + scrittura di una analysis page in `cross/analysis/`.

## Pre-condizioni

- Sei nel **hub anja**, non in un progetto. Verifica con `test -f config/projects.json`.
- `CLAUDE.md` del hub deve essere in context (lo schema globale, convenzioni link cross-progetto).

## Step-by-step

### 1. Carica registry

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/list_projects.py" --hub "$(pwd)" --json
```

Parse JSON: lista di progetti con `id`, `name`, `type`, `tags`, `location`.

**Edge cases**:
- Lista vuota → errore "hub vuoto, registra progetti prima"
- 1 solo progetto → avviso "meglio `/anja-query` direttamente; procedo comunque"

### 2. Identifica progetti rilevanti

Tre modalità:

| Input | Comportamento |
|---|---|
| `--projects p1,p2` esplicito | Filtra a quelli |
| Domanda menziona tag/type | Filtra per tag/type matching nel registry |
| Domanda generale | Tutti i progetti |

Se >5 progetti rilevanti → `AskUserQuestion`:

```
Hub ha N progetti registrati. La query è larga. Vuoi che mi concentri su:
- (a) Tutti N progetti
- (b) Solo quelli con type=dev (M progetti)
- (c) Solo questi specifici: <suggerisci 3-4 più rilevanti dai tag>
- (d) Specifica tu (passa --projects p1,p2,...)
```

### 3. Leggi `projects/<name>/wiki/index.md` per primo (regola d'oro cross-progetto)

Per ogni progetto rilevante, **batch read** di tutti gli `index.md`:

```
Read projects/p1/wiki/index.md
Read projects/p2/wiki/index.md
...
```

Ognuno è il catalogo del wiki del progetto, **categorizzato**. Ti dice subito cosa esiste nel wiki di quel progetto.

### 4. Identifica candidate pages cross-progetto

Da ogni index, individua link a pagine il cui titolo/one-liner combaciano con la domanda. Aggiungi grep cross-progetto:

```bash
grep -rli "<termine-chiave>" projects/*/wiki/ 2>/dev/null
```

Filtra falsi positivi (`log.md`, `transient: true`, sessions vecchie).

### 5. Limite di prudenza

Se totale pagine candidate >20 cross-progetto: **AskUserQuestion** per restringere scope:

```
Ho identificato 28 pagine candidate cross-progetto. È troppo per una sintesi pulita. Vuoi:
- (a) Solo entity (16 pagine)
- (b) Solo concept (8 pagine)
- (c) Restringere a 2-3 progetti
- (d) Andare avanti su tutto (cautela: risposta potrebbe essere generica)
```

### 6. Decidi: lettura inline o delega al subagent

| # pagine totali | Approccio |
|---|---|
| ≤5 | Main agent legge e sintetizza inline |
| >5 | Delega al subagent `cross-project-brainstorm` via Task tool (vedi step 7) |

### 7. Delega al subagent (se applicabile)

Invoca via Task tool:

```
subagent_type: cross-project-brainstorm
prompt: |
  Domanda originale: "<domanda>"
  Progetti rilevanti: <list-of-names>
  Candidate pages: 
    - projects/<p1>/wiki/<slug-1>.md
    - projects/<p1>/wiki/<slug-2>.md
    - projects/<p2>/wiki/<slug-3>.md
    - ...
  Slug suggerito per output: <slug>
  
  Leggi le pagine, sintetizza pattern/contrasti/applicabilità, scrivi
  cross/analysis/<slug>.md seguendo il template Cross-Analysis del tuo system
  prompt. Restituisci il summary strutturato.
```

L'agente lavora in context separato, ritorna solo il summary.

### 8. Sintesi inline (se ≤5 pagine)

Se main agent fa il lavoro direttamente:

- Read in batch parallelo delle candidate
- Sintesi con la disciplina della skill `query` di anja applicata cross-progetto:
  - **Citata**: ogni claim ha citazione `[[<project>/wiki/<page>]]`
  - **Risale alle source** quando rilevante
  - **Onesta sui gap**: dichiara se un tema è in un progetto e non in un altro
  - **Onesta sulle contraddizioni**: stessi concetti descritti diversamente in progetti diversi
- **Cross-specific**: enfasi su pattern, contrasti, applicabilità incrociata

### 9. Decidi se filare come analysis page

Default: sì (il valore della cross-query è proprio renderla riusabile).

**Skip** se `--no-file` passato o domanda triviale (count, navigazione, conferma).

### 10. Slug + scrittura analysis page

```bash
python3 "${HOME}/.claude/plugins/marketplaces/anjadev/scripts/slugify.py" "<tema della query>"
```

> Nota: `slugify.py` vive nel plugin `anjadev` (post-split). Path canonico al primo install. Fallback inline:
> `slug=$(printf '%s' "<tema>" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g; s/--*/-/g; s/^-//; s/-$//')`

Scrivi `cross/analysis/<slug>.md` seguendo il template Cross-Analysis di `agents/cross-project-brainstorm.md`:

```markdown
---
title: <tema>
type: analysis
created: YYYY-MM-DD
updated: YYYY-MM-DD
projects: [project-1, project-2, ...]
question: "<domanda originale>"
tags: [...]
---

# <tema>

## Domanda
> <domanda>

## Pattern / contrasti / applicabilità

### Pattern comuni
- ...

### Contrasti
- ...

### Applicabilità incrociata
- ...

## Per progetto

### [[<project-1>/wiki/...]] sintesi
...

### [[<project-2>/wiki/...]] sintesi
...

## Gap o contraddizioni emerse
- (se nulla, ometti)

## Pagine wiki citate
- [[<project>/wiki/<page>]]
```

### 11. Aggiorna `cross/index.md`

Entry sotto **Analisi**:

```
- [[<slug>]] — <one-liner del tema> (progetti: <p1>, <p2>)
```

Se `cross/index.md` non ha ancora una sezione Analisi popolata (placeholder), sostituisci il placeholder con la sezione attiva.

### 12. Append log entry

In `cross/log.md`:

```
## [YYYY-MM-DD] cross-query | <domanda riassunta>
```

Se filata, aggiungi nota:

```
## [YYYY-MM-DD] cross-query | <domanda> → analysis/<slug>.md
```

## Convenzione link cross-progetto

**Sempre con prefisso project name**:

```markdown
[[research-engine/wiki/regime-classification]]
[[DevForge/wiki/multi-agent-architecture]]
```

Path matching: `projects/<project-name>/` è il symlink → `<project>/.anjawiki/` originale → `wiki/<page>.md`.

**Mai** wikilink semplici (`[[regime-classification]]`) cross-progetto: ambigui se più progetti hanno pagine omonime.

## Edge case

| Caso | Cosa fare |
|---|---|
| Hub vuoto | Errore "registra progetti prima" |
| 1 solo progetto | Avviso "meglio `/anja-query` nel progetto"; procedi comunque |
| Domanda triviale | Rispondi senza filare |
| Nessuna pagina rilevante | Onestà: "niente materiale, suggerisco ingest mirato" |
| Contraddizioni profonde | Documenta nella analysis page con sezione dedicata, suggerisci risoluzione |
| Tag inconsistenti tra progetti | Segnala come finding, possibile lint hub futuro |
| Pagina-slug duplicato cross-progetto | Disambigua sempre con prefisso `<project>/wiki/<slug>` |

## Output finale

```
<risposta sintetica con [[<project>/wiki/<page>]] citations>

---
Progetti coinvolti: <list>
Pagine consultate: <N>
Pattern identificati: <K>
Contrasti: <M>
Contraddizioni: <X>
Filata come: cross/analysis/<slug>.md
Log entry: aggiunta
```
