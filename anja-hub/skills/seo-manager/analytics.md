# Analisi Google Analytics 4

## Setup e tool

- Stessa autenticazione di Search Console (token OAuth condiviso)
- `ga_list_properties` — account e property_id numerici accessibili
- `ga_report` — report con dimensioni e metriche GA4
- Il **property_id è numerico** (non l'URL): registrare quello di ogni sito
  nel CLAUDE.md del progetto dopo il primo ga_list_properties
- Date relative supportate: `28daysAgo`, `yesterday` (consigliato come end:
  i dati del giorno corrente sono parziali)

## Dimensioni e metriche più utili

| Obiettivo | dimensions | metrics |
|---|---|---|
| Panoramica canali | sessionDefaultChannelGroup | sessions, activeUsers, keyEvents |
| Sorgenti dettagliate | sessionSourceMedium | sessions, keyEvents |
| Pagine che convertono | landingPage | sessions, keyEvents, bounceRate |
| Contenuti più visti | pagePath | screenPageViews, activeUsers |
| Trend | date | sessions, activeUsers |
| Dispositivi | deviceCategory | sessions, bounceRate |
| E-commerce | itemName / sessionDefaultChannelGroup | purchaseRevenue, totalRevenue, transactions |

## Analisi standard

**1. Mix dei canali** — quota organico vs diretto vs social vs referral:
misura la dipendenza da un canale e l'effetto del lavoro SEO nel tempo.

**2. Landing organiche che (non) convertono** — incrocio con GSC: la pagina
porta traffico (GSC) ma converte? (`landingPage` + keyEvents). Se traffico sì
e conversioni no → problema di CTA/contenuto, non di SEO.

**3. Qualità del traffico per sorgente** — bounceRate e durata per
sessionSourceMedium: dove investire (contenuti, social, ads).

**4. E-commerce** — entrate per canale e per prodotto; carrelli vs acquisti.
Fondamentale come baseline PRIMA di attivare advertising.

**5. Baseline e confronti** — salvare i numeri in `<workspace>/files/reports/` come per GSC;
confronti su periodi omogenei (28gg vs 28gg precedenti, stessi giorni
della settimana).

**6. Validazione copertura (e-commerce)** — PRIMA di analisi profonde,
confrontare le transazioni/entrate GA4 con gli ordini reali della piattaforma,
mese per mese: la copertura può variare molto (visto sul campo: dal 48% al
100%). Soglie e conseguenze in ads.md (checklist audit, punto 0). Escludere
dal confronto gli ordini manuali/admin (`created_via` in WooCommerce).

## Incroci GSC × GA4 (il vero valore)

- GSC = domanda e visibilità (query, impression, CTR) · GA4 = comportamento
  e risultato (sessioni, conversioni)
- Pagina con tante impression GSC e poche sessioni GA → problema CTR (meta)
- Pagina con sessioni alte e keyEvents zero → problema conversione (CTA, UX)
- Query commerciali in crescita su GSC → pagine da potenziare e misurare in GA

## Errori comuni

- 403 "API has not been used/disabled": abilitare Analytics **Data** API e
  Analytics **Admin** API nel progetto Cloud delle credenziali
- 403 sulla proprietà: l'account OAuth non ha accesso a quella proprietà GA
- Property ID sbagliato: usare l'ID numerico GA4, non "UA-..." (Universal
  Analytics, dismesso) né l'URL del sito
