# SEO On-site: audit, meta, E-E-A-T

## Audit di un contenuto (checklist)

1. **Meta**: title e description curati? (vuoti = template di fallback)
2. **Slug**: breve, descrittivo, senza anni o stop-word
3. **Struttura**: un solo H1, H2/H3 gerarchici, paragrafi brevi, liste dove utile
4. **Keyword**: intento chiaro, keyword principale in title/H1/primo paragrafo
5. **Internal link**: collega ad almeno 2-3 contenuti pertinenti (e riceve link?)
6. **Immagini**: alt text descrittivi, pesi ragionevoli, featured image presente
7. **Canonical/robots**: corretti? noindex solo dove ha senso
8. **Freshness**: date nel titolo o dati obsoleti? (es. "nel 2024" su sito nel 2026)
9. **Schema markup**: tipo corretto per il contenuto (Article, Product, FAQ...)

## Workflow meta: singolo e batch

**Singolo**: leggi stato attuale → leggi contenuto reale → proponi secondo le
convenzioni (vedi SKILL.md) → mostra → applica dopo ok → verifica read-back.

**Batch**: segui il workflow in [wordpress.md](wordpress.md). Punti chiave:
proposte scritte dal contenuto reale di ogni pagina (mai template), validazione
lunghezze automatica, file JSON come storico, approvazione esplicita, report.

## Analisi del focus editoriale

Per ogni contenuto chiediti: *porta traffico in target rispetto a ciò che il
sito vende/offre?* Classifica:
- **On-focus**: tema collegato a servizi/prodotti → ottimizza al massimo
- **Off-focus**: traffico potenziale ma non in target → segnala all'utente con
  opzioni (riposizionare, tenere come contenuto di colore, deindicizzare).
  Mai eliminare o deindicizzare di tua iniziativa.
- **Datato**: anni nel titolo, dati vecchi → proporre refresh (meta senza anno
  nel frattempo)

## Cannibalizzazione

Contenuti che competono sulla stessa keyword/intento: individuali confrontando
title/temi simili. Rimedi in ordine di invasività: differenziare i meta per
intento (uno "guida", l'altro "checklist/rischi") → differenziare i contenuti →
consolidare con redirect. I primi due non richiedono tocchi al contenuto.

## E-E-A-T (Experience, Expertise, Authoritativeness, Trust)

Checklist per valutare un sito/pagina:
- **Experience**: casi studio reali, portfolio, risultati misurabili, screenshot
- **Expertise**: autori firmati con bio, pagina team con competenze, contenuti
  che dimostrano pratica diretta (non solo teoria)
- **Authoritativeness**: recensioni/testimonianze, certificazioni, partnership,
  menzioni e link esterni
- **Trust**: dati societari completi e visibili (P.IVA, sede, contatti),
  NAP coerente ovunque, HTTPS, policy legali aggiornate, trasparenza su prezzi,
  schema Organization/LocalBusiness

Output dell'analisi: gap per pilastro + interventi ordinati per impatto/sforzo.

## SEO per AI (LLM, AI Overview)

I motori conversazionali e le AI Overview leggono il DOM, non il design:
- HTML semantico rigoroso (article, section, header, liste, tabelle)
- Risposte dirette all'inizio delle sezioni (estraibilità)
- FAQ con domande reali (e schema FAQPage dove appropriato)
- Dati strutturati completi; entità chiare (chi, cosa, dove)
- Contenuti che citano fonti e numeri → più citabili dagli LLM
