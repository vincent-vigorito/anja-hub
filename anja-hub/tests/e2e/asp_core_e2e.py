#!/usr/bin/env python3
"""E2E F-AgentSessions: webapp vera (WS /api/chat) + steer/interrupt via REST.
Steering inline mid-turn (query nel turno in corso). Check severo sui frame
WS avanzati: un leftover = bug di protocollo (es. il double-done fixato)."""
import asyncio
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8765"
WS = "ws://127.0.0.1:8765/api/chat"
CONV = f"asp-e2e-{int(time.time())}"
R: dict[str, str] = {}
LEFTOVERS = 0


def post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read())


async def turn(ws, prompt: str, label: str, on_first_tool=None, timeout=240):
    global LEFTOVERS
    # pre-send: la WS deve essere VUOTA — un frame qui è un bug di protocollo
    try:
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=0.3)
            LEFTOVERS += 1
            print(f"  !!! LEFTOVER pre-turno [{label}]: {raw[:100]}")
    except asyncio.TimeoutError:
        pass

    print(f"\n=== TURN [{label}] {prompt[:60]!r}")
    await ws.send(json.dumps({
        "message": prompt, "conversation_id": CONV, "scope": "hub",
        "model": "haiku", "provider": "claude",
    }))
    events, acted, t0 = [], False, time.time()
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        ev = json.loads(raw)
        et = ev.get("type")
        events.append(ev)
        if et == "text":
            print(f"  [text +{time.time()-t0:5.1f}s] {ev.get('content','')[:80]!r}")
        elif et == "tool_use":
            print(f"  [tool +{time.time()-t0:5.1f}s] {ev.get('name')}")
            if not acted and on_first_tool:
                acted = True
                out = on_first_tool()
                print(f"  >>> {out}")
        elif et in ("done", "error"):
            print(f"  [{et} +{time.time()-t0:5.1f}s] {ev.get('message','')}")
            if et == "error":
                R[f"{label}_error"] = ev.get("message", "?")
            return events


def text_of(events):
    return " ".join(e.get("content", "") for e in events if e.get("type") == "text")


async def main():
    import websockets
    async with websockets.connect(WS, max_size=8 * 1024 * 1024) as ws:
        snap = json.loads(await ws.recv())
        assert snap.get("type") == "active_streams_snapshot", snap
        print(f"connesso, conv={CONV}")

        # 1 — steer inline mid-turn
        ev1 = await turn(
            ws,
            "Cerca con Grep la parola 'anja' nei file .md di questa directory, poi "
            "leggi DUE dei file trovati uno alla volta con Read e riassumili.",
            "steer",
            on_first_tool=lambda: post("/api/session/steer", {
                "conv_id": CONV,
                "message": "CAMBIO ISTRUZIONE: non leggere altri file, fermati e "
                           "chiudi la risposta con la parola esatta E2E-STEER-OK",
            }),
        )
        R["steer_inline"] = "PASS" if "E2E-STEER-OK" in text_of(ev1) else "FAIL"

        # 2 — contesto tra turni
        ev2 = await turn(ws, "Senza usare tool: quale parola esatta ti avevo chiesto "
                             "di scrivere a fine risposta?", "context")
        R["context"] = "PASS" if "E2E-STEER-OK" in text_of(ev2) else "FAIL"

        # 3 — interrupt secco
        ev3 = await turn(
            ws,
            "Leggi con Read QUATTRO file .md di questa directory uno alla volta e riassumili.",
            "interrupt",
            on_first_tool=lambda: post("/api/session/interrupt", {"conv_id": CONV}),
        )
        n_tools3 = len([e for e in ev3 if e.get("type") == "tool_use"])
        R["interrupt"] = ("PASS" if any(e.get("type") == "done" for e in ev3)
                          and n_tools3 <= 2 else f"FAIL (tools={n_tools3})")

        # 4 — riuso post-interrupt
        ev4 = await turn(ws, "Senza usare tool: scrivi solo la parola OK.", "reuse")
        R["reuse"] = "PASS" if "OK" in text_of(ev4).upper() else "FAIL"

        # 5 — session.set runtime (Fase 1: solo model) + turno successivo
        sr = post("/api/session/set", {"conv_id": CONV, "model": "haiku"})
        R["session_set"] = ("PASS" if sr.get("ok")
                            and sr.get("applied", {}).get("model") == "claude-haiku-4-5"
                            else f"FAIL {sr}")
        ev5 = await turn(ws, "Senza usare tool: scrivi solo SI.", "post-set")
        R["turn_after_set"] = "PASS" if "SI" in text_of(ev5).upper() else "FAIL"

    stats = get("/api/session/stats")
    ses = [s for s in stats["sessions"] if s["conv_id"] == CONV]
    print(f"\nstats sessione: {json.dumps(ses)[:280]}")
    R["single_session_5_turns"] = ("PASS" if len(ses) == 1
                                   and ses[0]["turn_count"] == 5
                                   and not ses[0]["turn_active"]
                                   else f"FAIL {ses}")
    R["no_leftover_frames"] = "PASS" if LEFTOVERS == 0 else f"FAIL ({LEFTOVERS} frame)"

    # Fase 0 — event-log persistito e replay
    log = get(f"/api/session/log?conv_id={CONV}")
    evs = log["events"]
    tps = [e["type"] for e in evs]
    R["log_persisted"] = ("PASS" if log["count"] > 0 else "FAIL (log vuoto)")
    R["log_5_turni"] = ("PASS" if tps.count("turn.started") == 5
                        and tps.count("turn.completed") == 5
                        and tps.count("done") == 5 else f"FAIL {tps}")
    order_ok = all(
        tps.index("turn.completed", tps.index("done", i) - 2) < tps.index("done", i)
        for i in [j for j, t in enumerate(tps) if t == "done"][:1]
    ) if "done" in tps else False
    R["log_envelope"] = ("PASS" if all("seq" in e and "ts" in e for e in evs)
                         else "FAIL")
    replay = get(f"/api/session/log?conv_id={CONV}&since_seq={evs[len(evs)//2]['seq']}")
    R["log_replay_since"] = ("PASS" if replay["count"] < log["count"]
                             and replay["events"][0]["seq"] > evs[len(evs)//2]["seq"] - 1
                             else "FAIL")

    print("\n" + "=" * 46)
    failed = 0
    for k, v in R.items():
        ok = v.startswith("PASS")
        failed += 0 if ok else 1
        print(f"  {'✅' if ok else '❌'} {k}: {v}")
    print("=" * 46)
    sys.exit(1 if failed else 0)


asyncio.run(main())
