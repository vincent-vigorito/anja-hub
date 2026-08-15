"""pp_integration.py — Printing Press CLI integration glue for anja (Fase P-CLI).

Connette il generatore PP (cli-printing-press) al sistema anja:
  - detect & install printing-press binary (idempotent)
  - generate new CLI from catalog/docs/HAR
  - install generated CLI into hub or workspace MCP + skills
  - list/uninstall/regenerate

Stdlib only + subprocess + Path.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


# ============================================================
# Paths
# ============================================================

def pp_binary() -> Optional[Path]:
    """Find printing-press binary on system. Returns Path if found, None otherwise."""
    # 1. $PATH
    found = shutil.which("printing-press")
    if found:
        return Path(found)
    # 2. ~/go/bin (Go default install location)
    go_bin = Path.home() / "go" / "bin" / "printing-press"
    if go_bin.is_file():
        return go_bin
    # 3. $GOPATH/bin
    gopath = os.environ.get("GOPATH")
    if gopath:
        cand = Path(gopath) / "bin" / "printing-press"
        if cand.is_file():
            return cand
    return None


def pp_library_root() -> Path:
    """Where PP stores published CLIs."""
    return Path.home() / "printing-press" / "library"


# ============================================================
# Install / detect
# ============================================================

def doctor() -> dict:
    """Diagnose PP install state. Returns {go_installed, pp_installed, pp_path, library_dirs}."""
    go = shutil.which("go")
    pp = pp_binary()
    library = pp_library_root()
    libs = []
    if library.is_dir():
        for child in library.iterdir():
            if child.is_dir():
                libs.append(child.name)
    pp_version = None
    if pp:
        try:
            r = subprocess.run([str(pp), "version"], capture_output=True, text=True, timeout=5)
            pp_version = r.stdout.strip() or r.stderr.strip()
        except Exception:
            pass
    return {
        "go_installed": bool(go),
        "go_path": go,
        "pp_installed": bool(pp),
        "pp_path": str(pp) if pp else None,
        "pp_version": pp_version,
        "library_root": str(library),
        "library_count": len(libs),
        "library_dirs": libs,
    }


def ensure_installed(via: str = "auto") -> dict:
    """Idempotent install di printing-press.

    via: 'auto' (brew→go), 'brew', 'go'. Returns {ok, action_taken, output, error}.
    """
    pp = pp_binary()
    if pp:
        return {"ok": True, "action_taken": "noop", "pp_path": str(pp)}

    log_lines: list[str] = []
    actions: list[str] = []

    # 1. Verify Go installed
    if not shutil.which("go"):
        if via in ("auto", "brew"):
            if shutil.which("brew"):
                log_lines.append("Installing Go via brew...")
                r = subprocess.run(["brew", "install", "go"], capture_output=True, text=True, timeout=600)
                log_lines.append(r.stdout[-500:])
                if r.returncode != 0:
                    return {"ok": False, "action_taken": actions, "output": "\n".join(log_lines),
                             "error": "brew install go failed: " + r.stderr[-300:]}
                actions.append("brew_install_go")
            else:
                return {"ok": False, "action_taken": actions, "output": "\n".join(log_lines),
                         "error": "Neither 'go' nor 'brew' found. Install Go manually from go.dev"}
        else:
            return {"ok": False, "error": "Go not installed, cannot proceed"}

    # 2. go install printing-press
    log_lines.append("Installing printing-press via go install...")
    r = subprocess.run(
        ["go", "install", "github.com/mvanhorn/cli-printing-press/v4/cmd/printing-press@latest"],
        capture_output=True, text=True, timeout=600,
    )
    log_lines.append(r.stdout[-300:] + r.stderr[-300:])
    if r.returncode != 0:
        return {"ok": False, "action_taken": actions, "output": "\n".join(log_lines),
                 "error": "go install printing-press failed: " + r.stderr[-300:]}
    actions.append("go_install_pp")

    pp = pp_binary()
    if not pp:
        return {"ok": False, "action_taken": actions, "output": "\n".join(log_lines),
                 "error": "PP installed but binary not found in expected paths"}
    return {"ok": True, "action_taken": actions, "pp_path": str(pp), "output": "\n".join(log_lines)}


# ============================================================
# Generate new CLI
# ============================================================

def generate_cli(name: str, source: str, source_type: str = "auto",
                  timeout_sec: int = 600) -> dict:
    """Genera una nuova PP CLI.

    Args:
        name: nome del servizio (es. "stripe", "notion")
        source: URL docs OR path OpenAPI file OR path HAR file OR "catalog"
        source_type: 'docs', 'spec', 'har', 'catalog', 'auto'
        timeout_sec: max wait per pipeline

    Returns dict with: ok, library_path, output, error.
    """
    pp = pp_binary()
    if not pp:
        return {"ok": False, "error": "printing-press not installed. Run ensure_installed() first."}

    # Auto-detect source type
    if source_type == "auto":
        if source == "catalog" or not source:
            source_type = "catalog"
        elif source.startswith("http://") or source.startswith("https://"):
            source_type = "docs"
        elif source.endswith(".har"):
            source_type = "har"
        elif source.endswith((".yaml", ".yml", ".json")):
            source_type = "spec"
        else:
            source_type = "catalog"

    # Build command
    if source_type == "catalog":
        # printing-press print <name>
        cmd = [str(pp), "print", name, "--force"]
    elif source_type == "docs":
        cmd = [str(pp), "generate", "--docs", source, "--name", name, "--force"]
    elif source_type == "spec":
        cmd = [str(pp), "generate", "--spec", source, "--name", name, "--force"]
    elif source_type == "har":
        # PP supports HAR via separate flow — usa browser-sniff
        cmd = [str(pp), "browser-sniff", "--har", source, "--name", name]
    else:
        return {"ok": False, "error": f"unsupported source_type: {source_type}"}

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        output = (r.stdout + "\n" + r.stderr)[-3000:]
        if r.returncode != 0:
            return {"ok": False, "output": output, "error": f"PP generate exit {r.returncode}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"PP generate timeout after {timeout_sec}s"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    library_path = pp_library_root() / name
    if not library_path.is_dir():
        return {"ok": False, "output": output,
                 "error": f"PP completed but library dir missing: {library_path}"}

    return {"ok": True, "library_path": str(library_path), "output": output,
            "source_type": source_type, "name": name}


# ============================================================
# Install into anja (hub/workspace MCP + skill catalog + wiki source page)
# ============================================================

def _read_manifest(library_path: Path) -> dict:
    m = library_path / "manifest.json"
    if not m.is_file():
        return {}
    try:
        return json.loads(m.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _mcp_binary_path(library_path: Path, cli_name: str) -> Optional[Path]:
    """Path of <name>-pp-mcp binary in library."""
    candidates = [
        library_path / "build" / "stage" / "bin" / f"{cli_name}-pp-mcp",
        library_path / "build" / f"{cli_name}-pp-mcp",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def install_pp_cli(hub_path: Path, name: str, scope: str = "hub",
                    workspace: Optional[str] = None,
                    extra_env: Optional[dict] = None) -> dict:
    """Idempotente: aggiunge PP CLI a anja (skill + MCP + wiki).

    scope: 'hub' or 'workspace'. Se workspace, deve essere passato workspace=<slug>.
    extra_env: variabili env aggiuntive per il MCP server (es. API keys).
    """
    library_path = pp_library_root() / name
    if not library_path.is_dir():
        return {"ok": False, "error": f"library dir not found: {library_path}"}

    mcp_bin = _mcp_binary_path(library_path, name)
    if not mcp_bin:
        return {"ok": False, "error": f"{name}-pp-mcp binary not found in {library_path}/build/"}

    manifest = _read_manifest(library_path)
    actions: list[str] = []

    # 1. Symlink SKILL.md → <hub>/skills/pp-<name>/SKILL.md
    skill_src = library_path / "SKILL.md"
    if skill_src.is_file():
        skill_dest_dir = hub_path / "skills" / f"pp-{name}"
        skill_dest_dir.mkdir(parents=True, exist_ok=True)
        skill_dest = skill_dest_dir / "SKILL.md"
        if skill_dest.is_symlink() or skill_dest.exists():
            skill_dest.unlink()
        skill_dest.symlink_to(skill_src)
        actions.append(f"skill symlinked: {skill_dest}")

    # 2. Register MCP server in .mcp.json (scoped)
    if scope == "hub":
        mcp_json_path = hub_path / ".mcp.json"
    elif scope == "workspace" and workspace:
        mcp_json_path = hub_path / "workspaces" / workspace / ".anjawiki" / ".mcp.json"
    else:
        return {"ok": False, "error": f"invalid scope: {scope} (workspace={workspace})"}

    mcp_json_path.parent.mkdir(parents=True, exist_ok=True)
    data = {"mcpServers": {}}
    if mcp_json_path.is_file():
        try:
            data = json.loads(mcp_json_path.read_text(encoding="utf-8"))
            data.setdefault("mcpServers", {})
        except Exception:
            pass

    server_key = f"pp_{name}"
    env_block = {}
    if extra_env:
        env_block.update(extra_env)
    data["mcpServers"][server_key] = {
        "command": str(mcp_bin),
        "args": [],
        "env": env_block,
    }
    mcp_json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    actions.append(f"mcp registered: {server_key} → {mcp_json_path.name}")

    # 3. Wiki source page (auto)
    wiki_dir = hub_path / "wiki" / "sources"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    page = wiki_dir / f"pp-{name}.md"
    if not page.is_file():
        desc = manifest.get("description") or f"Printing Press CLI for {name}"
        body = (
            f"---\n"
            f"title: PP CLI — {name}\n"
            f"type: source\n"
            f"created: {_today()}\n"
            f"updated: {_today()}\n"
            f"tags: [pp-cli, mcp, generated, {name}]\n"
            f"---\n\n"
            f"# {name} (Printing Press CLI)\n\n"
            f"{desc}\n\n"
            f"## Install info\n\n"
            f"- Library path: `{library_path}`\n"
            f"- MCP binary: `{mcp_bin}`\n"
            f"- Scope: `{scope}{':' + workspace if workspace else ''}`\n"
            f"- Skill catalog: `pp-{name}`\n\n"
            f"## Tools exposed\n\nSee `SKILL.md` o chiama `skill.load(\"pp-{name}\")`.\n"
        )
        page.write_text(body, encoding="utf-8")
        actions.append(f"wiki source page: {page.name}")

    return {"ok": True, "actions": actions, "server_key": server_key,
            "mcp_binary": str(mcp_bin), "mcp_json": str(mcp_json_path)}


def list_installed_pp(hub_path: Path) -> dict:
    """Lista PP CLIs in library + dove sono installate (hub/workspace)."""
    library = pp_library_root()
    out = []
    if library.is_dir():
        for child in sorted(library.iterdir()):
            if not child.is_dir():
                continue
            name = child.name
            manifest = _read_manifest(child)
            mcp_bin = _mcp_binary_path(child, name)
            # Find install locations
            installed_in: list[str] = []
            hub_mcp = hub_path / ".mcp.json"
            if hub_mcp.is_file():
                try:
                    d = json.loads(hub_mcp.read_text(encoding="utf-8"))
                    if f"pp_{name}" in (d.get("mcpServers") or {}):
                        installed_in.append("hub")
                except Exception:
                    pass
            ws_root = hub_path / "workspaces"
            if ws_root.is_dir():
                for ws in ws_root.iterdir():
                    ws_mcp = ws / ".anjawiki" / ".mcp.json"
                    if not ws_mcp.is_file():
                        continue
                    try:
                        d = json.loads(ws_mcp.read_text(encoding="utf-8"))
                        if f"pp_{name}" in (d.get("mcpServers") or {}):
                            installed_in.append(f"workspace:{ws.name}")
                    except Exception:
                        pass
            out.append({
                "name": name,
                "description": manifest.get("description", ""),
                "library_path": str(child),
                "mcp_binary": str(mcp_bin) if mcp_bin else None,
                "built": bool(mcp_bin),
                "installed_in": installed_in,
            })
    return {"items": out, "library_root": str(library), "count": len(out)}


def uninstall_pp_cli(hub_path: Path, name: str, scope: str = "hub",
                      workspace: Optional[str] = None,
                      delete_library: bool = False) -> dict:
    """Rimuove PP CLI da hub/workspace + opzionalmente cancella la library."""
    actions: list[str] = []

    # Determine target .mcp.json
    if scope == "hub":
        mcp_json_path = hub_path / ".mcp.json"
    elif scope == "workspace" and workspace:
        mcp_json_path = hub_path / "workspaces" / workspace / ".anjawiki" / ".mcp.json"
    else:
        return {"ok": False, "error": "scope/workspace error"}

    if mcp_json_path.is_file():
        try:
            data = json.loads(mcp_json_path.read_text(encoding="utf-8"))
            servers = data.get("mcpServers", {})
            key = f"pp_{name}"
            if key in servers:
                del servers[key]
                mcp_json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                actions.append(f"mcp removed: {key}")
        except Exception as e:
            return {"ok": False, "error": f"mcp.json error: {e}"}

    # Remove skill symlink (only if no other workspace uses it)
    # For safety: only if scope=hub
    if scope == "hub":
        skill_dir = hub_path / "skills" / f"pp-{name}"
        if skill_dir.is_dir():
            shutil.rmtree(skill_dir)
            actions.append(f"skill removed: pp-{name}")
        page = hub_path / "wiki" / "sources" / f"pp-{name}.md"
        if page.is_file():
            page.unlink()
            actions.append(f"wiki page removed: pp-{name}.md")

    if delete_library:
        lib = pp_library_root() / name
        if lib.is_dir():
            shutil.rmtree(lib)
            actions.append(f"library deleted: {lib}")

    return {"ok": True, "actions": actions}


# ============================================================
# Helpers
# ============================================================

def _today() -> str:
    from datetime import date
    return date.today().isoformat()


# ============================================================
# CLI (debug)
# ============================================================

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="PP integration helper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor")
    sub.add_parser("ensure")
    sp_l = sub.add_parser("list")
    sp_l.add_argument("--hub", default=os.environ.get("ANJA_HUB"), required="ANJA_HUB" not in os.environ)
    sp_g = sub.add_parser("generate")
    sp_g.add_argument("name")
    sp_g.add_argument("--source", default="catalog")
    sp_g.add_argument("--type", default="auto", dest="source_type")
    sp_i = sub.add_parser("install")
    sp_i.add_argument("name")
    sp_i.add_argument("--hub", default=os.environ.get("ANJA_HUB"), required="ANJA_HUB" not in os.environ)
    sp_i.add_argument("--scope", default="hub")
    sp_i.add_argument("--workspace", default=None)
    args = ap.parse_args()

    if args.cmd == "doctor":
        print(json.dumps(doctor(), indent=2))
    elif args.cmd == "ensure":
        print(json.dumps(ensure_installed(), indent=2))
    elif args.cmd == "list":
        print(json.dumps(list_installed_pp(Path(args.hub)), indent=2))
    elif args.cmd == "generate":
        print(json.dumps(generate_cli(args.name, args.source, args.source_type), indent=2))
    elif args.cmd == "install":
        print(json.dumps(install_pp_cli(Path(args.hub), args.name, args.scope, args.workspace), indent=2))
