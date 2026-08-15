"""image_gen.py — generazione immagini multi-modello per l'hub.

Un catalogo di modelli, engine 'llm' → LiteLLM (OpenAI GPT Image/DALL·E,
Google Nano Banana/Imagen, xAI Grok Imagine, …).

Le credenziali NON si duplicano: la key del provider (`OPENAI_API_KEY`,
`GEMINI_API_KEY`, …) è quella già usata dalla chat — risolta da vault
connettori (ws→hub) → `<hub>/.secrets.env` → environment. Asset salvati
in <scope>/data/media/.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import connectors_io
import image_llm
import llm_router

# Catalogo modelli immagine. `size`='' → non si passa size a LiteLLM
# (modelli che non lo accettano).
IMAGE_MODELS = [
    {"id": "gpt-image-1", "label": "OpenAI · GPT Image", "engine": "llm",
     "provider": "openai", "model": "gpt-image-1", "size": "1024x1024"},
    {"id": "dall-e-3", "label": "OpenAI · DALL·E 3", "engine": "llm",
     "provider": "openai", "model": "dall-e-3", "size": "1024x1024"},
    {"id": "nano-banana", "label": "Google · Nano Banana (2.5)", "engine": "llm",
     "provider": "gemini", "model": "gemini/gemini-2.5-flash-image", "size": ""},
    {"id": "nano-banana-3", "label": "Google · Nano Banana 3.1", "engine": "llm",
     "provider": "gemini", "model": "gemini/gemini-3.1-flash-image", "size": ""},
    {"id": "gemini-pro-image", "label": "Google · Gemini 3 Pro Image", "engine": "llm",
     "provider": "gemini", "model": "gemini/gemini-3-pro-image", "size": ""},
    {"id": "imagen-4", "label": "Google · Imagen 4", "engine": "llm",
     "provider": "gemini", "model": "gemini/imagen-4.0-generate-001", "size": ""},
    {"id": "sd35", "label": "Stability · SD 3.5 Large", "engine": "llm",
     "provider": "stability", "model": "stability/sd3.5-large", "size": ""},
    {"id": "qwen-image", "label": "Qwen · Image", "engine": "llm",
     "provider": "dashscope", "model": "dashscope/qwen-image-2.0", "size": ""},
    # xAI non ha un provider immagini nativo in tutte le versioni LiteLLM:
    # si passa dalla via OpenAI-compatibile (api_base x.ai). Niente `size`
    # (l'endpoint la rifiuta).
    {"id": "grok-image", "label": "xAI · Grok Imagine", "engine": "llm",
     "provider": "xai", "model": "openai/grok-imagine-image-2.0", "size": "",
     "api_base": "https://api.x.ai/v1"},
    {"id": "grok-image-hq", "label": "xAI · Grok Imagine HQ", "engine": "llm",
     "provider": "xai", "model": "openai/grok-imagine-image-quality", "size": "",
     "api_base": "https://api.x.ai/v1"},
]

# provider modello → env var della key (riusata da chat e immagini).
PROVIDER_ENV = {
    "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY",
    "xai": "XAI_API_KEY", "stability": "STABILITY_API_KEY",
    "dashscope": "DASHSCOPE_API_KEY",
}


def _model(model_id: str) -> dict | None:
    return next((m for m in IMAGE_MODELS if m["id"] == model_id), None)


def _slug(s: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "img").lower()).strip("-")
    return (s[:n] or "img").rstrip("-")


def _provider_key(provider: str, vals: dict, hub_path: Path) -> str:
    """Key del provider per i modelli 'llm': vault connettori → <hub>/.secrets.env →
    environment → (fallback) IMAGE_API_KEY del vault."""
    env = PROVIDER_ENV.get(provider, "")
    if not env:
        return ""
    hub_secrets = llm_router.load_secrets_env(Path(hub_path)) if hub_path else {}
    return (vals.get(env) or hub_secrets.get(env) or os.environ.get(env) or "").strip()


def _ready(m: dict, vals: dict, hub_path: Path) -> bool:
    return bool(_provider_key(m["provider"], vals, hub_path))


def catalog(hub_path: Path, secrets_dir: Path) -> list[dict]:
    """Catalogo modelli con flag `ready` (key presente) per lo scope (ws→hub)."""
    vals = connectors_io.resolve_values(hub_path, secrets_dir)
    out = []
    for m in IMAGE_MODELS:
        out.append({"id": m["id"], "label": m["label"], "engine": m["engine"],
                    "provider": m["provider"],
                    "ready": _ready(m, vals, hub_path),
                    "needs": "" if _ready(m, vals, hub_path)
                    else PROVIDER_ENV.get(m["provider"], "API key")})
    return out


def available(hub_path: Path, secrets_dir: Path) -> dict:
    """Engine grezzi disponibili (compat: usato altrove)."""
    vals = connectors_io.resolve_values(hub_path, secrets_dir)
    return {
        "llm": any(_provider_key(p, vals, hub_path) for p in ("openai", "gemini")),
    }


def _unique(media_dir: Path, stem: str, ext: str) -> Path:
    media_dir.mkdir(parents=True, exist_ok=True)
    dest = media_dir / f"{stem}{ext}"
    i = 1
    while dest.exists():
        dest = media_dir / f"{stem}-{i}{ext}"
        i += 1
    return dest


def generate(hub_path: Path, secrets_dir: Path, media_dir: Path, prompt: str, *,
             model: str = "", engine: str = "", **opts) -> dict:
    """Genera col modello scelto (id del catalogo). Ritorna {ok, model, engine,
    files:[...], error?}. `engine` legacy accettato come fallback."""
    m = _model(model)
    if not m:
        # compat: vecchia chiamata per engine → primo modello dell'engine
        m = next((x for x in IMAGE_MODELS if x["engine"] == (engine or "llm")), IMAGE_MODELS[0])
    vals = connectors_io.resolve_values(hub_path, secrets_dir)
    media_dir = Path(media_dir)

    key = _provider_key(m["provider"], vals, hub_path)
    if not key:
        return {"ok": False, "model": m["id"], "engine": "llm",
                "error": f"manca {PROVIDER_ENV.get(m['provider'], 'API key')} per {m['label']}"}
    size = opts.get("size") or m.get("size") or ""
    data, err = image_llm.generate(prompt, key, model=m["model"], size=size,
                                   api_base=m.get("api_base", ""))
    if err:
        return {"ok": False, "model": m["id"], "engine": "llm", "error": err}
    dest = _unique(media_dir, _slug(prompt), ".png")
    dest.write_bytes(data)
    return {"ok": True, "model": m["id"], "engine": "llm", "files": [str(dest)]}
