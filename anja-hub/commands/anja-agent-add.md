---
description: Crea un agent specializzato nel hub anja
argument-hint: [name] --role "..." [--model haiku|sonnet|opus] [--domain "..."]
allowed-tools: Bash, AskUserQuestion
---

# /anja-agent-add

Crea un nuovo agent specializzato in `<hub>/agents/<name>/` con triade AGENTS+SOUL+TOOLS, config.json (model+provider+scope+soul_inheritance) e sessions/.

## Usage

```bash
/anja-agent-add research --role "Research analyst con focus paper + citation graph"
/anja-agent-add writer --role "Technical writer per docs OSS" --model opus
/anja-agent-add researcher --role "Research assistant" --domain "academic ML"
```

## Behavior

1. Da `$ARGUMENTS` estrai `name` (kebab-case obbligatorio) + `--role` (1-2 frasi obbligatorio).
2. Se mancano, chiedi all'utente via AskUserQuestion (`name`, `role`, opzionale `model` e `domain`).
3. Esegui:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent_add.py --hub "$ANJA_HUB" --name <name> --role <role> [--model <model>] [--domain <domain>]
```

dove `$ANJA_HUB` è la root del hub corrente (cwd se contiene `config/projects.json`, altrimenti chiedi all'utente).

4. A fine creazione:
   - Conferma path: `<hub>/agents/<name>/`
   - Mostra prossimi step: rifinire AGENTS.md / SOUL.md, e aprire chat-as-agent dalla Mission Control.

## Esempi role suggeriti

- **research** — "Research analyst, focus paper + citation graph + source validation"
- **writer** — "Technical writer, docs OSS + dev blog, tone diretto e pragmatico"
- **researcher** — "Research assistant rigoroso, cita sempre fonti, distingue fact da opinion"
- **devops** — "DevOps engineer, focus infrastructure + observability"
- **product** — "Product manager pragmatico, tradeoff espliciti, decision frameworks ICE/RICE"

## Tip

I role specifici beneficiano di un AGENTS.md dettagliato (workflow tipici, output preferito, cosa delegare al hub). Dedica 5-10 min alla rifinitura dopo la creazione automatica.
