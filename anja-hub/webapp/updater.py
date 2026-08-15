"""updater.py — F-BackupDR Fase 3 (fondamenta) — versioning + migrazioni dell'hub.

Principio: **codice ≠ dati**. Un update sostituisce il CODICE (repo AnjaHub) e lascia i
DATI (`<hub>`) intatti, migrandoli di schema SOLO se serve. Questo modulo è il layer
COMUNE a qualsiasi transport (git/container) e trigger (manuale/auto):

  1. **Versioning**: `VERSION` (repo) = versione piattaforma; `<hub>/config.json:code_version`
     = versione con cui l'hub è stato migrato l'ultima volta → l'update sa da→a.
  2. **Migrazioni**: `migrations/NNNN_*.py` con `up(hub_path)` idempotente; il runner applica
     solo le pending, tracciandole in `<hub>/.anja-migrations.json`. Fail-safe (una migrazione
     che solleva ferma il flusso e NON viene marcata applicata).
  3. **apply()**: BACKUP pre-update (F-BackupDR, reason='pre-update', mai potato) → migrazioni
     → bump `code_version`. È il flusso sicuro "il codice nuovo tocca i vecchi .db".

L'orchestrazione git (fetch/checkout/restart/rollback) è il transport, montato sopra questo
layer in un secondo passo. Stdlib only.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]   # webapp → anja-hub → AnjaHub
VERSION_FILE = _REPO_ROOT / "VERSION"
MIGRATIONS_STATE = ".anja-migrations.json"


# ----------------------------------------------------------------------
# Versioning
# ----------------------------------------------------------------------

def current_version() -> str:
    """Versione del CODICE (piattaforma): file VERSION, fallback '0.0.0-dev'."""
    try:
        v = VERSION_FILE.read_text(encoding="utf-8").strip()
        return v or "0.0.0-dev"
    except OSError:
        return "0.0.0-dev"


def _parse(v: str) -> tuple:
    """'0.20.0' → (0,20,0). Ignora suffissi (-dev, -rc1). Non-numerico → 0."""
    core = v.split("-", 1)[0].split("+", 1)[0]
    parts = []
    for p in core.split("."):
        parts.append(int(p) if p.isdigit() else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_newer(a: str, b: str) -> bool:
    """True se la versione `a` è più recente di `b`."""
    return _parse(a) > _parse(b)


def _hub_config_path(hub: Path) -> Path:
    return Path(hub) / "config.json"


def hub_version(hub: Path) -> Optional[str]:
    """Versione con cui l'hub è stato migrato l'ultima volta (config.json:code_version)."""
    try:
        cfg = json.loads(_hub_config_path(hub).read_text(encoding="utf-8"))
        return cfg.get("code_version")
    except (OSError, json.JSONDecodeError):
        return None


def set_hub_version(hub: Path, version: str) -> None:
    """Scrive code_version in config.json preservando il resto (merge non distruttivo)."""
    p = _hub_config_path(hub)
    cfg = {}
    if p.is_file():
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cfg = {}
    cfg["code_version"] = version
    p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------
# Migration runner
# ----------------------------------------------------------------------

def _discover_migrations() -> list[tuple[str, object]]:
    """Moduli in migrations/ ordinati per prefisso numerico. Ognuno espone up(hub_path)."""
    import migrations  # package accanto a questo modulo
    found = []
    for mod in pkgutil.iter_modules(migrations.__path__):
        name = mod.name
        if name.startswith("_") or not name[0].isdigit():
            continue
        m = importlib.import_module(f"migrations.{name}")
        if hasattr(m, "up"):
            found.append((name, m))
    found.sort(key=lambda t: t[0])
    return found


def _state_path(hub: Path) -> Path:
    return Path(hub) / MIGRATIONS_STATE


def applied_migrations(hub: Path) -> list[dict]:
    try:
        return json.loads(_state_path(hub).read_text(encoding="utf-8")).get("applied", [])
    except (OSError, json.JSONDecodeError):
        return []


def _record_applied(hub: Path, mig_id: str, version: str) -> None:
    state = {"applied": applied_migrations(hub)}
    state["applied"].append({
        "id": mig_id, "at": datetime.now(timezone.utc).isoformat(), "code_version": version,
    })
    _state_path(hub).write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def pending_migrations(hub: Path) -> list[str]:
    done = {a["id"] for a in applied_migrations(hub)}
    return [name for name, _ in _discover_migrations() if name not in done]


def run_migrations(hub: Path) -> dict:
    """Applica in ordine le migrazioni pending, tracciandole. Fail-safe: alla prima che
    solleva, ferma e ritorna l'errore (le già applicate restano registrate)."""
    hub = Path(hub)
    done = {a["id"] for a in applied_migrations(hub)}
    ran, version = [], current_version()
    for name, mod in _discover_migrations():
        if name in done:
            continue
        try:
            mod.up(hub)
        except Exception as e:
            return {"ok": False, "applied": ran, "failed": name, "error": f"{type(e).__name__}: {e}"}
        _record_applied(hub, name, version)
        ran.append(name)
    return {"ok": True, "applied": ran}


# ----------------------------------------------------------------------
# Check + apply (il flusso "fondamenta")
# ----------------------------------------------------------------------

def check(hub: Path) -> dict:
    """Stato di aggiornamento dell'hub: versioni + migrazioni pending."""
    hub = Path(hub)
    code, hubv = current_version(), hub_version(hub)
    pend = pending_migrations(hub)
    return {
        "code_version": code,
        "hub_version": hubv,
        "needs_migration": bool(pend) or (hubv is not None and is_newer(code, hubv)),
        "pending_migrations": pend,
        "version_behind": hubv is not None and is_newer(code, hubv),
    }


def apply(hub: Path, *, backup: bool = True, extra_dirs=None) -> dict:
    """Applica l'update all'hub: BACKUP pre-update → migrazioni → bump code_version.
    NON tocca il codice (quello lo porta il transport); assume il codice già alla versione
    corrente. `extra_dirs`: dir fuori-hub da includere nel backup (es. conversazioni webapp)."""
    hub = Path(hub)
    result = {"from": hub_version(hub), "to": current_version()}
    if backup:
        try:
            import backup as backup_mod
            b = backup_mod.create_backup(hub, reason="pre-update", extra_dirs=extra_dirs)
            result["backup"] = {"ok": b.get("ok"), "archive": b.get("archive"), "error": b.get("error")}
            if not b.get("ok"):
                return {"ok": False, "stage": "backup", **result}
        except Exception as e:
            return {"ok": False, "stage": "backup", "error": f"{type(e).__name__}: {e}", **result}
    mig = run_migrations(hub)
    result["migrations"] = mig
    if not mig["ok"]:
        return {"ok": False, "stage": "migrations", **result}
    set_hub_version(hub, current_version())
    return {"ok": True, **result}


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def _cli(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="updater", description="Versioning + migrazioni hub anja")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("version", help="versione della piattaforma")
    s = sub.add_parser("status", help="stato update di un hub"); s.add_argument("hub")
    m = sub.add_parser("apply", help="backup + migrazioni + bump versione"); m.add_argument("hub")
    m.add_argument("--no-backup", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "version":
        print(current_version())
    elif a.cmd == "status":
        print(json.dumps(check(Path(a.hub)), indent=2, ensure_ascii=False))
    elif a.cmd == "apply":
        res = apply(Path(a.hub), backup=not a.no_backup)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0 if res.get("ok") else 1
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv[1:]))
