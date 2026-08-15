#!/usr/bin/env python3
"""Verifica DETERMINISMO del fix #1: run_workspace_query trova le card demo-brand N volte di fila."""
import os
import asyncio
from pathlib import Path

import pod_orchestrator

HUB = Path(os.environ.get("ANJA_HUB", ""))
Q = "cosa abbiamo in programma oggi su demo-brand? mostrami i task kanban attivi"
MARKERS = ["4580", "4581", "4582"]  # le 3 bozze blog ready


async def main() -> None:
    ok_count = 0
    for i in range(3):
        r = await pod_orchestrator.run_workspace_query(HUB, "demo-brand", Q)
        ans = r.get("answer", "") or ""
        found = [m for m in MARKERS if m in ans]
        ok = len(found) >= 2
        ok_count += ok
        print(f"run {i+1}: {'✓' if ok else '✗'}  card trovate={found}  err={r.get('error') or '-'}")
        print(f"        «{ans[:140].replace(chr(10),' ')}»")
    print(f"\n=== {ok_count}/3 run hanno trovato le card ===")


if __name__ == "__main__":
    asyncio.run(main())
