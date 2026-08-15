---
name: gen-image-video
description: Generazione di immagini e video con la CLI `giv` sui provider Gemini, OpenAI, x.ai e OpenRouter (inclusi ByteDance Seedream/Seedance). Caricala quando devi generare o modificare immagini da prompt, lavorare con reference di stile (--input), generare video text-to-video o animare un'illustrazione (image-to-video). Sostituisce i vecchi tool MCP image.generate/video.generate.
version: 1.0.0
category: media
tags: [image, video, generation, giv, gemini, openai, xai, openrouter]
platforms: [macos, linux]
requires_tools: [Bash]
---

# Skill: gen-image-video — CLI `giv`

## Setup (hub)

- Binario `giv` in PATH (installato dall'hub).
- **Chiavi**: giv le risolve da solo in ordine env di processo → `./credentials.env`
  → `./.env`. L'hub materializza `credentials.env` nella root di ogni workspace e
  dell'hub (da Settings → Integrations → Generazione immagini). Se lanci giv da
  un'altra directory: `set -a; . <hub>/credentials.env; set +a` prima del comando.
- **Output**: usa SEMPRE `--out <hub>/raw/images/$(date +%F)` per le immagini e
  `--out <hub>/raw/videos/$(date +%F)` per i video — è lì che la tab Media della
  UI le mostra. Copia poi in `data/` del workspace solo ciò che diventa asset
  di lavoro (brand, post).

## Contratto output (per parsing)

- **stdout: solo il manifest JSON** `{provider, model, prompt, files:[{path, mime, bytes}]}`.
  Log, avvisi e puntini di polling vanno su stderr.
- Exit code: 0 ok, 1 errore runtime (messaggio API in chiaro su stderr), 2 uso errato.
- File salvati come `<out>/<slug>-<timestamp>[-n].<ext>` — `--name` per slug stabile.
- Il mime nel manifest è quello **reale** del provider (Nano Banana → jpeg,
  gpt-image → png…): leggilo dal manifest, non dedurlo.
- Una chiamata può restituire **più file** (es. OpenRouter a volte dà 2 varianti):
  itera su `files[]`.

## Comandi

```bash
giv models [--provider P] [--all] [--json]      # elenca modelli image/video (verifica anche l'auth)
giv image  [flag] "<prompt>"                    # genera immagini
giv video  [flag] "<prompt>"                    # genera video (job asincrono, 1-5 min)
```

Flag comuni: `--provider` (gemini|openai|xai|openrouter, default gemini), `--model`
(default sensato per provider), `--out`, `--name`. Il prompt è un **singolo
argomento quotato**; i flag possono stare prima o dopo.

`image`: `-n <num>`, `--aspect <1:1|16:9|9:16|4:3|3:4>`, `--input <file>` ripetibile
(reference di stile / editing).
`video`: `--aspect`, `--resolution <720p|1080p>`, `--duration <s>`, `--negative`,
`--image <file>` (frame iniziale, image-to-video).

## Matrice provider (verificata 14/08/2026)

| provider | immagini | video | `--input` | `--aspect` | modelli chiave |
|---|---|---|---|---|---|
| `gemini` | ✅ | ✅ Veo | ✅ | ✅ | `gemini-3-pro-image` (top), `gemini-2.5-flash-image` (default), `gemini-3.1-flash-image`, `imagen-4.0-*`; video `veo-3.1-fast-generate-preview` |
| `openai` | ✅ | ❌ | ✅ (via edits) | ✅ (mappato su size) | `gpt-image-1` (default), `gpt-image-2` (top) |
| `xai` | ✅ | ❌ | ❌ | ❌ | `grok-imagine-image-2.0` (default), `-quality` |
| `openrouter` | ✅ | ✅ | ✅ | ✅ | `google/gemini-3-pro-image`, `bytedance-seed/seedream-5-0-lite|pro`; video `bytedance/seedance-2.0-mini` (economico), `google/veo-3.1` (default) |

- Veo (gemini): `--duration` 4|6|8 — il default del modello è 8s e **costa il doppio** di 4.
- Confronti multi-provider: stesso prompt, `--name` diverso per provider.

## Ricette

**Immagine con reference di stile**:

```bash
giv image --model gemini-3-pro-image --aspect 16:9 --name <slug> \
  --out <hub>/raw/images/$(date +%F) --input <reference.webp> \
  "<EXACT same style as the reference image> CHANGE the palette to: <hex+ruoli>. Subject: <scena>. NO text, NO words, NO letters, NO numbers anywhere."
```

**Animare un'illustrazione** (image-to-video, economico):

```bash
giv video --provider openrouter --model bytedance/seedance-2.0-mini \
  --duration 4 --resolution 720p --aspect 16:9 --image <illustrazione>.jpg \
  --out <hub>/raw/videos/$(date +%F) --name <slug> \
  "Bring this illustration to life with subtle 2D motion. Keep the EXACT flat style, colors and composition. NO text, NO camera movement."
```

**Verifica visiva — SEMPRE, dopo ogni generazione**: guarda l'immagine (Read del
file); per i video estrai 2-3 frame (`ffmpeg -sseof -0.2 -i <mp4> -frames:v 1
/tmp/last.png`) e controlla: stile coerente, palette giusta, **nessun
testo/lettera** indesiderato.

## Costi indicativi (agosto 2026)

- Immagini: Nano Banana flash ~$0.04 · Nano Banana Pro ~$0.13-0.25 · gpt-image-2 ~$0.25 ·
  Seedream Lite pochi cent · grok ~$0.07.
- Video: seedance-2.0-mini 4s/720p ~$0.12 · veo-3.1-fast 4s ~$0.60.
- Per prove ripetute usa modelli flash/lite/mini e `--duration 4`; il modello top
  solo per l'output finale.

## Quirk noti

- **OpenRouter, modelli ByteDance**: non compaiono in `giv models` ma esistono — usa
  gli id noti; scheda modello via `GET /api/v1/models/<id>/endpoints`.
- **HTTP 500 dal provider "Seed"** (ByteDance su OpenRouter): instabilità temporanea
  dell'endpoint, riprova più tardi — non è un errore del CLI.
- **Modelli Imagen**: non accettano `--input` (il CLI lo segnala); per editing usa
  i modelli `gemini-*-image`.
- I nomi modello **cambiano spesso**: in caso di 404 sul modello, rifai
  `giv models --provider <p>`.
- La clausola "NO text, NO letters" va **sempre** tenuta per le illustrazioni:
  tutti i modelli tendono a scrivere etichette.
- **MAI** usare i connettori claude.ai (`mcp__claude_ai_*`) o servizi esterni per
  generare media: crediti esterni, file fuori dall'hub.
