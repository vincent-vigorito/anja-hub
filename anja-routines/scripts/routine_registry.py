#!/usr/bin/env python3
"""
routine_registry.py — discovery e stato runtime delle routines.

Le routine vivono come file yaml in:
    <hub>/routines/*.yaml

Lo stato runtime (enabled/disabled, last_run, last_status) sta in:
    <hub>/routines/routines.json (index)

I run log sono markdown append-only in:
    <hub>/routines/runs/<name>-<timestamp>.md

Secrets:
    <hub>/routines/.secrets.env (gitignore)

Esempio struttura state.json:
{
  "news-arxiv": {
    "enabled": true,
    "last_run": "2026-05-03T08:00:12Z",
    "last_status": "ok",
    "last_log": "logs/2026-05-03/news-arxiv-080012.log",
    "last_duration_sec": 47
  }
}
"""

import json
import os
from pathlib import Path
from typing import Optional

from routine_validate import load_and_validate


def find_hub_root(start: Optional[Path] = None) -> Path:
    """Risale dalla cwd cercando una dir che contenga un `.anjawiki-hub` marker
    (oppure `routines/` se già esiste). Override via env ANJA_HUB."""
    env = os.environ.get("ANJA_HUB")
    if env:
        p = Path(env).expanduser().resolve()
        return p

    cur = (start or Path.cwd()).resolve()
    for parent in [cur, *cur.parents]:
        # marker primario: file .anjawiki-hub o registry hub.json
        if (parent / ".anjawiki-hub").exists():
            return parent
        if (parent / "registry" / "hub.json").is_file():
            return parent
        # marker fallback: dir routines/ già creata
        if (parent / "routines").is_dir():
            return parent

    # fallback noto in dev env
    candidates = [
        Path.home() / "Documents" / "TEST-HUB",
        Path.home() / "Documents" / "llm-wiki" / "anja-hub",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    raise RuntimeError(
        "no anja hub found. Run from inside a hub or set ANJA_HUB env var."
    )


def routines_dir(hub: Optional[Path] = None) -> Path:
    h = hub or find_hub_root()
    return h / "routines"


def state_path(hub: Optional[Path] = None) -> Path:
    return routines_dir(hub) / "routines.json"


def runs_dir(hub: Optional[Path] = None) -> Path:
    return routines_dir(hub) / "runs"


def secrets_path(hub: Optional[Path] = None) -> Path:
    return routines_dir(hub) / ".secrets.env"


def load_state(hub: Optional[Path] = None) -> dict:
    sp = state_path(hub)
    if not sp.is_file():
        return {}
    try:
        return json.loads(sp.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict, hub: Optional[Path] = None) -> None:
    sp = state_path(hub)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def workspace_routines_dirs(hub: Optional[Path] = None) -> list[tuple[Path, str]]:
    """Fase 22.10 — Ritorna lista (path, workspace_name) per ogni workspace con `.anjawiki/routines/`.

    Scansiona `<hub>/workspaces/<name>/.anjawiki/routines/` (internal) e segue symlinks (external).
    """
    h = hub or find_hub_root()
    ws_root = h / "workspaces"
    out: list[tuple[Path, str]] = []
    if not ws_root.is_dir():
        return out
    for ws_path in ws_root.iterdir():
        if ws_path.name.startswith(".") or ws_path.name.endswith(".meta.yaml"):
            continue
        # symlink → segue
        try:
            anjawiki = ws_path / ".anjawiki" if not ws_path.is_symlink() else ws_path.resolve()
            routines_subdir = anjawiki / "routines"
            if routines_subdir.is_dir():
                out.append((routines_subdir, ws_path.name))
        except Exception:
            continue
    return out


def list_routines(hub: Optional[Path] = None) -> list:
    """Ritorna lista di dict {name, file, yaml, state, valid, source}.

    Fase 22.10: scansiona sia `<hub>/routines/` sia ogni `<workspace>/.anjawiki/routines/`.
    `source` = 'hub' o 'workspace:<name>'. Su conflict same name, hub vince.
    """
    state = load_state(hub)
    out = []
    seen_names: set[str] = set()

    # 1. Hub-level routines
    rd = routines_dir(hub)
    if rd.is_dir():
        for yf in sorted(rd.glob("*.yaml")):
            if yf.name.startswith("."):
                continue
            obj = load_and_validate(yf)
            valid = obj is not None
            name = (obj or {}).get("name") or yf.stem
            default_enabled = bool((obj or {}).get("enabled", True))
            state_entry = dict(state.get(name, {}))
            state_entry.setdefault("enabled", default_enabled)
            out.append({
                "name": name,
                "file": str(yf),
                "yaml": obj,
                "valid": valid,
                "state": state_entry,
                "source": "hub",
            })
            seen_names.add(name)

    # 2. Workspace-level routines (Fase 22.10)
    for ws_routines_dir, ws_name in workspace_routines_dirs(hub):
        for yf in sorted(ws_routines_dir.glob("*.yaml")):
            if yf.name.startswith("."):
                continue
            # Workspace routines: auto-inject scope=project:<name> PRIMA della validation
            # se manca, così possono omettere il campo nel YAML
            raw_yaml = _load_yaml_raw(yf)
            if raw_yaml and not raw_yaml.get("scope"):
                raw_yaml["scope"] = f"project:{ws_name}"
            obj = _validate_with_scope_injected(yf, raw_yaml)
            valid = obj is not None
            name = (obj or raw_yaml or {}).get("name") or yf.stem
            if name in seen_names:
                print(f"[routine_registry] WARN: workspace routine '{name}' "
                      f"({ws_name}) shadowed by hub routine of same name")
                continue
            default_enabled = bool((obj or {}).get("enabled", True))
            state_entry = dict(state.get(name, {}))
            state_entry.setdefault("enabled", default_enabled)
            out.append({
                "name": name,
                "file": str(yf),
                "yaml": obj,
                "valid": valid,
                "state": state_entry,
                "source": f"workspace:{ws_name}",
            })
            seen_names.add(name)
    return out


def _load_yaml_raw(yf: Path) -> Optional[dict]:
    """Load YAML raw senza validation (per scope injection workspace routines)."""
    try:
        import yaml as _yaml
        with yf.open(encoding="utf-8") as f:
            data = _yaml.safe_load(f)
        return data if isinstance(data, dict) else None
    except ImportError:
        # Fallback: parser minimale (stdlib only)
        try:
            import json as _json
            text = yf.read_text(encoding="utf-8")
            return _json.loads(text)
        except Exception:
            return None
    except Exception:
        return None


def _validate_with_scope_injected(yf: Path, raw_yaml: Optional[dict]):
    """Valida con scope già iniettato. Usa validate_routine direct."""
    if not raw_yaml:
        return None
    try:
        from routine_validate import validate_routine
        errors, _warnings = validate_routine(raw_yaml)
        if errors:
            print(f"[routine_registry] workspace routine {yf.name} invalid: {errors}")
            return None
        return raw_yaml
    except (ImportError, AttributeError):
        return load_and_validate(yf)


def get_routine(name: str, hub: Optional[Path] = None) -> Optional[dict]:
    for r in list_routines(hub):
        if r["name"] == name:
            return r
    return None


def set_enabled(name: str, enabled: bool, hub: Optional[Path] = None) -> bool:
    state = load_state(hub)
    entry = state.get(name, {})
    entry["enabled"] = bool(enabled)
    state[name] = entry
    save_state(state, hub)
    return True


def record_run(
    name: str,
    status: str,
    log_path: str = "",
    duration_sec: float = 0.0,
    extra: Optional[dict] = None,
    hub: Optional[Path] = None,
) -> None:
    """Aggiorna state.json dopo un run."""
    from datetime import datetime, timezone
    state = load_state(hub)
    entry = state.get(name, {"enabled": True})
    entry["last_run"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry["last_status"] = status
    entry["last_log"] = log_path
    entry["last_duration_sec"] = round(duration_sec, 2)
    if extra:
        entry.update(extra)
    state[name] = entry
    save_state(state, hub)


# =================================================================
# CLI per debug / liste
# =================================================================

def _format_table(rs: list) -> str:
    if not rs:
        return "(no routines)"
    rows = []
    for r in rs:
        st = r["state"]
        en = "✓" if st.get("enabled", True) else "✗"
        ok = "ok" if r["valid"] else "INVALID"
        last = st.get("last_run", "—")
        last_st = st.get("last_status", "—")
        sched = (r["yaml"] or {}).get("schedule", "—")
        scope = (r["yaml"] or {}).get("scope", "—")
        rows.append((en, r["name"], scope, sched, last, last_st, ok))
    widths = [max(len(str(c)) for c in col) for col in zip(*rows)]
    headers = ["EN", "NAME", "SCOPE", "SCHEDULE", "LAST RUN", "STATUS", "VALID"]
    widths = [max(w, len(h)) for w, h in zip(widths, headers)]
    lines = ["  ".join(h.ljust(w) for h, w in zip(headers, widths))]
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append("  ".join(str(c).ljust(w) for c, w in zip(row, widths)))
    return "\n".join(lines)


def main():
    import argparse
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    e = sub.add_parser("enable")
    e.add_argument("name")
    d = sub.add_parser("disable")
    d.add_argument("name")
    sub.add_parser("hub")
    args = p.parse_args()

    if args.cmd == "list":
        print(_format_table(list_routines()))
    elif args.cmd == "enable":
        set_enabled(args.name, True)
        print(f"✅ {args.name} enabled")
    elif args.cmd == "disable":
        set_enabled(args.name, False)
        print(f"✅ {args.name} disabled")
    elif args.cmd == "hub":
        print(find_hub_root())


if __name__ == "__main__":
    main()
