#!/usr/bin/env python3
"""
agent_list.py — lista agents registrati nel hub.

Per ogni agent in `<hub>/agents/<name>/`, legge config.json e mostra:
    name, role, model, scope, sessions count.

Usage:
    python3 agent_list.py --hub <hub-root>
    python3 agent_list.py --hub <hub> --json
"""

import argparse
import json
import sys
from pathlib import Path


def list_agents(hub: Path) -> list:
    out = []
    agents_root = hub / "agents"
    if not agents_root.is_dir():
        return out
    for sub in sorted(agents_root.iterdir()):
        if not sub.is_dir():
            continue
        cfg_path = sub / "config.json"
        info = {"name": sub.name, "path": str(sub)}
        if cfg_path.is_file():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                info["role"] = cfg.get("role", "")
                info["model"] = cfg.get("default_model", "?")
                info["provider"] = cfg.get("default_provider", "claude")
                info["scope"] = cfg.get("scope", "hub")
                info["soul_inheritance"] = cfg.get("soul_inheritance", [])
                info["allowed_tools_count"] = len(cfg.get("allowed_tools", []))
            except Exception as e:
                info["_error"] = str(e)
        # count sessions
        sessions_dir = sub / "sessions"
        info["sessions"] = 0
        if sessions_dir.is_dir():
            info["sessions"] = sum(1 for _ in sessions_dir.rglob("*.md") if _.name != "index.md")
        out.append(info)
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--hub", required=True, help="hub root path")
    p.add_argument("--json", action="store_true", help="output JSON instead of table")
    args = p.parse_args()

    hub = Path(args.hub).expanduser().resolve()
    if not (hub / "config" / "projects.json").is_file():
        sys.exit(f"ERROR: {hub} non è un hub anja")

    agents = list_agents(hub)

    if args.json:
        print(json.dumps({"agents": agents}, indent=2, ensure_ascii=False))
        return

    if not agents:
        print(f"(nessun agent in {hub}/agents/. Crea con `anja-agent-add`.)")
        return

    # Tabular
    cols = ("NAME", "MODEL", "PROVIDER", "SESSIONS", "ROLE")
    rows = []
    for a in agents:
        rows.append((
            a["name"],
            a.get("model", "?"),
            a.get("provider", "?"),
            str(a.get("sessions", 0)),
            (a.get("role", "") or "")[:60],
        ))
    widths = [max(len(c), max(len(r[i]) for r in rows)) for i, c in enumerate(cols)]
    print("  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(r[i].ljust(widths[i]) for i in range(len(cols))))


if __name__ == "__main__":
    main()
