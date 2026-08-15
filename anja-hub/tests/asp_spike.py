#!/usr/bin/env python3
"""asp_spike.py — validazione SPIKE F-AgentSessions (claude_session.py).

Standalone, senza webapp: esercita il session pool direttamente.
Verifica le 4 assunzioni rischiose del design (anja-agent-sessions-design.md §5):

  1. STEER    — messaggio iniettato a metà turno viene recepito
  2. CONTEXT  — il turno successivo ricorda il precedente (sessione viva, no re-inject)
  3. INTERRUPT— il turno si ferma pulito e la sessione resta usabile
  4. REUSE    — turno post-interrupt funziona sulla stessa sessione

Uso:  /opt/homebrew/opt/python@3.12/bin/python3.12 asp_spike.py [--cwd <dir>]
Costo: ~4 turni haiku sul cwd indicato (default: repo AnjaHub).
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))

import claude_session  # noqa: E402

CONV = f"asp-spike-{int(time.time())}"
RESULTS: dict[str, str] = {}


async def run_turn(cwd: Path, prompt: str, label: str,
                   steer_after_tool: str = None,
                   interrupt_after_tool: bool = False,
                   turn_timeout: int = 180) -> list[dict]:
    """Consuma un turno; opzionalmente steera o interrompe dopo il primo tool_use."""
    events: list[dict] = []
    acted = False
    print(f"\n=== TURN [{label}]: {prompt[:70]!r}")
    t0 = time.time()

    async def _consume():
        nonlocal acted
        async for ev in claude_session.stream_turn(
            conv_id=CONV,
            user_prompt=prompt,
            system_prompt="Sei un assistente di test. Rispondi in modo conciso.",
            cwd=cwd,
            model="haiku",
            allowed_tools=["Read", "Glob", "Grep"],
        ):
            events.append(ev)
            et = ev.get("type")
            if et == "text":
                print(f"  [text +{time.time()-t0:5.1f}s] {ev['content'][:90]!r}")
            elif et == "tool_use":
                print(f"  [tool +{time.time()-t0:5.1f}s] {ev['name']} {str(ev['input'])[:60]}")
                if not acted and steer_after_tool:
                    acted = True
                    ok = await claude_session.pool.steer(CONV, steer_after_tool)
                    print(f"  >>> steer inviato={ok}")
                    RESULTS["steer_accepted"] = "PASS" if ok else "FAIL"
                elif not acted and interrupt_after_tool:
                    acted = True
                    t_int = time.time()
                    ok = await claude_session.pool.interrupt(CONV)
                    print(f"  >>> interrupt inviato={ok}")
                    RESULTS["interrupt_accepted"] = "PASS" if ok else "FAIL"
                    RESULTS["_interrupt_ts"] = str(t_int)
            elif et in ("error", "notice", "done"):
                print(f"  [{et} +{time.time()-t0:5.1f}s] {ev.get('message', '')}")

    try:
        await asyncio.wait_for(_consume(), timeout=turn_timeout)
    except asyncio.TimeoutError:
        print(f"  !!! turno oltre {turn_timeout}s — FAIL")
        events.append({"type": "error", "message": "spike watchdog timeout"})
    print(f"=== fine turno [{label}] in {time.time()-t0:.1f}s, {len(events)} eventi")
    return events


def all_text(events: list[dict]) -> str:
    return " ".join(e.get("content", "") for e in events if e.get("type") == "text")


async def main(cwd: Path):
    print(f"conv={CONV}  cwd={cwd}  pool_max={claude_session.MAX_SESSIONS}")

    # 1+2 — STEER: turno multi-tool con finestra per lo steering
    ev1 = await run_turn(
        cwd,
        "Trova con Glob i file *.md nella root della directory corrente, poi leggine "
        "TRE uno alla volta e riassumi ciascuno in una riga. Un file per volta.",
        "steer",
        steer_after_tool="CAMBIO ISTRUZIONE: fermati al prossimo file, non leggere gli "
                         "altri, e termina la risposta con la parola esatta: STEER-RECEPITO",
    )
    txt1 = all_text(ev1)
    RESULTS["steer_effect"] = "PASS" if "STEER-RECEPITO" in txt1 else "FAIL (marker assente)"

    # 3 — CONTEXT: la sessione ricorda il turno precedente senza re-inject
    ev2 = await run_turn(
        cwd,
        "Senza usare tool: quale parola esatta ti avevo chiesto di scrivere a fine "
        "risposta nel messaggio precedente?",
        "context",
    )
    RESULTS["context_retention"] = ("PASS" if "STEER-RECEPITO" in all_text(ev2)
                                    else "FAIL (non ricorda)")

    # 4 — INTERRUPT a metà turno
    ev3 = await run_turn(
        cwd,
        "Leggi con Read CINQUE file .md di questa directory uno alla volta e riassumili.",
        "interrupt",
        interrupt_after_tool=True,
    )
    if "_interrupt_ts" in RESULTS:
        t_int = float(RESULTS.pop("_interrupt_ts"))
        done_ev = [e for e in ev3 if e.get("type") == "done"]
        RESULTS["interrupt_clean_stop"] = ("PASS" if done_ev else "FAIL (nessun done)")

    # 5 — REUSE post-interrupt
    ev4 = await run_turn(cwd, "Senza usare tool: scrivi solo la parola OK.", "reuse")
    RESULTS["session_reuse_after_interrupt"] = ("PASS" if "OK" in all_text(ev4).upper()
                                                else "FAIL")

    stats = claude_session.pool.stats()
    print(f"\npool stats: {stats}")
    RESULTS["single_persistent_session"] = (
        "PASS" if len(stats["sessions"]) == 1
        and stats["sessions"][0]["turn_count"] == 4 else
        f"FAIL ({len(stats['sessions'])} sessioni, "
        f"turns={stats['sessions'][0]['turn_count'] if stats['sessions'] else 0})"
    )

    await claude_session.pool.close_all()

    print("\n" + "=" * 52)
    print("RISULTATI SPIKE")
    print("=" * 52)
    failed = 0
    for k, v in RESULTS.items():
        mark = "✅" if v.startswith("PASS") else "❌"
        if not v.startswith("PASS"):
            failed += 1
        print(f"  {mark} {k}: {v}")
    print("=" * 52)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd", default=str(Path(__file__).resolve().parents[2]))
    args = ap.parse_args()
    asyncio.run(main(Path(args.cwd)))
