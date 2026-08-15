"""M-DecisionTrail — store del "perché" delle azioni autonome.

    /opt/homebrew/opt/python@3.12/bin/python3.12 anja-hub/tests/test_decision_trail.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))
import decision_trail as dt  # noqa: E402


def main():
    hub = Path(tempfile.mkdtemp())
    dt.record(hub, actor="coding", trigger="coding-run abc su ws1", decision="verified",
              rationale="ruff+pytest passati", alternative="rollback al checkpoint",
              confidence=1.0, scope="coding", ref="abc")
    dt.record(hub, actor="judge", trigger="goal g1 step verify", decision="approved",
              rationale="criteri soddisfatti 4/4", alternative="re-do step", confidence=0.82, ref="g1")
    dt.record(hub, actor="steward", trigger="signal inbox: 'richiama ACME'", decision="task creato",
              rationale="nessun task simile (difflib<0.82)", confidence=0.7, ref="task-9")

    rows = dt.recent(hub, limit=10)
    assert len(rows) == 3, rows
    assert rows[0]["actor"] == "steward", "ordine ts desc (ultimo per primo)"
    assert dt.recent(hub, actor="coding") and dt.recent(hub, actor="coding")[0]["ref"] == "abc"
    print("✓ record + recent (ordine desc) + filtro per actor")

    s = dt.stats(hub)
    assert s["total"] == 3
    actors = {a["actor"] for a in s["by_actor"]}
    assert actors == {"coding", "judge", "steward"}, actors
    conf = next(a for a in s["by_actor"] if a["actor"] == "judge")["avg_conf"]
    assert abs(conf - 0.82) < 1e-6, conf
    print("✓ stats: total + by_actor + avg confidence")

    print("\nOK 2/2")


if __name__ == "__main__":
    main()
