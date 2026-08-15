# Piano editoriale social

## Impostazione

1. **Canali in base al target**, non per moda: B2B → LinkedIn (+X per tech);
   B2C locale → Instagram/Facebook; prodotto visuale → Instagram/TikTok;
   e-commerce → Instagram/Facebook (+ cataloghi prodotto)
2. **Pilastri di contenuto** (riusare quelli del blog): educativo, dimostrativo
   (casi/risultati), dietro le quinte, promozionale. Regola 80/20:
   80% valore, 20% promozione
3. **Frequenza sostenibile** per canale; la costanza batte il volume

## Repurposing blog → social (moltiplicatore)

Ogni articolo del blog genera almeno:
- 1 post LinkedIn (tesi + 3 punti chiave + link)
- 1 carosello (i punti dell'articolo in 5-8 slide)
- 2-3 post brevi (una statistica/citazione/takeaway ciascuno)
- 1 idea reel/short (la domanda a cui l'articolo risponde, in 30")

## Pipeline asset (collaudata)

**Organizzazione cartelle** (convenzione 2026-06): in `<workspace>/files/social/`
una cartella per **settimana di pubblicazione**, nominata con la data del
lunedì: `<YYYY-MM-DD>-<slug-tema>/` (es. `2026-06-15-scarichi-fosse/`).
Dentro: README di settimana (calendario + copy definitivi) e `media-urls.json`
al livello radice; immagini piatte se il tema è unico, altrimenti
sottocartelle per origine (`blog-<slug>/` per asset da articolo,
`prodotto-<slug>/` per asset di prodotto). Per ogni cartella settimana:
- **Visual hero** → generazione AI (Higgsfield), fotografico, SENZA testo
  (il testo nelle immagini AI è inaffidabile); scaricare in locale
- **Caroselli informativi** → generazione PROGRAMMATICA (PIL): testo perfetto,
  colori brand esatti, layout coerente. 1080×1350. Stesse slide → PNG per
  Instagram + PDF per LinkedIn (i caroselli LinkedIn sono documenti PDF).
  **Gotcha collaudati**: (1) mettere nel generatore una GUARDIA anti-overflow
  (limite y sopra footer/“Scorri” → errore se il testo sborda: mai fidarsi
  dell'occhio sulle slide lunghe); (2) niente glifi speciali nel testo
  (✓ ✗ ecc. in Arial = quadratino vuoto) → icone DISEGNATE (cerchio+polilinea);
  (3) verificare OGNI slide a video prima di mostrare/pubblicare
- **Brand del sito: si estrae UNA volta sola** → alla prima produzione visual,
  salvare palette/stile/regole in `<workspace>/data/BRAND.md` (hex, gradienti,
  layout card collaudato, font, vincoli tipo "mai prezzi"). Le volte successive
  si LEGGE da lì, senza rifare l'analisi del CSS. Aggiornarlo se il sito cambia
  o se un nuovo kit consolida scelte nuove.
- **Reel statico (text-slideshow)** → spesso preferibile al b-roll AI (look
  "stock sintetico"): 6-8 card 1080×1920 programmatiche (PIL), una frase per
  card, testo in safe zone centrale; montaggio ffmpeg (concat: hook ~2",
  card ~1,7", CTA ~2,5", totale ≤16") ESPORTATO MUTO — la musica trend si
  aggiunge dall'app in pubblicazione (diritti + reach). Gotcha: il file
  concat di ffmpeg va nella stessa cartella delle immagini (path relativi)
- Gotcha video AI: i filtri contenuti danno falsi positivi su scene innocue
  (es. close-up di lavandini/scarichi su Seedance) — riformulare o cambiare
  scena; i blocchi in genere non vengono addebitati (verificare col balance)
- **README.md** nella cartella: copy per canale + formato + orari consigliati
  + istruzioni (link nel primo commento su LinkedIn, link in bio su IG, UTM)
- **Pubblicazione**: se il progetto ha i tool `meta_*` (Graph API con token
  di sistema): `meta_publish_fb` (anche programmato) e `meta_publish_ig`
  (post/carosello; immagini da URL pubblici → caricarle prima su WP media).
  SEMPRE con approvazione esplicita dell'utente, mai d'iniziativa.
  IG non programma via API; LinkedIn resta manuale (PDF carosello)
- ⚠️ **Gotcha IG = JPEG, non PNG**: Instagram via Graph API rifiuta spesso i
  PNG con `Only photo or video can be accepted as media type` (anche se l'URL
  è 200 e `image/png` valido). Facebook invece accetta i PNG. → Per i caroselli
  IG **caricare le slide in JPEG** (convertire con `sips -s format jpeg`,
  qualità ~92 per non sgranare il testo). Conviene generare/caricare già in
  JPEG per IG fin dall'inizio
- **Album FB multi-foto** (il "carosello" su Facebook): caricare ogni foto con
  `published=false` su `/{page}/photos`, poi post su `/{page}/feed` con
  `attached_media[i]={"media_fbid": id}` + caption e link
- A pubblicazione avvenuta, TRE registrazioni: (1) intervento a diario (tipo
  `social`); (2) riga nella tabella **`social_posts`** del DB (post_id Graph,
  permalink, campagna=utm_campaign, product_ids spinti, plan_id) — alimenta la
  dashboard (tab Piano: insight live, click UTM, vendite per campagna);
  (3) flag `social_done` nel piano editoriale

## Misurare i post (dopo 2+ giorni)

- **Insight via Graph API**: media IG → `like_count,comments_count` +
  `/insights metric=reach,views,saved,shares,total_interactions`; post FB →
  `reactions.summary(true),comments.summary(true),shares` (la reach post-level
  FB spesso non è esposta)
- **Click reali**: GA4 `sessionCampaignName` = lo utm_campaign del kit
- **Vendite**: prodotti spinti × vendite shop dal primo post in poi —
  correlazione, non attribuzione: dirlo sempre
- Aspettativa onesta su account piccoli (<500 follower): reach organica di
  poche decine per post; all'inizio il valore è validare il flusso e costruire
  costanza, non i numeri del singolo post

**Variante e-commerce (kit prodotto):**
- Il prodotto è SEMPRE quello vero: packshot dallo shop (WooCommerce API,
  campo `images`), MAI generare packaging/etichette con l'AI
- Scena lifestyle: generazione AI col packshot come reference image
  (Higgsfield `medias role:"image"`), prompt che impone etichetta identica;
  verificare sempre l'etichetta nel risultato prima di pubblicare
- Card prodotto programmatica: packshot + 3 benefici + CTA, colori brand;
  niente prezzi in card se lo shop espone prezzi netto IVA
- Priorità prodotti: i top seller (dashboard → tab Shop), dove la domanda
  è già dimostrata

## Copy per social

- **Hook nella prima riga** (la sola visibile prima del "vedi altro")
- Un'idea per post, non tre
- CTA esplicita ma proporzionata al funnel (commenta/salva → scopri → contattaci)
- Hashtag: pochi e pertinenti (3-5), mix ampi+nicchia
- Adattare il tono al canale mantenendo la voce del brand (definirla nel
  CLAUDE.md del progetto)

## Metriche che contano

- Copertura e crescita follower = vanity, da contestualizzare
- Salvataggi, condivisioni, commenti = segnali veri di valore
- Click al sito e lead = obiettivo finale (UTM sempre sui link)
- Rivedere il piano ogni mese sui dati, non sulle impressioni
