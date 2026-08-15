# anja-routines

Plugin Claude Code per **agenti autonomi schedulati**. Routine yaml con scope dichiarativo (`hub` | `project:<name>`), eseguite da un daemon always-on tramite cron expression.

## Status

Daemon delle routine di **Anja Hub** — vedi il [README](../README.md) alla
radice del repo per quickstart e architettura.

## Idea base

```yaml
# <hub>/routines/news-arxiv.yaml
name: news-arxiv
scope: hub
schedule: "0 8 * * *"     # cron, ogni giorno alle 8
prompt: |
  Cerca nuovi paper arxiv di interesse pubblicati nelle ultime 24h.
  Riassumi 5-10 bullet point con link.
tools: [web_search, web_fetch]
output:
  - type: email
    to: you@example.com
    subject: "[anja] arxiv daily — {date}"
  - type: wiki_ingest
    target_project: research-engine
    raw_subdir: news-daily
```

Quando arriva il cron:
1. Daemon spawna `runner.py`
2. Runner usa `claude-agent-sdk` con prompt + cwd corretto (hub o progetto)
3. Output va dispatchato (email send, file write, ingest, ecc.)
4. Run loggato in `<hub>/routines/runs/<name>-<timestamp>.md`

## Comandi

| Comando | Cosa fa |
|---|---|
| `/anja-routine-add` | Wizard interattivo per costruire yaml |
| `/anja-routine-list` | Lista routine + stato |
| `/anja-routine-run <name>` | Trigger now (subprocess) |
| `/anja-routine-disable <name>` | Toggle enabled in yaml |
| `/anja-routine-history <name>` | Show runs di una routine |

## Struttura

```
anja-routines/
├── plugin.json + README.md
├── commands/                  # 5 slash command
├── scripts/
│   ├── daemon.py              # always-on loop, polling 30s
│   ├── runner.py              # singolo run di una routine
│   ├── routine_validate.py    # yaml schema validation
│   ├── routine_registry.py    # CRUD su routines.json del hub
│   └── tools/                 # action implementations
│       ├── email.py
│       ├── slack.py
│       ├── google_chat.py
│       ├── wiki_ingest.py
│       └── file.py
├── templates/
│   └── routine-skeleton.yaml
└── launchd/
    └── com.anja.routines.daemon.plist
```

E nel hub:

```
<hub>/routines/
├── <name>.yaml                # definizioni
├── runs/                      # history (markdown append-only)
│   └── <name>-<timestamp>.md
└── .secrets.env               # SMTP creds, webhook URLs (gitignore!)
```

## Avvio del daemon (in dev)

```bash
python3.12 ~/Documents/AnjaHub/anja-routines/scripts/daemon.py --hub ~/Documents/TEST-HUB
```

In produzione (Mac): registrare il plist `launchd/com.anja.routines.daemon.plist` come LaunchAgent (auto-start al login).

## Dipendenze

- Python 3.10+ (per claude-agent-sdk)
- `claude-agent-sdk` (già installato per Mission Control)
- `croniter` per parsing cron expression (nuova dep)
- stdlib `smtplib` per email
- `requests` o stdlib `urllib` per webhook (Slack/Google Chat)

## Vedi anche

- `anja-routines-design.md` — spec architetturale completa
- `anja-mission-control-design.md` — frontend per gestire le routine via UI (Fase 5b)
- `anja-design.md` §5 — modello D scope dichiarativo
