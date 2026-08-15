#!/usr/bin/env python3
"""migrate_aria_to_anja_sessions.py — Riscrive label 'ARIA' -> 'ANJA' nei file
<hub>/sessions/*.md mirror'd prima del rebrand 2026-05-16.

Pattern target:
- '[ARIA]'        (server.py compact_summary)
- '### [ARIA]'    (session_mirror.py role headers)

Uso:
  python3 migrate_aria_to_anja_sessions.py <hub-path> [--dry-run]
"""
import argparse
import re
import sys
from pathlib import Path

PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'### \[ARIA\]'), '### [ANJA]'),
    (re.compile(r'\[ARIA\]'), '[ANJA]'),
]


def migrate(hub: Path, dry_run: bool) -> int:
    sess_dir = hub / "sessions"
    if not sess_dir.is_dir():
        print(f"[migrate] no sessions/ in {hub}, nothing to do")
        return 0
    n_files = 0
    n_replaced_total = 0
    for md in sorted(sess_dir.glob("*.md")):
        text = md.read_text(encoding="utf-8")
        new_text = text
        for pat, repl in PATTERNS:
            new_text = pat.sub(repl, new_text)
        if new_text != text:
            n_replaced = text.count('[ARIA]')
            n_replaced_total += n_replaced
            if not dry_run:
                md.write_text(new_text, encoding="utf-8")
            print(f"  {'[dry] ' if dry_run else ''}{md.name}: {n_replaced} label{'s' if n_replaced != 1 else ''}")
            n_files += 1
    print(f"[migrate] {n_files} file{'s' if n_files != 1 else ''} {'would be ' if dry_run else ''}updated, "
          f"{n_replaced_total} total label replacements")
    return n_files


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("hub_path", help="Path al hub (es. ~/Documents/anja-test-hub)")
    ap.add_argument("--dry-run", action="store_true", help="Mostra cosa cambierebbe, non scrive")
    args = ap.parse_args()
    hub = Path(args.hub_path).expanduser().resolve()
    if not hub.is_dir():
        print(f"[err] hub path not found: {hub}")
        sys.exit(1)
    migrate(hub, args.dry_run)


if __name__ == "__main__":
    main()
