#!/usr/bin/env python3
"""E2E Fase 2 ASP — control-plane permessi sulla webapp vera.
Hub scope (Write non pre-approvato) → can_use_tool → permission.requested →
risposta via REST: allow / deny / always_allow (regola appresa) / auto-allow."""
import os
import asyncio
import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8765"
WS = "ws://127.0.0.1:8765/api/chat"
CONV = f"asp-perm-{int(time.time())}"
HUB = Path(os.environ.get("ANJA_HUB", ""))
TESTDIR = HUB / "asp-perm-e2e"
R: dict[str, str] = {}


def post(path, payload):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


async def turn(ws, prompt, label, decision=None, timeout=240):
    """decision: risposta da dare a OGNI permission.requested del turno."""
    print(f"\n=== TURN [{label}]")
    await ws.send(json.dumps({"message": prompt, "conversation_id": CONV,
                              "scope": "hub", "model": "haiku",
                              "provider": "claude"}))
    events, t0 = [], time.time()
    while True:
        ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        events.append(ev)
        et = ev.get("type")
        if et == "permission.requested":
            print(f"  [perm.req +{time.time()-t0:4.1f}s] {ev['tool']} {ev['target'][:60]!r}")
            if decision:
                out = post("/api/session/permission",
                           {"request_id": ev["request_id"], "decision": decision})
                print(f"  >>> {decision}: {out}")
        elif et == "permission.resolved":
            print(f"  [perm.res +{time.time()-t0:4.1f}s] {ev.get('decision')} by={ev.get('by')}")
        elif et == "text":
            print(f"  [text +{time.time()-t0:4.1f}s] {ev.get('content','')[:70]!r}")
        elif et in ("done", "error"):
            print(f"  [{et} +{time.time()-t0:4.1f}s] {ev.get('message','')}")
            return events


def types(evs):
    return [e["type"] for e in evs]


async def main():
    import websockets
    async with websockets.connect(WS, max_size=8 * 1024 * 1024) as ws:
        assert json.loads(await ws.recv())["type"] == "active_streams_snapshot"
        print(f"conv={CONV}")

        # A — allow esplicito
        f1 = TESTDIR / "uno.txt"
        ev = await turn(ws, f"Crea con il tool Write il file {f1} con contenuto 'ciao asp'. "
                            f"Solo questo, niente altro.", "allow", decision="allow")
        R["ask_emesso"] = "PASS" if "permission.requested" in types(ev) else "FAIL"
        R["allow_scrive"] = "PASS" if f1.is_file() else "FAIL (file assente)"

        # B — deny: il file NON deve esistere, il turno continua
        f2 = TESTDIR / "due.txt"
        ev = await turn(ws, f"Crea con Write il file {f2} con contenuto 'x'. "
                            f"Se non puoi, dì solo NEGATO.", "deny", decision="deny")
        R["deny_blocca"] = ("PASS" if not f2.exists()
                            and "permission.requested" in types(ev) else "FAIL")
        R["deny_continua"] = ("PASS" if "done" in types(ev) else "FAIL")

        # C — always_allow: scrive + impara la regola
        f3 = TESTDIR / "tre.txt"
        ev = await turn(ws, f"Crea con Write il file {f3} con contenuto 'tre'.",
                        "always", decision="always_allow")
        rules = json.loads((HUB / "config" / "asp_permissions.json").read_text())
        learned = [r for r in rules.get("rules", [])
                   if r.get("source") == "learned" and r.get("tool") == "Write"]
        R["always_scrive"] = "PASS" if f3.is_file() else "FAIL"
        R["regola_appresa"] = ("PASS" if learned
                               and learned[0]["pattern"].endswith("asp-perm-e2e/*")
                               else f"FAIL {learned}")

        # D — auto-allow dalla regola appresa: NESSUN ask
        f4 = TESTDIR / "quattro.txt"
        ev = await turn(ws, f"Crea con Write il file {f4} con contenuto 'quattro'.",
                        "auto")
        R["auto_allow_no_ask"] = ("PASS" if "permission.requested" not in types(ev)
                                  and f4.is_file() else "FAIL")
        auto = [e for e in ev if e.get("type") == "permission.resolved"
                and e.get("decision") == "auto-allow"]
        R["auto_allow_evento"] = "PASS" if auto else "FAIL"

    # audit: decision-trail
    sys.path.insert(0, "..")
    import decision_trail
    rows = [r for r in decision_trail.recent(HUB, limit=50)
            if r.get("actor") == "asp-permission"]
    R["decision_trail"] = (f"PASS ({len(rows)} record)" if len(rows) >= 4
                          else f"FAIL ({len(rows)})")

    print("\n" + "=" * 46)
    failed = 0
    for k, v in R.items():
        ok = v.startswith("PASS")
        failed += 0 if ok else 1
        print(f"  {'✅' if ok else '❌'} {k}: {v}")
    print("=" * 46)
    sys.exit(1 if failed else 0)


asyncio.run(main())
