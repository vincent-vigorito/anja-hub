#!/usr/bin/env python3
"""
mcp_images_server.py — MCP server per generazione immagini.

Provider supportati (auto-routing in base alla key disponibile):
- xAI Grok Imagine     → https://api.x.ai/v1/images/generations
- OpenAI DALL-E        → https://api.openai.com/v1/images/generations

Tool esposti:
- image.generate(prompt, provider?, model?, size?, n?) → genera + salva PNG
- image.list(limit?) → lista immagini precedenti

Salvataggio: <ANJA_ROOT>/raw/images/<YYYY-MM-DD>/<slug>-<hex4>.png
Config via env: ANJA_ROOT (default cwd), API keys via os.environ.

Stdlib only (urllib + json + base64).
"""

import base64
import json
import os
import re
import secrets as _secrets
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


SCOPE = os.environ.get("ANJA_SCOPE", "hub")
ROOT = Path(os.environ.get("ANJA_ROOT", os.getcwd())).resolve()


# =================================================================
# helpers
# =================================================================

def _images_dir() -> Path:
    """Directory dove salviamo le immagini generate."""
    if SCOPE == "project":
        base = ROOT / ".anjawiki" / "raw" / "images"
    else:  # hub, agent
        base = ROOT / "raw" / "images"
    today = datetime.now().strftime("%Y-%m-%d")
    d = base / today
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slugify(s: str, max_len: int = 32) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return (s[:max_len] or "image")


# =================================================================
# Catalogo unico (image_gen della webapp): stessi modelli della UI.
# =================================================================

_WEBAPP = Path(__file__).resolve().parent.parent / "webapp"

# guida per l'LLM: quando usare quale modello
_MODEL_GUIDE = {
    "nano-banana-3": "veloce ed economico — buon default",
    "nano-banana": "come sopra, generazione precedente",
    "gemini-pro-image": "qualità alta (hero image, materiale importante)",
    "imagen-4": "fotorealismo Google di qualità",
    "gpt-image-1": "il migliore quando servono scritte/testo nell'immagine o istruzioni complesse",
    "dall-e-3": "alternativa OpenAI",
    "grok-image": "stile creativo/libero",
    "grok-image-hq": "come grok-image, più qualità",
    "sd35": "Stable Diffusion 3.5",
    "qwen-image": "alternativa Qwen",
}
_DEFAULT_ORDER = ("nano-banana-3", "gpt-image-1", "grok-image", "nano-banana",
                  "imagen-4", "dall-e-3", "grok-image-hq", "sd35")


def _hub_and_secrets() -> tuple[Path, Path]:
    env_hub = os.environ.get("ANJA_HUB", "")
    if env_hub and (Path(env_hub) / "config" / "projects.json").is_file():
        hub = Path(env_hub)
    elif (ROOT / "config" / "projects.json").is_file():
        hub = ROOT
    else:
        hub = next((p for p in ROOT.parents
                    if (p / "config" / "projects.json").is_file()), ROOT)
    secrets = (ROOT / ".anjawiki") if SCOPE == "project" else (hub / ".anjawiki")
    return hub, secrets


def _image_gen():
    if str(_WEBAPP) not in sys.path:
        sys.path.insert(0, str(_WEBAPP))
    import image_gen  # noqa: PLC0415
    return image_gen


def _ready_models() -> list[dict]:
    hub, secrets = _hub_and_secrets()
    return [m for m in _image_gen().catalog(hub, secrets) if m["ready"]]


def _default_model(ready: list[dict]) -> str:
    """IMAGE_DEFAULT_MODEL (vault/env) se valido, altrimenti ordine di preferenza."""
    ready_ids = [m["id"] for m in ready]
    hub, secrets = _hub_and_secrets()
    try:
        if str(_WEBAPP) not in sys.path:
            sys.path.insert(0, str(_WEBAPP))
        import connectors_io  # noqa: PLC0415
        pref = (connectors_io.resolve_values(hub, secrets).get("IMAGE_DEFAULT_MODEL")
                or os.environ.get("IMAGE_DEFAULT_MODEL") or "").strip()
    except Exception:
        pref = (os.environ.get("IMAGE_DEFAULT_MODEL") or "").strip()
    if pref in ready_ids:
        return pref
    return next((m for m in _DEFAULT_ORDER if m in ready_ids),
                ready_ids[0] if ready_ids else "")


# =================================================================
# image.generate
# =================================================================

def tool_image_generate(args: dict) -> dict:
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        return {"error": "prompt required"}
    if len(prompt) > 4000:
        return {"error": "prompt too long (max 4000 chars)"}

    try:
        ready = _ready_models()
    except Exception as e:
        return {"error": f"catalogo immagini non disponibile: {type(e).__name__}: {e}"}
    if not ready:
        return {"error": "nessun modello immagine pronto — aggiungi le key in "
                         "Settings → Integrations → Generazione immagini"}
    ready_ids = [m["id"] for m in ready]

    model_id = (args.get("model") or "").strip()
    provider = (args.get("provider") or "").strip().lower()
    if not model_id and provider:
        # compat: provider legacy → primo modello ready di quel provider
        aliases = {"google": "gemini", "grok": "xai"}
        p = aliases.get(provider, provider)
        model_id = next((m["id"] for m in ready if m["provider"] == p), "")
        if not model_id:
            return {"error": f"nessun modello pronto per provider '{provider}' "
                             f"(disponibili: {ready_ids})"}
    if not model_id:
        model_id = _default_model(ready)
    if model_id not in ready_ids:
        return {"error": f"modello '{model_id}' non pronto — disponibili: {ready_ids}"}

    n = max(1, min(int(args.get("n", 1)), 4))
    size = (args.get("size") or "").strip()
    opts = {"size": size} if size else {}

    hub, secrets = _hub_and_secrets()
    out_dir = _images_dir()
    gen = _image_gen()
    saved = []
    for _ in range(n):
        res = gen.generate(hub, secrets, out_dir, prompt, model=model_id, **opts)
        if not res.get("ok"):
            if saved:   # parziale: riporta ciò che c'è + l'errore
                break
            return {"error": res.get("error") or "generazione fallita",
                    "model": model_id}
        for fpath in res.get("files") or []:
            fp = Path(fpath)
            today = datetime.now().strftime("%Y-%m-%d")
            entry = {"path": str(fp), "size_bytes": fp.stat().st_size,
                     "web_url": f"/api/media/images/{today}/{fp.name}"}
            try:
                entry["rel_path"] = str(fp.relative_to(ROOT))
            except ValueError:
                entry["rel_path"] = fp.name
            saved.append(entry)

    m = next((x for x in ready if x["id"] == model_id), {})
    return {
        "provider": m.get("provider", ""),
        "model": model_id,
        "prompt": prompt,
        "count": len(saved),
        "images": saved,
    }


# =================================================================
# image.list
# =================================================================

def tool_image_list(args: dict) -> dict:
    limit = int(args.get("limit", 20))
    if SCOPE == "project":
        base = ROOT / ".anjawiki" / "raw" / "images"
    else:
        base = ROOT / "raw" / "images"
    if not base.is_dir():
        return {"images": []}
    items = []
    # walk date dirs reverse
    for date_dir in sorted(base.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        for f in sorted(date_dir.glob("*.png"), reverse=True):
            try:
                items.append({
                    "path": str(f),
                    "rel_path": str(f.relative_to(ROOT)),
                    "date": date_dir.name,
                    "size_bytes": f.stat().st_size,
                    "mtime": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds"),
                })
                if len(items) >= limit:
                    break
            except Exception:
                continue
        if len(items) >= limit:
            break
    return {"images": items, "count": len(items)}


# =================================================================
# JSON-RPC dispatch (MCP protocol)
# =================================================================

def _generate_description() -> str:
    """Descrizione col MENU dei modelli pronti + default: è ciò che guida la
    scelta dell'LLM. Best-effort: se il catalogo non è caricabile, testo base."""
    base = ("Genera una o più immagini da un prompt testuale (catalogo unico "
            "dell'hub: Google, OpenAI, xAI…). Salva PNG e "
            "restituisce i path. Usa quando l'utente chiede di "
            "generare/creare/disegnare un'immagine.")
    try:
        ready = _ready_models()
        if not ready:
            return base + " ATTENZIONE: nessun modello pronto (mancano le key)."
        default = _default_model(ready)
        menu = "; ".join(
            f"{m['id']}" + (" [DEFAULT]" if m["id"] == default else "")
            + (f" = {_MODEL_GUIDE[m['id']]}" if m["id"] in _MODEL_GUIDE else "")
            for m in ready)
        return (base + f" Modelli pronti: {menu}. Senza `model` usa il default; "
                "scegli un modello diverso solo se il compito lo richiede "
                "(es. testo nell'immagine → gpt-image-1; hero di qualità → "
                "gemini-pro-image/imagen-4).")
    except Exception:
        return base


def _tool_specs() -> list:
    return [
        {
            "name": "image.generate",
            "description": _generate_description(),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Prompt testuale dell'immagine"},
                    "model": {"type": "string", "description": "Id modello dal catalogo (vuoto = default dell'hub)"},
                    "provider": {"type": "string", "description": "Legacy: provider (gemini/openai/xai) → primo modello pronto di quel provider. Preferisci `model`."},
                    "n": {"type": "integer", "default": 1, "description": "Numero immagini (1-4)"},
                    "size": {"type": "string", "description": "Es. '1024x1024' (solo modelli che la supportano)"},
                },
                "required": ["prompt"],
            },
        },
        {
            "name": "image.list",
            "description": "Lista immagini generate precedentemente, ordinate per data desc.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 20},
                },
            },
        },
    ]

TOOL_HANDLERS = {
    "image.generate": tool_image_generate,
    "image.list": tool_image_list,
}


# Nomi sul wire: canonici puntati (`image.generate`) internamente, flat (`image_generate`) in tools/list —
# Grok Build e i client OpenAI-style scartano i nomi col punto, Claude Code li mostra
# già flat. tools/call accetta entrambe le forme.
def _wire_name(name: str) -> str:
    return name.replace(".", "_")


def _canonical_name(name: str) -> str:
    return _CANONICAL_BY_WIRE.get(name, name)


_CANONICAL_BY_WIRE = {_wire_name(n): n for n in TOOL_HANDLERS}


def handle_request(req: dict) -> Optional[dict]:
    method = req.get("method")
    rid = req.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "anja_images", "version": "0.1.0"},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": [{**t, "name": _wire_name(t["name"])} for t in _tool_specs()]}}
    if method == "tools/call":
        params = req.get("params") or {}
        name = _canonical_name(params.get("name") or "")
        args = params.get("arguments") or {}
        handler = TOOL_HANDLERS.get(name)
        if not handler:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown tool: {name}"}}
        try:
            result = handler(args)
        except Exception as e:
            result = {"error": f"{type(e).__name__}: {e}"}
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "isError": "error" in result},
        }
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown method: {method}"}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        resp = handle_request(req)
        if resp is not None:
            print(json.dumps(resp), flush=True)


if __name__ == "__main__":
    main()
