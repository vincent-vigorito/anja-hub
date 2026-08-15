"""Self-health dell'always-on (M-SelfHealth).

Raccoglie lo stato dei componenti che possono degradare *in silenzio* — daemon
asyncio, MCP server, code-index, provider LLM, disco — in una lista di check
uniformi. Il server lo chiama in loop e pubblica un alert (push) quando un check
peggiora; `collect()` è puro (riceve lo stato dei daemon dal server, che ha gli
handle dei task) così è testabile in isolamento.

    collect(hub_path, daemons={"telegram": True, ...}) -> {status, ts, checks:[...]}
"""
from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

INDEX_STALE_DAYS = 7
DISK_WARN_GB = 1.0
DISK_ERR_GB = 0.2
PROVIDER_ENVS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "OPENROUTER_API_KEY")

SEV = {"ok": 0, "warn": 1, "error": 2}


def _chk(name: str, ok: bool, detail: str, severity: str = "error") -> dict:
    return {"name": name, "ok": ok, "severity": "ok" if ok else severity, "detail": detail}


def _secrets(hub_path: Path) -> dict:
    f = Path(hub_path) / ".secrets.env"
    out: dict = {}
    if f.is_file():
        for raw in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _check_daemons(daemons: dict) -> list:
    checks = []
    for name, alive in (daemons or {}).items():
        checks.append(_chk(f"daemon:{name}", bool(alive),
                           "vivo" if alive else "morto o non avviato", severity="error"))
    return checks


def _check_mcp(hub_path: Path) -> list:
    p = Path(hub_path) / ".mcp.json"
    if not p.is_file():
        return [_chk("mcp:config", True, "nessun .mcp.json (ok se non usi MCP)", severity="warn")]
    try:
        servers = (json.loads(p.read_text(encoding="utf-8")) or {}).get("mcpServers", {})
    except Exception as e:
        return [_chk("mcp:config", False, f".mcp.json illeggibile: {e}", severity="error")]
    checks = []
    for name, cfg in servers.items():
        args = cfg.get("args") or []
        script = next((a for a in args if str(a).endswith(".py")), None)
        if script and not Path(script).is_file():
            checks.append(_chk(f"mcp:{name}", False, f"script assente: {script}", severity="error"))
        else:
            checks.append(_chk(f"mcp:{name}", True, "config + script presenti", severity="warn"))
    return checks or [_chk("mcp:config", True, "nessun server in mcpServers", severity="warn")]


def _check_index(hub_path: Path) -> list:
    p = Path(hub_path) / ".anjawiki" / "code-index.db"
    if not p.is_file():
        return [_chk("index:code", True, "code-index.db assente (non indicizzato)", severity="warn")]
    age_days = (time.time() - p.stat().st_mtime) / 86400
    ok = age_days <= INDEX_STALE_DAYS
    return [_chk("index:code", ok, f"ultimo update {age_days:.1f}gg fa"
                 + ("" if ok else f" (stale > {INDEX_STALE_DAYS}gg → /anja-index-code)"), severity="warn")]


def _check_providers(hub_path: Path) -> list:
    sec = _secrets(hub_path)
    import os
    present = [e for e in PROVIDER_ENVS if sec.get(e) or os.environ.get(e)]
    ok = bool(present)
    names = ", ".join(e.replace("_API_KEY", "").lower() for e in present) or "nessuno"
    return [_chk("providers:keys", ok, f"chiavi presenti: {names}", severity="error")]


def _check_disk(hub_path: Path) -> list:
    try:
        free_gb = shutil.disk_usage(str(hub_path)).free / (1024 ** 3)
    except Exception as e:
        return [_chk("disk:free", True, f"non determinabile: {e}", severity="warn")]
    if free_gb < DISK_ERR_GB:
        return [_chk("disk:free", False, f"solo {free_gb:.2f} GB liberi", severity="error")]
    if free_gb < DISK_WARN_GB:
        return [_chk("disk:free", False, f"{free_gb:.2f} GB liberi (sotto {DISK_WARN_GB})", severity="warn")]
    return [_chk("disk:free", True, f"{free_gb:.1f} GB liberi", severity="ok")]


def collect(hub_path, daemons: dict | None = None) -> dict:
    hub_path = Path(hub_path)
    checks = (_check_daemons(daemons or {}) + _check_mcp(hub_path) + _check_index(hub_path)
              + _check_providers(hub_path) + _check_disk(hub_path))
    worst = max((SEV[c["severity"]] for c in checks), default=0)
    status = {0: "ok", 1: "degraded", 2: "error"}[worst]
    return {
        "status": status,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "failing": [c for c in checks if not c["ok"]],
        "checks": checks,
    }
