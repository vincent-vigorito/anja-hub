"""embeddings.py — helper di embedding per la webapp (semantic search + fuzzy dedup).

Self-contained (usa `httpx`, già dep). Endpoint OpenAI-compatible `/v1/embeddings`;
provider di default OpenRouter (1 key per OpenAI/Voyage/Cohere/...). Replica l'approccio
già provato del plugin (`code.search`), ma senza dipendere dal path del plugin.

GRACEFUL by design: se non c'è una key (o la chiamata fallisce) `get_embedder` ritorna
None (o l'embedder ritorna []) → i chiamanti ricadono sul comportamento lessicale/esatto
esistente. Nessuna regressione se non configurato.

Config (env o `<hub>` secrets): ANJA_EMBED_API_KEY | OPENROUTER_API_KEY | OPENAI_API_KEY.
Override: ANJA_EMBED_PROVIDER (openrouter|openai), ANJA_EMBED_MODEL.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Callable, Optional

_ENDPOINTS = {
    "openrouter": "https://openrouter.ai/api/v1/embeddings",
    "openai": "https://api.openai.com/v1/embeddings",
}
_DEFAULT_MODEL = {
    "openrouter": "openai/text-embedding-3-small",
    "openai": "text-embedding-3-small",
}


def _read_secrets(hub_path: Path) -> dict:
    out: dict = {}
    for rel in (".anjawiki/.secrets.env", ".secrets.env"):
        f = Path(hub_path) / rel
        if not f.is_file():
            continue
        for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            out.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return out


def _resolve(hub_path: Optional[Path]):
    """→ (provider, model, api_key) oppure None se nessuna key configurata."""
    env = os.environ
    sec = _read_secrets(hub_path) if hub_path else {}
    provider = (env.get("ANJA_EMBED_PROVIDER") or "openrouter").lower()
    key = env.get("ANJA_EMBED_API_KEY") or sec.get("ANJA_EMBED_API_KEY")
    if not key:
        if provider == "openai":
            key = env.get("OPENAI_API_KEY") or sec.get("OPENAI_API_KEY")
        else:
            provider = "openrouter"
            key = env.get("OPENROUTER_API_KEY") or sec.get("OPENROUTER_API_KEY")
    if not key:  # ultimo tentativo: una OpenAI key qualunque provider
        key = env.get("OPENAI_API_KEY") or sec.get("OPENAI_API_KEY")
        if key:
            provider = "openai"
    if not key or provider not in _ENDPOINTS:
        return None
    model = env.get("ANJA_EMBED_MODEL") or _DEFAULT_MODEL[provider]
    return provider, model, key


def get_embedder(hub_path: Optional[Path]) -> Optional[Callable[[list], list]]:
    """Ritorna una funzione `embed(texts) -> list[vec]`, oppure None se non configurabile.
    L'embedder è best-effort: su errore di rete ritorna [] (i chiamanti fanno fallback)."""
    resolved = _resolve(hub_path)
    if not resolved:
        return None
    provider, model, key = resolved

    def embed(texts: list) -> list:
        texts = [t for t in (texts or []) if t]
        if not texts:
            return []
        try:
            import httpx
        except ImportError:
            return []
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        if provider == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/vincent-vigorito/anja-hub"
            headers["X-Title"] = "anja-hub"
        try:
            with httpx.Client(timeout=30) as c:
                r = c.post(_ENDPOINTS[provider], headers=headers,
                           json={"model": model, "input": list(texts)})
                r.raise_for_status()
                return [d["embedding"] for d in r.json()["data"]]
        except Exception as e:  # noqa: BLE001
            print(f"[embeddings] {type(e).__name__}: {e}")
            return []

    return embed


def cosine(a: list, b: list) -> float:
    """Cosine similarity di due vettori. 0.0 se degeneri/mismatch."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def rrf_merge(*rankings: list, k: int = 60) -> list:
    """Reciprocal Rank Fusion di N liste ordinate di chiavi (hashable). Ritorna le chiavi
    ordinate per punteggio RRF decrescente. Fonde ranking non comparabili (lessicale +
    semantico) senza normalizzare gli score."""
    scores: dict = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores, key=lambda x: scores[x], reverse=True)
