"""context_composer.py — unified system-prompt builder cross-provider (Fase 7u, M-Cx 1).

Compone un blob `ProjectContext` identico per Claude SDK e LiteLLM, così che ogni
chat (qualunque provider) parta sapendo del progetto. Sezioni nell'ordine:

1. IDENTITY block — chi è l'agent, con chi parla, oggi, timezone (Fase 12 prep).
2. PROJECT_CONTEXT — CLAUDE.md cwd in versione slim (preserve-only le sezioni
   semanticamente importanti: Stato/Roadmap/Filosofia/Convenzioni). Limite ~4k.
3. ACTIVE_MEMORY — HOT+WARM dal context_loader esistente (triade + log + sessions
   + wiki match by user_prompt).

Il composer NON gestisce MCP scoping (vedi mcp_scoper.py M-Cx 2) né cache_control
LiteLLM (M-Cx 6). Quelle responsabilità stanno a valle.

Stdlib only.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
try:
    import injection_guard
except ImportError:
    injection_guard = None

CHARS_PER_TOKEN = 3.5
CLAUDE_MD_BUDGET_TOKENS = 4000
CACHE_TTL_SECONDS = 3600  # 1h


def _est_tokens(s: str) -> int:
    return int(len(s) / CHARS_PER_TOKEN)


def _truncate_to_tokens(s: str, max_tokens: int) -> str:
    max_chars = int(max_tokens * CHARS_PER_TOKEN)
    if len(s) <= max_chars:
        return s
    # tronca a paragrafo più vicino
    cut = s[:max_chars]
    last_para = cut.rfind("\n\n")
    if last_para > max_chars * 0.6:
        cut = cut[:last_para]
    return cut + "\n\n[...truncated for budget...]"


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:].lstrip()
    return text


def _strip_html_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)


def _slim_claude_md(text: str, budget_tokens: int = CLAUDE_MD_BUDGET_TOKENS) -> str:
    """Riduci CLAUDE.md a un slim semantico.

    Strategia: se sotto budget → full. Altrimenti tieni:
      - prime ~300 token (tagline + intro)
      - heading di livello 2/3 con sotto-paragrafo iniziale (1° paragrafo)
      - tronca al budget.
    """
    text = _strip_html_comments(_strip_frontmatter(text)).strip()
    if _est_tokens(text) <= budget_tokens:
        return text
    # quick slim: prendi intro + heading + first paragraph dopo ogni heading
    lines = text.splitlines()
    intro_chars = int(300 * CHARS_PER_TOKEN)
    intro = "\n".join(lines[:30])[:intro_chars]
    sections: list[str] = []
    cur_heading: Optional[str] = None
    cur_para: list[str] = []
    out: list[str] = []
    for ln in lines:
        if re.match(r"^#{1,3}\s", ln):
            if cur_heading is not None:
                snippet = "\n".join(cur_para[:4]).strip()
                if snippet:
                    sections.append(f"{cur_heading}\n{snippet}")
            cur_heading = ln
            cur_para = []
        else:
            if cur_heading is not None and ln.strip():
                cur_para.append(ln)
    if cur_heading is not None and cur_para:
        sections.append(f"{cur_heading}\n" + "\n".join(cur_para[:4]).strip())
    body = "\n\n".join(sections)
    composed = f"{intro}\n\n---\n\n{body}"
    return _truncate_to_tokens(composed, budget_tokens)


def _read_claude_md(scope_root: Path) -> str:
    """Try common locations: scope_root/CLAUDE.md, scope_root/.anjawiki/CLAUDE.md."""
    for candidate in (scope_root / "CLAUDE.md", scope_root / ".anjawiki" / "CLAUDE.md"):
        if candidate.is_file():
            try:
                return candidate.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass
    return ""


def _identity_block(
    scope_kind: str,
    target_name: Optional[str],
    hub_name: str,
    user_name: str = "user",
    timezone: str = "",
    today: Optional[str] = None,
    agent_display_name: Optional[str] = None,
    user_display_name: Optional[str] = None,
) -> str:
    """Identity block — Fase 12 M-Id 3+4. Usa display_name quando presente."""
    today = today or datetime.now().strftime("%Y-%m-%d")
    you_name = agent_display_name or target_name
    if scope_kind == "agent" and you_name:
        you = f"`{you_name}`, a specialized agent in hub `{hub_name}`"
    elif scope_kind == "project" and target_name:
        if agent_display_name:
            you = f"`{agent_display_name}`, the assistant scoped to project `{target_name}`"
        else:
            you = f"the personal AI assistant scoped to project `{target_name}`"
    else:
        if agent_display_name:
            you = f"`{agent_display_name}`, the personal AI assistant for hub `{hub_name}`"
        else:
            you = f"the personal AI assistant for hub `{hub_name}`"
    speaking_with = user_display_name or user_name
    tz_part = f" ({timezone})" if timezone else ""
    return (
        "# Identity\n"
        f"- You are: {you}\n"
        f"- Speaking with: {speaking_with}\n"
        f"- Today: {today}{tz_part}\n"
        "- Use the user's name only when relevant — not as a ritual greeting.\n"
        "- You are model-agnostic: do not identify as any specific underlying model\n"
        "  unless explicitly asked. Identify as your role above.\n"
    )


def _read_user_hot(hub_path: Path, user_slug: str) -> tuple[str, dict]:
    """Legge `<hub>/users/<slug>.md` HOT. Ritorna (formatted, meta).

    Skippa frontmatter + commenti HTML. Estrae anche il `name` dal frontmatter
    se presente per uso come display_name.
    """
    f = hub_path / "users" / f"{user_slug}.md"
    if not f.is_file():
        return "", {"loaded": False, "reason": "not found"}
    try:
        raw = f.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return "", {"loaded": False, "reason": str(e)}

    # Estrai 'name' dal frontmatter
    display_name = None
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end != -1:
            fm = raw[3:end]
            m = re.search(r"^name:\s*(.+?)\s*$", fm, re.M)
            if m:
                display_name = m.group(1).strip()

    body = _strip_html_comments(_strip_frontmatter(raw)).strip()
    return f"# User profile (HOT)\n\n{body}", {
        "loaded": True,
        "display_name": display_name,
        "tokens": _est_tokens(body),
    }


def _read_project_user_hot(project_root: Path, user_slug: str) -> tuple[str, dict]:
    """Legge `<project>/.anjawiki/users/<slug>.md` HOT (Fase 14 overlay).

    Ritorna (formatted, meta). Se file assente o vuoto, ritorna ("", meta=loaded:False).
    """
    f = project_root / ".anjawiki" / "users" / f"{user_slug}.md"
    if not f.is_file():
        return "", {"loaded": False, "reason": "not found"}
    try:
        raw = f.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return "", {"loaded": False, "reason": str(e)}
    body = _strip_html_comments(_strip_frontmatter(raw)).strip()
    if not body:
        return "", {"loaded": False, "reason": "empty"}
    return f"# User profile (project overlay)\n\n{body}", {
        "loaded": True,
        "tokens": _est_tokens(body),
    }


def _read_dialectic_block(dialectic_path: Path, label: str, top_n: int = 5) -> tuple[str, dict]:
    """Legge top-N osservazioni active da un dialectic file (Fase 14).

    `label` es. 'hub' / 'project'. Output marker: '# Working memory (dialectic — <label>)'.
    """
    if not dialectic_path or not dialectic_path.is_file():
        return "", {"loaded": False}
    try:
        from dialectic_io import top_active, format_active_for_context
    except ImportError:
        return "", {"loaded": False, "reason": "dialectic_io missing"}
    obs = top_active(dialectic_path, n=top_n)
    if not obs:
        return "", {"loaded": False, "reason": "empty"}
    body = format_active_for_context(obs)
    if injection_guard is not None:
        body, _ = injection_guard.neutralize_invisible(body)
    header = (
        f"# Working memory (dialectic — {label})\n"
        f"<!-- rolling insight, may decay. promoted patterns live in USER profile -->\n\n"
    )
    section = header + body
    return section, {"loaded": True, "count": len(obs), "tokens": _est_tokens(body)}


def _read_skills_catalog(hub_path: Optional[Path]) -> tuple[str, dict]:
    """Fase 16-bis — Catalog skills (3-level lazy disclosure).

    Iniettato sempre nel system prompt (~700-1500 token). Body delle skill viene
    caricato on-demand via tool skill.load.
    """
    try:
        import skills_catalog
    except ImportError:
        return "", {"loaded": False, "reason": "skills_catalog missing"}
    try:
        skills = skills_catalog.list_skills(hub_path)
    except Exception as e:
        return "", {"loaded": False, "reason": str(e)}
    if not skills:
        return "", {"loaded": False, "reason": "no skills"}
    section = skills_catalog.format_catalog_for_prompt(skills)
    return section, {"loaded": True, "count": len(skills), "tokens": _est_tokens(section)}


def _read_workspaces_list(hub_path: Path) -> tuple[str, dict]:
    """Fase 22 — Legge lista workspace disponibili nel hub per inject in scope=hub.

    Ritorna ("# Available workspaces\n...", meta) o ("", {loaded:False}).
    Compact format per minimizzare token (~50 token per workspace).
    """
    if not hub_path:
        return "", {"loaded": False}
    registry = hub_path / "config" / "projects.json"
    if not registry.is_file():
        return "", {"loaded": False, "reason": "no registry"}
    try:
        import json as _json
        with registry.open(encoding="utf-8") as f:
            data = _json.load(f)
    except Exception:
        return "", {"loaded": False, "reason": "registry parse error"}

    projects = data.get("projects", [])
    if not projects:
        return "", {"loaded": False, "reason": "empty"}

    lines = ["# Available workspaces",
             "<!-- list of workspaces in this hub. Use workspace.list or @<responsabile> to interact -->",
             ""]
    for p in projects:
        name = p.get("name", "")
        if not name:
            continue
        meta_file = hub_path / "workspaces" / f"{name}.meta.yaml"
        kind = "external"
        responsabile = ""
        if meta_file.is_file():
            for line in meta_file.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("kind:"):
                    kind = line.split(":", 1)[1].strip().strip('"').strip("'")
                if line.startswith("responsabile:"):
                    responsabile = line.split(":", 1)[1].strip().strip('"').strip("'")
        descr = (p.get("description") or "").strip()
        if len(descr) > 80:
            descr = descr[:77] + "..."
        resp_part = f" · responsabile: `@{responsabile}`" if responsabile else ""
        descr_part = f" — {descr}" if descr else ""
        lines.append(f"- `{name}` [{kind}]{resp_part}{descr_part}")

    section = "\n".join(lines)
    return section, {"loaded": True, "count": len(projects), "tokens": _est_tokens(section)}


def _read_workspace_meta(workspace_root: Path) -> dict:
    """Fase 22 — Legge meta.yaml di un workspace (responsabile, kind, type)."""
    meta_path = workspace_root / ".anjawiki" / "meta.yaml"
    if not meta_path.is_file():
        return {}
    out = {}
    try:
        for line in meta_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if ":" in line and not line.startswith("#"):
                k, v = line.split(":", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return out


def _read_hub_knowledge(hub_path: Path, max_chars: int = 4000) -> tuple[str, dict]:
    """F-HubKnowledge — knowledge layer di dominio dell'hub (`<hub>/.anjawiki/wiki/`).

    Blocco HOT leggero: overview (tesi) + index (catalogo). Le entity/concept vere
    si caricano on-demand via wiki tool — qui solo l'awareness, capped a max_chars
    per non gonfiare il context. È la "lente" con cui l'agent ragiona e analizza.
    """
    wiki = hub_path / ".anjawiki" / "wiki"
    idx = wiki / "index.md"
    if not idx.is_file():
        return "", {"loaded": False}
    chunks = []
    for f in (wiki / "overview.md", idx):
        if f.is_file():
            try:
                t = f.read_text(encoding="utf-8", errors="replace").strip()
                if t:
                    chunks.append(t)
            except Exception:
                pass
    body = "\n\n".join(chunks)
    if not body:
        return "", {"loaded": False}
    # F-Security-Injection: l'hub knowledge deriva da source ingerite (contenuto
    # esterno). Togli eventuali caratteri invisibili/bidi prima dell'iniezione.
    if injection_guard is not None:
        body, _ = injection_guard.neutralize_invisible(body)
    truncated = len(body) > max_chars
    if truncated:
        body = body[:max_chars] + "\n\n…(troncato — usa wiki.read/wiki.search per il dettaglio)"
    section = "# Hub knowledge (le tue competenze di dominio)\n\n" + body
    return section, {"loaded": True, "chars": len(body), "truncated": truncated}


def _resolve_default_user(hub_path: Path) -> Optional[str]:
    """Legge default_user dal hub `config.json`."""
    cfg = hub_path / "config.json"
    if not cfg.is_file():
        return None
    try:
        import json
        data = json.loads(cfg.read_text(encoding="utf-8"))
        return data.get("default_user")
    except Exception:
        return None


def _resolve_default_agent_name(hub_path: Path) -> Optional[str]:
    """Legge default_agent_name dal hub `config.json`."""
    cfg = hub_path / "config.json"
    if not cfg.is_file():
        return None
    try:
        import json
        data = json.loads(cfg.read_text(encoding="utf-8"))
        return data.get("default_agent_name")
    except Exception:
        return None


def _resolve_agent_display_name(hub_path: Path, agent_slug: str) -> Optional[str]:
    """Legge display_name (o name) da `<hub>/agents/<slug>/config.json`."""
    cfg = hub_path / "agents" / agent_slug / "config.json"
    if not cfg.is_file():
        return None
    try:
        import json
        data = json.loads(cfg.read_text(encoding="utf-8"))
        return data.get("display_name") or data.get("name")
    except Exception:
        return None


def _anjadev_dir() -> Path:
    """Root del plugin anjadev installato (override via ANJADEV_DIR per dev locale)."""
    import os
    env = os.environ.get("ANJADEV_DIR")
    return Path(env) if env else Path.home() / ".claude" / "plugins" / "marketplaces" / "anjadev"


def _load_active_memory(scope_root: Path, user_prompt: str = "") -> tuple[str, dict]:
    """Carica HOT+WARM via context_loader.py. Best-effort.

    Ritorna (formatted_string, meta_dict). meta contiene tokens stimati e flag.
    """
    ctx_loader_path = _anjadev_dir() / "scripts" / "context_loader.py"
    if not ctx_loader_path.is_file():
        print(f"[context-composer] WARNING: context_loader.py non trovato: {ctx_loader_path}")
        return "", {"loaded": False, "reason": "context_loader.py not found"}
    try:
        spec = importlib.util.spec_from_file_location("context_loader", str(ctx_loader_path))
        cl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cl)  # type: ignore
        ctx = cl.build_session_context(scope_root, user_prompt=user_prompt)
        formatted = cl.format_for_prompt(ctx)
        return formatted, {
            "loaded": True,
            "tokens_estimated": ctx.get("tokens_estimated", 0),
            "hot_truncated": ctx.get("hot_truncated", False),
            "warm_truncated": ctx.get("warm_truncated", False),
        }
    except Exception as e:
        return "", {"loaded": False, "reason": f"{type(e).__name__}: {e}"}


# =================================================================
# Main entry
# =================================================================

def compose_context(
    scope_root: Path,
    scope_kind: str,
    target_name: Optional[str] = None,
    hub_name: str = "",
    user_prompt: str = "",
    user_name: str = "user",
    timezone: str = "",
    include_claude_md: bool = True,
    claude_md_budget: int = CLAUDE_MD_BUDGET_TOKENS,
    hub_path: Optional[Path] = None,
    include_user_hot: bool = True,
    project_path: Optional[Path] = None,
    include_dialectic: bool = True,
) -> tuple[str, dict]:
    """Compose unified ProjectContext blob.

    Sezioni: identity → user HOT → CLAUDE.md slim → active memory.

    `hub_path` (Fase 12): root del hub per leggere `users/<default_user>.md`,
    `agents/<target>/config.json` (display_name), `config.json` (default_user,
    default_agent_name). Se None → solo modalità legacy senza identity dinamica.
    """
    if not hub_name:
        hub_name = scope_root.name

    # Risolvi identità Fase 12 + overlay Fase 14
    user_display_name = None
    agent_display_name = None
    user_hot_section = ""
    user_hot_meta: dict = {"loaded": False}
    project_user_hot_section = ""
    project_user_hot_meta: dict = {"loaded": False}
    dialectic_hub_section = ""
    dialectic_project_section = ""
    dialectic_meta: dict = {"hub": {"loaded": False}, "project": {"loaded": False}}
    user_slug = None

    if hub_path is not None:
        # User profile HOT (hub-level, base)
        user_slug = _resolve_default_user(hub_path)
        if user_slug and include_user_hot:
            user_hot_section, user_hot_meta = _read_user_hot(hub_path, user_slug)
            if user_hot_meta.get("display_name"):
                user_display_name = user_hot_meta["display_name"]
            # Fase 14 — dialectic hub-level
            if include_dialectic:
                hub_dialectic_file = hub_path / "users" / f"{user_slug}-dialectic.md"
                dialectic_hub_section, dialectic_meta["hub"] = _read_dialectic_block(
                    hub_dialectic_file, label="hub", top_n=5
                )
        # Agent display name
        if scope_kind == "agent" and target_name:
            agent_display_name = _resolve_agent_display_name(hub_path, target_name)
        elif scope_kind == "hub":
            agent_display_name = _resolve_default_agent_name(hub_path)

    # Fase 14 — Project overlay (in project scope o agent-scoped-to-project)
    if project_path is not None and user_slug and include_user_hot:
        project_user_hot_section, project_user_hot_meta = _read_project_user_hot(
            project_path, user_slug
        )
        if include_dialectic:
            project_dialectic_file = project_path / ".anjawiki" / "users" / f"{user_slug}-dialectic.md"
            dialectic_project_section, dialectic_meta["project"] = _read_dialectic_block(
                project_dialectic_file, label="project", top_n=5
            )

    # Fase 16-bis — Skills catalog (lazy disclosure)
    skills_catalog_section = ""
    skills_catalog_meta = {"loaded": False}
    if hub_path is not None:
        skills_catalog_section, skills_catalog_meta = _read_skills_catalog(hub_path)

    # Fase 22 — Workspace awareness
    workspaces_list_section = ""
    workspaces_list_meta = {"loaded": False}
    workspace_meta_block = ""
    workspace_kind = None
    if hub_path is not None and scope_kind == "hub":
        # In hub scope, mostra elenco workspace per Anja
        workspaces_list_section, workspaces_list_meta = _read_workspaces_list(hub_path)

    # F-HubKnowledge — knowledge di dominio dell'hub, iniettata come "lente" in tutti
    # gli scope (in hub scope è la conoscenza propria di Anja; in project/workspace è
    # l'overlay con cui analizza il progetto alla luce delle competenze dell'hub).
    hub_knowledge_section = ""
    hub_knowledge_meta = {"loaded": False}
    if hub_path is not None:
        hub_knowledge_section, hub_knowledge_meta = _read_hub_knowledge(hub_path)
    if project_path is not None:
        # In workspace scope, mostra meta (responsabile, kind, type) del workspace corrente
        ws_meta = _read_workspace_meta(project_path)
        workspace_kind = ws_meta.get("kind")
        if ws_meta:
            block_lines = ["# Current workspace metadata"]
            for k in ("name", "kind", "type", "responsabile"):
                v = ws_meta.get(k)
                if v:
                    block_lines.append(f"- {k}: `{v}`")
            workspace_meta_block = "\n".join(block_lines)

    identity = _identity_block(
        scope_kind=scope_kind,
        target_name=target_name,
        hub_name=hub_name,
        user_name=user_name,
        timezone=timezone,
        agent_display_name=agent_display_name,
        user_display_name=user_display_name,
    )

    project_section = ""
    if include_claude_md:
        raw = _read_claude_md(scope_root)
        if raw:
            slim = _slim_claude_md(raw, budget_tokens=claude_md_budget)
            project_section = "# Project context (CLAUDE.md, slim)\n\n" + slim

    memory_section, mem_meta = _load_active_memory(scope_root, user_prompt=user_prompt)

    # Fase 18.A — Goals injection (1-liner per goal attivo)
    goals_section = ""
    try:
        import goal_io
        if scope_kind == "hub":
            # Hub scope: overview cross-workspace
            goals_section = goal_io.hub_workspaces_goals_overview(hub_path)
            # + hub-level meta goals
            own = goal_io.goals_summary_block(hub_path, "hub", max_items=5)
            if own:
                goals_section = (own + "\n\n" + goals_section).strip()
        elif scope_kind == "project" and target_name:
            goals_section = goal_io.goals_summary_block(hub_path, f"workspace:{target_name}", max_items=5)
    except Exception:
        goals_section = ""

    parts = [identity]
    if user_hot_section:
        parts.append(user_hot_section)
    if project_user_hot_section:
        parts.append(project_user_hot_section)
    if workspace_meta_block:
        parts.append(workspace_meta_block)
    if dialectic_hub_section:
        parts.append(dialectic_hub_section)
    if dialectic_project_section:
        parts.append(dialectic_project_section)
    if project_section:
        parts.append(project_section)
    if workspaces_list_section:
        parts.append(workspaces_list_section)
    if hub_knowledge_section:
        parts.append(hub_knowledge_section)
    if goals_section:
        parts.append(goals_section)
    if skills_catalog_section:
        parts.append(skills_catalog_section)
    if memory_section:
        parts.append(memory_section)
    composed = "\n\n===\n\n".join(parts)

    meta = {
        "scope_kind": scope_kind,
        "target_name": target_name,
        "hub_name": hub_name,
        "agent_display_name": agent_display_name,
        "user_display_name": user_display_name,
        "tokens_total_est": _est_tokens(composed),
        "tokens_identity": _est_tokens(identity),
        "tokens_user_hot": _est_tokens(user_hot_section),
        "tokens_user_hot_project": _est_tokens(project_user_hot_section),
        "tokens_dialectic_hub": _est_tokens(dialectic_hub_section),
        "tokens_dialectic_project": _est_tokens(dialectic_project_section),
        "tokens_claude_md": _est_tokens(project_section),
        "tokens_memory": _est_tokens(memory_section),
        "tokens_workspaces_list": _est_tokens(workspaces_list_section),
        "tokens_hub_knowledge": _est_tokens(hub_knowledge_section),
        "hub_knowledge_meta": hub_knowledge_meta,
        "tokens_workspace_meta": _est_tokens(workspace_meta_block),
        "tokens_goals": _est_tokens(goals_section),
        "tokens_skills_catalog": _est_tokens(skills_catalog_section),
        "skills_catalog_meta": skills_catalog_meta,
        "memory_meta": mem_meta,
        "user_hot_meta": user_hot_meta,
        "project_user_hot_meta": project_user_hot_meta,
        "dialectic_meta": dialectic_meta,
        "workspaces_list_meta": workspaces_list_meta,
        "workspace_kind": workspace_kind,
    }
    return composed, meta


# =================================================================
# CLI debug
# =================================================================

def _main():
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Test ContextComposer su uno scope.")
    ap.add_argument("--scope-root", required=True)
    ap.add_argument("--scope-kind", default="hub", choices=["hub", "project", "agent"])
    ap.add_argument("--target-name", default=None)
    ap.add_argument("--hub-path", default=None,
                    help="Hub root per leggere users/, agents/, config.json (Fase 12 identity)")
    ap.add_argument("--user-prompt", default="")
    ap.add_argument("--user-name", default="user")
    ap.add_argument("--timezone", default="")
    ap.add_argument("--no-claude-md", action="store_true")
    ap.add_argument("--show-content", action="store_true")
    args = ap.parse_args()

    composed, meta = compose_context(
        scope_root=Path(args.scope_root),
        scope_kind=args.scope_kind,
        target_name=args.target_name,
        user_prompt=args.user_prompt,
        user_name=args.user_name,
        timezone=args.timezone,
        include_claude_md=not args.no_claude_md,
        hub_path=Path(args.hub_path) if args.hub_path else None,
    )

    print(json.dumps(meta, indent=2, ensure_ascii=False))
    if args.show_content:
        print("\n========= COMPOSED =========\n")
        print(composed)


if __name__ == "__main__":
    _main()
