#!/usr/bin/env python3
"""migrate_workspace_routines.py — Fase 22.10.

Sposta routine in `<hub>/routines/` con `scope: project:<name>` verso
`<hub>/workspaces/<name>/.anjawiki/routines/<routine>.yaml`.

Usage:
  python3 migrate_workspace_routines.py --hub ~/Documents/TEST-HUB [--dry-run]

Idempotente. Skip se workspace non esiste o file destinazione già presente.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path


def read_scope(yaml_path: Path) -> str:
    """Estrae il campo `scope` dal yaml (parser minimale)."""
    try:
        for line in yaml_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("scope:"):
                v = line.split(":", 1)[1].strip().strip('"').strip("'")
                return v
    except Exception:
        return ""
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    hub = Path(args.hub).expanduser().resolve()
    if not hub.is_dir():
        print(f"ERROR: hub not found: {hub}", file=sys.stderr)
        sys.exit(1)

    hub_routines = hub / "routines"
    if not hub_routines.is_dir():
        print(json.dumps({"status": "ok", "moved": [], "skipped": [], "errors": ["no routines/"]}))
        return

    report = {"hub": str(hub), "dry_run": args.dry_run, "moved": [], "skipped": [], "errors": []}

    for yf in sorted(hub_routines.glob("*.yaml")):
        scope = read_scope(yf)
        if not scope.startswith("project:"):
            continue
        ws_name = scope.split(":", 1)[1].strip()
        if not re.match(r"^[a-z0-9][a-z0-9_-]*$", ws_name):
            report["errors"].append({"file": yf.name, "reason": f"invalid ws name: {ws_name}"})
            continue
        ws_root = hub / "workspaces" / ws_name
        if not ws_root.exists() and not ws_root.is_symlink():
            report["skipped"].append({"file": yf.name, "reason": f"workspace '{ws_name}' not found"})
            continue
        # Risolvi target dir (segue symlink per external)
        if ws_root.is_symlink():
            ws_anjawiki = ws_root.resolve()
        else:
            ws_anjawiki = ws_root / ".anjawiki"
        target_dir = ws_anjawiki / "routines"
        target_path = target_dir / yf.name
        if target_path.exists():
            report["skipped"].append({"file": yf.name, "reason": "already exists in workspace"})
            continue

        if args.dry_run:
            report["moved"].append({"file": yf.name, "from": str(yf), "to": str(target_path), "action": "would_move"})
            continue

        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(yf), str(target_path))
        # README placeholder
        readme = target_dir / "README.md"
        if not readme.exists():
            readme.write_text(
                f"# routines/\n\nRoutine schedulate del workspace `{ws_name}` (auto-scope: `project:{ws_name}`).\nPortabili col workspace.\n",
                encoding="utf-8",
            )
        report["moved"].append({"file": yf.name, "from": str(yf), "to": str(target_path), "action": "moved"})

    report["status"] = "ok"
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
