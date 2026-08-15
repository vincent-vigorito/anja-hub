#!/usr/bin/env python3
"""asp_log_test.py — Fase 0 ASP: registry promosso + persistenza JSONL.
Senza LLM: valida envelope {seq,ts,type}, turn.started/completed auto-emessi,
parità buffer↔disco, replay con since_seq, sanitizzazione conv_id."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))

import asp_log
import chat_stream_registry as reg

FAILED = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name} {detail if not cond else ''}")
    if not cond:
        FAILED.append(name)


with tempfile.TemporaryDirectory() as tmp:
    log = asp_log.AspLog(Path(tmp))
    reg.set_persist(log.append)

    # --- turno completo
    st = reg.register("conv-a", scope="hub", model="haiku", provider="claude",
                      user_msg="ciao mondo", title="t")
    st.append({"type": "text", "content": "risposta "})
    st.append({"type": "tool_use", "name": "Read", "input": {}})
    st.append({"type": "usage", "input_tokens": 10, "output_tokens": 5})
    st.append({"type": "done"})
    st.completed = True

    types = [e["type"] for e in st.buffer]
    check("turn.started è il primo evento", types[0] == "turn.started", str(types))
    check("turn.completed prima di done",
          types[-2:] == ["turn.completed", "done"], str(types))
    check("envelope seq+ts su ogni evento",
          all("seq" in e and "ts" in e for e in st.buffer))
    tc = st.buffer[-2]
    check("turn.completed ha riepilogo",
          tc.get("tool_uses") == 1 and tc.get("usage", {}).get("output_tokens") == 5,
          str(tc))

    disk = log.read("conv-a")
    check("parità buffer↔disco", disk == st.buffer,
          f"disk={len(disk)} buf={len(st.buffer)}")
    check("replay since_seq", [e["seq"] for e in log.read("conv-a", since_seq=3)]
          == [e["seq"] for e in st.buffer if e["seq"] > 3])

    # --- dedup session_id (l'SDK lo riemette a ogni messaggio: 61/78 eventi
    # nel log della prima validazione TG reale)
    st2 = reg.register("conv-dedup", scope="hub", model="haiku",
                       provider="claude", user_msg="x", title="t")
    st2.append({"type": "session_id", "session_id": "s-1"})
    st2.append({"type": "session_id", "session_id": "s-1"})
    st2.append({"type": "session_id", "session_id": "s-1"})
    st2.append({"type": "session_id", "session_id": "s-2"})
    sids = [e for e in st2.buffer if e["type"] == "session_id"]
    check("session_id dedup (stesso id 1 volta, cambio passa)",
          len(sids) == 2 and sids[0]["session_id"] == "s-1"
          and sids[1]["session_id"] == "s-2", str(sids))

    # --- doppio done: turn.completed non duplicato
    st.append({"type": "done"})
    check("turn.completed non duplicato su done ripetuto",
          [e["type"] for e in st.buffer].count("turn.completed") == 1)

    # --- turno nuovo stessa conv → nuovo state, nuovo turn.started
    st2 = reg.register("conv-a", scope="hub", model="haiku", provider="claude",
                       user_msg="secondo", title="t")
    check("nuovo turno = nuovo state", st2 is not st)
    disk2 = log.read("conv-a")
    check("log accumula i turni",
          [e["type"] for e in disk2].count("turn.started") == 2)

    # --- sanitizzazione conv_id (path traversal)
    evil = "../../../etc/passwd"
    log.append(evil, {"seq": 1, "ts": 0, "type": "text"})
    inside = list(Path(tmp).rglob("*.jsonl"))
    check("conv_id ostile resta dentro sessions-log",
          all(str(p).startswith(str(Path(tmp))) for p in inside)
          and not Path("/etc/passwd.jsonl").exists()
          and log.read(evil) != [])

    # --- errore turno: turn.completed porta l'errore
    st3 = reg.register("conv-err", scope="hub", model="haiku", provider="claude",
                       user_msg="x", title="")
    st3.append({"type": "error", "message": "boom"})
    st3.error = "boom"
    st3.append({"type": "done"})
    tc3 = [e for e in st3.buffer if e["type"] == "turn.completed"][0]
    check("turn.completed riporta error", tc3.get("error") == "boom", str(tc3))

    reg.set_persist(None)
    reg._streams.clear()

print("=" * 44)
if FAILED:
    print(f"FAILED: {FAILED}")
    sys.exit(1)
print("ALL PASS")
