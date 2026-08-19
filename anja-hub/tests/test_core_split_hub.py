"""F-AnjadevCoreSplit — lato AnjaHub: scaffold con due server, migration dei .mcp.json
esistenti (hub-level senza gruppi = il caso live), scoper che instrada sul runtime.

    /opt/homebrew/opt/python@3.12/bin/python3.12 anja-hub/tests/test_core_split_hub.py
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webapp"))
import blueprint_scaffold as bs   # noqa: E402
import mcp_scoper                 # noqa: E402

spec = importlib.util.spec_from_file_location("mig", ROOT / "scripts" / "migrate_memory_hub_split.py")
mig = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mig)

PASS = FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label} {detail}")


def _w(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")


def _r(p: Path) -> dict:
    return json.loads(p.read_text())


def make_hub(tmp: Path) -> Path:
    """Hub 'come la live': anja_memory hub-level SENZA gruppi + ANJA_HUB_WEBAPP,
    workspace con kanban/goals su anja_memory, lead con `agents`, config lead con
    anja_agents, una .anjawiki/.mcp.json legacy con solo anja_hub_ops."""
    hub = tmp / "hub"
    _w(hub / "config" / "projects.json", {"projects": [{"name": "acme", "type": "marketing",
                                                         "location": {"path": str(hub / "workspaces" / "acme")}}]})
    mem = lambda root, groups: {"command": "python3", "args": ["/plug/scripts/mcp_memory_server.py"],
                                "env": {"ANJA_SCOPE": "project", "ANJA_ROOT": root, "ANJA_HUB": str(hub),
                                        "ANJA_HUB_WEBAPP": "/old/webapp", "ANJA_TOOL_GROUPS": groups}}
    _w(hub / ".mcp.json", {"mcpServers": {
        "anja_memory": {"command": "python3", "args": ["/plug/scripts/mcp_memory_server.py"],
                        "env": {"ANJA_SCOPE": "hub", "ANJA_ROOT": str(hub), "ANJA_HUB": str(hub),
                                "ANJA_HUB_WEBAPP": "/old/webapp"}},
        "hub_api": {"command": "python3", "args": ["/x/mcp_hub_api_server.py"], "env": {}}}})
    ws = hub / "workspaces" / "acme"
    _w(ws / ".mcp.json", {"mcpServers": {"anja_marketing": {"command": "python3", "args": ["/x/m.py"], "env": {}},
                                         "anja_memory": mem(str(ws), "memory,skills,sessions,kanban,goals,roadmap")}})
    _w(ws / ".anjawiki" / ".mcp.json", {"mcpServers": {"anja_hub_ops": {"command": "python3", "args": ["/x/ops.py"], "env": {}}}})
    for role, lead in (("anja-acme", True), ("seo-copy", False)):
        adir = ws / ".anjawiki" / "agents" / role
        _w(adir / ".mcp.json", {"mcpServers": {
            "anja_marketing": {"command": "python3", "args": ["/x/m.py"], "env": {}},
            "anja_memory": mem(str(ws), "memory,skills,sessions,kanban,goals,roadmap" + (",agents" if lead else ""))}})
        cfg = {"name": role, "mcp_servers": (["anja_agents", "anja_marketing"] if lead else ["anja_marketing"])}
        if lead:
            cfg["workspace_lead"] = True
        _w(adir / "config.json", cfg)
    return hub


def main():
    tmp = Path(tempfile.mkdtemp())
    hub = make_hub(tmp)

    print("scaffold: _build_mcp_servers → anja_memory core + anja_hub_runtime")
    ws = hub / "workspaces" / "acme"
    srv = bs._build_mcp_servers("acme", ws, ws / ".anjawiki", hub, "cms,analytics,social")
    check("tre server (marketing, memory, runtime)", set(srv) == {"anja_marketing", "anja_memory", "anja_hub_runtime"}, str(list(srv)))
    menv = srv["anja_memory"]["env"]
    check("memory: gruppi core, niente kanban/goals", menv["ANJA_TOOL_GROUPS"] == "memory,skills,sessions,roadmap", str(menv))
    check("memory: niente ANJA_HUB_WEBAPP, ANJA_HUB presente", "ANJA_HUB_WEBAPP" not in menv and menv["ANJA_HUB"] == str(hub))
    renv = srv["anja_hub_runtime"]["env"]
    check("runtime: script per posizione", srv["anja_hub_runtime"]["args"] == [str(ROOT / "scripts" / "mcp_hub_runtime.py")])
    check("runtime: stesso interprete di anja_memory", srv["anja_hub_runtime"]["command"] == "python3")
    check("runtime default specialista kanban,goals + ANJA_WORKSPACE_SCOPE",
          renv["ANJA_TOOL_GROUPS"] == "kanban,goals" and renv["ANJA_WORKSPACE_SCOPE"] == "workspace:acme"
          and renv["ANJA_SCOPE"] == "project" and renv["ANJA_HUB"] == str(hub), str(renv))
    srv_lead = bs._build_mcp_servers("acme", ws, ws / ".anjawiki", hub, "cms", runtime_groups=bs.RUNTIME_GROUPS_LEAD)
    check("runtime lead: kanban,goals,agents", srv_lead["anja_hub_runtime"]["env"]["ANJA_TOOL_GROUPS"] == "kanban,goals,agents")
    srv_extra = bs._build_mcp_servers("acme", ws, ws / ".anjawiki", hub, "cms", extra_servers=("anja_hub_runtime", "hub_api", "anja_agents"))
    check("extra: runtime non duplicato, hub_api reale copiato, nome logico ignorato",
          list(srv_extra).count("anja_hub_runtime") == 1 and "hub_api" in srv_extra and "anja_agents" not in srv_extra, str(list(srv_extra)))

    print("migration: dry-run non scrive")
    before = {p: p.read_text() for p in hub.rglob("*.json")}
    rep = mig.run(hub, dry_run=True)
    check("4 file .mcp.json + 1 config cambierebbero", len(rep["changed"]) == 5, str([c["file"] for c in rep["changed"]]))
    check("nessun file scritto", all(p.read_text() == t for p, t in before.items()))
    check("legacy .anjawiki/.mcp.json (solo hub_ops) unchanged", "workspaces/acme/.anjawiki/.mcp.json" in rep["unchanged"], str(rep["unchanged"]))

    print("migration: apply")
    rep = mig.run(hub, dry_run=False)
    h = _r(hub / ".mcp.json")["mcpServers"]
    check("hub-level: anja_memory gruppi core espliciti (era vuoto=tutti)",
          h["anja_memory"]["env"]["ANJA_TOOL_GROUPS"] == mig.CORE_GROUPS_HUB, str(h["anja_memory"]["env"]))
    check("hub-level: via ANJA_HUB_WEBAPP", "ANJA_HUB_WEBAPP" not in h["anja_memory"]["env"])
    check("hub-level: runtime con i 6 gruppi, scope hub",
          h["anja_hub_runtime"]["env"]["ANJA_TOOL_GROUPS"] == "agents,tasks,workspace,kanban,goals,pp"
          and h["anja_hub_runtime"]["env"]["ANJA_SCOPE"] == "hub" and "ANJA_WORKSPACE_SCOPE" not in h["anja_hub_runtime"]["env"], str(h["anja_hub_runtime"]))
    check("hub-level: hub_api intatto", h["hub_api"]["args"] == ["/x/mcp_hub_api_server.py"])
    w = _r(ws / ".mcp.json")["mcpServers"]
    check("ws root: memory senza kanban/goals, ordine conservato",
          w["anja_memory"]["env"]["ANJA_TOOL_GROUPS"] == "memory,skills,sessions,roadmap", str(w["anja_memory"]["env"]))
    check("ws root: runtime kanban,goals + agents (chat di workspace orchestra)",
          w["anja_hub_runtime"]["env"]["ANJA_TOOL_GROUPS"] == "kanban,goals,agents"
          and w["anja_hub_runtime"]["env"]["ANJA_WORKSPACE_SCOPE"] == "workspace:acme", str(w["anja_hub_runtime"]["env"]))
    check("ws root: runtime copia ANJA_ROOT/ANJA_HUB/interprete da anja_memory",
          w["anja_hub_runtime"]["env"]["ANJA_ROOT"] == str(ws) and w["anja_hub_runtime"]["command"] == "python3")
    lead = _r(ws / ".anjawiki" / "agents" / "anja-acme" / ".mcp.json")["mcpServers"]
    check("lead: runtime kanban,goals,agents (agents era già sul memory)",
          lead["anja_hub_runtime"]["env"]["ANJA_TOOL_GROUPS"] == "kanban,goals,agents", str(lead["anja_hub_runtime"]["env"]))
    spec_ = _r(ws / ".anjawiki" / "agents" / "seo-copy" / ".mcp.json")["mcpServers"]
    check("specialista: runtime kanban,goals (niente agents)",
          spec_["anja_hub_runtime"]["env"]["ANJA_TOOL_GROUPS"] == "kanban,goals", str(spec_["anja_hub_runtime"]["env"]))
    lcfg = _r(ws / ".anjawiki" / "agents" / "anja-acme" / "config.json")
    check("config lead: anja_agents → anja_hub_runtime, marketing conservato",
          lcfg["mcp_servers"] == ["anja_hub_runtime", "anja_marketing"], str(lcfg))
    scfg = _r(ws / ".anjawiki" / "agents" / "seo-copy" / "config.json")
    check("config specialista intatta", scfg["mcp_servers"] == ["anja_marketing"])
    print("migration: idempotente")
    rep2 = mig.run(hub, dry_run=False)
    check("seconda corsa: 0 cambi", not rep2["changed"], str(rep2["changed"]))

    print("scoper: keyword → anja_hub_runtime (entry reale), tier0 vuoto")
    m = mcp_scoper.DEFAULT_MANIFEST
    check("tier0 = []", m["tier0"] == [])
    check("nessun nome logico fantasma nella keyword map",
          not any(v in ("anja_kanban", "anja_goals", "anja_agents", "anja_workspace", "anja_tasks", "anja_pp", "anja_soul")
                  for v in m["keyword_map"].values()), str(set(m["keyword_map"].values())))
    final, meta = mcp_scoper.scope_mcps(hub, "hub", user_prompt="quante card ci sono nel kanban?")
    check("hub scope + 'kanban' → hub_api, anja_memory, anja_hub_runtime montati (tutti reali)",
          final == ["hub_api", "anja_memory", "anja_hub_runtime"] and not meta["dropped_unavailable"], str(meta))
    final, meta = mcp_scoper.scope_mcps(hub, "project", target_name="acme", cwd=ws, user_prompt="ciao")
    check("project scope: tier1 = .mcp.json del workspace (marketing, memory, runtime)",
          set(final) == {"anja_marketing", "anja_memory", "anja_hub_runtime"}, str(final))
    final, meta = mcp_scoper.scope_mcps(hub, "agent", target_name="seo-copy", cwd=ws / ".anjawiki" / "agents" / "seo-copy",
                                        user_prompt="scrivi l'articolo", agent_config=scfg)
    check("agent specialista senza keyword → solo anja_marketing", final == ["anja_marketing"], str(final))
    final, meta = mcp_scoper.scope_mcps(hub, "agent", target_name="seo-copy", cwd=ws / ".anjawiki" / "agents" / "seo-copy",
                                        user_prompt="segna il task come fatto nel kanban", agent_config=scfg)
    check("agent specialista + keyword kanban → + anja_hub_runtime (entry presente nel suo .mcp.json)",
          final == ["anja_marketing", "anja_hub_runtime"], str(final))
    final, meta = mcp_scoper.scope_mcps(hub, "agent", target_name="anja-acme", cwd=ws / ".anjawiki" / "agents" / "anja-acme",
                                        user_prompt="delega a @seo-copy", agent_config=lcfg)
    check("lead: runtime da config (tier1), non dipende dalla keyword",
          final[:2] == ["anja_hub_runtime", "anja_marketing"] and meta["reasons"]["anja_hub_runtime"].startswith("tier1"), str(meta["reasons"]))

    print("=" * 44)
    if FAIL:
        print(f"FAIL: {FAIL} (pass {PASS})")
        sys.exit(1)
    print(f"ALL PASS ({PASS})")


if __name__ == "__main__":
    main()
