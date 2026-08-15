# Piano editoriale blog

## Pipeline operativa (flow ripetibile)

Gli articoli pianificati vivono nella tabella `editorial_plan` del DB di
progetto, con stati: `idea → brief → bozza → pubblicato → repurposed`
(oppure `scartato`). Ogni passaggio di stato aggiorna la riga.

1. **Dati → idee**: query GSC (quick win, opportunità senza pagina dedicata),
   vendite (e-commerce: contenuti attorno ai prodotti che convertono),
   competitor (Ads Library / SERP). Ogni idea entra con `rationale` che cita
   i dati a supporto. Approvazione utente sul lotto di idee.
2. **Idea → brief**: template brief (sotto), salvato nella riga.
3. **Brief → bozza**: scrivere l'articolo seguendo gli standard qualità,
   creare su WP con `wp_create_content` (SEMPRE `status=draft`) +
   meta SEO contestuali con `wp_set_seo`; salvare `content_id` nella riga.
4. **Review umana → pubblicazione**: la pubblicazione la decide l'utente
   (o ok esplicito in chat). Registrare l'intervento a diario.
5. **Repurposing social**: vedi social.md; spuntare `social_done`.
6. **Misurazione**: la pagina entra automaticamente in gsc_pages; confronto
   a 4-6 settimane dalla pubblicazione.

## Fonti per la keyword research (in ordine di affidabilità)

1. **Search Console** (se integrata): query con impression alte e CTR basso,
   keyword in posizione 8-20 (quick win), pagine che calano
2. **SERP API** (se integrata): competitor, People Also Ask, related searches
3. **Il sito stesso**: contenuti esistenti da aggiornare/espandere prima di
   crearne di nuovi (refresh > new quando c'è già autorità)
4. In assenza di dati: intervista all'utente su clienti tipo, domande frequenti
   in trattativa, stagionalità del business

## Struttura del piano

- **Pilastri** (3-5 temi legati all'offerta) → **cluster** di articoli per
  pilastro → eventuale **pillar page** che li collega
- **Variante e-commerce, "settimana a tema"** (collaudata): ogni settimana ruota
  attorno a UN problema-prodotto — articolo indiretto (educativo) + post social
  che ne derivano + prodotto eroe. Il tema si sceglie dove c'è **gap domanda↔
  vendita** (query GSC alte + prodotto che non vende = priorità; stagionalità
  che scade batte tutto). L'articolo dà la destinazione, il social intercetta,
  la scheda (già ottimizzata) converte.
- Mix di intenti: informativi (top funnel), comparativi (middle), commerciali
  (bottom). Regola pratica: 60/25/15
- Calendario con: titolo provvisorio, keyword target, intento, pilastro,
  formato, CTA prevista, link interni pianificati
- Cadenza sostenibile > cadenza ambiziosa: meglio 2 articoli/mese costanti

## Brief articolo standard

```
Titolo provvisorio:
Keyword principale + 2 secondarie:
Intento di ricerca:
Angolo/tesi (perché diverso da ciò che è già in SERP):
Struttura H2/H3 proposta:
Domande da rispondere (PAA):
Link interni da inserire (2-3) e da ricevere:
CTA finale:
Elementi E-E-A-T: (esperienza diretta, dati, casi reali da citare)
Lunghezza indicativa:
```

## Standard qualità articolo

- Risposta diretta nel primo paragrafo, poi approfondimento
- H2/H3 che funzionano da indice (un lettore che scorre capisce tutto)
- Esempi concreti e numeri > affermazioni generiche
- Mai anno nel titolo salvo contenuti volutamente annuali (e allora si
  pianifica il refresh)
- Bozza sempre (`status=draft`) → review umana → pubblicazione
- Meta SEO scritti insieme all'articolo, non dopo
