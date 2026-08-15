# Analisi Google Search Console

## Setup e tool

- **Credenziali: preferire OAuth utente** (token condiviso con Analytics e Ads,
  rigenerabile con lo script di auth del progetto): eredita TUTTE le proprietà
  dell'account Google senza configurazione per-proprietà. Il service account è
  l'alternativa, ma Search Console a volte rifiuta l'email del service account
  ("utente non trovato") — verificato sul campo: in quel caso OAuth è l'unica via.
- `gsc_list_properties` — primo check: proprietà accessibili e permessi
- `gsc_query` — dati di ricerca: click, impression, CTR %, posizione media
- Identificatore proprietà: `sc-domain:dominio.it` (proprietà Dominio) oppure
  `https://dominio.it/` (proprietà Prefisso URL). Registrare quello giusto nel
  CLAUDE.md del progetto dopo il primo `gsc_list_properties`.
- I dati GSC arrivano con **~2 giorni di ritardo**: end_date = oggi-2.
- Confronti periodo su periodo: stessi giorni della settimana (28gg vs 28gg).

## Analisi standard (in ordine di valore)

**1. Quick win — posizioni 8-20**
Query con impression alte e posizione 8-20: già rilevanti per Google, manca
poco al salto di pagina. `dimensions=["query","page"]`, filtrare righe con
8 ≤ position ≤ 20 e impressions sopra soglia. Azione: rafforzare la pagina
(contenuto, internal link, meta) sulla query specifica.

**2. CTR sotto la media a parità di posizione**
Query in top 10 con CTR basso rispetto all'atteso (~pos1 >25%, pos3 ~10%,
pos5 ~6%, pos10 ~2.5%): il problema è il copy in SERP. Azione: riscrivere
meta title/description su quella query (workflow in seo-onsite.md).

**3. Cannibalizzazione reale**
`dimensions=["query","page"]`: query con 2+ pagine che si alternano in SERP
con posizioni simili. Azione: differenziare intenti o consolidare (vedi
seo-onsite.md → Cannibalizzazione).

**4. Trend e cali**
`dimensions=["date"]` per il trend complessivo; pagine in calo: confronto due
periodi con `dimensions=["page"]`. Azione: refresh contenuti che calano.

**5. Opportunità di contenuto nuovo**
Query con impression e zero/pochi click dove il sito non ha una pagina
dedicata: input diretto per il piano editoriale (editorial.md).

## Pattern di chiamata utili

- Panoramica 28gg: `dimensions=["query"]`, row_limit alto, poi ordinare per impressions
- Performance di una pagina: `page_filter="/slug/"`, `dimensions=["query"]`
- Brand vs non-brand: due query con `query_filter` (nome brand) e confronto
- Mobile vs desktop: `dimensions=["device"]`
- Baseline post-intervento SEO: salvare i numeri PRIMA (click, impression,
  CTR, posizione per le pagine toccate) e confrontare dopo 4-6 settimane

## Errori comuni

- 403 "User does not have sufficient permission": l'account (OAuth o service
  account) non ha accesso a QUELLA proprietà, o si usa l'identificatore
  sbagliato: sc-domain vs https:// (possono esistere entrambe e una essere vuota)
- Proprietà giusta ma zero righe: range date troppo recente (ritardo dati)
  o search_type sbagliato
