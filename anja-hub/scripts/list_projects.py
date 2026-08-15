#!/usr/bin/env python3
"""list_projects.py — elenca workspace del registry hub (Fase 22).

Conservato nome `list_projects` per back-compat. Output augmenta ogni progetto con
`workspace_kind: hub | internal | external` letto da `<hub>/workspaces/<name>.meta.yaml`.
"""

import argparse
import json
import sys
from pathlib import Path


def _read_workspace_kind(hub: Path, name: str) -> str:
    """Legge `kind` da `<hub>/workspaces/<name>.meta.yaml`. Fallback detection da path."""
    meta = hub / "workspaces" / f"{name}.meta.yaml"
    if meta.is_file():
        try:
            for line in meta.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("kind:"):
                    return line.split(":", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass
    p = hub / "workspaces" / name
    if p.is_symlink():
        return "external"
    if p.is_dir():
        return "internal"
    return "external"


def main() -> None:
    p = argparse.ArgumentParser(description="List anja hub workspaces (legacy: projects).")
    p.add_argument("--hub", required=True, help="path to hub directory")
    p.add_argument("--json", action="store_true", help="output JSON instead of table")
    args = p.parse_args()

    hub = Path(args.hub).resolve()
    registry_path = hub / "config" / "projects.json"
    if not registry_path.is_file():
        sys.exit(f"ERROR: registry not found: {registry_path}")

    with registry_path.open(encoding="utf-8") as f:
        registry = json.load(f)

    projects = registry["projects"]

    # Augment with workspace_kind (Fase 22)
    for proj in projects:
        proj["workspace_kind"] = _read_workspace_kind(hub, proj.get("name", ""))

    if args.json:
        print(json.dumps(projects, indent=2, ensure_ascii=False))
        return

    if not projects:
        print("(nessun workspace registrato)")
        print()
        print("Usa: /anja-register --kind local --path <project-path>  (external)")
        print("oppure: workspace.create da chat con Anja  (internal)")
        return

    print(f"anja hub: {len(projects)} workspace registrati")
    print()
    print(f"  {'NAME':<28} {'TYPE':<10} {'WS-KIND':<10} {'LOC':<7} {'LAST SYNC':<22} LOCATION")
    print(f"  {'-' * 28} {'-' * 10} {'-' * 10} {'-' * 7} {'-' * 22} {'-' * 30}")
    for p in projects:
        loc = p["location"]
        if loc["kind"] == "local":
            location = loc.get("path", "")
        else:
            location = f"{loc.get('host', '?')}:{loc.get('path', '?')}"
        last = p.get("last_sync") or "(mai)"
        if last != "(mai)":
            last = last[:19]
        ws_kind = p.get("workspace_kind", "?")
        print(
            f"  {p['name']:<28} {p.get('type', '?'):<10} "
            f"{ws_kind:<10} {loc['kind']:<7} {last:<22} {location}"
        )


if __name__ == "__main__":
    main()
