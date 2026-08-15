# Operare su SwerpiCommerce

Come gestire pagine, prodotti e SEO di un sito **SwerpiCommerce** — a differenza
di WordPress qui **non ci sono tool MCP** per il CMS: si opera via la **CLI
`swerpicommerce-pp-cli`** in Bash (129 operazioni generate dall'OpenAPI). Gli
analytics (GA/GSC) e i social (Meta) restano identici al backend WordPress: quelli
sono tool MCP `anja_marketing` e valgono per ogni brand.

## Credenziali: dove stanno

Come per WordPress, le credenziali NON sono in un `.env` unico: ogni brand ha le sue
in `<workspace>/.anjawiki/.secrets.env`:

```
SWERPICOMMERCE_BASE_URL=https://<tenant>/api/v2
SWERPICOMMERCE_API_ID=<api-id>
SWERPICOMMERCE_API_SECRET=<api-secret>
SWERPICOMMERCE_BEARER_AUTH=<bearer-token>   # opzionale: se manca, generalo (sotto)
```

La CLI legge dall'ambiente SOLO `SWERPICOMMERCE_BASE_URL` e
`SWERPICOMMERCE_BEARER_AUTH`; `API_ID`/`API_SECRET` servono a generare il bearer
la prima volta. Sourcerà le variabili dal vault del brand (non stampare mai i
segreti):

```bash
set -a; . "$WORKSPACE/.anjawiki/.secrets.env"; set +a
# se SWERPICOMMERCE_BEARER_AUTH non è ancora impostato:
swerpicommerce-pp-cli swerpicommerce-auth token \
  --api-id "$SWERPICOMMERCE_API_ID" --api-secret "$SWERPICOMMERCE_API_SECRET" --agent
# il token è in .data.data.token (NON scade, revocabile con token-revoke <id>);
# salvalo nel campo "Bearer token" dei Connettori del workspace, poi ri-applica
# al runtime. Verifica senza effetti collaterali:
swerpicommerce-pp-cli swerpicommerce-auth me --agent
```

Se `swerpicommerce-pp-cli` non è nel PATH è la CLI del repo
`la CLI SwerpiCommerce installata sull'host` (installazione una-tantum
sull'host). `doctor` mostra base URL attiva e stato auth: usalo a inizio sessione.
Nota: il body del token vuole `api_id`, non `api_key`; molti comandi-risorsa non
compaiono in `--help` ma funzionano (elenco completo: `swerpicommerce-pp-cli api`).

## Scoprire i comandi

Non memorizzare le 129 operazioni: la CLI si autodescrive. Parti sempre da:

```bash
swerpicommerce-pp-cli --help              # gruppi di comandi (pages, products, media, seo, orders…)
swerpicommerce-pp-cli products --help     # operazioni di un gruppo
swerpicommerce-pp-cli products get --help # parametri di una singola operazione
```

Output in JSON: aggiungi `--json` (o l'equivalente) e processa con `jq` per estrarre
solo i campi che servono, così non saturi il contesto.

## Regole di sicurezza (identiche a WordPress, applicate alla CLI)

- **Bozza sempre**: pagine/prodotti nuovi o modificati vanno creati/aggiornati in
  stato bozza; il passaggio a `published`/`live` solo con ok esplicito dell'utente.
- **Slug mai toccato** su contenuti esistenti (rompe i link e la SEO).
- **Leggi prima di scrivere**: recupera lo stato attuale (`... get <id>`) prima di un
  update — un update può sostituire l'intero corpo, come `wp_update_content`.
- **Read-back**: dopo ogni scrittura rileggi dalla CLI e confronta campo per campo,
  poi riporta l'esito reale (ID, stato, URL).
- **Mai eliminare senza conferma** esplicita nella conversazione corrente.
- **SWCSS / design compile**: per le pagine vetrina SwerpiCommerce usa SWCSS + il
  passo di `design compile` della CLI; genera in una pagina/bozza separata, mostra la
  preview e travasa nel target solo dopo l'ok.

## Workflow batch

Come per WordPress, per operazioni di massa i comandi uno-a-uno sono inefficienti:

1. **Censimento** — `... list` con filtri → elenco con stato meta (chi ha
   title/description e chi no).
2. **Estrazione** — titolo + incipit del contenuto reale: i meta si scrivono dal
   contenuto, mai da template.
3. **Proposte** — file JSON nel workspace (`files/proposals/…`): `{id: {title,
   description, target_keywords}}`, con validazione automatica delle lunghezze PRIMA
   di mostrarle.
4. **Approvazione** — sintesi per tema + esempi + percorso file; `AskUserQuestion`.
5. **Applicazione** — loop `swerpicommerce-pp-cli ... update <id> ...` in bozza, con
   storico nel JSON e read-back finale.

## Cosa resta MCP (backend-agnostico)

Non passano dalla CLI, valgono uguali: `marketing_status`, `audit_products` /
`audit_content` (scoring SEO/E-E-A-T/GEO), analytics GA/GSC, e i tool social
(`meta_*`, `social_kit_build`). Usa quelli per l'analisi e l'audit; la CLI solo per
leggere/scrivere sul CMS SwerpiCommerce.
