#!/usr/bin/env python3
"""metrics_refresh_all.py — refresh schedulato delle metriche di TUTTI i progetti.

Itera i progetti locali del registry (`<hub>/config/projects.json`) e per ognuno
chiama `metrics_collector.refresh()` (GSC/GA/Ads/Merchant/Woo/Meta/social →
`<ws>/data/metrics.db`) con i valori dei Connettori (vault workspace + fallback
hub). Ogni sorgente non configurata è già gestita con grazia dal collector: qui
si somma e si riporta. Stampa una riga di riepilogo (per il messaggio Telegram
della routine `metrics-refresh-nightly`) + JSON dettagliato su stderr.

Usage: python3 metrics_refresh_all.py --hub <hub> [--days 90] [--max-minutes 15]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

WEBAPP = Path(__file__).resolve().parent.parent / "webapp"
sys.path.insert(0, str(WEBAPP))


def local_projects(hub: Path) -> list[tuple[str, Path]]:
    reg = hub / "config" / "projects.json"
    if not reg.is_file():
        return []
    out = []
    for p in json.loads(reg.read_text(encoding="utf-8")).get("projects", []):
        loc = p.get("location") or {}
        if loc.get("kind") != "local" or not loc.get("path"):
            continue
        raw = Path(loc["path"])
        root = raw.parent if raw.name == ".anjawiki" else raw
        if root.is_dir():
            out.append((p.get("name", root.name), root))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub", required=True)
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--max-minutes", type=float, default=15.0)
    args = ap.parse_args()
    hub = Path(args.hub).expanduser().resolve()

    import connectors_io
    import metrics_collector

    deadline = time.time() + args.max_minutes * 60
    results, skipped_time = [], []
    for name, root in local_projects(hub):
        if time.time() > deadline - 10:
            skipped_time.append(name)
            continue
        try:
            vals = connectors_io.resolve_values(hub, root / ".anjawiki")
            res = metrics_collector.refresh(
                root / "data" / "metrics.db", vals,
                scope_dir=root / ".anjawiki", hub_dir=hub / ".anjawiki",
                days=args.days)
        except Exception as e:
            res = {"ok": False, "collected": 0, "sources": [], "note": f"error: {e}"}
        res["_project"] = name
        results.append(res)

    parts, errors = [], 0
    for r in results:
        configured = any(s.get("configured") for s in (r.get("sources") or []))
        if not r.get("ok"):
            errors += 1
            parts.append(f"{r['_project']}: ERR")
        elif not configured and not r.get("collected"):
            parts.append(f"{r['_project']}: skip")
        else:
            parts.append(f"{r['_project']}: {r.get('collected', 0)} righe")
    line = ("📊 metrics refresh — " + (" · ".join(parts) or "nessun progetto locale")
            + (f" · skip per tempo: {', '.join(skipped_time)}" if skipped_time else "")
            + (f" · errori: {errors}" if errors else ""))
    print(line)
    print(json.dumps({"summary": line, "results": results, "skipped_time": skipped_time},
                     ensure_ascii=False, default=str), file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
