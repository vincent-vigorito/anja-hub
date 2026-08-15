#!/usr/bin/env python3
"""Smoke del path legacy (flag ASP SPENTI): il one-shot claude deve funzionare
identico a main — un solo done per turno (fix double-done), contesto tra turni,
niente frame ASP, endpoint sessione 404, niente JSONL."""
import os
import asyncio
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8765"
WS = "ws://127.0.0.1:8765/api/chat"
CONV = f"legacy-smoke-{int(time.time())}"
HUB = Path(os.environ.get("ANJA_HUB", ""))
ASP_ONLY = {"permission.requested", "plan.proposed", "diff.ready",
            "todo.updated", "subagent.started"}
R: dict[str, str] = {}


async def turn(ws, msg):
    await ws.send(json.dumps({"message": msg, "conversation_id": CONV,
                              "scope": "hub", "model": "haiku",
                              "provider": "claude"}))
    events = []
    while True:
        ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=180))
        events.append(ev)
        if ev.get("type") in ("done", "error"):
            break
    # frame orfani post-done (il vecchio double-done li produceva)
    leftover = []
    try:
        leftover.append(json.loads(await asyncio.wait_for(ws.recv(), timeout=2.5)))
    except asyncio.TimeoutError:
        pass
    return events, leftover


async def main():
    import websockets
    async with websockets.connect(WS, max_size=8 * 1024 * 1024) as ws:
        assert json.loads(await ws.recv())["type"] == "active_streams_snapshot"
        ev1, left1 = await turn(ws, "Mi chiamo Vincent. Rispondi solo: ciao Vincent.")
        ev2, left2 = await turn(ws, "Come mi chiamo? Rispondi solo col nome.")

    text1 = " ".join(e.get("content", "") for e in ev1 if e.get("type") == "text")
    text2 = " ".join(e.get("content", "") for e in ev2 if e.get("type") == "text")
    R["turno_ok"] = "PASS" if "incent" in text1 and not any(
        e.get("type") == "error" for e in ev1) else f"FAIL {text1[:80]}"
    R["contesto"] = "PASS" if "incent" in text2 else f"FAIL {text2[:80]}"
    dones = sum(1 for e in ev1 if e.get("type") == "done") + \
        sum(1 for e in ev2 if e.get("type") == "done")
    R["un_done_per_turno"] = "PASS" if dones == 2 else f"FAIL ({dones})"
    R["no_frame_orfani"] = "PASS" if not left1 and not left2 else f"FAIL {left1+left2}"
    asp = [e["type"] for e in ev1 + ev2 if e.get("type") in ASP_ONLY]
    R["no_frame_asp"] = "PASS" if not asp else f"FAIL {asp}"

    try:
        urllib.request.urlopen(BASE + "/api/session/stats", timeout=10)
        R["endpoint_asp_404"] = "FAIL (raggiungibile)"
    except urllib.error.HTTPError as e:
        R["endpoint_asp_404"] = "PASS" if e.code == 404 else f"FAIL ({e.code})"
    logf = HUB / "sessions-log" / f"{CONV}.jsonl"
    R["no_jsonl"] = "PASS" if not logf.exists() else "FAIL"

    print("=" * 46)
    failed = 0
    for k, v in R.items():
        ok = v.startswith("PASS")
        failed += 0 if ok else 1
        print(f"  {'✅' if ok else '❌'} {k}: {v}")
    print("=" * 46)
    sys.exit(1 if failed else 0)


asyncio.run(main())
