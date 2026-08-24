"""browser_policy.py — F-AgentBrowser F1: config per-workspace + entry .mcp.json.

Il browser è OPT-IN per workspace, con allowlist di domini obbligatoria
(fail-closed: enabled senza origins → niente server). v1 è SOLO LETTURA
(policy `read`, gate server-side in scripts/mcp_browser_gate.py) e usa
`--isolated --storage-state`: il login si importa come storage state esportato
dal Mac (`npx playwright codegen --save-storage=state.json <url>`), l'unico
segreto su disco è `<ws>/.browser/state.json` (0600). Il profilo persistente
(e il takeover login) arrivano con la Fase 2 — vedi anja-agent-browser-design.md.

Config in `<ws>/.anjawiki/preferences.json`:
  "browser": {"enabled": true, "allowed_origins": ["https://dash.example.com"],
              "policy": "read"}
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SERVER_NAME = "anja_browser"
VALID_POLICIES = ("read",)          # act-ask/act-auto: Fase 3 del design
STATE_NAME = "state.json"
OUTPUT_MAX_BYTES = 50 * 1024 * 1024


def browser_dir(ws_root: Path) -> Path:
    return Path(ws_root) / ".browser"


def load_config(ws_root: Path) -> dict:
    p = Path(ws_root) / ".anjawiki" / "preferences.json"
    try:
        return (json.loads(p.read_text(encoding="utf-8")) or {}).get("browser", {}) or {}
    except Exception:
        return {}


def hub_load_config(hub: Path) -> dict:
    """Config del browser HUB-LEVEL (dashboard dell'operatore, non dei clienti):
    vive in config/config.json → "browser", come il binding mail."""
    p = Path(hub) / "config" / "config.json"
    try:
        return (json.loads(p.read_text(encoding="utf-8")) or {}).get("browser", {}) or {}
    except Exception:
        return {}


def hub_save_config(hub: Path, cfg: dict) -> dict:
    errs = validate(cfg)
    if errs:
        raise ValueError("; ".join(errs))
    p = Path(hub) / "config" / "config.json"
    obj = {}
    try:
        obj = json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        obj = {}
    obj["browser"] = {"enabled": bool(cfg.get("enabled")),
                      "allowed_origins": [str(o).strip() for o in (cfg.get("allowed_origins") or [])
                                          if str(o).strip()],
                      "policy": cfg.get("policy", "read")}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    return obj["browser"]


def validate(cfg: dict) -> list[str]:
    errs = []
    if not isinstance(cfg, dict):
        return ["browser config must be an object"]
    if cfg.get("enabled"):
        origins = cfg.get("allowed_origins")
        if not isinstance(origins, list) or not [o for o in origins if str(o).strip()]:
            errs.append("enabled browser requires a non-empty allowed_origins list (fail-closed)")
        for o in origins or []:
            if not str(o).startswith(("http://", "https://")):
                errs.append(f"origin must start with http(s):// — got '{o}'")
    policy = cfg.get("policy", "read")
    if policy not in VALID_POLICIES:
        errs.append(f"policy '{policy}' not supported in v1 (only: {VALID_POLICIES})")
    return errs


def save_config(ws_root: Path, cfg: dict) -> dict:
    errs = validate(cfg)
    if errs:
        raise ValueError("; ".join(errs))
    p = Path(ws_root) / ".anjawiki" / "preferences.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    obj = {}
    try:
        obj = json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        obj = {}
    obj["browser"] = {"enabled": bool(cfg.get("enabled")),
                      "allowed_origins": [str(o).strip() for o in (cfg.get("allowed_origins") or [])
                                          if str(o).strip()],
                      "policy": cfg.get("policy", "read")}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    return obj["browser"]


def mcp_entry(ws_root: Path, cfg: dict, gate_script: Path) -> dict | None:
    """Entry `anja_browser` per il .mcp.json del workspace, o None se disabilitato/invalido."""
    if not cfg.get("enabled") or validate(cfg):
        return None
    ws_root = Path(ws_root)
    bdir = browser_dir(ws_root)
    out_dir = bdir / "output"
    child = ["npx", "-y", "@playwright/mcp@latest",
             "--headless", "--browser", "chromium", "--isolated",
             "--allowed-origins", ";".join(cfg["allowed_origins"]),
             "--output-dir", str(out_dir),
             "--output-max-size", str(OUTPUT_MAX_BYTES),
             "--block-service-workers",
             "--timeout-navigation", "30000"]
    state = bdir / STATE_NAME
    if state.is_file():
        child += ["--storage-state", str(state)]
    return {"command": sys.executable,
            "args": [str(gate_script), "--policy", cfg.get("policy", "read"), "--"] + child}


def write_mcp_entry(ws_root: Path, gate_script: Path, cfg: dict | None = None) -> bool:
    """Materializza/rimuove l'entry nel .mcp.json dello scope dal config.
    cfg None → prefs del workspace; per l'hub passare hub_load_config(hub).
    Ritorna True se l'entry è presente dopo la scrittura."""
    ws_root = Path(ws_root)
    if cfg is None:
        cfg = load_config(ws_root)
    entry = mcp_entry(ws_root, cfg, gate_script)
    mcp_path = ws_root / ".mcp.json"
    data = {"mcpServers": {}}
    if mcp_path.is_file():
        try:
            data = json.loads(mcp_path.read_text(encoding="utf-8")) or {"mcpServers": {}}
        except Exception:
            data = {"mcpServers": {}}
    data.setdefault("mcpServers", {})
    if entry is None:
        if SERVER_NAME not in data["mcpServers"]:
            return False
        data["mcpServers"].pop(SERVER_NAME, None)
    else:
        data["mcpServers"][SERVER_NAME] = entry
        bdir = browser_dir(ws_root)
        (bdir / "output").mkdir(parents=True, exist_ok=True)
        gi = bdir / ".gitignore"
        if not gi.is_file():
            gi.write_text("*\n", encoding="utf-8")
    tmp = mcp_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, mcp_path)
    return entry is not None


def save_storage_state(ws_root: Path, raw: bytes) -> dict:
    """Salva lo storage state esportato (0600) dopo un sanity check di forma."""
    try:
        obj = json.loads(raw.decode("utf-8"))
    except Exception:
        raise ValueError("not valid JSON")
    if not isinstance(obj, dict) or "cookies" not in obj:
        raise ValueError("not a Playwright storage state (missing 'cookies' — export with: "
                         "npx playwright codegen --save-storage=state.json <url>)")
    bdir = browser_dir(ws_root)
    bdir.mkdir(parents=True, exist_ok=True)
    dest = bdir / STATE_NAME
    fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
    return {"cookies": len(obj.get("cookies") or []),
            "origins": len(obj.get("origins") or [])}
