---
name: {{user_name}}
slug: {{user_slug}}
pronouns: {{pronouns}}
languages: [{{language}}]
timezone: {{timezone}}
default: {{is_default}}
created: {{today}}
updated: {{today}}
type: user-hot
---

# {{user_name}}

<!--
  USER HOT — sempre injected nel context di ogni chat (~500 token target).
  Tieni qui solo il "rolling profile": ruolo, contesto operativo, preferenze strong.
  Per dettagli (gusti, hobby, persone, episodi) vedi `{{user_slug}}-detail.md`.

  L'agent può aggiornare questo file via tool MCP `user.update(section, content)`.
-->

## Profilo

<una frase su chi sei e cosa fai oggi>

## Preferenze di comunicazione

- Lingua: {{language}}
- Tono: <es. diretto e conciso, no fluff>
- Format: <es. concreto, non enciclopedico>

## Contesto operativo

<una/due frasi sul tuo lavoro/contesto attuale>

## Note

<eventuali fatti permanenti rilevanti per ogni interazione>
