---
name: {{user_name}}
slug: {{user_slug}}
type: user-detail
created: {{today}}
updated: {{today}}
---

# {{user_name}} — dettagli

<!--
  USER DETAIL — NON injected automaticamente. L'agent lo legge via tool MCP
  `user.read(detail=true)` quando rilevante (es. utente menziona un hobby, un
  amico, un episodio passato).

  Sezioni libere — l'agent appende qui via `user.update(section, content,
  mode='append')`.
-->

## Gusti e preferenze

<musica, film, libri, cibo, attività... ciò che ti piace e non ti piace>

## Persone importanti

<amici, famiglia, colleghi rilevanti — nome + 1 riga di contesto>

## Hobby e interessi

<cosa fai nel tempo libero, progetti collaterali>

## Episodi rilevanti

<aneddoti, decisioni passate importanti, contesto storico utile>

## Obiettivi a lungo termine

<dove vuoi arrivare nei prossimi 1-3 anni>
