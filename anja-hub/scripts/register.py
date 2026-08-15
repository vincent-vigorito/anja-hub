#!/usr/bin/env python3
"""
register.py — aggiunge un progetto al registry del hub anja.

Modalità v1:
  - --kind local --path <project-path>

SSH (--kind ssh) verrà aggiunta in fase successiva.
"""

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

META_KEYS = ("id", "name", "type", "created", "init_mode")


def read_meta_yaml(meta_path: Path):
    if not meta_path.is_file():
        return None
    text = meta_path.read_text(encoding="utf-8")
    info = {}
    for key in META_KEYS:
        m = re.search(rf'^\s*{key}:\s*"?([^"\n]+?)"?\s*$', text, re.M)
        if m:
            info[key] = m.group(1)
    m = re.search(r'^\s*tags:\s*\[([^\]]*)\]', text, re.M)
    if m:
        raw = m.group(1).strip()
        info["tags"] = [t.strip().strip("\"'") for t in raw.split(",") if t.strip()]
    else:
        info["tags"] = []
    return info


def load_registry(hub_root: Path):
    path = hub_root / "config" / "projects.json"
    if not path.is_file():
        sys.exit(f"ERROR: registry not found: {path}. Run /anja-hub-init first.")
    with path.open(encoding="utf-8") as f:
        return json.load(f), path


def save_registry(registry: dict, registry_path: Path) -> None:
    with registry_path.open("w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)
        f.write("\n")


def append_log(hub_root: Path, line: str) -> None:
    log_path = hub_root / "cross" / "log.md"
    if not log_path.is_file():
        return
    with log_path.open("a", encoding="utf-8") as f:
        f.write("\n" + line + "\n")


def register_local(hub_root: Path, project_path: Path) -> None:
    project_path = project_path.resolve()
    anjawiki = project_path / ".anjawiki"
    if not anjawiki.is_dir():
        sys.exit(f"ERROR: .anjawiki/ not found in {project_path}")

    meta = read_meta_yaml(anjawiki / "meta.yaml")
    if not meta or "id" not in meta or "name" not in meta:
        sys.exit("ERROR: cannot read meta.yaml or missing 'id'/'name' fields")

    registry, registry_path = load_registry(hub_root)

    for p in registry["projects"]:
        if p["id"] == meta["id"]:
            sys.exit(
                f"ERROR: project '{meta['name']}' (id {meta['id']}) already registered. "
                f"Use unregister first, or use a different project."
            )
        if p["name"] == meta["name"]:
            sys.exit(
                f"ERROR: name '{meta['name']}' already in use by id {p['id']}. "
                f"Pick another name or unregister the existing one."
            )

    entry = {
        "id": meta["id"],
        "name": meta["name"],
        "type": meta.get("type", ""),
        "tags": meta.get("tags", []),
        "location": {
            "kind": "local",
            "path": str(project_path),
        },
        "last_sync": None,
        "description": "",
    }
    registry["projects"].append(entry)
    save_registry(registry, registry_path)

    projects_dir = hub_root / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    link = projects_dir / meta["name"]
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(anjawiki)

    today = date.today().isoformat()
    append_log(
        hub_root,
        f"## [{today}] register | {meta['name']} (kind=local, id={meta['id']})",
    )

    print(f"[anja-hub] progetto '{meta['name']}' registrato.")
    print(f"  ID:       {meta['id']}")
    print(f"  Type:     {meta.get('type', '?')}")
    print(f"  Tags:     {meta.get('tags', [])}")
    print(f"  Path:     {project_path}")
    print(f"  Symlink:  {link} → {anjawiki}")


def main() -> None:
    p = argparse.ArgumentParser(description="Register a project to anja hub.")
    p.add_argument("--hub", required=True, help="path to hub directory")
    p.add_argument("--kind", choices=("local", "ssh"), default="local")
    p.add_argument("--path", required=True, help="local path (kind=local) or remote path (kind=ssh)")
    p.add_argument("--host", help="SSH host (required if --kind=ssh)")
    args = p.parse_args()

    hub = Path(args.hub).resolve()
    if not hub.is_dir():
        sys.exit(f"ERROR: hub not found: {hub}")

    if args.kind == "ssh":
        sys.exit("SSH support not implemented in MVP. Use --kind local for now.")

    register_local(hub, Path(args.path))


if __name__ == "__main__":
    main()
