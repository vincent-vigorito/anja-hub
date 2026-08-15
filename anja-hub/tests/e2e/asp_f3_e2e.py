#!/usr/bin/env python3
"""E2E Fase 3 ASP — todo.updated dal TodoWrite reale + ciclo plan mode
(plan.proposed → approve → esecuzione con permission)."""
import os
import asyncio
import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8765"
WS = "ws://127.0.0.1:8765/api/chat"
CONV = f"asp-f3-{int(time.time())}"
HUB = Path(os.environ.get("ANJA_HUB", ""))
R: dict[str, str] = {}


def post(path, payload):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


async def turn(ws, prompt, label, on_event=None, timeout=300):
    print(f"\n=== TURN [{label}]")
    await ws.send(json.dumps({"message": prompt, "conversation_id": CONV,
                              "scope": "hub", "model": "haiku",
                              "provider": "claude"}))
    events, t0 = [], time.time()
    while True:
        ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        events.append(ev)
        et = ev.get("type")
        if et == "todo.updated":
            st = ["✓" if t["status"] == "completed" else
                  ("▸" if t["status"] == "in_progress" else "▢")
                  for t in ev.get("todos", [])]
            print(f"  [todo +{time.time()-t0:4.1f}s] {' '.join(st)}")
        elif et in ("plan.proposed", "permission.requested", "plan.resolved",
                    "permission.resolved", "subagent.started", "thinking"):
            print(f"  [{et} +{time.time()-t0:4.1f}s] {str(ev.get('plan', ev.get('tool', ev.get('label',''))))[:60]!r}")
        elif et == "text":
            print(f"  [text +{time.time()-t0:4.1f}s] {ev.get('content','')[:60]!r}")
        if on_event:
            on_event(ev)
        if et in ("done", "error"):
            print(f"  [{et} +{time.time()-t0:4.1f}s] {ev.get('message','')}")
            return events


def types(evs):
    return [e["type"] for e in evs]


async def main():
    import websockets
    async with websockets.connect(WS, max_size=8 * 1024 * 1024) as ws:
        assert json.loads(await ws.recv())["type"] == "active_streams_snapshot"
        print(f"conv={CONV}")

        # A — todo.updated dal tracking reale (TaskCreate/TaskUpdate sul CLI
        # ≥2.1.2xx, TodoWrite sui vecchi)
        ev = await turn(ws,
            "Pianifica ESATTAMENTE 3 passi col sistema di task/todo "
            "(TaskCreate+TaskUpdate, o TodoWrite se è quello che hai): "
            "(1) conta i file .md nella root con Glob, (2) leggi AGENTS.md, "
            "(3) scrivi una riga di sintesi. Esegui i passi aggiornando lo "
            "stato man mano (in_progress → completed).", "todo")
        todo_evs = [e for e in ev if e.get("type") == "todo.updated"]
        R["todo_emessi"] = (f"PASS ({len(todo_evs)} update)" if len(todo_evs) >= 2
                            else f"FAIL ({len(todo_evs)})")
        R["todo_progressione"] = ("PASS" if todo_evs
                                  and any(t["status"] == "completed"
                                          for t in todo_evs[-1]["todos"])
                                  else "FAIL")
        R["todo_no_chip"] = ("PASS" if not any(
            e.get("type") == "tool_use" and e.get("name") == "TodoWrite"
            for e in ev) else "FAIL")

        # B — plan mode: set → proponi → approva → esegui
        sr = post("/api/session/set", {"conv_id": CONV, "permission_mode": "plan"})
        R["set_plan_mode"] = ("PASS" if sr.get("ok")
                              and sr.get("applied", {}).get("permission_mode") == "plan"
                              else f"FAIL {sr}")

        target = HUB / "asp-f3-plan.txt"
        target.unlink(missing_ok=True)

        def responder(ev):
            if ev.get("type") == "plan.proposed":
                out = post("/api/session/plan",
                           {"request_id": ev["request_id"], "decision": "approve"})
                print(f"  >>> approve: {out}")
            elif ev.get("type") == "permission.requested":
                out = post("/api/session/permission",
                           {"request_id": ev["request_id"], "decision": "allow"})
                print(f"  >>> allow: {out}")

        ev = await turn(ws,
            f"Prepara un piano per creare il file {target} con dentro la parola "
            f"PIANO-ESEGUITO, poi eseguilo.", "plan", on_event=responder)
        R["plan_proposto"] = "PASS" if "plan.proposed" in types(ev) else "FAIL"
        R["plan_risolto"] = ("PASS" if any(
            e.get("type") == "plan.resolved" and e.get("decision") == "approve"
            for e in ev) else "FAIL")
        R["post_approve_esegue"] = ("PASS" if target.is_file()
                                    and "PIANO-ESEGUITO" in target.read_text()
                                    else "FAIL (file assente: comportamento post-approve?)")
        target.unlink(missing_ok=True)

    print("\n" + "=" * 46)
    failed = 0
    for k, v in R.items():
        ok = v.startswith("PASS")
        failed += 0 if ok else 1
        print(f"  {'✅' if ok else '❌'} {k}: {v}")
    print("=" * 46)
    sys.exit(1 if failed else 0)


asyncio.run(main())
