# Advertising (Google Ads + Meta Ads)

Metodo per gestire campagne a pagamento. Vale per qualsiasi account; i dati
degli account specifici stanno nel CLAUDE.md del progetto.

## Principi non negoziabili

1. **Mai modificare una campagna attiva senza approvazione esplicita**
   dell'utente nella conversazione corrente (è denaro reale). Vale anche per
   pause/riattivazioni. Creazione in bozza/pausa quando la piattaforma lo consente.
2. **Niente spesa senza conversioni tracciate**: prima key events/pixel
   (vedi misurazione), poi campagne. Una campagna non misurata è un costo cieco.
3. **Ogni modifica a diario** (`interventions`, tipo `ads`) → marker in
   dashboard → si legge l'effetto nelle settimane successive.
4. **Proposte sempre con i numeri**: mai "consiglio di aumentare il budget",
   sempre "campagna X: ROAS 1,9 vs target 2,5 → propongo Y perché Z".
5. Budget di test limitati e dichiarati; soglie di allarme condivise prima.

## Audit di un account (checklist)

Ordine di lavoro: prima i soldi buttati, poi le ottimizzazioni.

0. **PRIMA DI TUTTO: validare il tracking contro la fonte di verità.**
   Confrontare le entrate analytics con gli ordini reali (e-commerce: ordini
   piattaforma, mese per mese). Copertura ≥85% = analisi affidabili;
   60-85% = solo direzionali (trend sì, valori assoluti no); <60% = sistemare
   la misurazione prima di analizzare. Nei B2B controllare la quota di
   ordini manuali/admin (`created_via` in WooCommerce): sono invisibili
   all'analytics E estranei alle campagne — escluderli dal confronto.

1. **Campagne zombie**: spesa con entrate ~zero su periodi lunghi → tagliare
   subito. Guardare TUTTA la storia, non solo l'ultimo mese.
2. **ROAS per campagna nel tempo** (mensile, a barre): trend > fotografia.
   Una campagna in apprendimento che migliora ≠ una stagnante allo stesso ROAS.
3. **Anomalie mensili** (crolli/picchi isolati): incrociare con diario,
   stagionalità, problemi sito/stock/tracking prima di toccare le campagne.
4. **Breakeven reale**: chiedere SEMPRE il margine lordo medio.
   `ROAS di pareggio = 1 / margine` (margine 40% → breakeven 2,5).
   Un ROAS sopra 1 può essere comunque in perdita — dirlo chiaramente.
5. **Ripetizione d'acquisto** (B2B/consumabili): se i clienti riordinano,
   valutare su LTV non sul primo ordine; verificare new vs returning in GA4.
6. **Termini di ricerca** (richiede API piattaforma): sprechi su query
   irrilevanti → negative. Su PMax visibilità limitata: usare insights/temi.
7. **Spesa per prodotto** (e-commerce): pausare nel feed i prodotti in
   perdita cronica, spingere i best seller (incrociare con dati ordini).
8. **Brand vs non-brand**: il brand convertirebbe comunque? Attenzione
   all'autocannibalizzazione delle Search brand.

## Architetture di riferimento

**Google**
- Search brand (presidio economico) / Search non-brand per intenti caldi
- Performance Max per e-commerce con feed (richiede mesi di apprendimento
  e conversioni: non giudicarla nelle prime 4-6 settimane)
- Shopping standard solo se serve controllo che PMax non dà

**Meta**
- Funnel: prospecting (interessi/lookalike) → retargeting (pixel: visitatori,
  carrelli abbandonati) → DPA con catalogo per e-commerce
- Struttura: poche campagne CBO, adset per audience, 2-3 creatività in test
- Le creatività si producono con la pipeline di `social.md` (visual AI +
  card programmatiche); UTM su tutti i link

**Naming**: `[obiettivo]-[tipo]-[target/tema]-[n]` — i nomi sono i dati di domani.

## Workflow operativo

```
1. ANALISI    dati DB/API → findings con numeri
2. PROPOSTA   elenco interventi: azione, motivo, impatto atteso, rischio
3. APPROVAZIONE  esplicita dell'utente, intervento per intervento
4. APPLICAZIONE  via API/MCP, una modifica alla volta, verificata
5. DIARIO     ogni modifica registrata con data
6. MISURA     review a 2-4 settimane (mai giudicare prima, salvo emorragie)
```

## Metriche oneste

- **ROAS lordo** (entrate/spesa) è vanity senza margine: convertirlo sempre
  in **contributo netto** = entrate × margine − spesa
- **CPA** per lead-gen: costo per lead E tasso lead→cliente (chiedere)
- **Incrementalità**: quota di conversioni che sarebbero arrivate comunque
  (brand, retargeting aggressivo). Non sempre misurabile: almeno nominarla.
- Entrate attribuite da GA4 ≠ entrate riportate dalla piattaforma ads
  (attribuzione diversa): scegliere UNA fonte per i confronti nel tempo.

## Strumenti del progetto

| Cosa | Come | Stato |
|---|---|---|
| Reporting Google per campagna | DB `ga_ads_daily` (link Ads→GA4) | ✅ |
| Query libere (campagne, prodotti, termini) | Google Ads API GAQL (`ads_client.search`) | ✅ Basic Access attivo |
| Scrittura Google guardata (pausa/budget) | `ads_client.set_campaign_status/budget` | ✅ dry-run di default |
| Dashboard: tab Ads (ROAS mensile + breakeven, prodotti win/bleed) | `dashboard/live.py` | ✅ |
| Gestione campagne Meta | Connettore MCP `ads_*` o token Ads per sito (`META_ADS_TOKEN`) | ✅ con approvazione |
| Audience/pixel Meta | Pixel di progetto + connettore | ✅ |
| Creatività | Pipeline social.md (Higgsfield + PIL) | ✅ |

## Google Ads API — note operative (collaudate)

- **Livelli di accesso**: Test/Basic/Standard regolano *quota e account
  raggiungibili*, NON lettura vs scrittura — il Basic scrive già (15k op/giorno).
  Lo Standard serve solo per volumi da piattaforma multi-cliente.
- **Auth**: stesso token OAuth di GSC/GA4 (scope `adwords`) + developer token e
  mappa `customers {sito: id}` in `credentials/google-ads.json`.
- **GAQL, gotcha date**: `DURING` accetta solo preset brevi (`LAST_30_DAYS`,
  `THIS_MONTH`...). Per 90gg/12 mesi usare `BETWEEN 'YYYY-MM-DD' AND 'YYYY-MM-DD'`
  (errore tipico: `INVALID_VALUE_WITH_DURING_OPERATOR`).
- **Scrittura = denaro reale**: i metodi mutate hanno `dry_run=True` di default
  (`validateOnly`: l'API valida senza applicare). Applicare solo con ok esplicito
  → `dry_run=False`, e registrare a diario. Mai modifiche d'iniziativa.
- **Esclusioni feed per VARIANTE, non per prodotto**: la stessa referenza può
  perdere in una taglia e vincere in un'altra (es. 5L ROAS 0, 25L ROAS 5+).
  Le liste escludi/spingi vanno costruite a livello di variante.
- **Conversioni Ads ≠ ordini**: confrontare le conversioni dichiarate con gli
  ordini reali del periodo (es. 45 conv vs 25 ordini totali = sovra-conteggio
  → azioni di conversione secondarie contate come primarie). Il ROAS Ads è
  quindi un tetto massimo, non il valore vero.

## Gotcha imparati sul campo

- **PMax parte male sempre**: ROAS ~0,3 nei primi 2 mesi è normale
  apprendimento; giudicare dal mese 3-4 in poi sul trend.
- **Campagne zombie silenziose**: Shopping/Search lasciate accese con spesa
  bassa "che non disturba" bruciano migliaia di euro in un anno. Audit
  storico completo, sempre.
- Giugno/mesi parziali: mai confrontare un mese in corso con mesi interi
  senza dirlo.
