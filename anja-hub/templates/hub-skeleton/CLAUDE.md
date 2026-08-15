# Schema anja-hub per questa directory

> Questo è un **hub anja**: aggregatore di progetti locali e remoti SSH. Letto da Claude all'inizio di ogni sessione nel hub.

## Struttura

- `projects/<name>/` — **symlink (locale)** o **mirror rsync (SSH)** dei `.anjawiki/` dei progetti registrati
- `cross/` — sintesi cross-progetto generate dall'LLM (analysis, log)
- `sessions/` — journal di sessione globale (aggregato dai progetti, futuro)
- `personal/` (opzionale) — wiki personale, separato dai progetti, init con `/anja-init --type personal`
- `config/projects.json` — registry: token → location

## Direzione dei riferimenti

I link vanno **solo dal hub verso i progetti**, mai il contrario. I wiki di progetto restano self-contained.

## Workflow chiave

### Aggiungere un progetto

```bash
/anja-register --kind local --path /path/to/project   # progetto deve avere .anjawiki/
/anja-register --kind ssh --host user@server --path /remote/path   # futuro
```

### Vedere cosa c'è

```bash
/anja-list                # tabella
/anja-list --json         # JSON
```

### Sync

```bash
/anja-sync --all          # ricostruisce symlink locale; rsync SSH (futuro)
/anja-sync --name foo     # solo un progetto
```

### Ragionare cross-progetto

```bash
/anja-cross-query "<domanda>"
```

Workflow:

1. Leggi `cross/index.md` per primo (catalogo analisi cross-progetto già fatte)
2. Carica registry via `list_projects.py --json`
3. Identifica progetti rilevanti (tag, type, o esplicito via `--projects`)
4. Apri `projects/<name>/wiki/index.md` di ognuno (regola d'oro)
5. Identifica candidate cross-progetto + grep su termini chiave
6. Se >5 pagine totali: delega al subagent `cross-project-brainstorm`
7. Sintetizza con citazioni `[[<project>/wiki/<page>]]` (vedi convenzione sotto)
8. Fila in `cross/analysis/<slug>.md`
9. Aggiorna `cross/index.md` + entry log

## Convenzione link cross-progetto

Dal `cross/` punti ai progetti via wikilink **path-extended**:

```markdown
Vedi [[research-engine/wiki/citation-graph]] e [[DevForge/wiki/multi-agent-architecture]] per il pattern.
```

Il path `<project-name>/wiki/<slug>` corrisponde alla risoluzione tramite il symlink `projects/<project-name>/`.

**Mai** wikilink semplici (`[[regime-classification]]`) cross-progetto: ambigui se più progetti hanno pagine omonime.

Per linkare un progetto come tale (overview): `[[<project>/wiki/overview]]` o `[[<project>/wiki/index]]`.

## Convenzioni

Le pagine di `cross/` seguono le stesse convenzioni del wiki di progetto (frontmatter YAML, [[wikilinks]], log format).

Riferimenti a progetti registrati: `[[<name>/wiki/<page>]]` (tramite il symlink). Esempio: `[[research-engine/wiki/citation-graph]]`.

## Log format del hub

Tipi validi nel `cross/log.md`: `hub-init`, `register`, `unregister`, `sync`, `cross-query`, `cross-rebuild`, `lint-hub`.

Pattern come per i progetti: `## [YYYY-MM-DD] tipo | descrizione`. Pattern regex parser: `(\w[\w-]*)`.

## Note

Il hub è un **aggregatore read-only**: non modifica i wiki dei progetti. Per modificare un progetto, lavora nel suo `.anjawiki/` direttamente (i comandi `anja` sono progetto-locali).
