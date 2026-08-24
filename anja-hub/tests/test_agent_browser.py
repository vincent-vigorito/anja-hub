"""F-AgentBrowser F1: gate read-only, config fail-closed, writer .mcp.json, endpoint.

Il gate è testato con un CHILD FINTO (script che parla JSON-RPC e espone tool
read+act): tools/list filtrata, tools/call negata sui tool di azione e su
browser_evaluate, passthrough del resto.

    /opt/homebrew/opt/python@3.12/bin/python3.12 anja-hub/tests/test_agent_browser.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "anja-hub" / "webapp"))
GATE = REPO / "anja-hub" / "scripts" / "mcp_browser_gate.py"

import browser_policy as bp        # noqa: E402

PASS, FAIL = 0, 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label} {detail}")


FAKE_CHILD = r'''
import json, sys
TOOLS = ["browser_navigate", "browser_snapshot", "browser_take_screenshot",
         "browser_click", "browser_fill_form", "browser_evaluate", "browser_tabs"]
for line in sys.stdin:
    req = json.loads(line)
    m = req.get("method")
    if m == "initialize":
        out = {"jsonrpc": "2.0", "id": req["id"], "result": {"protocolVersion": "x",
               "serverInfo": {"name": "fake-playwright", "version": "0"}, "capabilities": {}}}
    elif m == "tools/list":
        out = {"jsonrpc": "2.0", "id": req["id"], "result": {"tools": [
            {"name": n, "description": n, "inputSchema": {"type": "object"}} for n in TOOLS]}}
    elif m == "tools/call":
        name = req["params"]["name"]
        out = {"jsonrpc": "2.0", "id": req["id"], "result": {"content": [
            {"type": "text", "text": json.dumps({"called": name})}]}}
    else:
        continue
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
'''


def gate_session(policy="read"):
    tmp = Path(tempfile.mkdtemp())
    child = tmp / "fake_child.py"
    child.write_text(FAKE_CHILD)
    proc = subprocess.Popen(
        [sys.executable, str(GATE), "--policy", policy, "--",
         sys.executable, str(child)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, bufsize=1)
    return proc


def rpc(proc, req):
    proc.stdin.write(json.dumps(req) + "\n")
    proc.stdin.flush()
    return json.loads(proc.stdout.readline())


def test_gate():
    print("mcp_browser_gate (child finto)")
    proc = gate_session("read")
    try:
        r = rpc(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        check("initialize passthrough", r["result"]["serverInfo"]["name"] == "fake-playwright")
        r = rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = [t["name"] for t in r["result"]["tools"]]
        check("read tools presenti", "browser_snapshot" in names and "browser_navigate" in names)
        check("act tools filtrati da tools/list",
              "browser_click" not in names and "browser_fill_form" not in names, str(names))
        check("evaluate mai listato", "browser_evaluate" not in names)
        r = rpc(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                       "params": {"name": "browser_snapshot", "arguments": {}}})
        check("call read → passthrough al child",
              json.loads(r["result"]["content"][0]["text"])["called"] == "browser_snapshot")
        r = rpc(proc, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                       "params": {"name": "browser_click", "arguments": {}}})
        check("call act → negata dal gate (non arriva al child)",
              "error" in r and "azione" in r["error"]["message"], str(r)[:150])
        r = rpc(proc, {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                       "params": {"name": "browser_evaluate", "arguments": {}}})
        check("call evaluate → negata sempre", "error" in r and "JS" in r["error"]["message"])
    finally:
        proc.kill()

    proc = gate_session("act")
    try:
        r = rpc(proc, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        names = [t["name"] for t in r["result"]["tools"]]
        check("policy act: click listato, evaluate comunque no",
              "browser_click" in names and "browser_evaluate" not in names, str(names))
    finally:
        proc.kill()


def test_policy():
    print("browser_policy: config + writer")
    tmp = Path(tempfile.mkdtemp())
    ws = tmp / "workspaces" / "alpha"
    (ws / ".anjawiki").mkdir(parents=True)

    errs = bp.validate({"enabled": True})
    check("enabled senza origins → invalido (fail-closed)", errs, str(errs))
    check("origin senza schema → invalido", bp.validate(
        {"enabled": True, "allowed_origins": ["dash.example.com"]}))
    check("policy act-auto → invalida in v1", bp.validate(
        {"enabled": True, "allowed_origins": ["https://x.it"], "policy": "act-auto"}))
    check("config valida → ok", not bp.validate(
        {"enabled": True, "allowed_origins": ["https://x.it"], "policy": "read"}))

    bp.save_config(ws, {"enabled": True, "allowed_origins": ["https://dash.x.it"]})
    present = bp.write_mcp_entry(ws, GATE)
    entry = json.loads((ws / ".mcp.json").read_text())["mcpServers"][bp.SERVER_NAME]
    check("writer: entry col gate e le origins", present
          and str(GATE) in entry["args"][0]
          and "--allowed-origins" in entry["args"] and "https://dash.x.it" in entry["args"],
          str(entry)[:200])
    check("writer: isolated, headless, niente storage-state (assente)",
          "--isolated" in entry["args"] and "--headless" in entry["args"]
          and "--storage-state" not in entry["args"])
    check(".browser/.gitignore creato", (ws / ".browser" / ".gitignore").is_file())

    info = bp.save_storage_state(ws, json.dumps({"cookies": [{"name": "s"}], "origins": []}).encode())
    check("storage state salvato 0600", info["cookies"] == 1 and
          oct((bp.browser_dir(ws) / bp.STATE_NAME).stat().st_mode)[-3:] == "600")
    bp.write_mcp_entry(ws, GATE)
    entry = json.loads((ws / ".mcp.json").read_text())["mcpServers"][bp.SERVER_NAME]
    check("writer: --storage-state appare dopo l\'import", "--storage-state" in entry["args"])
    try:
        bp.save_storage_state(ws, b'{"nope": 1}')
        check("state senza cookies → rifiutato", False)
    except ValueError:
        check("state senza cookies → rifiutato", True)

    bp.save_config(ws, {"enabled": False, "allowed_origins": []})
    present = bp.write_mcp_entry(ws, GATE)
    check("disabled → entry rimossa", not present and bp.SERVER_NAME not in
          json.loads((ws / ".mcp.json").read_text())["mcpServers"])


def test_endpoints():
    print("endpoint webapp")
    from fastapi.testclient import TestClient
    import server
    tmp = Path(tempfile.mkdtemp())
    hub = tmp / "hub"
    ws = hub / "workspaces" / "alpha"
    (ws / ".anjawiki").mkdir(parents=True)
    (hub / "config").mkdir()
    (hub / "config" / "config.json").write_text("{}")
    (hub / "config" / "projects.json").write_text(json.dumps({"projects": [
        {"name": "alpha", "location": {"kind": "local", "path": str(ws)}}]}))
    server.HUB_PATH = hub
    c = TestClient(server.app)

    r = c.put("/api/browser/config", json={"scope": "project:alpha", "browser":
              {"enabled": True, "allowed_origins": ["https://dash.x.it"]}})
    check("PUT config → entry materializzata", r.status_code == 200 and r.json()["mcp_present"],
          r.text[:150])
    r = c.put("/api/browser/config", json={"scope": "project:alpha", "browser": {"enabled": True}})
    check("PUT enabled senza origins → 400", r.status_code == 400)
    r = c.get("/api/browser/config?scope=project:alpha")
    check("GET config", r.status_code == 200 and r.json()["browser"]["enabled"]
          and not r.json()["state_present"])
    state = json.dumps({"cookies": [{"name": "sid"}], "origins": []})
    r = c.post("/api/browser/state-import", data={"scope": "project:alpha"},
               files={"file": ("state.json", state, "application/json")})
    check("state import → ok", r.status_code == 200 and r.json()["cookies"] == 1, r.text[:150])
    r = c.post("/api/browser/state-reset", json={"scope": "project:alpha"})
    check("state reset", r.status_code == 200 and r.json()["removed"])
    r = c.get("/api/browser/config?scope=hub")
    check("scope hub → 400 (browser è per-workspace)", r.status_code == 400)


def main():
    test_gate()
    test_policy()
    test_endpoints()
    print("=" * 44)
    if FAIL:
        print(f"FAIL: {FAIL} (pass {PASS})")
        sys.exit(1)
    print(f"ALL PASS ({PASS})")


if __name__ == "__main__":
    main()
