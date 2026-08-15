# anja-hub

Plugin **hub aggregator** per `anja`. Aggrega progetti locali (e in futuro remoti SSH) per ragionamento cross-progetto.

Richiede `anja` plugin installato.

## Status

Componente principale di **Anja Hub** — vedi il [README](../README.md) alla
radice del repo per quickstart e architettura.

## Comandi

| Comando | Cosa fa |
|---|---|
| `/anja-hub-init` | Inizializza la cartella corrente come hub |
| `/anja-register` | Registra un progetto (locale in MVP) |
| `/anja-list` | Elenca progetti registrati |
| `/anja-sync` | Riconcilia symlink (e in futuro mirror SSH) |
| `/anja-unregister` | Rimuove progetto dal registry _(non in MVP)_ |

## Struttura hub

```
<hub-dir>/
├── CLAUDE.md              # schema globale del hub
├── projects/              # symlink (locale) o mirror rsync (SSH)
│   ├── progetto-a/        # → /path/to/progetto-a/.anjawiki
│   └── progetto-b/
├── cross/                 # sintesi cross-progetto generate dall'LLM
│   ├── index.md
│   ├── log.md
│   └── analysis/          # popolato a runtime via /anja-cross-query (futuro)
├── sessions/              # journal aggregato (futuro)
│   └── index.md
├── config/
│   └── projects.json      # registry: id → name, type, location, last_sync
└── personal/              # (opzionale) wiki personale, init separato con /anja-init
```

## Quick start

```bash
# 1. Inizializza il hub in una directory dedicata
cd ~/anja-hub-instance
/anja-hub-init

# 2. Registra un progetto esistente con .anjawiki/
/anja-register --kind local --path ~/Documents/projects/foo

# 3. Vedi cosa c'è
/anja-list

# 4. Sync (ricrea symlink)
/anja-sync --all
```

## Architettura

Pattern **plugin orchestra, Claude esegue**, come `anja`. Il hub è un **aggregatore read-only**: non modifica i wiki dei progetti, li legge tramite symlink (locale) o mirror rsync (SSH, futuro).

I link vanno **solo dal hub verso i progetti**, mai il contrario. I wiki di progetto restano self-contained.

## Decisione di design

Il **registry** (`config/projects.json`) è in **JSON** (non YAML come nello spec originale). Motivazione: stdlib Python parse/dump diretto, niente dipendenze esterne. Il `meta.yaml` dei progetti resta YAML (semplice, regex-parsable per i pochi campi che servono).
