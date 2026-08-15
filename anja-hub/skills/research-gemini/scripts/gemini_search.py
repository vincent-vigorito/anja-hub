#!/usr/bin/env python3
"""gemini_search.py — ricerca web via Gemini API + Grounding with Google Search.

Usage:
    python3 gemini_search.py "query string" [limit=10]

Env: GEMINI_API_KEY (obbligatoria). GEMINI_SEARCH_MODEL per override modello
(default gemini-3.5-flash — economico, il grounding costa per query eseguita).

Output: JSON {"query", "count", "results": [{title, url, snippet}], "answer"}.
`answer` è la risposta sintetizzata dal modello coi dati freschi di Google
Search; `results` sono le fonti citate (groundingChunks).
On error: JSON {"error": "..."} (sempre parseabile).

Stdlib only.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TIMEOUT_SEC = 60


def search(query: str, limit: int = 10) -> dict:
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key:
        return {"error": "GEMINI_API_KEY non configurata (Settings → Integrations)"}
    model = (os.environ.get("GEMINI_SEARCH_MODEL") or "gemini-3.5-flash").strip()

    body = json.dumps({
        "contents": [{"parts": [{"text": query}]}],
        "tools": [{"google_search": {}}],
    }).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT.format(model=model), data=body, method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8")).get("error", {}).get("message", "")
        except Exception:
            detail = str(e)
        return {"error": f"HTTP {e.code}: {detail}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    cand = (data.get("candidates") or [{}])[0]
    answer = "".join(p.get("text", "")
                     for p in (cand.get("content") or {}).get("parts") or [])
    meta = cand.get("groundingMetadata") or {}
    results, seen = [], set()
    for chunk in meta.get("groundingChunks") or []:
        web = chunk.get("web") or {}
        url = web.get("uri", "")
        if not url or url in seen:
            continue
        seen.add(url)
        results.append({"title": web.get("title", url), "url": url, "snippet": ""})
        if len(results) >= limit:
            break
    return {"query": query, "count": len(results), "results": results,
            "answer": answer.strip(),
            "search_queries": meta.get("webSearchQueries") or []}


def main() -> None:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print(json.dumps({"error": "usage: gemini_search.py \"query\" [limit]"}))
        sys.exit(2)
    query = sys.argv[1].strip()
    try:
        limit = max(1, min(int(sys.argv[2]), 20)) if len(sys.argv) > 2 else 10
    except ValueError:
        limit = 10
    print(json.dumps(search(query, limit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
