#!/usr/bin/env python3
"""
agent_add.py — crea un nuovo agent specializzato dentro <hub>/agents/<name>/.

Layout creato:
    <hub>/agents/<name>/
    ├── AGENTS.md       (role + stato)
    ├── SOUL.md         (personality, eredita hub)
    ├── TOOLS.md        (auto-gen via tools_md.py)
    ├── CLAUDE.md       (composed da AGENTS+SOUL+TOOLS)
    ├── config.json     (model, provider, allowed_tools, scope, ...)
    └── sessions/
        └── index.md

Riusa:
    - SOUL baseline `anja/templates/soul-baselines/agent.md` (con AGENT_ROLE/DOMAIN placeholder)
    - anja/scripts/compose_claude_md.py per CLAUDE.md auto-composto
    - anja/scripts/tools_md.py per TOOLS.md (se chiamato manualmente)

Usage:
    python3 agent_add.py --hub <hub-root> --name research --role "Research analyst"
    python3 agent_add.py --hub <hub> --name writer --role "Technical writer" --model opus
    python3 agent_add.py --hub <hub> --name coordinator --role "..." --domain "cross-workspace orchestration"
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path


VALID_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
DEFAULT_MODEL = "sonnet"


def get_hub_template_dir() -> Path:
    """Templates agent-skeleton in anja-hub/templates/."""
    here = Path(__file__).resolve()
    return here.parent.parent / "templates" / "agent-skeleton"


def get_anja_plugin_root() -> Path:
    """Plugin anjadev installato (scripts compose/tools_md, baselines).
    Override via ANJADEV_DIR per puntare a un checkout di sviluppo."""
    import os
    env = os.environ.get("ANJADEV_DIR")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "plugins" / "marketplaces" / "anjadev"


def get_agent_baseline() -> str:
    """Read baseline SOUL personality per type=agent."""
    baseline_path = get_anja_plugin_root() / "templates" / "soul-baselines" / "agent.md"
    if baseline_path.is_file():
        return baseline_path.read_text(encoding="utf-8").strip()
    return "(baseline mancante)"


def detect_user_name() -> str:
    import os, getpass
    return os.environ.get("USER") or getpass.getuser() or "user"


def write_from_template(template_dir: Path, target_dir: Path, replacements: dict) -> None:
    """Copia ricorsiva template → target con placeholder substitution."""
    for src in template_dir.rglob("*"):
        rel = src.relative_to(template_dir)
        dst = target_dir / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            continue
        text = src.read_text(encoding="utf-8")
        for k, v in replacements.items():
            text = text.replace(f"{{{k}}}", v)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding="utf-8")


def patch_config(agent_dir: Path, replacements: dict, model: str, provider: str = "claude", effort: str = "off") -> None:
    """Carica config.json appena scritto e patcha valori non-string (model, provider, effort)."""
    cfg_path = agent_dir / "config.json"
    if not cfg_path.is_file():
        return
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["default_model"] = model
    cfg["default_provider"] = provider
    if effort and effort != "off":
        cfg["default_effort"] = effort
    elif "default_effort" in cfg:
        del cfg["default_effort"]
    if cfg.get("providers"):
        cfg["providers"][0]["model"] = model
        cfg["providers"][0]["provider"] = provider
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def compose_agent_claude(agent_dir: Path) -> None:
    """Best-effort: rigenera CLAUDE.md composed dall'agent."""
    script = get_anja_plugin_root() / "scripts" / "compose_claude_md.py"
    if not script.is_file():
        return
    try:
        subprocess.run(
            [sys.executable, str(script), "--target", str(agent_dir), "--quiet"],
            check=False, capture_output=True, timeout=8,
        )
    except Exception:
        pass


def regen_agent_tools(agent_dir: Path) -> None:
    """Best-effort: rigenera TOOLS.md (anche se per agent eredita hub, ne ha uno proprio)."""
    script = get_anja_plugin_root() / "scripts" / "tools_md.py"
    if not script.is_file():
        return
    try:
        # tools_md non ha mode 'agent' — passiamo --target che lui interpreta come "non-project root"
        # in M-PA 1 ci basta che TOOLS.md esista; in M-PA seguenti aggiungiamo agent-aware mode.
        subprocess.run(
            [sys.executable, str(script), "--target", str(agent_dir), "--dry-run"],
            check=False, capture_output=True, timeout=5,
        )
    except Exception:
        pass


def main():
    p = argparse.ArgumentParser(description="Crea un nuovo agent anja-hub.")
    p.add_argument("--hub", required=True, help="hub root path")
    p.add_argument("--name", required=True, help="agent name (kebab-case)")
    p.add_argument("--role", required=True, help="role description (1-2 frasi)")
    p.add_argument("--domain", default="", help="domain area (es. 'academic research', 'data engineering')")
    p.add_argument("--model", default=DEFAULT_MODEL, help="default model (free-text per non-claude provider)")
    p.add_argument("--provider", default="claude", help="default provider (claude|openai|xai|openrouter|...)")
    p.add_argument("--effort", default="off", help="thinking effort per claude (off|low|medium|high)")
    p.add_argument("--force", action="store_true", help="sovrascrivi se l'agent già esiste")
    p.add_argument("--project", default="", help="(Fase 13+) crea agent nello scope del project: <hub>/workspaces/<project>/.anjawiki/agents/<name>")
    args = p.parse_args()

    hub = Path(args.hub).expanduser().resolve()
    if not (hub / "config" / "projects.json").is_file():
        sys.exit(f"ERROR: {hub} non sembra un hub anja (manca config/projects.json)")

    if not VALID_NAME_RE.match(args.name):
        sys.exit("ERROR: name must be kebab-case (lowercase, digits, dash, underscore)")

    if args.project:
        if not VALID_NAME_RE.match(args.project):
            sys.exit("ERROR: project must be kebab-case")
        project_root = hub / "workspaces" / args.project
        if not project_root.is_dir():
            sys.exit(f"ERROR: project workspace '{args.project}' non trovato in {hub}/workspaces/")
        agents_root = project_root / ".anjawiki" / "agents"
    else:
        agents_root = hub / "agents"
    agents_root.mkdir(parents=True, exist_ok=True)
    agent_dir = agents_root / args.name

    if agent_dir.exists():
        if not args.force:
            sys.exit(f"ERROR: agent '{args.name}' già esiste in {agent_dir}. Usa --force per sovrascrivere.")
        else:
            shutil.rmtree(agent_dir)

    template_dir = get_hub_template_dir()
    if not template_dir.is_dir():
        sys.exit(f"ERROR: template {template_dir} not found")

    today = date.today().isoformat()
    domain = args.domain or args.role
    soul_baseline = get_agent_baseline().replace("{AGENT_ROLE}", args.role).replace("{AGENT_DOMAIN}", domain)

    replacements = {
        "DATE": today,
        "AGENT_NAME": args.name,
        "AGENT_ROLE": args.role,
        "AGENT_ROLE_DESCRIPTION": args.role,
        "AGENT_DOMAIN": domain,
        "AGENT_SOUL_BASELINE": soul_baseline,
        "AGENT_MODEL": args.model,
    }

    write_from_template(template_dir, agent_dir, replacements)
    patch_config(agent_dir, replacements, args.model, provider=args.provider, effort=args.effort)
    compose_agent_claude(agent_dir)

    print(f"[anja] agent '{args.name}' creato in {agent_dir.relative_to(hub)}/")
    print(f"  Role:  {args.role}")
    print(f"  Model: {args.model}")
    print(f"  Files: AGENTS.src.md + SOUL.md + TOOLS.md → AGENTS.md (composed) + CLAUDE.md (@AGENTS.md) + config.json + sessions/")
    print(f"\nProssimi step:")
    print(f"  - Rifinisci AGENTS.src.md / SOUL.md per dettagli specifici")
    print(f"  - Avvia chat-as-agent in Mission Control (M-PA 2, in arrivo)")


if __name__ == "__main__":
    main()
