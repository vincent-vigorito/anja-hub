#!/usr/bin/env python3
"""
sync.py — riconcilia symlink (locale) e mirror (SSH, futuro) del hub anja.
"""

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path


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


def sync_local(hub_root: Path, entry: dict):
    project_path = Path(entry["location"]["path"])
    src = project_path / ".anjawiki"
    if not src.is_dir():
        return False, f"source missing: {src}"

    link = hub_root / "projects" / entry["name"]
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(src)
    return True, f"symlink → {src}"


def sync_ssh(hub_root: Path, entry: dict):
    return False, "SSH sync not implemented in MVP"


def main() -> None:
    p = argparse.ArgumentParser(description="Sync anja hub projects.")
    p.add_argument("--hub", required=True, help="path to hub directory")
    p.add_argument("--name", help="sync only this project")
    p.add_argument("--all", action="store_true", help="sync all (default if --name omitted)")
    args = p.parse_args()

    hub = Path(args.hub).resolve()
    if not hub.is_dir():
        sys.exit(f"ERROR: hub not found: {hub}")

    registry, registry_path = load_registry(hub)
    targets = registry["projects"]
    if args.name:
        targets = [p for p in targets if p["name"] == args.name]
        if not targets:
            sys.exit(f"ERROR: no project named '{args.name}' in registry")

    if not targets:
        print("[anja-hub] nessun progetto da sync")
        return

    print(f"[anja-hub] sync {'all' if not args.name else args.name} ({len(targets)} progetti)")
    print()

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ok_count = 0
    fail_count = 0

    for entry in targets:
        kind = entry["location"]["kind"]
        if kind == "local":
            ok, msg = sync_local(hub, entry)
        elif kind == "ssh":
            ok, msg = sync_ssh(hub, entry)
        else:
            ok, msg = False, f"unknown kind: {kind}"

        status = "✓" if ok else "✗"
        print(f"  {status} {entry['name']:<30} ({kind})  {msg}")

        if ok:
            entry["last_sync"] = now_iso
            ok_count += 1
        else:
            fail_count += 1

    save_registry(registry, registry_path)

    today = date.today().isoformat()
    append_log(
        hub,
        f"## [{today}] sync | {len(targets)} progetti ({ok_count} ok, {fail_count} failed)",
    )

    print()
    print(f"Registry aggiornato. last_sync = {now_iso}")


if __name__ == "__main__":
    main()
