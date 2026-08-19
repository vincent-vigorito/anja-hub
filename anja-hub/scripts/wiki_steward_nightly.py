#!/usr/bin/env python3
"""wiki_steward_nightly.py — AnjaHub è CONSUMER dello steward di anjadev (H1).

Itera i wiki dell'hub e lancia `anjadev/scripts/steward.py --root <r> --apply` su ognuno:
  * hub stesso (hub-knowledge: `<hub>/.anjawiki/wiki` o `<hub>/wiki`, journal in `<hub>/sessions/`)
  * ogni `<hub>/workspaces/<ws>/` con `.anjawiki/`
Cap tempo totale (default 10 min): oltre, skip con log. Stampa un riepilogo di una riga
(per il messaggio Telegram della routine) + JSON dettagliato su stderr. Decision-trail:
un record `actor=steward` per ogni root toccato (best-effort).

Lanciato dalla routine `wiki-steward-nightly` (04:15, dopo il dreaming delle 04:00 —
due notturni, due tesi: dreaming = chi è l'utente, steward = cosa sa il repo).

Usage: python3 wiki_steward_nightly.py --hub <hub> [--dry-run] [--max-minutes 10] [--since 7d]
Env: ANJADEV_DIR (default ~/.claude/plugins/marketplaces/anjadev), ANJA_STEWARD_BIN, ANJA_STEWARD_MODEL.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _anjadev_dir() -> Path:
    env = os.environ.get("ANJADEV_DIR")
    return Path(env) if env else Path.home() / ".claude" / "plugins" / "marketplaces" / "anjadev"


def roots_for(hub: Path) -> list[tuple[str, Path]]:
    out = []
    if (hub / ".anjawiki" / "wiki").is_dir() or ((hub / "wiki").is_dir() and (hub / "sessions").is_dir()):
        out.append(("hub", hub))
    ws_root = hub / "workspaces"
    if ws_root.is_dir():
        for ws in sorted(ws_root.iterdir()):
            if ws.is_dir() and not ws.is_symlink() and (ws / ".anjawiki" / "wiki").is_dir():
                out.append((f"ws:{ws.name}", ws))
    return out


def run_one(steward: Path, root: Path, dry_run: bool, since: str, budget_sec: float) -> dict:
    cmd = [sys.executable, str(steward), "--root", str(root), "--since", since] + ([] if dry_run else ["--apply"])
    env = dict(os.environ, ANJA_JOURNAL="0", ANJA_AUTO_SUMMARY="0", ANJA_WIKI_EMBED="0")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=max(30, budget_sec), env=env)
    except subprocess.TimeoutExpired:
        return {"errors": ["timeout"], "patches_applied": 0, "distilled": 0, "compact": None, "clusters": []}
    try:
        return json.loads(r.stdout)
    except Exception:
        return {"errors": [f"no-json rc={r.returncode}: {(r.stderr or r.stdout)[-200:]}"], "patches_applied": 0,
                "distilled": 0, "compact": None, "clusters": []}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-minutes", type=float, default=10.0)
    ap.add_argument("--since", default="7d")
    args = ap.parse_args()
    hub = Path(args.hub).expanduser().resolve()
    steward = _anjadev_dir() / "scripts" / "steward.py"
    if not steward.is_file():
        print(f"wiki steward: anjadev non trovato ({steward}) — set ANJADEV_DIR", file=sys.stderr)
        print("🌙 wiki steward — anjadev steward.py non trovato (ANJADEV_DIR?)")
        sys.exit(2)
    deadline = time.time() + args.max_minutes * 60
    results, skipped = [], []
    for label, root in roots_for(hub):
        left = deadline - time.time()
        if left < 30:
            skipped.append(label)
            continue
        rep = run_one(steward, root, args.dry_run, args.since, left)
        rep["_label"] = label
        results.append(rep)
        if not args.dry_run and (rep.get("patches_applied") or rep.get("distilled")):
            try:
                sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "webapp"))
                import decision_trail
                decision_trail.record(hub, actor="steward", trigger="nightly",
                                      decision=f"apply: {rep.get('patches_applied', 0)} patch",
                                      rationale=f"distilled={rep.get('distilled', 0)} compact={rep.get('compact')} "
                                                f"rejected={rep.get('patches_rejected', 0)}",
                                      scope=("hub" if label == "hub" else f"workspace:{label[3:]}"))
            except Exception:
                pass
    tot_p = sum(r.get("patches_applied", 0) for r in results)
    tot_d = sum(r.get("distilled", 0) for r in results)
    tot_a = sum((r.get("compact") or {}).get("archived", 0) for r in results)
    errs = [f"{r['_label']}: {e}" for r in results for e in r.get("errors", []) if e != "lock held"]
    per_root = ", ".join(f"{r['_label']}={r.get('patches_applied', 0)}p/{r.get('distilled', 0)}d" for r in results) or "nessun wiki"
    tag = "DRY-RUN " if args.dry_run else ""
    line = (f"🌙 wiki steward {tag}— {tot_p} patch, {tot_d} session distillate, {tot_a} archiviate "
            f"[{per_root}]" + (f" · skip per tempo: {', '.join(skipped)}" if skipped else "")
            + (f" · errori: {len(errs)}" if errs else ""))
    print(line)
    print(json.dumps({"summary": line, "results": results, "skipped": skipped, "errors": errs},
                     ensure_ascii=False, default=str), file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
