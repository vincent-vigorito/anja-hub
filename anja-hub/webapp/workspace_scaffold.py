"""workspace_scaffold.py — Fase 22 — Scaffold di workspace internal.

Crea la struttura `<hub>/workspaces/<name>/.anjawiki/` con:
  - meta.yaml (kind: internal, responsabile: <slug>)
  - CLAUDE.md template
  - wiki/{index,log,overview}.md
  - files/, data/, scripts/ (empty + README)
  - agents/<responsabile_slug>/ (config.json + AGENTS/SOUL/TOOLS.md)
  - users/ (overlay vuoto)
  - sessions/

Aggiunge anche entry nel registry `<hub>/config/projects.json`.

Stdlib only.
"""

from __future__ import annotations

import json
import re
import secrets
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


WORKSPACE_TYPES = ("office", "lab", "studio", "inbox", "custom", "marketing")


def _slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"^-+|-+$", "", s)
    return s[:40] or "workspace"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _uuid7_canonical() -> str:
    """RFC 9562 UUIDv7 canonical fallback per Python < 3.14."""
    ts_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = secrets.token_bytes(10)
    b = bytearray(16)
    b[0:6] = ts_ms.to_bytes(6, "big")
    b[6] = 0x70 | (rand[0] & 0x0F)
    b[7] = rand[1]
    b[8] = 0x80 | (rand[2] & 0x3F)
    b[9] = rand[3]
    b[10:16] = rand[4:10]
    h = b.hex()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _gen_anja_id() -> str:
    """`anja_<uuid7-canonical>` — RFC 9562 time-sortable identifier."""
    try:
        return f"anja_{uuid.uuid7()}"  # Python 3.14+
    except AttributeError:
        return f"anja_{_uuid7_canonical()}"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _yaml_simple(data: dict) -> str:
    lines = []
    for k, v in data.items():
        if isinstance(v, str) and (":" in v or "#" in v):
            v = f'"{v}"'
        if isinstance(v, list):
            v = "[" + ", ".join(str(x) for x in v) + "]"
        lines.append(f"{k}: {v}")
    return "\n".join(lines) + "\n"


# =================================================================
# Templates
# =================================================================

def _claude_md_template(name: str, responsabile_name: str, role_description: str,
                        ws_type: str) -> str:
    return f"""# Workspace `{name}`

Tipo: **{ws_type}**

## Identità del workspace

Sei in workspace `{name}`. Il responsabile di questo workspace è **{responsabile_name}**.

## Contesto

{role_description}

## Struttura

- `files/` — output strutturato (report, dashboard, documenti generati)
- `data/` — dataset di input
- `scripts/` — script utility per il workflow
- `wiki/` — knowledge tematico ({name}-specifico)
- `agents/` — responsabile + sub-agents
- `users/` — overlay identity utente in questo contesto
- `sessions/` — log conversazioni
- `log.md` — append-only audit

## Workflow tipici

Specifica qui i workflow ricorrenti per questo workspace.

## Convenzioni

- File generati vanno in `files/` con timestamp nel nome (es. `report-YYYY-MM-DD.docx`)
- Script utility in `scripts/` (Python/shell)
- Wiki page seguono frontmatter anja standard
"""


def _responsabile_soul_template(name: str, role: str, workspace_name: str) -> str:
    return f"""---
slug: {name}
name: {name}
type: agent
created: {_today()}
updated: {_today()}
workspace: {workspace_name}
workspace_lead: true
---

# {name}

## Role

{role}

## Personalità

Pragmatico, focus sul lavoro del workspace `{workspace_name}`. Non divagare in altri domini.

## Limiti operativi

- Lavori principalmente in workspace `{workspace_name}`
- Hub agent `Anja` resta accessibile per coordinamento cross-workspace
- Per task fuori dominio, suggerisci di delegare o switchare workspace

## Stile di collaborazione

- Risposte concise, focalizzate sul deliverable
- Output strutturato (markdown ben formattato, tabelle quando opportuno)
- Confermi prima di azioni distruttive (delete, overwrite, send)
"""


def _responsabile_agents_template(name: str, role: str) -> str:
    return f"""---
slug: {name}
type: agent
created: {_today()}
---

# {name} — istruzioni operative

## Domini di expertise

{role}

## Tool a disposizione

- `workspace.read_file(scope, path)` — leggi file nel workspace
- `workspace.write_file(scope, path, content)` — scrivi in files/scripts/data
- `workspace.list_files(scope, path?)` — lista
- Standard tools: Read, Edit, Write, Bash (sandboxed al workspace)

## Pattern operativi

1. Quando l'utente chiede un output → scrivi in `files/`
2. Quando l'utente chiede uno script → scrivi in `scripts/`
3. Quando salvi dataset → `data/`
4. Sempre aggiorna `log.md` con un'entry

## Memoria

Hai accesso al wiki del workspace (`wiki/`) per knowledge ricorrente.
Il tuo dialectic vive in `users/<slug>-dialectic.md` (workspace-scoped).
"""


def _responsabile_tools_template(name: str) -> str:
    return f"""---
slug: {name}
type: tools-doc
---

# {name} — Tools

## MCP servers usati

- `anja_memory` (dal `.mcp.json` dell'hub/workspace)
- `anja_hub_runtime` (workspace.* file ops, kanban, goals — keyword-routed)
- altri server keyword-routed

## Tool patterns

(documenta qui workflow ricorrenti se emerge friction sui tool)
"""


def _wiki_index_template(workspace_name: str) -> str:
    return f"""---
title: Index
type: index
created: {_today()}
updated: {_today()}
---

# {workspace_name} — Wiki Index

## Entities

_(pagine entity verranno create con l'uso)_

## Concepts

_(pagine concept verranno create con l'uso)_

## Sources

_(fonti ingest)_

## Analysis

_(analisi tematiche)_
"""


def _wiki_log_template(workspace_name: str) -> str:
    return f"""---
title: Log
type: log
created: {_today()}
---

# Log workspace `{workspace_name}`

## [{_today()}] init | workspace creato (Fase 22 scaffold)
"""


def _wiki_overview_template(workspace_name: str, role: str) -> str:
    return f"""---
title: Overview
type: overview
created: {_today()}
updated: {_today()}
---

# {workspace_name} — Overview

## Scope

{role}

## Stato corrente

Workspace appena creato, in attesa di primo lavoro.
"""


# =================================================================
# Main scaffold
# =================================================================

def scaffold_workspace(
    hub_path: Path,
    name: str,
    responsabile_name: str,
    role_description: str,
    ws_type: str = "office",
    responsabile_provider: str = "claude",
    responsabile_model: str = "sonnet",
    responsabile_effort: Optional[str] = None,
) -> dict:
    """Crea uno workspace internal completo.

    Returns: {ok, path, slug, responsabile_slug, registry_entry, errors[]}
    """
    if ws_type not in WORKSPACE_TYPES:
        return {"ok": False, "error": f"invalid ws_type: {ws_type} (allowed: {WORKSPACE_TYPES})"}

    slug = _slugify(name)
    resp_slug = _slugify(responsabile_name)

    ws_root = hub_path / "workspaces" / slug
    anjawiki = ws_root / ".anjawiki"

    if ws_root.exists():
        return {"ok": False, "error": f"workspace path already exists: {ws_root}"}

    errors = []

    try:
        # Directory tree
        anjawiki.mkdir(parents=True, exist_ok=False)
        # Fase 22.10: include routines/
        for sub in ("wiki", "files", "data", "scripts", "agents", "users", "sessions", "routines"):
            (anjawiki / sub).mkdir(exist_ok=True)

        # README placeholders
        for sub in ("files", "data", "scripts"):
            _write(anjawiki / sub / "README.md",
                   f"# {sub}/\n\nSpazio di lavoro `{sub}` del workspace `{slug}`.\n"
                   f"Gestito dal responsabile `{resp_slug}`.\n")

        # Fase 22.10: README routines/
        _write(anjawiki / "routines" / "README.md",
               f"# routines/\n\nRoutine schedulate del workspace `{slug}`.\n\n"
               f"Routine qui dentro hanno scope auto-inferito `project:{slug}` (puoi sovrascrivere col campo `scope`).\n"
               f"Sono **portabili**: backup/migrazione del workspace include le sue routine.\n\n"
               f"Esempio: `monthly-pnl.yaml` ⇒ ultimo giorno del mese genera P/L report in `files/`.\n")

        # meta.yaml
        _write(anjawiki / "meta.yaml", _yaml_simple({
            "id": _gen_anja_id(),
            "name": slug,
            "kind": "internal",
            "type": ws_type,
            "responsabile": resp_slug,
            "created": _today(),
            "schema_version": "v22",
        }))

        # CLAUDE.md
        _write(anjawiki / "CLAUDE.md", _claude_md_template(
            slug, responsabile_name, role_description, ws_type
        ))

        # Wiki seed
        _write(anjawiki / "wiki" / "index.md", _wiki_index_template(slug))
        _write(anjawiki / "wiki" / "log.md", _wiki_log_template(slug))
        _write(anjawiki / "wiki" / "overview.md", _wiki_overview_template(slug, role_description))

        # Workspace-level log (audit)
        _write(anjawiki / "log.md", f"# {slug} audit log\n\n## [{_today()}] init | workspace + responsabile {resp_slug}\n")

        # Responsabile agent
        agent_dir = anjawiki / "agents" / resp_slug
        agent_dir.mkdir(parents=True, exist_ok=True)
        agent_config = {
            "name": resp_slug,
            "display_name": responsabile_name,
            "role": "responsabile workspace",
            "domain": role_description,
            "default_provider": responsabile_provider,
            "default_model": responsabile_model,
            "default_effort": responsabile_effort or "off",
            "workspace_lead": True,
            "workspace_name": slug,
            "created": _now_iso(),
        }
        _write(agent_dir / "config.json", json.dumps(agent_config, indent=2, ensure_ascii=False) + "\n")
        _write(agent_dir / "SOUL.md", _responsabile_soul_template(resp_slug, role_description, slug))
        _write(agent_dir / "AGENTS.md", _responsabile_agents_template(resp_slug, role_description))
        _write(agent_dir / "TOOLS.md", _responsabile_tools_template(resp_slug))

        # Hub Ops (legacy, scope-locked T2): scaffold workspace .mcp.json con anja_hub_ops.
        # Dà al responsabile workspace accesso filesystem-diretto (hub.script_lifecycle, agent.update, ecc.).
        # NB: il self-management hub *principale* è migrato a REST :8765/api/* (vedi thin MCP hub_api).
        # anja_hub_ops resta come transport legacy per i sub-agent responsabili, che girano scollegati
        # dalla webapp e hanno bisogno di accesso diretto scope-locked. Vedi anja-techdebt.md (P2).
        import sys as _sys
        _hub_ops_script = str(Path(__file__).resolve().parent.parent / "scripts" / "mcp_hub_ops.py")
        if Path(_hub_ops_script).is_file():
            _write(anjawiki / ".mcp.json", json.dumps({
                "mcpServers": {
                    "anja_hub_ops": {
                        "command": _sys.executable,
                        "args": [_hub_ops_script],
                        "env": {
                            "ANJA_SCOPE": f"workspace:{slug}",
                            "ANJA_ROOT": str(anjawiki.resolve()),
                            "ANJA_HUB": str(hub_path.resolve()),
                        },
                    },
                },
            }, indent=2, ensure_ascii=False) + "\n")

        # Marker meta in <hub>/workspaces/<name>.meta.yaml
        marker = hub_path / "workspaces" / f"{slug}.meta.yaml"
        _write(marker, _yaml_simple({
            "kind": "internal",
            "name": slug,
            "responsabile": resp_slug,
            "type": ws_type,
            "created": _today(),
        }))

        # Update registry
        registry_path = hub_path / "config" / "projects.json"
        if registry_path.is_file():
            try:
                with registry_path.open(encoding="utf-8") as f:
                    reg = json.load(f)
            except Exception as e:
                errors.append(f"registry read error: {e}")
                reg = {"projects": []}
        else:
            registry_path.parent.mkdir(parents=True, exist_ok=True)
            reg = {"projects": []}

        # Check if already in registry
        already_in = any(p.get("name") == slug for p in reg.get("projects", []))
        registry_entry = None
        if not already_in:
            registry_entry = {
                "id": _gen_anja_id(),
                "name": slug,
                "type": ws_type,
                "tags": [],
                "location": {
                    "kind": "local",
                    "path": str(ws_root.resolve()),  # project root, NON .anjawiki (coerente con register.py)
                },
                "last_sync": _now_iso(),
                "description": role_description,
                "workspace_kind": "internal",
                "responsabile": resp_slug,
            }
            reg.setdefault("projects", []).append(registry_entry)
            with registry_path.open("w", encoding="utf-8") as f:
                json.dump(reg, f, indent=2, ensure_ascii=False)

        return {
            "ok": True,
            "path": str(ws_root),
            "slug": slug,
            "responsabile_slug": resp_slug,
            "registry_entry": registry_entry,
            "type": ws_type,
            "errors": errors,
        }
    except Exception as e:
        import traceback
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc(),
            "path": str(ws_root),
        }


# =================================================================
# Templates per type
# =================================================================

TYPE_DEFAULTS = {
    "office": {
        "role_hint": "Workspace ufficio: report, documenti, briefing periodici, gestione amministrativa.",
        "responsabile_name_hint": "anja-office",
    },
    "lab": {
        "role_hint": "Workspace laboratorio: analisi dati, esperimenti, prototipi, scripts utility.",
        "responsabile_name_hint": "anja-lab",
    },
    "studio": {
        "role_hint": "Workspace creativo: drafts, idee, content, brief.",
        "responsabile_name_hint": "anja-studio",
    },
    "inbox": {
        "role_hint": "Workspace catch-all: capture rapido, voice notes, link, da categorizzare.",
        "responsabile_name_hint": "anja-inbox",
    },
    "custom": {
        "role_hint": "Workspace personalizzato.",
        "responsabile_name_hint": "anja-custom",
    },
    "marketing": {
        "role_hint": "Workspace marketing (blueprint marketing-site): gestione SEO/contenuti/ads/social/analisi di un brand. Un brand = un workspace.",
        "responsabile_name_hint": "anja-brand",
    },
}


if __name__ == "__main__":
    # Smoke test
    import sys
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        hub = Path(tmp) / "test-hub"
        hub.mkdir(parents=True)
        (hub / "config").mkdir()
        (hub / "config" / "projects.json").write_text('{"projects": []}', encoding="utf-8")
        (hub / "workspaces").mkdir()
        result = scaffold_workspace(
            hub_path=hub,
            name="Research Test",
            responsabile_name="anja-research",
            role_description="Web search + paper analysis + citation graph navigation",
            ws_type="office",
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if result.get("ok"):
            print("\nStruttura creata:")
            import subprocess
            subprocess.run(["find", result["path"], "-type", "f"], check=False)
