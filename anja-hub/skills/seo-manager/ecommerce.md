# SEO per e-commerce

## Workflow catalogo completo (collaudato su 99 prodotti WooCommerce)

Per ottimizzare un intero catalogo, due ondate in sequenza:

1. **Audit automatico** — script che per ogni prodotto raccoglie dati Woo +
   meta SEO + analisi testo (heading, tabelle, FAQ, dosaggi, link, bold ratio)
   + GSC per pagina/query + vendite, e calcola score per pilastro (SEO/EEAT/GEO)
   e priorità = potenziale × gap. Output JSON + report markdown ordinato.
2. **Ondata 1 (batch meccanici)** — meta title/description/short + brand nativo
   + SKU padre + alt, tutti dal contenuto reale, con validazione lunghezze.
3. **Ondata 2 (testi), 3 livelli per profondità/priorità**:
   - **A** riscrittura completa (come-funziona → settori d'impiego → tabelle
     dosaggi → FAQ dalle query → 2-3 link interni) per i top per priorità;
   - **B** ristrutturazione del contenuto esistente (heading veri, tabelle,
     de-grassettatura, FAQ, link) per la fascia media;
   - **C/coda** stessa struttura, prodotti a bassa domanda.
4. **Coppie/gemelli**: prodotti simili (versione spray vs liquida, food-grade
   FDA vs NSF, concentrato vs pronto uso) → FAQ "che differenza c'è" speculari
   con link reciproci. Costruisce rete di link interni e anti-cannibalizzazione.

Regole trasversali: lotti con file di proposta nella cartella del sito
(`<workspace>/files/proposals/`), backup di ogni originale, read-back che conta
h2/tabelle/link/img/video, diario interventi, score rimisurato a fine lotto. Gotcha: **wc/v3 strippa gli `<iframe>`** → per i
video usare l'URL YouTube nuda su riga propria (WordPress fa oEmbed).

## Testi categoria (≠ schede prodotto)

Endpoint: `wc/v3 PUT /products/categories/{id}`, campo `description`.
L'intento è **scegliere tra più prodotti**, non leggere un prodotto:

- **Priorità dai dati, non dai vuoti**: incrociare GSC (pagine `categoria-prodotto`)
  con la gerarchia. Spesso le categorie a 0 impression sono sottocategorie
  marginali (non vale riempirle); il valore è potenziare le poche categorie che
  ricevono già impression ma stanno in posizione mediocre. Per ognuna estrarre
  le query reali (page_filter) e scrivere keyword-led.
- **Lunghezza calibrata al tema**: il testo di solito esce **sopra la griglia**
  prodotti → ~130-180 parole, non muri da 350 (spingono i prodotti sotto la piega).
  Per contenuti SEO lunghi (FAQ, "come scegliere") usare un blocco **sotto** la
  griglia via hook `woocommerce_after_shop_loop` (snippet PHP nel tema).
- **Struttura**: claim d'apertura in `<p><strong>` + 2 paragrafi brevi + riga
  "Come scegliere" con 3-4 link ai bestseller + CTA breve. Grassetto parco (3-5).
- **GOTCHA term description**: WordPress **strippa h1-h6** dal campo description
  delle tassonomie (consentiti solo nei post) e **rimuove i wrapper `<p>`** nel
  raw (li riaggiunge `wpautop` in display). Quindi: niente `<h2>`, usare
  `<p><strong>`; nel read-back verificare `'<h2' not in saved` e i `<strong>`/link
  invece di contare i `<p>`. Link interni: usare il `permalink` reale dall'API.
- **GOTCHA meta SEO categoria (SEOPress)**: il *testo* (description) è scrivibile
  via `wc/v3`, ma i **meta title/description SEOPress delle term NON sono scrivibili
  via API**. L'endpoint `seopress/v1/terms/{id}` è **solo GET** (legge
  title/description/og/robots) e `wp/v2/product_cat` ha `meta:[]` (termmeta non
  registrati in REST: un POST risponde 200 ma non persiste). Soluzioni: applicarli
  a mano nel pannello SEOPress, oppure via WP-CLI
  (`wp term meta update <id> _seopress_titles_title|_seopress_titles_desc "..."`).
  Workflow: generare le proposte (title ≤62, desc 120-158) da query GSC e salvarle
  in `<workspace>/files/proposals/meta/` (JSON) + uno script `.sh` di comandi WP-CLI pronti.

## Schede prodotto

- **Title pattern**: [Prodotto] + attributo differenziante (+ brand se rientra)
  — mai solo il nome secco se ha varianti/modelli
- **Description meta**: beneficio + differenziatore + CTA ("Spedizione 24h",
  "Reso gratuito" se veri — i trust signal alzano il CTR)
- **Descrizione prodotto**: unica, mai copiata dal produttore (duplicate
  content); rispondere alle domande pre-acquisto
- **Schema Product** completo: prezzo, disponibilità, recensioni (AggregateRating)
  → rich snippet in SERP
- Immagini: alt descrittivi, nomi file parlanti

## Categorie = pagine SEO

Le categorie sono spesso le pagine più importanti per keyword commerciali
("scarpe running uomo" si vince con la categoria, non col prodotto):
- Testo introduttivo utile (150-300 parole) sopra o sotto la griglia
- Meta title con keyword commerciale + eventuale conteggio/beneficio
- Gerarchia pulita e breadcrumb (+ schema BreadcrumbList)

## Architettura e indicizzazione

- **Noindex**: carrello, checkout, account, ricerca interna, filtri combinati
- **Canonical**: varianti prodotto → prodotto principale; paginazione gestita
  con coerenza
- **Faceted navigation**: ogni combinazione di filtri indicizzabile è un rischio
  di crawl budget — indicizzare solo le facet con volume di ricerca reale
- Prodotti esauriti: 200 + alternativa se torna, 301 a categoria se permanente

## WooCommerce specifico

- Pagine utility (cart/checkout/account) noindex di default o via plugin SEO —
  verificare sempre lo stato reale prima di proporre meta
- API REST `wc/v3` per prodotti (integrazione futura: SEO schede via API)
- Feed Google Merchant per Shopping (quando si attiva l'advertising)

## Conversione (CRO minimo da presidiare)

- Trust signal visibili: recensioni, resi, pagamenti, contatti
- Velocità mobile delle pagine prodotto (la maggioranza del traffico)
- CTA coerenti dal contenuto informativo verso le pagine commerciali
