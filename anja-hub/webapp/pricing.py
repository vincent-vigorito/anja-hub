"""Pricing dei modelli LLM (M-CostObservability).

Prezzi USD per 1M token, match per substring sul model id (la chiave più LUNGA che
è contenuta nel model id vince → `claude-opus` batte `claude` per `claude-opus-4-8`).
Default inline editabili via `<hub>/config/pricing.json` (merge/override).

I prezzi vanno tenuti allineati ai listini dei provider: sono un riferimento, non
una verità — un modello senza prezzo viene loggato con `priced=False` (costo 0) così
si vede subito che la tabella va aggiornata.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

# input/output = USD per 1M token. Aggiorna coi listini correnti.
_DEFAULTS: dict = {
    "models": {
        "claude-opus": {"input": 15.0, "output": 75.0},
        "claude-sonnet": {"input": 3.0, "output": 15.0},
        "claude-haiku": {"input": 1.0, "output": 5.0},
        "claude-3-5-haiku": {"input": 0.80, "output": 4.0},
        "opus": {"input": 15.0, "output": 75.0},     # alias breve (routine usano "opus"/"sonnet"/"haiku")
        "sonnet": {"input": 3.0, "output": 15.0},
        "haiku": {"input": 1.0, "output": 5.0},
        "grok-4": {"input": 5.0, "output": 15.0},
        "grok": {"input": 5.0, "output": 15.0},
        "gpt-5": {"input": 5.0, "output": 15.0},
        "gpt-4o": {"input": 2.5, "output": 10.0},
        "gpt-4": {"input": 2.5, "output": 10.0},
        "gemini-2": {"input": 1.25, "output": 5.0},
    },
    "default": {"input": 0.0, "output": 0.0},
}


def load_pricing(hub_path: Optional[Path] = None) -> dict:
    """Default inline + override da `<hub>/config/pricing.json` (se esiste)."""
    table = {"models": dict(_DEFAULTS["models"]), "default": dict(_DEFAULTS["default"])}
    if hub_path:
        p = Path(hub_path) / "config" / "pricing.json"
        if p.is_file():
            try:
                override = json.loads(p.read_text(encoding="utf-8"))
                table["models"].update(override.get("models", {}))
                if "default" in override:
                    table["default"] = override["default"]
            except Exception:
                pass
    return table


_PROVIDER_BY_FAMILY = (("claude", "anthropic"), ("grok", "xai"), ("gpt", "openai"),
                       ("gemini", "google"), ("o1", "openai"), ("o3", "openai"))


def provider_of(model: str) -> str:
    """Inferisce il provider dal model id (per i raggruppamenti della UI)."""
    m = (model or "").lower()
    for fam, prov in _PROVIDER_BY_FAMILY:
        if fam in m:
            return prov
    return ""


def cost_of(model: str, input_tokens: int, output_tokens: int,
            table: Optional[dict] = None) -> tuple[float, bool]:
    """(cost_usd, priced). priced=False se il modello non è in tabella (costo 0)."""
    table = table or _DEFAULTS
    m = (model or "").lower()
    best, best_len = None, -1
    for key, price in table["models"].items():
        if key.lower() in m and len(key) > best_len:
            best, best_len = price, len(key)
    priced = best is not None
    p = best or table.get("default", {"input": 0.0, "output": 0.0})
    cost = (int(input_tokens or 0) * p["input"] + int(output_tokens or 0) * p["output"]) / 1_000_000
    return round(cost, 6), priced
