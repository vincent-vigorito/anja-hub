#!/usr/bin/env python3
"""test_dreaming.py — F-Dreaming: consolidamento memoria notturno.

Verifica la decadenza delle observation stantie (deterministica) e il ciclo di
consolidamento end-to-end (promozione mature → USER.md + decadenza + report),
riusando la machinery di promozione esistente su un hub temporaneo.

Run: /opt/homebrew/opt/python@3.12/bin/python3.12 anja-hub/tests/test_dreaming.py
Exit 0 = OK, 1 = regressione.
"""

import asyncio
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))
import dialectic_io as dio     # noqa: E402
import dreaming                # noqa: E402

RESULTS: list[bool] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    RESULTS.append(bool(cond))
    print(f"  {'✓' if cond else '❌'} {name}" + (f" — {detail}" if detail and not cond else ""))
    return cond


def _obs(text, sightings, last_seen, sessions, tag=""):
    return {"text": text, "tag": tag, "sightings": sightings, "last_seen": last_seen, "sessions": sessions}


def _write_dialectic(path, actives):
    data = dio.empty_dialectic("mem", "hub")
    data["active"] = actives
    dio.write_dialectic(path, data)


def test_decay_stale():
    print("decay_stale (deterministico, today=2026-07-08, DECAY_DAYS=21 → cutoff 2026-06-17):")
    tmp = Path(tempfile.mkdtemp())
    dpath = tmp / "mem-dialectic.md"
    _write_dialectic(dpath, [
        _obs("matura (bypassa: sightings>=3)", 4, "2026-05-01", ["s1", "s2", "s3"]),
        _obs("stantia da maggio", 1, "2026-05-01", ["s1"]),
        _obs("recente", 1, "2026-07-07", ["s1"]),
        _obs("senza data (safety)", 1, "", ["s1"]),
    ])
    decayed = dreaming.decay_stale(dpath, decay_days=21, today="2026-07-08")
    check("decade solo la stantia low-sighting", decayed == ["stantia da maggio"], str(decayed))
    data = dio.read_dialectic(dpath)
    active_texts = [o["text"] for o in data["active"]]
    check("matura resta in Active", "matura (bypassa: sightings>=3)" in active_texts)
    check("recente resta in Active", "recente" in active_texts)
    check("senza data resta in Active", "senza data (safety)" in active_texts)
    check("stantia NON è più in Active", "stantia da maggio" not in active_texts)
    check("stantia è in Decayed", any(d["text"] == "stantia da maggio" for d in data["decayed"]))


def test_consolidate_end_to_end():
    print("consolidate end-to-end (promozione + decadenza + report):")
    tmp = Path(tempfile.mkdtemp())
    (tmp / "users").mkdir(parents=True)
    dpath = tmp / "users" / "mem-dialectic.md"
    upath = tmp / "users" / "mem.md"
    real_today = datetime.now().strftime("%Y-%m-%d")
    _write_dialectic(dpath, [
        _obs("vincent preferisce risposte concise", 4, real_today, ["s1", "s2", "s3"], tag="pref"),  # mature → promote
        _obs("contesto transiente antico", 1, "2020-01-01", ["s1"]),                                  # ancient → decay
        _obs("osservazione recente", 1, real_today, ["s1"]),                                          # recent → keep
    ])
    report = asyncio.run(dreaming.consolidate(tmp, "mem", projects=None))

    check("report.changed True", report.get("changed") is True)
    check("mature promossa nel report", "vincent preferisce risposte concise" in report["promoted"])
    check("antica decaduta nel report", "contesto transiente antico" in report["decayed"])
    check("USER.md creato con la preferenza promossa",
          upath.is_file() and "vincent preferisce risposte concise" in upath.read_text())

    data = dio.read_dialectic(dpath)
    active_texts = [o["text"] for o in data["active"]]
    promoted_texts = [p["text"] for p in data["promoted"]]
    decayed_texts = [d["text"] for d in data["decayed"]]
    check("mature spostata in Promoted", "vincent preferisce risposte concise" in promoted_texts)
    check("antica spostata in Decayed", "contesto transiente antico" in decayed_texts)
    check("recente ancora in Active", "osservazione recente" in active_texts)
    check("mature NON più in Active", "vincent preferisce risposte concise" not in active_texts)

    # idempotenza: una seconda passata non ripromuove/ridecade (già consolidato)
    report2 = asyncio.run(dreaming.consolidate(tmp, "mem", projects=None))
    check("seconda passata: nessuna nuova promozione", report2["promoted"] == [])
    check("seconda passata: nessuna nuova decadenza", report2["decayed"] == [])


def main() -> int:
    test_decay_stale()
    test_consolidate_end_to_end()
    passed, total = sum(RESULTS), len(RESULTS)
    print(f"\n{'✅' if passed == total else '❌'} {passed}/{total} check superati")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
