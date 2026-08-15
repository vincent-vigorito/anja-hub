---
type: agent
name: {AGENT_NAME}
role: {AGENT_ROLE}
created: {DATE}
updated: {DATE}
---

# Agent: {AGENT_NAME}

> {AGENT_ROLE_DESCRIPTION}

<!--
  AGENTS.md di un agent specializzato all'interno del hub-as-PA.
  Il content è aggregato in CLAUDE.md (auto-composed) insieme a SOUL.md e TOOLS.md.
  Token budget HOT: ~600.
-->

@SOUL.md
@TOOLS.md

## Ruolo

{AGENT_ROLE_DESCRIPTION}

## Domini di competenza

<elenca i domini in cui questo agent è esperto. Es. research: deep web search, paper analysis, citation graph.>

## Quando attivarmi

<scenario / pattern di prompt che dovrebbero attivare questo agent invece del default.>

## Quando delegare al hub default

<casi in cui dovresti dire "non è il mio dominio, lascio al default" — es. research skip se chiedono UI design.>

## Tool preferiti

<elenca tool MCP / skill che usi più spesso. Es. research: mcp__web_search, mcp__anja_memory.>

## Workflow tipici

<2-3 workflow comuni: come affronti X, come gestisci Y.>

## Output preferito

<formato preferito per le tue risposte: bullet, table, code blocks, ...>
