# Gestione siti del progetto (onboarding, registro, offboarding)

Procedura per collegare un sito nuovo alla piattaforma e tenerne il registro.
Vale per qualsiasi progetto che usa la struttura `siti/<dominio>/`
(template: `siti/sito-demo/` · guida credenziali: `SETUP.md` in root).

## Onboarding di un sito nuovo ("aggiungi questo sito")

Quando l'utente chiede di aggiungere un sito, eseguire in ordine:

1. **Cartella dal template**: copiare `siti/sito-demo/` → `siti/<dominio>/`
   (PIANO.md scheletro con il dominio compilato, `proposals/meta/`, `reports/`,
   `social/`, `.env.example` → rinominato `.env` vuoto da compilare).
2. **Credenziali all'utente**: indicare COSA serve e DOVE va (sezioni di
   SETUP.md pertinenti: WordPress sempre; Meta/Ads solo se li userà).
   **Mai chiedere di incollare segreti in chat**: l'utente li scrive
   direttamente nel `.env`. Le chiavi Google sono per-account (di solito già
   configurate): per un sito nuovo servono solo proprietà GSC e property GA4.
3. **Verifica connessioni** (appena l'utente dice "fatto"):
   `check_connection.py <dominio>` (WP) · `check_meta.py <dominio>` (se Meta,
   stampa PAGE_ID/IG_USER_ID da aggiungere al .env) · `gsc_list_properties` /
   `ga_list_properties` per trovare proprietà e property id.
4. **Rilevazione stack** (da annotare nel registro, vedi sotto): plugin SEO
   attivo (guardare i **namespace REST**, non le capabilities), WooCommerce,
   page builder (rischio "pagina v2", vedi wordpress.md), cache REST, CPT,
   pagine noindex. Alla prima produzione visual: estrarre e salvare il brand
   in `siti/<dominio>/BRAND.md` (vedi social.md) — mai ri-analizzarlo dopo.
5. **Registrazione nel DB** (per collector e dashboard):
   `INSERT OR REPLACE INTO sites (site, gsc_property, ga_property_id, notes)`.
   ⚠️ `gsc_property`: verificare la FORMA giusta (`https://dominio.it/` vs
   `sc-domain:` — possono esistere entrambe e una essere vuota).
6. **Primo popolamento**: `collect_metrics.py --site <dominio> --full`
   (+ `--content`, + `--shop` se WooCommerce).
7. **Conoscenza di dominio** (rende l'agente "esperto del sito"):
   a) generare gli indici `siti/<dominio>/catalogo/` (un file per tipo:
      prodotti/pagine/articoli) con
      `uv run python scripts/gen_catalogo.py --site <dominio>` (richiede il
      `--content` del punto 6; rigenerarli quando il catalogo cambia);
   b) compilare la prima bozza di `siti/<dominio>/ESPERTO.md` dal template
      `sito-demo/ESPERTO.md`, ricavando linee/servizi e clienti tipo dai
      contenuti WP e il linguaggio dalle query GSC; le sezioni segnate
      ⬜ TODO (fatti aziendali, compliance, tone of voice) si validano con
      l'utente — mai inventarle.
8. **Registro aggiornato**: riga/blocco del sito in `siti/SITI.md` (vedi sotto)
   + contesto in `siti/<dominio>/CLAUDE.md` (regole operative e deroghe).
9. **Baseline**: salvare i numeri di partenza GSC/GA in
   `siti/<dominio>/reports/` (saranno il confronto di ogni lavoro futuro).

Output finale all'utente: riepilogo di cosa è attivo, cosa manca (es. Meta non
configurato) e proposta del primo passo di lavoro (audit, seo-onsite.md).

## Il registro: `siti/SITI.md`

Indice dei siti collegati, un blocco per sito. NON duplica PIANO.md (operatività)
né CLAUDE.md (regole): tiene **identificativi + particolarità da ricordare**.

Formato del blocco:

```markdown
## <dominio> — <tipo: vetrina | blog | e-commerce> · <stato: attivo | in onboarding | archiviato>
- **WP**: utente API, plugin SEO, builder/CPT rilevanti
- **GSC**: proprietà (forma esatta) · **GA4**: property id
- **Social/Ads**: pagina FB, IG, ad account (e in quale business vivono)
- **Da ricordare**: 2-5 punti che evitano errori (deroghe, gotcha specifici,
  decisioni dell'utente sul sito)
- **Operatività**: → `siti/<dominio>/PIANO.md`
```

**Regola di manutenzione**: ogni volta che si scopre una particolarità nuova
del sito (un gotcha, una decisione, un vincolo) → aggiornare "Da ricordare"
nel registro se è durevole, o PIANO.md se è operativa. Il registro si rilegge
all'inizio di ogni sessione di lavoro su un sito.

## Offboarding / archivio

- NON cancellare la cartella `siti/<dominio>/` né le righe del DB (lo storico
  delle metriche e dei lavori resta consultabile).
- Segnare lo stato `archiviato` nel registro, revocare le Application Password
  e i token lato servizi (WP, Meta, Google) — la revoca la fa l'utente.
