---
description: Lista gli agent specializzati nel hub anja
allowed-tools: Bash
---

# /anja-agent-list

Mostra tabella di tutti gli agent in `<hub>/agents/<name>/`, con name, model, provider, sessions count, role.

## Usage

```bash
/anja-agent-list           # tabella
/anja-agent-list --json    # JSON output
```

## Behavior

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent_list.py --hub "$ANJA_HUB"
```

dove `$ANJA_HUB` è la root del hub corrente.

Se nessun agent registrato, suggerisci di crearne uno con `/anja-agent-add`.
