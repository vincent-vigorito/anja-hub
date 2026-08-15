"""M-SelfHealth — collect() dei check di salute dell'always-on.

    /opt/homebrew/opt/python@3.12/bin/python3.12 anja-hub/tests/test_self_health.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))
import self_health  # noqa: E402


def _get(res, name):
    return next(c for c in res["checks"] if c["name"] == name)


def main():
    hub = Path(tempfile.mkdtemp())

    # 1. hub spoglio: daemon morto, no provider, no mcp, no index --------------
    res = self_health.collect(hub, daemons={"telegram": True, "goal_scheduler": False})
    assert _get(res, "daemon:telegram")["ok"]
    assert not _get(res, "daemon:goal_scheduler")["ok"] and _get(res, "daemon:goal_scheduler")["severity"] == "error"
    assert not _get(res, "providers:keys")["ok"]            # nessuna chiave
    assert res["status"] == "error"                         # daemon morto + no provider
    assert any(c["name"] == "daemon:goal_scheduler" for c in res["failing"])
    print("✓ spoglio: daemon morto=error · no provider=error · status=error")

    # 2. provider + mcp ok ----------------------------------------------------
    (hub / ".secrets.env").write_text('ANTHROPIC_API_KEY="sk-xxx"\n', encoding="utf-8")
    script = hub / "fake_mcp.py"; script.write_text("# mcp\n", encoding="utf-8")
    (hub / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "anja_memory": {"command": "python3", "args": [str(script)]},
        "broken": {"command": "python3", "args": [str(hub / "missing.py")]},
    }}), encoding="utf-8")
    res = self_health.collect(hub, daemons={"telegram": True})
    assert _get(res, "providers:keys")["ok"], "ANTHROPIC presente"
    assert _get(res, "mcp:anja_memory")["ok"], "script presente"
    assert not _get(res, "mcp:broken")["ok"], "script mancante → error"
    print("✓ provider chiave presente · mcp script presente vs mancante")

    # 3. index fresh vs stale -------------------------------------------------
    (hub / ".anjawiki").mkdir()
    idx = hub / ".anjawiki" / "code-index.db"; idx.write_text("x", encoding="utf-8")
    assert _get(self_health.collect(hub), "index:code")["ok"], "fresh"
    old = time.time() - (self_health.INDEX_STALE_DAYS + 3) * 86400
    os.utime(idx, (old, old))
    ic = _get(self_health.collect(hub), "index:code")
    assert not ic["ok"] and ic["severity"] == "warn", "stale → warn"
    print("✓ index: fresh=ok · vecchio=stale warn")

    # 4. disco (reale, di solito ok) ------------------------------------------
    assert _get(self_health.collect(hub), "disk:free")["name"] == "disk:free"
    print("✓ disk:free presente")

    print("\nOK 4/4")


if __name__ == "__main__":
    main()
