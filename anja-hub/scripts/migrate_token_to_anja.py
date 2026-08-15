#!/usr/bin/env python3
"""migrate_token_to_anja.py — Riscrive token legacy `swk_<hex12>` -> `anja_<uuid7>`
nei meta.yaml dei progetti registrati nel hub.

Aggiorna anche il registry `<hub>/config/projects.json` con i nuovi token,
preservando il mapping vecchio->nuovo durante una singola run (token usato
in piu' file punta allo stesso nuovo identifier).

Uso:
  python3 migrate_token_to_anja.py <hub-path> [--dry-run]

Nota: opt-in. Stdlib only.
"""
from __future__ import annotations

import argparse
import re
import secrets
import sys
import time
import uuid
from pathlib import Path

SWK_PATTERN = re.compile(r"\b(swk_[0-9a-f]{12})\b")


def _uuid7_canonical() -> str:
    ts_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = secrets.token_bytes(10)
    b = bytearray(16)
    b[0:6] = ts_ms.to_bytes(6, "big")
    b[6] = 0x70 | (rand[0] & 0x0F)
    b[7] = rand[1]
    b[8] = 0x80 | (rand[2] & 0x3F)
    b[9] = rand[3]
    b[10:16] = rand[4:10]
    h = b.hex()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _new_token() -> str:
    try:
        return f"anja_{uuid.uuid7()}"  # Python 3.14+
    except AttributeError:
        return f"anja_{_uuid7_canonical()}"


def migrate_file(path: Path, mapping: dict[str, str], dry_run: bool) -> int:
    """Riscrive tutte le occorrenze di token swk_ -> anja_ in `path`.
    Riusa `mapping` per garantire stabilita' cross-file (stesso swk_xxx -> stesso anja_yyy).
    Return: numero di token unici sostituiti.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return 0
    found = set(SWK_PATTERN.findall(text))
    if not found:
        return 0
    new_text = text
    for old in found:
        new = mapping.setdefault(old, _new_token())
        new_text = new_text.replace(old, new)
    if new_text != text and not dry_run:
        path.write_text(new_text, encoding="utf-8")
    return len(found)


def find_targets(hub: Path) -> list[Path]:
    """File candidati alla migration: meta.yaml dei progetti + registry hub."""
    targets: list[Path] = []
    for sub in ("projects", "workspaces"):
        d = hub / sub
        if not d.is_dir():
            continue
        for meta in d.rglob("meta.yaml"):
            targets.append(meta)
    reg = hub / "config" / "projects.json"
    if reg.is_file():
        targets.append(reg)
    return targets


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("hub_path", help="Path al hub (es. ~/Documents/anja-test-hub)")
    ap.add_argument("--dry-run", action="store_true", help="Mostra cosa cambierebbe, non scrive")
    args = ap.parse_args()

    hub = Path(args.hub_path).expanduser().resolve()
    if not hub.is_dir():
        sys.exit(f"[err] hub path not found: {hub}")

    targets = find_targets(hub)
    if not targets:
        print(f"[migrate] nessun target trovato in {hub} (no projects/, no workspaces/, no config/projects.json)")
        return

    mapping: dict[str, str] = {}
    files_changed = 0
    for t in targets:
        n = migrate_file(t, mapping, args.dry_run)
        if n:
            files_changed += 1
            print(f"  {'[dry] ' if args.dry_run else ''}{t.relative_to(hub)}: {n} token{'s' if n != 1 else ''}")

    print(f"[migrate] {files_changed} file{'s' if files_changed != 1 else ''} "
          f"{'would be ' if args.dry_run else ''}updated; "
          f"{len(mapping)} unique token{'s' if len(mapping) != 1 else ''} mapped")
    if mapping and not args.dry_run:
        print("\nToken mapping (vecchio -> nuovo):")
        for old, new in mapping.items():
            print(f"  {old} -> {new}")


if __name__ == "__main__":
    main()
