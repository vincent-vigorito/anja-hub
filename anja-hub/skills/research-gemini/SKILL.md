---
name: research-gemini
description: Ricerca web con risposta sintetizzata e fonti citate via Gemini API + Grounding with Google Search. Da usare quando serve contesto fresco dal web con qualità Google e citazioni verificabili — più accurata di DuckDuckGo, senza account SerpAPI (usa la GEMINI_API_KEY già configurata). A pagamento per query di ricerca eseguita.
version: 1.0.0
category: research
tags: [research, web, google, gemini, grounding, citations]
platforms: [macos, linux]
requires_tools: [Bash]
---

# Skill: research-gemini — Grounding with Google Search

Ricerca web tramite la Gemini API col tool `google_search`: il modello decide
le query, cerca su Google e risponde con una **sintesi già pronta** + le
**fonti citate** (groundingChunks). Un'unica chiamata = ricerca + sintesi.

## Requisiti

- `GEMINI_API_KEY` nell'env (sincronizzata dall'hub in `<hub>/.secrets.env`
  da Settings → Integrations → Generazione immagini).
- Costo: ~$14-35 / 1000 query di ricerca (con Gemini 3 si paga per query
  eseguita) + i token del modello. Per uso quotidiano gratuito usa
  `research-duckduckgo`.

## Uso

```bash
python3 <plugin-root>/skills/research-gemini/scripts/gemini_search.py "query" [limit]
```

Output JSON:

```json
{"query": "...", "count": 5,
 "results": [{"title": "...", "url": "...", "snippet": ""}],
 "answer": "sintesi con dati freschi dal web",
 "search_queries": ["query eseguite da Google"]}
```

- `answer` è già la risposta: riusala come base della tua sintesi.
- `results` sono le fonti: cita SEMPRE con `[title](url)` markdown. NON
  inventare URL non presenti.
- `{"error": "..."}` su fallimento (key mancante, HTTP error) — fallback:
  `skill.load("research-duckduckgo")`.
- Override modello: env `GEMINI_SEARCH_MODEL` (default `gemini-3.5-flash`).

## Quando preferirla

- Domande che beneficiano della qualità/freschezza di Google (news, prezzi,
  release recenti, dati locali) e richiedono fonti citabili.
- NON per lookup banali (usa DDG gratis) né per ricerche approfondite
  multi-fonte con report: per quelle c'è la skill `deep-research`.
