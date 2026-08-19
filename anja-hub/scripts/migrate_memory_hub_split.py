#!/usr/bin/env python3
"""migrate_memory_hub_split.py — F-AnjadevCoreSplit (2026-08-19).

anjadev v0.21 non espone più i gruppi hub (agents/tasks/workspace/kanban/goals/pp):
vivono in `anja_hub_runtime` (anja-hub/scripts/mcp_hub_runtime.py), stessi nomi
tool. Questo script porta un hub ESISTENTE al nuovo layout, idempotente:

  * ogni `.mcp.json` sotto l'hub (hub-level, workspace root, `.anjawiki/`,
    agent dir) che definisce `anja_memory`:
      - toglie i gruppi hub da `ANJA_TOOL_GROUPS` (o, se l'env era vuoto =
        "tutti", lo fissa ai gruppi core espliciti);
      - toglie `ANJA_HUB_WEBAPP` (anjadev non importa più la webapp);
      - aggiunge `anja_hub_runtime` con i gruppi hub che quell'entry usava
        (hub-level: tutti e 6; workspace senza gruppi espliciti: planning
        `kanban,goals`, + `agents` per root del workspace e lead).
  * ogni `config.json` di agent: i nomi logici del vecchio scoper
    (anja_agents/anja_kanban/anja_goals/anja_workspace/anja_tasks/anja_pp) in
    `mcp_servers` → `anja_hub_runtime`; i lead (`workspace_lead`) lo ricevono
    comunque (orchestrano e delegano).

Usage:
  python3 migrate_memory_hub_split.py --hub /srv/app/hub --dry-run   # mostra il diff
  python3 migrate_memory_hub_split.py --hub /srv/app/hub             # applica

Ordine sicuro sulla live: PRIMA questo script (con anjadev 0.20.x ancora
installato: anja_memory core + runtime convivono, nomi tool disgiunti), POI
`/plugin update anja@anjadev` a 0.21 → nessuna finestra senza planning.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

HUB_GROUPS = ("agents", "tasks", "workspace", "kanban", "goals", "pp")
CORE_GROUPS_HUB = "memory,sessions,soul,user,skills,wiki,roadmap,code,graph"
CORE_GROUPS_WS = "memory,skills,sessions,roadmap"
RUNTIME_GROUPS_HUB = "agents,tasks,workspace,kanban,goals,pp"
RUNTIME_GROUPS_WS = "kanban,goals"
RUNTIME_GROUPS_WS_LEAD = "kanban,goals,agents"
LOGICAL_TO_RUNTIME = {"anja_agents", "anja_kanban", "anja_goals", "anja_workspace", "anja_tasks", "anja_pp"}
RUNTIME_SCRIPT = Path(__file__).resolve().parent / "mcp_hub_runtime.py"
SKIP_DIRS = {"node_modules", ".git", "venv", ".venv", "__pycache__", "raw", "uploads", "backups"}


def _iter_mcp_json(hub: Path):
    """Tutti i .mcp.json sotto l'hub (walk con potatura delle dir rumorose)."""
    stack = [hub]
    while stack:
        d = stack.pop()
        try:
            entries = sorted(d.iterdir())
        except (PermissionError, FileNotFoundError):
            continue
        for e in entries:
            if e.is_dir() and not e.is_symlink():
                if e.name in SKIP_DIRS:
                    continue
                stack.append(e)
            elif e.is_file() and e.name == ".mcp.json":
                yield e


def _iter_agent_configs(hub: Path):
    for base in [hub / "agents"] + sorted((hub / "workspaces").glob("*/.anjawiki/agents")) if (hub / "workspaces").is_dir() else [hub / "agents"]:
        if not base.is_dir():
            continue
        for adir in sorted(base.iterdir()):
            cfg = adir / "config.json"
            if adir.is_dir() and cfg.is_file():
                yield cfg


def _classify(mcp_path: Path, hub: Path) -> tuple[str, str, str | None]:
    """(kind, ws_slug, lead_flag) — kind ∈ hub | ws_root | ws_anjawiki | agent | other."""
    rel = mcp_path.relative_to(hub).parts
    if len(rel) == 1:
        return "hub", "", None
    if rel[0] != "workspaces" or len(rel) < 3:
        return "other", "", None
    ws = rel[1]
    if len(rel) == 3:
        return "ws_root", ws, None
    if rel[2] == ".anjawiki" and len(rel) == 4:
        return "ws_anjawiki", ws, None
    if rel[2] == ".anjawiki" and rel[3] == "agents" and len(rel) == 6:
        cfg = mcp_path.parent / "config.json"
        lead = None
        if cfg.is_file():
            try:
                lead = bool(json.loads(cfg.read_text(encoding="utf-8")).get("workspace_lead"))
            except Exception:
                lead = None
        return "agent", ws, lead
    return "other", ws, None


def migrate_mcp_json(data: dict, kind: str, ws_slug: str, is_lead: bool | None,
                     hub: Path, notes: list[str]) -> dict:
    servers = data.setdefault("mcpServers", {})
    mem = servers.get("anja_memory")
    if not isinstance(mem, dict):
        return data
    env = mem.setdefault("env", {})
    raw = (env.get("ANJA_TOOL_GROUPS") or "").strip()
    groups = [g.strip() for g in raw.split(",") if g.strip()]
    hub_used = [g for g in groups if g in HUB_GROUPS]
    core = [g for g in groups if g not in HUB_GROUPS]

    # 1) anja_memory: gruppi core espliciti, via ANJA_HUB_WEBAPP
    if not raw:
        env["ANJA_TOOL_GROUPS"] = CORE_GROUPS_HUB if kind == "hub" else CORE_GROUPS_WS
        notes.append(f"anja_memory: ANJA_TOOL_GROUPS vuoto (=tutti) → {env['ANJA_TOOL_GROUPS']}")
    elif hub_used:
        env["ANJA_TOOL_GROUPS"] = ",".join(core)
        notes.append(f"anja_memory: tolti gruppi hub {hub_used}")
    if env.pop("ANJA_HUB_WEBAPP", None) is not None:
        notes.append("anja_memory: tolto ANJA_HUB_WEBAPP")

    # 2) anja_hub_runtime: con i gruppi hub che quell'entry usava
    if kind == "hub":
        rt_groups = RUNTIME_GROUPS_HUB
    elif hub_used:
        rt_groups = ",".join(dict.fromkeys(hub_used))
        if kind in ("ws_root",) or is_lead:
            if "agents" not in hub_used:
                rt_groups += ",agents"
    else:
        rt_groups = RUNTIME_GROUPS_WS_LEAD if (kind == "ws_root" or is_lead) else RUNTIME_GROUPS_WS
    if "anja_hub_runtime" not in servers:
        rt_env = {
            "ANJA_SCOPE": env.get("ANJA_SCOPE") or ("hub" if kind == "hub" else "project"),
            "ANJA_ROOT": env.get("ANJA_ROOT") or str(hub),
            "ANJA_HUB": env.get("ANJA_HUB") or str(hub),
            "ANJA_TOOL_GROUPS": rt_groups,
        }
        if ws_slug:
            rt_env["ANJA_WORKSPACE_SCOPE"] = f"workspace:{ws_slug}"
        servers["anja_hub_runtime"] = {
            "command": mem.get("command") or sys.executable,
            "args": [str(RUNTIME_SCRIPT)],
            "env": rt_env,
        }
        notes.append(f"anja_hub_runtime aggiunto ({rt_groups})")
    return data


def migrate_agent_config(cfg: dict, notes: list[str]) -> dict:
    servers = cfg.get("mcp_servers")
    if not isinstance(servers, list):
        return cfg
    new = []
    for s in servers:
        if s in LOGICAL_TO_RUNTIME:
            if "anja_hub_runtime" not in new:
                new.append("anja_hub_runtime")
            notes.append(f"mcp_servers: {s} → anja_hub_runtime")
        elif s not in new:
            new.append(s)
    if cfg.get("workspace_lead") and "anja_hub_runtime" not in new:
        new.insert(0, "anja_hub_runtime")
        notes.append("mcp_servers: lead → + anja_hub_runtime")
    if new != servers:
        cfg["mcp_servers"] = new
    return cfg


def _diff(before: str, after: str, label: str) -> str:
    return "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                        fromfile=f"{label} (before)", tofile=f"{label} (after)"))


def run(hub: Path, dry_run: bool) -> dict:
    report = {"hub": str(hub), "dry_run": dry_run, "changed": [], "unchanged": [], "errors": []}
    if not RUNTIME_SCRIPT.is_file():
        report["errors"].append(f"runtime non trovato: {RUNTIME_SCRIPT}")
        return report
    for f in _iter_mcp_json(hub):
        try:
            before = f.read_text(encoding="utf-8")
            data = json.loads(before)
        except Exception as e:
            report["errors"].append(f"{f}: {e}")
            continue
        kind, ws, lead = _classify(f, hub)
        notes: list[str] = []
        data = migrate_mcp_json(data, kind, ws, lead, hub, notes)
        after = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        if not notes:
            report["unchanged"].append(str(f.relative_to(hub)))
            continue
        rel = str(f.relative_to(hub))
        report["changed"].append({"file": rel, "kind": kind, "notes": notes})
        print(f"\n== {rel}  [{kind}{' lead' if lead else ''}]")
        for n in notes:
            print(f"   - {n}")
        if dry_run:
            print(_diff(before, after, rel))
        else:
            f.write_text(after, encoding="utf-8")
    for cfg_path in _iter_agent_configs(hub):
        try:
            before = cfg_path.read_text(encoding="utf-8")
            cfg = json.loads(before)
        except Exception as e:
            report["errors"].append(f"{cfg_path}: {e}")
            continue
        notes = []
        cfg = migrate_agent_config(cfg, notes)
        if not notes:
            continue
        rel = str(cfg_path.relative_to(hub))
        report["changed"].append({"file": rel, "kind": "agent_config", "notes": notes})
        print(f"\n== {rel}  [agent_config]")
        for n in notes:
            print(f"   - {n}")
        after = json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"
        if dry_run:
            print(_diff(before, after, rel))
        else:
            cfg_path.write_text(after, encoding="utf-8")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--hub", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    hub = Path(args.hub).expanduser().resolve()
    if not (hub / "config" / "projects.json").is_file():
        sys.exit(f"not an anja hub (no config/projects.json): {hub}")
    report = run(hub, args.dry_run)
    print(f"\n[migrate] {'DRY-RUN ' if args.dry_run else ''}changed={len(report['changed'])} "
          f"unchanged={len(report['unchanged'])} errors={len(report['errors'])}")
    for e in report["errors"]:
        print(f"  ! {e}")
    sys.exit(1 if report["errors"] else 0)


if __name__ == "__main__":
    main()
