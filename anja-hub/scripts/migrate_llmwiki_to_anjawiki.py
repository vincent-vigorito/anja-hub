#!/usr/bin/env python3
"""migrate_llmwiki_to_anjawiki.py — Rinomina la cartella `.llmwiki/` -> `.anjawiki/`
nei progetti registrati nel hub anja.

Cerca:
- `<hub>/projects/*/.llmwiki`        (symlink o cartelle: rename in-place)
- `<hub>/workspaces/*/.llmwiki`      (cartelle interne workspace)
- `<hub>/.llmwiki` (raro, se hub usa wiki proprio)

Aggiorna anche il `<hub>/config/projects.json` se referenzia path `.llmwiki`.

NOTA: il rename del filesystem **non riguarda i progetti non-registrati nel hub**.
Per quelli devi rinominare manualmente, oppure passare il path al --extra-path.

Uso:
  python3 migrate_llmwiki_to_anjawiki.py <hub-path> [--dry-run] [--extra-path PATH]...

Stdlib only.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def rename_dir_or_link(old: Path, dry_run: bool) -> bool:
    """Rinomina old -> old.parent / '.anjawiki'. Funziona sia per cartelle che symlink."""
    new = old.parent / ".anjawiki"
    if new.exists():
        print(f"  [skip] {new} esiste già")
        return False
    if not (old.exists() or old.is_symlink()):
        return False
    if dry_run:
        print(f"  [dry] {old} -> {new}")
        return True
    os.rename(old, new)
    print(f"  {old} -> {new}")
    return True


def migrate_hub(hub: Path, extra_paths: list[Path], dry_run: bool) -> int:
    candidates: list[Path] = []
    for sub in ("projects", "workspaces"):
        d = hub / sub
        if not d.is_dir():
            continue
        # 1 livello: <hub>/{sub}/<name>/.llmwiki
        for child in d.iterdir():
            llm = child / ".llmwiki"
            if llm.exists() or llm.is_symlink():
                candidates.append(llm)
    # hub stesso
    hub_llm = hub / ".llmwiki"
    if hub_llm.exists() or hub_llm.is_symlink():
        candidates.append(hub_llm)
    # extra paths (es. dev env esterni al hub)
    for p in extra_paths:
        llm = (p / ".llmwiki") if (p / ".llmwiki").exists() else p if p.name == ".llmwiki" else None
        if llm and (llm.exists() or llm.is_symlink()):
            candidates.append(llm)

    n_done = 0
    for c in candidates:
        if rename_dir_or_link(c, dry_run):
            n_done += 1

    # Aggiorna registry config/projects.json (path string match)
    reg = hub / "config" / "projects.json"
    if reg.is_file():
        text = reg.read_text(encoding="utf-8")
        if ".llmwiki" in text:
            new_text = text.replace(".llmwiki", ".anjawiki")
            n_repl = text.count(".llmwiki")
            if not dry_run:
                reg.write_text(new_text, encoding="utf-8")
            print(f"  {'[dry] ' if dry_run else ''}{reg.relative_to(hub)}: {n_repl} path reference{'s' if n_repl != 1 else ''}")

    print(f"\n[migrate] {n_done} director{'ies' if n_done != 1 else 'y'} "
          f"{'would be ' if dry_run else ''}renamed")
    return n_done


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("hub_path", help="Path al hub (es. ~/Documents/anja-test-hub)")
    ap.add_argument("--dry-run", action="store_true", help="Mostra cosa cambierebbe, non scrive")
    ap.add_argument("--extra-path", action="append", default=[],
                    help="Path aggiuntivo da migrare (puo' essere ripetuto)")
    args = ap.parse_args()

    hub = Path(args.hub_path).expanduser().resolve()
    if not hub.is_dir():
        sys.exit(f"[err] hub path not found: {hub}")

    extras = [Path(p).expanduser().resolve() for p in args.extra_path]
    migrate_hub(hub, extras, args.dry_run)


if __name__ == "__main__":
    main()
