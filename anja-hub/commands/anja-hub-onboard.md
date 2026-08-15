---
description: Onboarding guidato dell'hub (crea/aggiorna profilo utente + agent principale)
argument-hint: [--hub <path>]
allowed-tools: Bash, AskUserQuestion
---

# /anja-hub-onboard

Conduce (o ri-esegue) l'**onboarding dell'hub** da Claude Code, senza passare dalla webapp:
crea il profilo utente, imposta il nome dell'agent principale, salva un breve profilo.

Equivalente conversazionale del wizard web `/onboarding`. Utile per re-runnare l'onboarding o
per configurare l'hub da terminale/Telegram.

Argomenti: `$ARGUMENTS`

## Workflow

### Step 1: Determina l'hub

Se `--hub <path>` è passato, usalo. Altrimenti usa la directory corrente se è un hub
(contiene `config.json` + `cross/`), oppure chiedi il path via `AskUserQuestion`.

### Step 2: Raccogli i dati (conversazionale)

Chiedi, una domanda alla volta:
1. **Nome utente** (obbligatorio).
2. **Nome dell'agent principale** (default `Anja`).
3. **Profilo** (facoltativo, due righe: ruolo / su cosa lavora / preferenze di lavoro).

### Step 3: Crea il profilo

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/users_init.py" \
  --hub "<HUB>" --name "<NOME>" --language it --default --force
```

Poi imposta il nome dell'agent in `<HUB>/config.json` → chiave `default_agent_name`
(merge, non sovrascrivere il resto del file).

Se è stato fornito un profilo, appendilo in coda a `<HUB>/users/<slug>.md` sotto una sezione
`## About (onboarding)`.

### Step 4: Conferma

Riporta: utente creato (`<slug>`), agent impostato, profilo salvato sì/no. Suggerisci di aprire
la Mission Control (`server.py --hub <HUB>`) se non è già in esecuzione.

> Nota: se preferisci la UI, lo stesso flusso è disponibile come wizard web alla rotta `/onboarding`
> (mostrato automaticamente al primo avvio quando l'hub non ha ancora un utente).
