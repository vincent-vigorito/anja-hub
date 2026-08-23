"""metrics_refresh_all.py: refresh schedulato metriche per tutti i progetti locali.

    /opt/homebrew/opt/python@3.12/bin/python3.12 anja-hub/tests/test_metrics_refresh_all.py
"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "anja-hub" / "scripts" / "metrics_refresh_all.py"
sys.path.insert(0, str(SCRIPT.parent))

import metrics_refresh_all as mra  # noqa: E402

PASS, FAIL = 0, 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label} {detail}")


def main():
    tmp = Path(tempfile.mkdtemp())
    hub = tmp / "hub"
    (hub / "config").mkdir(parents=True)
    ws_a = hub / "workspaces" / "alpha"
    (ws_a / ".anjawiki").mkdir(parents=True)
    ws_b = hub / "workspaces" / "beta"
    ws_b.mkdir(parents=True)
    (hub / "config" / "projects.json").write_text(json.dumps({"projects": [
        {"name": "alpha", "location": {"kind": "local", "path": str(ws_a / ".anjawiki")}},
        {"name": "beta", "location": {"kind": "local", "path": str(ws_b)}},
        {"name": "remoto", "location": {"kind": "ssh", "path": "/x"}},
        {"name": "sparito", "location": {"kind": "local", "path": str(tmp / "non-esiste")}},
    ]}))

    print("local_projects")
    projs = mra.local_projects(hub)
    names = [n for n, _ in projs]
    check("solo locali esistenti", names == ["alpha", "beta"], str(names))
    check(".anjawiki normalizzato alla root", dict(projs)["alpha"] == ws_a, str(projs))

    print("run end-to-end (collector fake)")
    import contextlib
    import io
    import types
    fake_conn = types.ModuleType("connectors_io")
    fake_conn.resolve_values = lambda hub, scope: {"GSC_SITE": "x" if "alpha" in str(scope) else ""}
    fake_mc = types.ModuleType("metrics_collector")

    def _refresh(db, vals, scope_dir=None, hub_dir=None, days=90):
        if vals.get("GSC_SITE"):
            return {"ok": True, "collected": 42, "sources": [{"configured": True}], "note": "ok"}
        return {"ok": True, "collected": 0, "sources": [{"configured": False}], "note": "no sources"}

    fake_mc.refresh = _refresh
    sys.modules["connectors_io"] = fake_conn
    sys.modules["metrics_collector"] = fake_mc
    out, err = io.StringIO(), io.StringIO()
    argv = sys.argv
    sys.argv = ["metrics_refresh_all.py", "--hub", str(hub)]
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            mra.main()
    except SystemExit as e:
        code = e.code or 0
    finally:
        sys.argv = argv
    first = out.getvalue().splitlines()[0] if out.getvalue() else ""
    check("exit 0", code == 0, err.getvalue()[-300:])
    check("riga riepilogo con conteggi e skip",
          first.startswith("📊 metrics refresh") and "alpha: 42 righe" in first and "beta: skip" in first, first)
    detail = json.loads(err.getvalue().strip().splitlines()[-1])
    check("JSON dettaglio su stderr", len(detail["results"]) == 2 and detail["summary"] == first, "")

    print("=" * 44)
    if FAIL:
        print(f"FAIL: {FAIL} (pass {PASS})")
        sys.exit(1)
    print(f"ALL PASS ({PASS})")


if __name__ == "__main__":
    main()
