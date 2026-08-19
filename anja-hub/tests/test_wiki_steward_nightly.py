"""H1 — wrapper notturno dello steward (AnjaHub consumer di anjadev).

roots_for: hub + workspace con .anjawiki; run su hub temporaneo con ANJADEV_DIR → riga di
riepilogo + JSON su stderr; anjadev assente → exit 2 con messaggio; routine YAML valida.

    /opt/homebrew/opt/python@3.12/bin/python3.12 anja-hub/tests/test_wiki_steward_nightly.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
SCRIPT = ROOT / "scripts" / "wiki_steward_nightly.py"
ANJADEV = Path(os.environ.get("ANJADEV_DIR") or Path.home() / ".claude" / "plugins" / "marketplaces" / "anjadev")
PY = sys.executable
PASS = FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ✓ {label}")
    else:
        FAIL += 1; print(f"  ✗ {label} {detail}")


def main():
    tmp = Path(tempfile.mkdtemp()); hub = tmp / "hub"
    (hub / "config").mkdir(parents=True); (hub / "config" / "projects.json").write_text('{"projects": []}')
    (hub / ".anjawiki" / "wiki" / "sessions").mkdir(parents=True)
    (hub / "sessions").mkdir()
    for ws in ("alpha", "beta"):
        (hub / "workspaces" / ws / ".anjawiki" / "wiki").mkdir(parents=True)
    (hub / "workspaces" / "nowiki").mkdir(parents=True)
    spec = importlib.util.spec_from_file_location("wsn", SCRIPT); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    labels = [l for l, _ in m.roots_for(hub)]
    check("roots_for: hub + 2 workspace con wiki, nowiki escluso", labels == ["hub", "ws:alpha", "ws:beta"], str(labels))

    if (ANJADEV / "scripts" / "steward.py").is_file():
        r = subprocess.run([PY, str(SCRIPT), "--hub", str(hub), "--dry-run"], capture_output=True, text=True,
                           env=dict(os.environ, ANJADEV_DIR=str(ANJADEV), ANJA_STEWARD_BIN="none"), timeout=120)
        check("dry-run: rc 0 + riga di riepilogo", r.returncode == 0 and r.stdout.startswith("🌙 wiki steward DRY-RUN"), r.stdout + r.stderr[-200:])
        rep = json.loads([l for l in r.stderr.splitlines() if l.startswith("{")][-1])
        check("3 root processati, nessun errore (sessions assenti ≠ errore)", len(rep["results"]) == 3 and not rep["errors"], str(rep["errors"]))
    else:
        print("  (anjadev non installato: salto il run)")
    r = subprocess.run([PY, str(SCRIPT), "--hub", str(hub)], capture_output=True, text=True,
                       env=dict(os.environ, ANJADEV_DIR=str(tmp / "nope")), timeout=30)
    check("anjadev assente → exit 2 + messaggio", r.returncode == 2 and "non trovato" in r.stdout, r.stdout)

    yaml_path = ROOT / "templates" / "hub-skeleton" / "routines" / "wiki-steward-nightly.yaml"
    r = subprocess.run([PY, str(REPO / "anja-routines" / "scripts" / "routine_validate.py"), str(yaml_path)], capture_output=True, text=True)
    check("routine YAML valida (scope hub, Bash, telegram, 04:15)", r.returncode == 0 and "15 4 * * *" in yaml_path.read_text() and "type: telegram" in yaml_path.read_text(), r.stdout[-200:] + r.stderr[-200:])
    print("=" * 44)
    if FAIL:
        print(f"FAIL: {FAIL} (pass {PASS})"); sys.exit(1)
    print(f"ALL PASS ({PASS})")


if __name__ == "__main__":
    main()
