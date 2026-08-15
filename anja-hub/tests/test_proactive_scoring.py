#!/usr/bin/env python3
"""test_proactive_scoring.py — F-Proactive-5/5b heartbeat scoring.

Copre lo scoring base + il backoff per-task (strato C, regressione) + il nuovo
declassamento adattivo per-CATEGORIA (strato D, F-Proactive-5b).

Run: /opt/homebrew/opt/python@3.12/bin/python3.12 anja-hub/tests/test_proactive_scoring.py
Exit 0 = OK, 1 = regressione.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))
import proactive_scoring as ps  # noqa: E402

NOW = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)
RESULTS: list[bool] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    RESULTS.append(bool(cond))
    print(f"  {'✓' if cond else '❌'} {name}" + (f" — {detail}" if detail and not cond else ""))
    return cond


def _task(tid, priority=1, due_at=None, updated_at="", metadata=None, tags=None, title="t"):
    return {"id": tid, "title": title, "priority": priority, "due_at": due_at,
            "updated_at": updated_at, "metadata": metadata or {}, "tags": tags or []}


def _iso(delta_h):
    return (NOW + timedelta(hours=delta_h)).isoformat()


def test_score_and_category():
    print("Scoring base + derivazione categoria:")
    check("priority pura", ps.score_task(_task(1, priority=2), NOW) == 2.0)
    check("overdue +3", ps.score_task(_task(1, priority=1, due_at=_iso(-5)), NOW) == 4.0)
    check("commitment -0.5", ps.score_task(_task(1, priority=2, metadata={"origin": "commitment"}), NOW) == 1.5)
    check("category esplicita", ps.task_category(_task(1, metadata={"category": "Bills"})) == "bills")
    check("origin fallback", ps.task_category(_task(1, metadata={"origin": "commitment"})) == "commitment")
    check("tag fallback", ps.task_category(_task(1, tags=["Health"])) == "health")
    check("default general", ps.task_category(_task(1)) == "general")


def test_per_task_backoff():
    print("Backoff per-task (strato C, regressione):")
    t = _task(1, priority=3)  # score 3.0 >= soglia 2.0
    # notificato 3 volte (= MAX_NOTIFY) senza reazione → 4a volta soppresso
    hb = {"1": {"notify_count": 3, "last_seen_updated": "2026-07-01"}}
    sel, _ = ps.select_for_heartbeat([t], hb, NOW)
    check("task ripetuto MAX_NOTIFY senza reazione → soppresso", sel == [])
    # overdue bypassa il backoff
    t_od = _task(1, priority=1, due_at=_iso(-3))
    sel, _ = ps.select_for_heartbeat([t_od], hb, NOW)
    check("overdue bypassa il backoff per-task", len(sel) == 1)
    # reazione (updated_at > last_seen) resetta il contatore → riselezionato
    t_r = _task(1, priority=3, updated_at="2026-07-05")
    sel, ns = ps.select_for_heartbeat([t_r], hb, NOW)
    check("reazione resetta il contatore → riselezionato", len(sel) == 1 and ns["1"]["notify_count"] == 1)


def test_layer_d_category():
    print("Layer D — declassamento per-categoria (F-Proactive-5b):")
    # Categoria "reminders" storicamente ignorata: 8 notificati, 0 reazioni → penalità 1.5
    ignored = {ps._CATEGORIES_KEY: {"reminders": {"notified": 8, "reacted": 0}}}
    t = _task(1, priority=3, metadata={"category": "reminders"})  # score 3.0
    sel, _ = ps.select_for_heartbeat([t], ignored, NOW)
    check("categoria ignorata → task penalizzato sotto soglia (3.0-1.5<2.0)", sel == [])

    # Stessa priorità ma categoria diversa (senza storia) → selezionato
    t2 = _task(2, priority=3, metadata={"category": "urgent"})
    sel, _ = ps.select_for_heartbeat([t2], ignored, NOW)
    check("categoria pulita → selezionato", len(sel) == 1)

    # Categoria reagita (react-ratio alta) → nessuna penalità
    reacted = {ps._CATEGORIES_KEY: {"reminders": {"notified": 8, "reacted": 6}}}
    sel, _ = ps.select_for_heartbeat([t], reacted, NOW)
    check("categoria reagita → nessuna penalità → selezionato", len(sel) == 1)

    # Campione insufficiente (< min_sample) → nessuna penalità
    small = {ps._CATEGORIES_KEY: {"reminders": {"notified": 2, "reacted": 0}}}
    sel, _ = ps.select_for_heartbeat([t], small, NOW)
    check("campione < min_sample → nessuna penalità", len(sel) == 1)

    # Overdue in categoria ignorata → bypassa la penalità (critico)
    t_od = _task(3, priority=1, due_at=_iso(-2), metadata={"category": "reminders"})  # score 4.0
    sel, _ = ps.select_for_heartbeat([t_od], ignored, NOW)
    check("overdue in categoria ignorata → bypassa penalità → selezionato", len(sel) == 1)


def test_state_persistence():
    print("Persistenza stato per-categoria:")
    t = _task(1, priority=3, metadata={"category": "bills"})
    sel, ns = ps.select_for_heartbeat([t], {}, NOW)
    check("selected riporta la category", sel and sel[0].get("category") == "bills")
    check("_categories persistito in new_state", isinstance(ns.get(ps._CATEGORIES_KEY), dict))
    check("categoria segnalata → notified incrementato", ns[ps._CATEGORIES_KEY]["bills"]["notified"] == 1)
    check("_categories NON è trattato come task entry", "notify_count" not in ns.get(ps._CATEGORIES_KEY, {}))

    # Recupero: se ricomincia a reagire, la penalità si allenta round dopo round
    ignored = {ps._CATEGORIES_KEY: {"bills": {"notified": 8, "reacted": 0}}}
    t_r = _task(1, priority=3, updated_at="2026-07-07", metadata={"category": "bills"})
    _, ns = ps.select_for_heartbeat([t_r], {**ignored, "1": {"last_seen_updated": "2026-07-01"}}, NOW)
    check("reazione incrementa reacted della categoria", ns[ps._CATEGORIES_KEY]["bills"]["reacted"] == 1)


def main() -> int:
    test_score_and_category()
    test_per_task_backoff()
    test_layer_d_category()
    test_state_persistence()
    passed, total = sum(RESULTS), len(RESULTS)
    print(f"\n{'✅' if passed == total else '❌'} {passed}/{total} check superati")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
