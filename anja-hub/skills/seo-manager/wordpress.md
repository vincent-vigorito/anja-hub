# Operare su WordPress

Come gestire contenuti e SEO di un sito WordPress via REST API
(Application Password + eventuale plugin SEO con API, es. SEOPress PRO).

## Multi-sito: dove stanno le credenziali

Le credenziali NON stanno in un .env unico: ogni sito ha la sua cartella
`<workspace>/.anjawiki/.secrets.env` nella radice del progetto (es. `<workspace>/.anjawiki/.secrets.env`)
con `WP_BASE_URL`, `WP_USERNAME`, `WP_APP_PASSWORD`.

- `wp_list_sites` — elenca i siti configurati, stato config e sito attivo
- `wp_use_site(site)` — seleziona il sito su cui operare (verifica la connessione)
- Senza selezione esplicita: vale `WP_SITE` (env) oppure l'unico sito con
  config completa; con più siti completi la selezione è OBBLIGATORIA
- Negli script batch: `load_config("<dominio>")` per puntare il sito giusto

**Regola di sicurezza multi-sito**: prima di QUALSIASI scrittura (singola o
batch) verifica e dichiara all'utente su quale sito stai operando
(`wp_list_sites` o `wp_site_info`). Mai assumere il sito implicitamente quando
ne esiste più di uno.

## Tool MCP disponibili (server `wordpress`)

| Tool | Uso | Note |
|---|---|---|
| `wp_list_sites` / `wp_use_site` | Multi-sito: elenco e selezione | Vedi sezione multi-sito sopra |
| `wp_site_info` | Verifica connessione, info sito e utente | Primo check di ogni sessione di lavoro sul sito |
| `wp_list_content` | Elenco articoli/pagine con filtri | Campi sintetici, niente contenuto completo |
| `wp_get_content` | Singolo contenuto con HTML completo | SEMPRE prima di modificare |
| `wp_create_content` | Crea articolo/pagina | Default `status=draft` — pubblicare solo su richiesta esplicita |
| `wp_update_content` | Aggiorna campi | `content` sostituisce TUTTO il corpo: partire dall'HTML attuale |
| `wp_delete_content` | Cestina/elimina | Solo con conferma esplicita; default cestino (`force=false`) |
| `wp_get_seo` / `wp_set_seo` | Meta SEO (title, desc, keywords, robots, canonical, social) | `wp_set_seo` rilegge e restituisce lo stato salvato |
| `wp_list_terms` / `wp_create_term` | Categorie e tag | Per gli ID da assegnare ai contenuti |

## Regole di sicurezza

- Nuovi contenuti sempre **bozza** salvo richiesta esplicita di pubblicare
- **Leggi prima di modificare**: mai `wp_update_content` con `content` senza
  aver recuperato l'HTML attuale
- Mai eliminare senza conferma esplicita nella conversazione corrente
- Dopo ogni scrittura: riepilogo con ID, stato e link
- Operazioni SEO meta non toccano mai il campo `content`

## Workflow batch (collaudato su 60 articoli + 23 pagine reali)

Per operazioni di massa i tool MCP uno-a-uno sono inefficienti: usare il client
Python del progetto (`WordPressClient`, metodo `seo_apply`) via script.

1. **Censimento** — elenca tutto con stato meta: quali contenuti hanno
   title/description e quali no (publish + draft)
2. **Estrazione** — titolo + incipit (~300 char, HTML strippato) di ogni
   contenuto da sistemare: i meta si scrivono dal contenuto reale
3. **Proposte** — file JSON nel progetto: `{id: {title, description,
   target_keywords}}` + campo `page`/`note` per leggibilità
4. **Validazione automatica** — script che controlla i range di lunghezza
   PRIMA di mostrare le proposte all'utente
5. **Approvazione** — presentare sintesi per tema + esempi + percorso del file;
   AskUserQuestion con opzioni chiare
6. **Apply con read-back** — per ogni contenuto applica e verifica campo per
   campo; in caso di mismatch segnala senza proseguire ciecamente
7. **Report** — conteggio OK/falliti + promemoria cache (vedi sotto)

## Gotcha noti (imparati sul campo)

- **Cache REST**: plugin come LiteSpeed Cache possono cachare le risposte
  REST come pubbliche → riletture post-scrittura stantie. Soluzione: parametro
  cache-buster su ogni GET (il client lo fa di default) e consigliare di
  disattivare la cache REST. Dopo batch grossi: suggerire Purge All.
- **SEOPress `target-keywords`**: il PUT vuole il param
  `_seopress_analysis_target_kw` come stringa CSV (non documentato — verificato
  dal sorgente del plugin). Già gestito dal client.
- **SEOPress endpoint utili non ancora integrati**: content-analysis,
  redirection, schemas (manual/automatic), Google News, generate-metas-by-ai.
- **Pagine WooCommerce** (carrello, checkout, account): tipicamente noindex —
  verificare prima di proporre meta (inutili se noindex).
- **Title template**: se il meta title è vuoto, il plugin SEO usa il template
  globale (es. `%%title%% - %%sitetitle%%`) — "manca il title" non significa
  pagina senza title, ma title non curato.
- **Capabilities residue**: plugin disattivati lasciano capabilities nel DB
  (es. Rank Math) — per capire il plugin SEO attivo guardare i namespace REST,
  non le capabilities.
- **Verbo DELETE bloccato**: alcuni siti (Cloudflare/WAF) rispondono 520 al
  metodo HTTP DELETE pur accettando GET/POST. Soluzione: POST con header
  `X-HTTP-Method-Override: DELETE` (il client lo fa in automatico come fallback).
- **Refresh di articoli pubblicati**: mai lavorare sul vivo — bozza di lavoro
  separata (titolo prefissato es. "[BOZZA REFRESH id]"), review, poi travaso
  del SOLO campo content nell'articolo originale (slug e titolo invariati)
  e cestino della bozza di lavoro.
- **Pagine costruite con page builder** (Stackable & co.): MAI modificarne il
  content via API (si rompe il layout); spesso duplicano i blocchi
  DESKTOP/MOBILE nel DOM → doppi H1 e heading segnaposto visibili ai crawler.
  **Metodo "pagina v2"**: ricostruire la pagina come bozza separata in blocchi
  Gutenberg CORE con la struttura corretta; l'utente travasa i blocchi
  nell'originale dall'editor (slug/meta intatti). Per il look: riusare le
  classi del tema (es. `gradient-text`), gruppi `alignfull` con sfondo +
  testo da palette core, kicker uppercase via style typography, font-size
  custom (i temi con tipografia fluida li convertono in clamp da soli).
  Blocchi "invalidi" in editor → "Tenta il recupero" li sistema.
  Se la pagina ha una CTA/banner fisso sitewide, NON duplicare la CTA finale.
- **Testimonianze**: mai inventarle — placeholder [DA COMPLETARE] e richiesta
  all'utente di fornirne di reali (anti-pattern E-E-A-T inventarle).
- **Iframe strippati da wc/v3**: l'endpoint REST WooCommerce sanitizza il
  campo `description` e rimuove gli `<iframe>` (es. video YouTube), anche con
  utente `unfiltered_html`. Soluzione: URL nuda su riga propria (senza `<p>`)
  → WordPress la converte in embed al rendering (oEmbed). Il read-back deve
  contare anche gli iframe/URL per accorgersene.
