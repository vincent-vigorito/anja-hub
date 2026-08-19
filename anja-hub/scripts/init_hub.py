#!/usr/bin/env python3
"""
init_hub.py — inizializza una directory come hub anja.

Crea:
  - struttura cartelle (cross/, sessions/, projects/, config/)
  - CLAUDE.md (schema globale del hub)
  - cross/index.md, cross/log.md
  - sessions/index.md
  - config/projects.json (registry vuoto)
"""

import argparse
import json
import shutil
import sys
from datetime import date
from pathlib import Path

PLACEHOLDER_FILES = (
    "cross/index.md",
    "cross/log.md",
    "sessions/index.md",
)

# Triade files use single-brace placeholders ({KEY}) per consistency con templates project triade
TRIADE_FILES = ("AGENTS.src.md", "SOUL.md", "TOOLS.md")


def _detect_user_name() -> str:
    import os, getpass
    return os.environ.get("USER") or os.environ.get("USERNAME") or getpass.getuser() or "user"


def _read_hub_soul_baseline() -> str:
    """Read hub baseline from anja/templates/soul-baselines/hub.md."""
    # plugin anja-hub è sibling di anja
    here = Path(__file__).resolve()
    anja_root = here.parent.parent.parent / "anja"
    baseline_path = anja_root / "templates" / "soul-baselines" / "hub.md"
    if baseline_path.is_file():
        return baseline_path.read_text(encoding="utf-8").strip()
    return "(baseline hub mancante — installa anche il plugin `anja`)"


def get_template_dir() -> Path:
    script = Path(__file__).resolve()
    plugin_root = script.parent.parent
    template = plugin_root / "templates" / "hub-skeleton"
    if not template.is_dir():
        sys.exit(f"ERROR: template directory not found: {template}")
    return template


def copy_hub_template(src: Path, dst: Path) -> None:
    if dst.exists() and any(dst.iterdir()):
        sys.exit(f"ERROR: target exists and is not empty: {dst}")
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)


def substitute_placeholders(target: Path, replacements: dict) -> None:
    """Replace {{KEY}} (double-brace) placeholders in PLACEHOLDER_FILES."""
    for rel in PLACEHOLDER_FILES:
        f = target / rel
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8")
        for key, val in replacements.items():
            text = text.replace(f"{{{{{key}}}}}", val)
        f.write_text(text, encoding="utf-8")


def substitute_triade_placeholders(target: Path, replacements: dict) -> None:
    """Replace {KEY} (single-brace) placeholders in TRIADE_FILES."""
    for fname in TRIADE_FILES:
        f = target / fname
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8")
        for key, val in replacements.items():
            text = text.replace(f"{{{key}}}", val)
        f.write_text(text, encoding="utf-8")


def init_registry(target: Path) -> None:
    registry_path = target / "config" / "projects.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps({"projects": []}, indent=2) + "\n",
        encoding="utf-8",
    )


def init_runtime_dirs(target: Path) -> None:
    for sub in ("projects",):
        (target / sub).mkdir(parents=True, exist_ok=True)


def ensure_hub_wiki(hub_root: Path) -> bool:
    """F-HubKnowledge — garantisce il knowledge layer proprio dell'hub in
    `<hub>/.anjawiki/`. Idempotente: crea solo ciò che manca, non sovrascrive.

    Questo è il wiki di DOMINIO dell'hub (le competenze dell'utente: es. Incus,
    Linux, finanza) — distinto dai wiki dei workspace/progetti (conoscenza di
    dominio dei membri) e da `cross/` (sintesi cross-progetto). Usato sia da
    init_hub (nuovo hub) sia da server boot (migration hub esistenti).

    Returns True se ha creato qualcosa (migration avvenuta), False se già presente.
    """
    aw = hub_root / ".anjawiki"
    wiki = aw / "wiki"
    created_now = not (wiki / "index.md").is_file()

    for sub in ("wiki", "wiki/entities", "wiki/concepts", "wiki/sources",
                "wiki/analysis", "wiki/sessions", "raw", "skills"):
        (aw / sub).mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    name = hub_root.name

    meta = aw / "meta.yaml"
    if not meta.is_file():
        meta.write_text(
            f"name: {name}\ntype: personal\nscope: hub\ncreated: \"{today}\"\n"
            f"description: knowledge di dominio dell'hub (competenze dell'utente)\n",
            encoding="utf-8")

    index = wiki / "index.md"
    if not index.is_file():
        index.write_text(
            f"---\ntitle: Index\ntype: index\ncreated: \"{today}\"\n---\n\n"
            "# Indice del wiki dell'hub\n\n"
            "> Knowledge di dominio dell'hub — le competenze con cui l'agent principale "
            "ragiona, idea e analizza i progetti. Catalogo: leggi qui per primo.\n\n"
            "## Entities\n\n## Concepts\n\n## Sources\n\n## Analysis\n",
            encoding="utf-8")

    log = wiki / "log.md"
    if not log.is_file():
        log.write_text(
            f"---\ntitle: Log\ntype: log\ncreated: \"{today}\"\n---\n\n"
            "# Log cronologico\n\nAppend-only. `## [YYYY-MM-DD] tipo | descrizione`.\n",
            encoding="utf-8")

    overview = wiki / "overview.md"
    if not overview.is_file():
        overview.write_text(
            f"---\ntitle: Overview\ntype: overview\ncreated: \"{today}\"\nupdated: \"{today}\"\n---\n\n"
            "# Overview\n\n> Le aree di competenza dell'hub. Aggiorna quando una nuova "
            "fonte cambia la tesi corrente su un dominio.\n",
            encoding="utf-8")

    return created_now


def main() -> None:
    p = argparse.ArgumentParser(description="Initialize a anja hub directory.")
    p.add_argument(
        "--target",
        required=True,
        help="path to the hub directory (will be created if missing, must be empty)",
    )
    args = p.parse_args()

    target = Path(args.target).resolve()
    template = get_template_dir()
    created = date.today().isoformat()
    hub_name = target.name

    replacements = {
        "DATE": created,
        "CREATED": created,
        "HUB_NAME": hub_name,
        "SOUL_BASELINE": _read_hub_soul_baseline(),
        "USER_NAME": _detect_user_name(),
        "USER_LANG": "it",
        "USER_TONE": "diretto e conciso",
        "USER_EMAIL": "<da popolare>",
        "USER_TZ": "<da popolare, es: Europe/Rome>",
    }

    copy_hub_template(template, target)
    substitute_placeholders(target, replacements)
    substitute_triade_placeholders(target, replacements)
    init_registry(target)
    init_runtime_dirs(target)
    ensure_hub_wiki(target)
    _write_hub_config(target)
    _register_anja_memory_mcp(target)
    _register_hub_runtime_mcp(target)
    _regenerate_tools_md(target)
    _compose_claude_md(target)

    print(f"[anja-hub] inizializzato in {target}")
    print(f"  Created:  {created}")
    print(f"  Registry: {target / 'config' / 'projects.json'}")
    print(f"  Source:   AGENTS.src.md + SOUL.md + TOOLS.md → AGENTS.md (composed) + CLAUDE.md (@AGENTS.md)")
    print()
    print("Prossimi step:")
    print("  /anja-register --kind local --path <project-path>")
    print("  /anja-list                  per vedere il registry")
    print("  /anja-sync --all            per (ri)costruire i symlink")


def _write_hub_config(hub_root: Path) -> None:
    """Crea <hub>/config.json (top-level) con memory section default. Non-destructive."""
    cfg_path = hub_root / "config.json"
    existing = {}
    if cfg_path.is_file():
        try:
            existing = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
        if "memory" in existing:
            return
    existing.setdefault("memory", {
        "hot_budget_tokens": 1500,
        "warm_budget_tokens": 3000,
        "log_entries_count": 3,
        "session_summaries_count": 5,
        "wiki_match_max_pages": 3,
        "cc_memory_mirror": True,
    })
    # Default provider chain (per Fase 7 multi-LLM, già preparata)
    existing.setdefault("default_provider", "claude")
    existing.setdefault("default_model", "sonnet")
    cfg_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _anjadev_dir() -> Path:
    """Root del plugin anjadev installato (override via ANJADEV_DIR per dev locale)."""
    import os
    env = os.environ.get("ANJADEV_DIR")
    return Path(env) if env else Path.home() / ".claude" / "plugins" / "marketplaces" / "anjadev"


# F-AnjadevCoreSplit: anja_memory (anjadev, CLI puro) espone SOLO i gruppi core;
# il piano di lavoro degli agent (agents/tasks/workspace/kanban/goals/pp) sta nel
# server anja_hub_runtime di questo repo. Stessi nomi tool di prima.
HUB_MEMORY_GROUPS = "memory,sessions,soul,user,skills,wiki,roadmap,code,graph"
HUB_RUNTIME_GROUPS = "agents,tasks,workspace,kanban,goals,pp"
HUB_RUNTIME_SCRIPT = Path(__file__).resolve().parent / "mcp_hub_runtime.py"


def _load_mcp_json(mcp_path: Path) -> dict:
    data = {"mcpServers": {}}
    if mcp_path.is_file():
        try:
            data = json.loads(mcp_path.read_text(encoding="utf-8"))
            if "mcpServers" not in data:
                data["mcpServers"] = {}
        except Exception:
            pass
    return data


def _register_anja_memory_mcp(hub_root: Path) -> None:
    """Registra anja_memory (plugin anjadev) nel <hub>/.mcp.json, gruppi core espliciti."""
    mcp_server_path = _anjadev_dir() / "scripts" / "mcp_memory_server.py"
    if not mcp_server_path.is_file():
        print(f"[init-hub] WARNING: mcp_memory_server.py non trovato: {mcp_server_path}")
        return
    mcp_path = hub_root / ".mcp.json"
    data = _load_mcp_json(mcp_path)
    if "anja_memory" in data["mcpServers"]:
        return
    data["mcpServers"]["anja_memory"] = {
        "command": sys.executable,
        "args": [str(mcp_server_path)],
        "env": {
            "ANJA_SCOPE": "hub",
            "ANJA_ROOT": str(hub_root),
            "ANJA_HUB": str(hub_root),
            "ANJA_TOOL_GROUPS": HUB_MEMORY_GROUPS,
        },
    }
    mcp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _register_hub_runtime_mcp(hub_root: Path) -> None:
    """Registra anja_hub_runtime (piano di lavoro degli agent) nel <hub>/.mcp.json."""
    mcp_path = hub_root / ".mcp.json"
    data = _load_mcp_json(mcp_path)
    if "anja_hub_runtime" in data["mcpServers"]:
        return
    data["mcpServers"]["anja_hub_runtime"] = {
        "command": sys.executable,
        "args": [str(HUB_RUNTIME_SCRIPT)],
        "env": {
            "ANJA_SCOPE": "hub",
            "ANJA_ROOT": str(hub_root),
            "ANJA_HUB": str(hub_root),
            "ANJA_TOOL_GROUPS": HUB_RUNTIME_GROUPS,
        },
    }
    mcp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _regenerate_tools_md(hub_root: Path) -> None:
    """Best-effort: rigenera TOOLS.md chiamando tools_md.py."""
    import subprocess
    script = _anjadev_dir() / "scripts" / "tools_md.py"
    if not script.is_file():
        return
    try:
        subprocess.run(
            [sys.executable, str(script), "--hub", str(hub_root)],
            check=False, capture_output=True, timeout=10,
        )
    except Exception:
        pass


def _compose_claude_md(hub_root: Path) -> None:
    """Best-effort: rigenera CLAUDE.md composed da AGENTS+SOUL+TOOLS."""
    import subprocess
    script = _anjadev_dir() / "scripts" / "compose_claude_md.py"
    if not script.is_file():
        return
    try:
        subprocess.run(
            [sys.executable, str(script), "--target", str(hub_root)],
            check=False, capture_output=True, timeout=10,
        )
    except Exception:
        pass


def _make_claude_md_symlink(hub_root: Path) -> None:
    # LEGACY no-op: CLAUDE.md è ora generato da compose_claude_md come wrapper `@AGENTS.md`
    # (vedi compose). Mantenuto per back-compat con i call-site esistenti.
    return


if __name__ == "__main__":
    main()
