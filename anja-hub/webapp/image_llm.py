"""image_llm.py — generazione immagini via LiteLLM (BYO-key).

Modello "bring your own key": usa la key del cliente (dal vault, IMAGE_API_KEY)
per generare immagini coi modelli immagine via LiteLLM (`dall-e-3`, `gpt-image-1`,
…). Ritorna i bytes PNG, senza dipendenze esterne oltre litellm/httpx (già
nell'hub). Portato da anja-marketer webapp/core/images.py.
"""

from __future__ import annotations

import base64

import httpx

DEFAULT_MODEL = "dall-e-3"


def generate(prompt: str, api_key: str, *, model: str = "", size: str = "1024x1024",
             api_base: str = ""):
    """Genera un'immagine. Ritorna (bytes_png|None, error|None)."""
    if not (prompt or "").strip():
        return None, "prompt vuoto"
    if not api_key:
        return None, "API key immagini mancante"
    try:
        import litellm
    except Exception as e:  # noqa: BLE001
        return None, f"LiteLLM non disponibile: {e}"
    kwargs = {"model": model or DEFAULT_MODEL, "prompt": prompt, "api_key": api_key, "n": 1}
    if size:   # alcuni modelli (es. Gemini) non accettano 'size'
        kwargs["size"] = size
    if api_base:   # endpoint OpenAI-compatibili di terzi (es. xAI api.x.ai/v1)
        kwargs["api_base"] = api_base
    try:
        resp = litellm.image_generation(**kwargs)
    except Exception as e:  # noqa: BLE001
        return None, f"errore generazione: {e}"

    data = getattr(resp, "data", None) or []
    if not data:
        return None, "risposta senza immagine"
    item = data[0]
    b64 = getattr(item, "b64_json", None) or (item.get("b64_json") if isinstance(item, dict) else None)
    url = getattr(item, "url", None) or (item.get("url") if isinstance(item, dict) else None)
    if b64:
        try:
            return base64.b64decode(b64), None
        except Exception as e:  # noqa: BLE001
            return None, f"decodifica fallita: {e}"
    if url:
        # URL del provider (non input utente): scarico i bytes.
        try:
            with httpx.Client(timeout=60.0, follow_redirects=True) as c:
                r = c.get(url)
                r.raise_for_status()
                return r.content, None
        except httpx.HTTPError as e:
            return None, f"download fallito: {e}"
    return None, "risposta senza immagine utilizzabile"
