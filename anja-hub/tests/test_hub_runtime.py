"""anja_hub_runtime (F-AnjadevCoreSplit) — i tool hub-only spostati da anjadev.

Congela: registry (28 tool, 6 gruppi, filtro env + gruppo ignoto), import webapp
per posizione, kanban/goals/tasks/workspace sui moduli reali su un hub temporaneo,
e la regressione 0.20.2–0.20.4 di agent.delegate (pod omonimi in due workspace:
mai il primo match, ambiguità esplicita, target qualificato ws/nome, auto-route
vincolato al workspace) con un SDK finto — niente LLM, niente rete.

    /opt/homebrew/opt/python@3.12/bin/python3.12 anja-hub/tests/test_hub_runtime.py
"""
import importlib.util
import json
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "scripts" / "mcp_hub_runtime.py"

PASS = FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label} {detail}")


def load_runtime(**env):
    """Importa il runtime con l'env dato (SCOPE/ROOT/gruppi sono letti a import)."""
    for k in ("ANJA_SCOPE", "ANJA_ROOT", "ANJA_HUB", "ANJA_TOOL_GROUPS", "ANJA_WORKSPACE_SCOPE"):
        os.environ.pop(k, None)
    os.environ.update(env)
    spec = importlib.util.spec_from_file_location(f"hub_runtime_{len(env)}_{id(env)}", RUNTIME)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def call(mod, tool, **args):
    resp = mod.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": tool, "arguments": args}})
    if "error" in resp:
        return {"_rpc_error": resp["error"]}
    return json.loads(resp["result"]["content"][0]["text"])


def make_hub(tmp: Path) -> Path:
    hub = tmp / "hub"
    (hub / "config").mkdir(parents=True)
    (hub / "config" / "projects.json").write_text(json.dumps({"projects": [
        {"name": "alpha", "type": "marketing", "location": {"path": str(hub / "workspaces" / "alpha")}},
        {"name": "beta", "type": "marketing", "location": {"path": str(hub / "workspaces" / "beta")}},
    ]}))
    (hub / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "anja_marketing": {"command": "python3", "args": ["/x/mcp_marketing_server.py"], "env": {}}}}))
    (hub / "agents" / "anja").mkdir(parents=True)
    (hub / "agents" / "anja" / "config.json").write_text(json.dumps(
        {"name": "anja", "role": "hub default", "default_model": "sonnet"}))
    for ws in ("alpha", "beta"):
        wsr = hub / "workspaces" / ws
        (wsr / "files").mkdir(parents=True)
        (wsr / "data").mkdir()
        aw = wsr / ".anjawiki"
        (aw / "wiki").mkdir(parents=True)
        (aw / "log.md").write_text("# log\n")
        (wsr / f"../{ws}.meta.yaml").resolve()
        (hub / "workspaces" / f"{ws}.meta.yaml").write_text(f"kind: internal\nresponsabile: anja-{ws}\n")
        for role, kws, lead in (("seo-copy", ["seo", "articolo"], False),
                                (f"anja-{ws}", ["brand"], True)):
            d = aw / "agents" / role
            d.mkdir(parents=True)
            cfg = {"name": role, "role": f"{role} di {ws}", "default_model": "sonnet",
                   "auto_route_keywords": kws, "workspace_name": ws}
            if lead:
                cfg["workspace_lead"] = True
            (d / "config.json").write_text(json.dumps(cfg))
    return hub


def install_fake_sdk(calls: list):
    """claude_agent_sdk finto: registra cwd/model/mcp_servers e risponde col nome del ruolo."""
    fake = types.ModuleType("claude_agent_sdk")

    class ClaudeAgentOptions:
        def __init__(self, **kw):
            self.kw = kw

    class TextBlock:
        def __init__(self, text):
            self.text = text

    class AssistantMessage:
        def __init__(self, text):
            self.content = [TextBlock(text)]

    async def query(prompt, options):
        calls.append({"prompt": prompt, **options.kw})
        yield AssistantMessage(f"fake reply from {options.kw['system_prompt'].splitlines()[0]}")

    fake.ClaudeAgentOptions = ClaudeAgentOptions
    fake.query = query
    sys.modules["claude_agent_sdk"] = fake


def main():
    tmp = Path(tempfile.mkdtemp())
    hub = make_hub(tmp)

    print("registry (scope=hub, default = tutti)")
    rt = load_runtime(ANJA_SCOPE="hub", ANJA_ROOT=str(hub))
    lst = rt.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    names = [t["name"] for t in lst["result"]["tools"]]
    check("28 tool", len(names) == 28, str(len(names)))
    check("6 gruppi", set(rt.TOOL_GROUPS) == {"agents", "tasks", "workspace", "kanban", "goals", "pp"})
    check("nessun tool core (wiki/memory/skill/roadmap/code)",
          not any(n.split(".")[0] in ("wiki", "memory", "skill", "roadmap", "code", "sessions", "soul", "user", "graph") for n in names))
    check("handler per ogni tool", all(n in rt.TOOL_HANDLERS for n in names))
    check("webapp per posizione", rt.WEBAPP_DIR == ROOT / "webapp" and (rt.WEBAPP_DIR / "kanban_io.py").is_file())
    check("initialize → anja_hub_runtime",
          rt.handle_request({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}})["result"]["serverInfo"]["name"] == "anja_hub_runtime")

    print("filtro gruppi + gruppo ignoto")
    rt2 = load_runtime(ANJA_SCOPE="hub", ANJA_ROOT=str(hub), ANJA_TOOL_GROUPS="kanban,goals,nope")
    n2 = [t["name"] for t in rt2.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})["result"]["tools"]]
    check("kanban+goals = 15", len(n2) == 15 and all(n.startswith(("kanban.", "goal.")) for n in n2), str(n2))
    r = rt2.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "agent.list", "arguments": {}}})
    check("tool fuori gruppo → -32601", r.get("error", {}).get("code") == -32601)

    print("kanban / goals / tasks / workspace sui moduli webapp reali")
    r = call(rt, "kanban.create", title="Scrivi articolo", scope="workspace:alpha", priority=2)
    check("kanban.create", r.get("ok") and r["task"]["id"] >= 1, str(r)[:200])
    tid = r["task"]["id"]
    r = call(rt, "kanban.show", id=tid)
    check("kanban.show id", r.get("task", {}).get("title") == "Scrivi articolo", str(r)[:200])
    r = call(rt, "kanban.complete", id=tid, summary="fatto")
    check("kanban.complete", r.get("ok") and r["task"]["status"] == "done", str(r)[:200])
    r = call(rt, "kanban.search", query="articolo")
    check("kanban.search", any(t["id"] == tid for t in r.get("results", [])), str(r)[:200])
    r = call(rt, "goal.create", title="Raddoppiare il traffico", scope="workspace:alpha")
    check("goal.create", "id" in r and "error" not in r, str(r)[:200])
    r = call(rt, "goal.list", scope="workspace:alpha")
    check("goal.list", len(r.get("goals", [])) == 1, str(r)[:200])
    r = call(rt, "task.schedule_one_shot", when="in 30 min", prompt="ping", name="oneshot-test")
    check("task.schedule_one_shot scrive routines/oneshot-test.yaml",
          r.get("scheduled") and (hub / "routines" / "oneshot-test.yaml").is_file(), str(r)[:200])
    r = call(rt, "task.list")
    check("task.list", r.get("count") == 1, str(r)[:200])
    r = call(rt, "task.cancel", name="oneshot-test")
    check("task.cancel", r.get("cancelled") and not (hub / "routines" / "oneshot-test.yaml").exists())
    r = call(rt, "workspace.write_file", scope="workspace:alpha", path="files/note.md", content="ciao")
    check("workspace.write_file", r.get("ok") and (hub / "workspaces" / "alpha" / "files" / "note.md").read_text() == "ciao", str(r)[:200])
    r = call(rt, "workspace.read_file", scope="workspace:alpha", path="files/note.md")
    check("workspace.read_file", r.get("content") == "ciao")
    r = call(rt, "workspace.write_file", scope="workspace:alpha", path="../../evil.txt", content="x")
    check("path traversal negato", "error" in r and "traversal" in r["error"], str(r))
    r = call(rt, "workspace.write_file", scope="workspace:alpha", path="CLAUDE.md", content="x")
    check("write fuori files/data/scripts negato", "error" in r, str(r))
    r = call(rt, "workspace.list")
    check("workspace.list dal registry", {w["name"] for w in r["workspaces"]} == {"alpha", "beta"}
          and all(w["kind"] == "internal" for w in r["workspaces"]), str(r)[:200])
    r = call(rt, "agent.list")
    check("agent.list: hub + 4 di workspace", r["count"] == 5 and
          sorted(a.get("workspace", "") for a in r["agents"]) == ["", "alpha", "alpha", "beta", "beta"], str(r)[:300])

    print("agent.delegate — regressione 0.20.2–0.20.4 (pod omonimi)")
    calls = []
    install_fake_sdk(calls)
    r = call(rt, "agent.delegate", target="seo-copy", prompt="scrivi un articolo seo")
    check("nome duplicato senza workspace → ambiguo, niente spawn",
          "ambiguo" in r.get("error", "") and set(r.get("candidates", [])) == {"alpha", "beta"} and not calls, str(r)[:200])
    r = call(rt, "agent.delegate", target="beta/seo-copy", prompt="scrivi un articolo seo")
    check("target qualificato beta/seo-copy → spawn su beta",
          r.get("workspace") == "beta" and r.get("agent") == "seo-copy" and len(calls) == 1, str(r)[:200])
    check("cwd = hub (.mcp.json), mcp merge hub",
          Path(calls[-1]["cwd"]).resolve() == hub.resolve() and "anja_marketing" in calls[-1]["mcp_servers"], str(calls[-1])[:200])
    check("least-privilege default: Read/Grep/Glob + mcp pattern, permission default",
          calls[-1]["allowed_tools"][:3] == ["Read", "Grep", "Glob"] and calls[-1]["permission_mode"] == "default"
          and calls[-1]["strict_mcp_config"] is True, str(calls[-1])[:300])
    check("sessione loggata in agents/seo-copy/sessions di beta",
          any((hub / "workspaces" / "beta" / ".anjawiki" / "agents" / "seo-copy" / "sessions").rglob("*.md")))
    r = call(rt, "agent.delegate", target="seo-copy", workspace="alpha", prompt="articolo")
    check("param workspace=alpha vincola", r.get("workspace") == "alpha" and len(calls) == 2, str(r)[:200])
    r = call(rt, "agent.delegate", prompt="scrivi un articolo seo")
    check("auto-route senza workspace, keyword pari in 2 brand → chiede workspace",
          "ambiguo" in r.get("error", "") and len(calls) == 2, str(r)[:200])
    r = call(rt, "agent.delegate", prompt="scrivi un articolo seo per beta")
    check("auto-route: workspace inferito dal prompt → beta/seo-copy",
          r.get("workspace") == "beta" and r.get("routing", {}).get("routed_to") == "seo-copy" and len(calls) == 3, str(r)[:200])
    r = call(rt, "agent.delegate", prompt="parlami del brand", workspace="alpha")
    check("auto-route keyword del lead", r.get("agent") == "anja-alpha" and len(calls) == 4, str(r)[:200])
    r = call(rt, "agent.delegate", target="ghost", prompt="x")
    check("agent inesistente → not found", "not found" in r.get("error", ""), str(r))

    print("scope=project (workspace) con ANJA_HUB")
    rtp = load_runtime(ANJA_SCOPE="project", ANJA_ROOT=str(hub / "workspaces" / "alpha"), ANJA_HUB=str(hub),
                       ANJA_TOOL_GROUPS="kanban,goals")
    r = call(rtp, "kanban.show", limit=5)
    check("kanban.show dal workspace via ANJA_HUB", any(t["id"] == tid for t in r.get("tasks", [])), str(r)[:200])
    rtn = load_runtime(ANJA_SCOPE="project", ANJA_ROOT=str(hub / "workspaces" / "alpha"))
    r = call(rtn, "kanban.show")
    check("senza ANJA_HUB → errore graceful", r.get("error") == "hub root not determinable", str(r))

    print("=" * 44)
    if FAIL:
        print(f"FAIL: {FAIL} (pass {PASS})")
        sys.exit(1)
    print(f"ALL PASS ({PASS})")


if __name__ == "__main__":
    main()
