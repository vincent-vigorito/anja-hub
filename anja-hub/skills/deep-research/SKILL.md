---
name: deep-research
description: Ricerca approfondita multi-fonte con report citato via Gemini Deep Research (Interactions API). Da usare per dossier, analisi di mercato, stato dell'arte, confronti complessi — quando servono decine di fonti e un report strutturato, non una singola ricerca. Task async (~20 min, ~$1-3): si LANCIA e il report arriva come notifica — mai aspettarlo nel turno corrente.
version: 1.0.0
category: research
tags: [research, deep-research, gemini, report, async]
platforms: [macos, linux]
---

# Skill: deep-research — Gemini Deep Research (async)

L'agente Deep Research di Google pianifica la ricerca da solo, esegue fino a
160 ricerche web e produce un **report completo con citazioni**. L'hub lo
orchestra: tu lo LANCI, il polling lo fa il server, il report arriva come
**notifica** (bell + Telegram) e come file in `<hub>/raw/research/<data>/`.

## Regola d'oro

⏳ **MAI aspettare il report nel turno corrente** (ci mette ~20-40 minuti).
Lancia, conferma all'utente che è partita, chiudi il turno. Quando l'utente
torna a chiedere, controlla lo stato.

## Lancio

```bash
# Bash (provider Claude SDK)
curl -s -X POST http://127.0.0.1:8765/api/research/deep \
  -H 'Content-Type: application/json' \
  -d '{"query": "<domanda di ricerca dettagliata>", "mode": "standard"}'
```

Senza Bash: tool MCP `hub.api(method="POST", path="research/deep", body={...})`
o WebFetch sullo stesso endpoint.

- `mode`: `standard` (default — veloce, ~$1-3/task) | `max` (massima
  completezza, ~$3-7/task). Usa `max` SOLO se l'utente chiede esplicitamente
  la massima profondità.
- Risposta: `{"ok": true, "task_id": "dr-...", "status": "in_progress", "eta": "..."}`.
- Serve `GEMINI_API_KEY` configurata (tier a pagamento) — se manca, l'endpoint
  risponde 400 con istruzioni.

## Stato e report

```bash
curl -s http://127.0.0.1:8765/api/research/deep            # lista task
curl -s http://127.0.0.1:8765/api/research/deep/<task_id>  # stato singolo
```

`status`: `in_progress` → `completed` (con `report_path`) | `failed` | `timeout`.
A completamento leggi il report da `report_path` (markdown citato) e
sintetizzalo; per filarlo nel wiki usa `/anja-ingest <report_path>`.

## Scrivere una buona query

Il task costa: investi nel prompt. Includi obiettivo, contesto, perimetro e
formato atteso — es. "Analizza il mercato italiano della cosmesi naturale
2024-2026: player principali, canali di vendita, trend prezzi, normative.
Report con sezioni e fonti per ogni claim."
