"""blueprint_scaffold.py — F-WorkspaceBlueprint — istanzia un workspace verticale da un blueprint.

Un **blueprint** (`anja-hub/blueprints/<name>/`) è una ricetta: pod di agenti +
skill condivisa + tool group + schema vault. `scaffold_from_blueprint()` crea il
workspace-brand riusando `workspace_scaffold.scaffold_workspace` per la base
(lead = responsabile), poi materializza gli specialisti, il vault placeholder e i
`.mcp.json` per-scope col server `anja_marketing` scopizzato sul brand.

Modello MCP (opzione 1 — `.mcp.json` per-scope, GENERATI non scritti a mano):
  - `<ws_root>/.mcp.json`                  → chat scope=project (lead/workspace)
  - `<ws>/.anjawiki/agents/<r>/.mcp.json`  → chat scope=agent (specialista)

IMPORTANTE (verificato 2026-06-15): `claude_chat` risolve le DEFINIZIONI dei server
SOLO dal `.mcp.json` del cwd (`setting_sources=[]`). Quindi ogni `.mcp.json` generato
deve essere **self-contained**: include `anja_marketing` (brand) + `anja_memory`
(riscopizzato sul brand: wiki/memory/skill) + i server hub reali dichiarati dall'agente
(es. `anja_images` per il social). I server "logici" non presenti nell'hub `.mcp.json`
(es. `anja_agents`) NON si possono includere così → da verificare live.

Sono artefatti derivati: `resync_marketing_mcp()` li rigenera tutti dal template.
Stdlib only. Vedi anja-marketing-workspace-design.md §4/§5.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

from workspace_scaffold import (
    _now_iso,
    _slugify,
    _write,
    scaffold_workspace,
)

_ANJAHUB_ROOT = Path(os.environ.get("ANJAHUB_ROOT") or Path(__file__).resolve().parents[2])
BLUEPRINTS_DIR = _ANJAHUB_ROOT / "anja-hub" / "blueprints"
MARKETING_SERVER = str(_ANJAHUB_ROOT / "anja-hub" / "scripts" / "mcp_marketing_server.py")
MARKETING_SERVER_NAME = "anja_marketing"


class BlueprintError(Exception):
    """Blueprint mancante o malformato."""


def _blueprint_bases(hub_path: Optional[Path] = None) -> list[tuple[Path, str]]:
    """Basi di ricerca in ordine di precedenza: `<hub>/blueprints/` (verticali
    privati, per-installazione) prima dei built-in del repo."""
    bases = []
    if hub_path:
        bases.append((Path(hub_path) / "blueprints", "hub"))
    bases.append((BLUEPRINTS_DIR, "builtin"))
    return bases


def resolve_blueprint_dir(name: str, hub_path: Optional[Path] = None) -> Path:
    """Dir del blueprint `name`, cercata in hub → built-in.
    Difesa path-traversal: il nome finisce nel filesystem."""
    searched = []
    for base, _origin in _blueprint_bases(hub_path):
        bp_dir = (base / name).resolve()
        if not bp_dir.is_relative_to(base.resolve()):
            raise BlueprintError(f"nome blueprint non valido: {name!r}")
        if (bp_dir / "blueprint.json").is_file():
            return bp_dir
        searched.append(str(base))
    raise BlueprintError(f"blueprint '{name}' non trovato in {searched}")


def load_blueprint(name: str, hub_path: Optional[Path] = None) -> dict:
    bp_dir = resolve_blueprint_dir(name, hub_path)
    return json.loads((bp_dir / "blueprint.json").read_text(encoding="utf-8"))


def list_blueprints(hub_path: Optional[Path] = None) -> list[dict]:
    """Catalogo: metadata di ogni blueprint disponibile (F5), hub + built-in.
    A parità di nome vince l'hub (override locale). `origin` per la galleria."""
    out, seen = [], set()
    for base, origin in _blueprint_bases(hub_path):
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            bp_file = d / "blueprint.json"
            if not d.is_dir() or not bp_file.is_file():
                continue
            try:
                bp = json.loads(bp_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            name = bp.get("name", d.name)
            if name in seen:
                continue
            seen.add(name)
            out.append({
                "name": name,
                "version": bp.get("version", ""),
                "description": bp.get("description", ""),
                "workspace_type": bp.get("workspace_type", "custom"),
                "backends": bp.get("backends", []),
                "default_backend": bp.get("default_backend", ""),
                "ecommerce_optional": bool(bp.get("ecommerce_optional", False)),
                "pod": bp.get("pod", []),
                "shared_skill": bp.get("shared_skill", ""),
                "origin": origin,
            })
    return out


def _subst(text: str, mapping: dict[str, str]) -> str:
    for k, v in mapping.items():
        text = text.replace(k, v)
    return text


# ----------------------------------------------------------------------
# Generazione .mcp.json (single source = questi helper, rigenerabili)
# ----------------------------------------------------------------------

def _hub_servers(hub_path: Path) -> dict:
    """mcpServers dell'hub `.mcp.json` (per copiarne le definizioni reali)."""
    f = hub_path / ".mcp.json"
    if not f.is_file():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8")).get("mcpServers", {}) or {}
    except Exception:
        return {}


def _marketing_entry(ws_slug: str, ws_root: Path, anjawiki: Path, hub_path: Path, groups: str) -> dict:
    """Definizione del server anja_marketing scopizzato su un brand."""
    return {
        "command": sys.executable,
        "args": [MARKETING_SERVER],
        "env": {
            "ANJA_SCOPE": f"workspace:{ws_slug}",
            "ANJA_ROOT": str(anjawiki),
            # I deliverable (kit social, ecc.) stanno in <ws>/files/ — alla radice,
            # IN VISTA, non sepolti in .anjawiki/ (plumbing). Vedi hoist 2026-06-16.
            "ANJA_FILES_ROOT": str(ws_root),
            "ANJA_HUB": str(hub_path),
            "ANJA_MARKETING_VAULT": str(anjawiki / ".secrets.env"),
            "ANJA_GOOGLE_CONNECTORS": str(hub_path / "config" / "connectors"),
            "ANJA_TOOL_GROUPS": groups,
        },
    }


# Set tool per workspace: memoria + skill + sessioni + PLANNING (kanban/goals/roadmap).
# Il planning trio è essenziale: senza, l'agente non sa rispondere a "cosa c'è in
# programma / quante card / che goal" e improvvisa HTTP all'API hub → 404.
# (Il taglio precedente "memory,skills,sessions" era la token-economy basata sulla
# metrica cumulativa sbagliata — vedi audit roadmap. kanban/goals/roadmap leggono
# sqlite/markdown locali, costo trascurabile.)
MEMORY_GROUPS_LEAN = "memory,skills,sessions,kanban,goals,roadmap"


def _memory_entry(hub_memory: dict, ws_root: Path, hub_path: Path) -> dict:
    """Copia la def hub di anja_memory, riscopizzata sul brand + tool group sfoltiti."""
    e = json.loads(json.dumps(hub_memory))  # deep copy
    env = e.setdefault("env", {})
    env["ANJA_ROOT"] = str(ws_root)
    env["ANJA_SCOPE"] = "project"
    # ANJA_HUB: i tool hub-level scoped (kanban/goals/roadmap → <hub>/data/*.db) lo usano
    # per risolvere l'hub root in scope=project; senza → "hub root not determinable".
    env["ANJA_HUB"] = str(hub_path)
    # ANJA_HUB_WEBAPP: il server MCP (processo separato) importa kanban_io/goals dalla
    # webapp; la convention <hub>/../anja-hub/webapp non matcha sempre → puntiamo al dir
    # reale (blueprint_scaffold.py vive nella webapp). Senza → "modulo kanban non disponibile".
    env["ANJA_HUB_WEBAPP"] = str(Path(__file__).resolve().parent)
    env["ANJA_TOOL_GROUPS"] = MEMORY_GROUPS_LEAN
    return e


def _build_mcp_servers(ws_slug: str, ws_root: Path, anjawiki: Path, hub_path: Path,
                       groups: str, extra_servers: tuple = ()) -> dict:
    """Set self-contained: anja_marketing + anja_memory + server hub extra dichiarati."""
    hub = _hub_servers(hub_path)
    servers: dict = {MARKETING_SERVER_NAME: _marketing_entry(ws_slug, ws_root, anjawiki, hub_path, groups)}
    if "anja_memory" in hub:
        servers["anja_memory"] = _memory_entry(hub["anja_memory"], ws_root, hub_path)
    for name in extra_servers:
        if name in (MARKETING_SERVER_NAME, "anja_memory"):
            continue
        if name in hub:  # solo server hub REALI (non i nomi logici)
            servers[name] = json.loads(json.dumps(hub[name]))
    return servers


def _write_mcp_json(target_dir: Path, ws_slug: str, ws_root: Path, anjawiki: Path,
                    hub_path: Path, groups: str, extra_servers: tuple = ()) -> None:
    cfg = {"mcpServers": _build_mcp_servers(ws_slug, ws_root, anjawiki, hub_path, groups, extra_servers)}
    _write(target_dir / ".mcp.json", json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")


def _groups_for_agent(allowed_tools: list[str], default: str) -> str:
    """Deriva ANJA_TOOL_GROUPS dai tool consentiti del ruolo (least-token)."""
    groups: list[str] = []
    blob = " ".join(allowed_tools)
    if "__gsc_" in blob or "__ga_" in blob or "__merchant_" in blob:
        groups.append("analytics")
    # wp_upload_media è social-oriented (URL pubblici per IG), non un tool CMS di contenuto
    if any("__wp_" in t for t in allowed_tools if "wp_upload_media" not in t):
        groups.append("cms")
    if "__meta_" in blob or "__social_kit" in blob or "wp_upload_media" in blob:
        groups.append("social")
    return ",".join(dict.fromkeys(groups)) or default


def _adapt_agent_for_backend(cfg: dict, backend: str) -> dict:
    """SwerpiCommerce non ha tool MCP CMS: la gestione contenuti passa dalla CLI
    `swerpicommerce` (Bash) + modulo skill `swerpicommerce`, non dai `wp_*`.
    Per gli agenti che scrivono sul CMS (avevano `wp_*`) togliamo quei tool e diamo
    Bash; il modulo skill `wordpress` diventa `swerpicommerce`. Gli analytics/social
    (GA/GSC/Meta) sono backend-agnostici e restano. NO-OP per wp/woo."""
    if backend != "swerpi":
        return cfg
    tools = cfg.get("allowed_tools", [])
    # I `wp_*` non esistono su swerpi → tolti tutti (tranne wp_upload_media, social-oriented).
    # Bash+modulo servono solo a chi opera sui CONTENUTI del CMS, non a chi fa solo il
    # connection-check `wp_site_info` (es. il lead, che delega e non scrive).
    _wp_meta = ("wp_site_info", "wp_list_sites", "wp_use_site")
    had_cms = any("__wp_" in t and "wp_upload_media" not in t
                  and not any(m in t for m in _wp_meta) for t in tools)
    kept = [t for t in tools if "__wp_" not in t or "wp_upload_media" in t]
    mods = [("swerpicommerce" if m == "wordpress" else m) for m in cfg.get("skill_modules", [])]
    if had_cms:  # chi scriveva sul CMS opera via CLI: serve Bash + il modulo metodologico
        if "Bash" not in kept:
            kept.append("Bash")
        if "swerpicommerce" not in mods:
            mods.append("swerpicommerce")
    cfg["allowed_tools"] = kept
    cfg["skill_modules"] = mods
    return cfg


def _soul_md(cfg: dict, is_lead: bool) -> str:
    lead_line = "workspace_lead: true\n" if is_lead else ""
    modules = ", ".join(cfg.get("skill_modules", [])) or "all"
    return f"""---
slug: {cfg['name']}
name: {cfg['name']}
type: agent
workspace: {cfg.get('workspace_name', '')}
{lead_line}---

# {cfg.get('display_name', cfg['name'])}

## Role

{cfg.get('role', '')}

## Personalità

{cfg.get('persona', '')}

## Skill & fatti del brand

Carica la skill condivisa `seo-manager` (moduli rilevanti: {modules}).
I **fatti** del brand vivono nel workspace: `data/ESPERTO.md` (ruolo + dominio),
`data/catalogo/` (indici prodotti/pagine/articoli), `data/BRAND.md` (visual).
Metodo nella skill, fatti nel workspace.
"""


def _governance_hint(cfg: dict, backend: str = "wp") -> str:
    tools = " ".join(cfg.get("allowed_tools", []))
    if cfg.get("workspace_lead"):
        return "Orchestri e deleghi agli specialisti: non operi tu sulle scritture."
    if "meta_publish" in tools:
        return "Pubblichi sui social SOLO con ok esplicito dell'utente; immagini IG da URL pubblici."
    if backend == "swerpi" and "Bash" in cfg.get("allowed_tools", []):
        return ("Operi sul CMS SwerpiCommerce via CLI `swerpicommerce` (Bash): SEMPRE in bozza, "
                "mai live senza ok; slug mai toccato; read-back campo per campo dopo ogni scrittura.")
    if any(w in tools for w in ("wp_create_content", "wp_update_content", "wp_delete_content")):
        return ("Scrivi SEMPRE in bozza, mai live senza ok; slug mai toccato; "
                "read-back campo per campo dopo ogni scrittura.")
    if "wp_set_seo" in tools:
        return "Solo bozze e meta SEO; nessuna pubblicazione senza ok esplicito."
    return "READ-ONLY: leggi e produci report; non scrivi mai sul sito né sugli ads."


def _agents_md(cfg: dict, is_lead: bool, backend: str = "wp") -> str:
    modules = ", ".join(cfg.get("skill_modules", [])) or "all"
    servers = ", ".join(cfg.get("mcp_servers", [])) or "—"
    tools_block = "\n".join(f"- `{t}`" for t in cfg.get("allowed_tools", [])) or "- (solo tool nativi)"
    lead_line = "workspace_lead: true\n" if is_lead else ""
    return f"""---
slug: {cfg['name']}
type: agent
workspace: {cfg.get('workspace_name', '')}
{lead_line}---

# {cfg.get('display_name', cfg['name'])} — istruzioni operative

## Ruolo
{cfg.get('role', '')}

## Governance
{_governance_hint(cfg, backend)}

## Metodo & fatti del brand
Carica la skill condivisa `seo-manager` (moduli rilevanti: {modules}).
I **fatti** del brand vivono nel workspace: `data/ESPERTO.md`, `data/catalogo/`, `data/BRAND.md`.
Output e deliverable in `files/` (`reports/`, `proposals/`, `social/`).

## Tool a disposizione (least-privilege)
MCP server (dal `.mcp.json` scoped sul brand): {servers}
Tool consentiti:
{tools_block}
"""


def _tools_md(cfg: dict) -> str:
    servers = "\n".join(f"- `{s}`" for s in cfg.get("mcp_servers", [])) or "- (nessuno)"
    tools = "\n".join(f"- `{t}`" for t in cfg.get("allowed_tools", [])) or "- (solo tool nativi)"
    return f"""---
slug: {cfg['name']}
type: tools-doc
---

# {cfg.get('display_name', cfg['name'])} — Tools

## MCP server (scoped sul brand)
{servers}

## Tool consentiti
{tools}

I tool MCP arrivano già puntati al brand attivo (vault del workspace): lo **scope è il brand**,
niente selezione di sito a runtime.
"""


def _claude_md(cfg: dict, is_lead: bool) -> str:
    return f"""# Agente `{cfg['name']}` — workspace `{cfg.get('workspace_name', '')}`

{cfg.get('role', '')}

- Identità / personalità → `SOUL.md`
- Istruzioni operative + governance → `AGENTS.md`
- Tool → `TOOLS.md`
- Metodo: skill condivisa `seo-manager`. Fatti del brand: `data/ESPERTO.md`, `data/catalogo/`, `data/BRAND.md`.
"""


def _apply_agent_template(adir: Path, tmpl_path: Path, mapping: dict[str, str], is_lead: bool,
                          backend: str = "wp") -> dict:
    cfg = json.loads(_subst(tmpl_path.read_text(encoding="utf-8"), mapping))
    cfg = _adapt_agent_for_backend(cfg, backend)
    adir.mkdir(parents=True, exist_ok=True)
    cfg_path = adir / "config.json"
    if cfg_path.is_file():  # merge col config esistente (lead già creato da scaffold_workspace)
        try:
            existing = json.loads(cfg_path.read_text(encoding="utf-8"))
            existing.update(cfg)
            cfg = existing
        except Exception:
            pass
    cfg.setdefault("created", _now_iso())
    _write(cfg_path, json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    _write(adir / "SOUL.md", _soul_md(cfg, is_lead))
    _write(adir / "AGENTS.md", _agents_md(cfg, is_lead, backend))
    _write(adir / "TOOLS.md", _tools_md(cfg))
    _write(adir / "CLAUDE.md", _claude_md(cfg, is_lead))
    return cfg


def _scaffold_routines(bp_dir: Path, anjawiki: Path, mapping: dict[str, str]) -> list[str]:
    """Copia blueprints/<bp>/routines/*.yaml in <ws>/.anjawiki/routines/ con placeholder sostituiti."""
    src = bp_dir / "routines"
    if not src.is_dir():
        return []
    out = []
    for f in sorted(src.glob("*.yaml")):
        name = _subst(f.name, mapping)
        _write(anjawiki / "routines" / name, _subst(f.read_text(encoding="utf-8"), mapping))
        out.append(name)
    return out


def _scaffold_content(bp_dir: Path, ws_root: Path, mapping: dict[str, str], ecommerce: bool) -> list[str]:
    """Materializza la triade del brand (ESPERTO/BRAND/PIANO) + catalogo skeleton da
    blueprints/<bp>/content/ in <ws>/data/, con placeholder sostituiti. Così un brand
    nuovo nasce completo (Piano/Catalogo pronti, identità da compilare). `prodotti.md`
    solo se ecommerce. Non sovrascrive file già presenti."""
    content = bp_dir / "content"
    if not content.is_dir():
        return []
    data_dir = ws_root / "data"
    written = []
    for fname in ("ESPERTO.md", "BRAND.md", "PIANO.md"):
        src = content / fname
        dest = data_dir / fname
        if src.is_file() and not dest.exists():
            _write(dest, _subst(src.read_text(encoding="utf-8"), mapping))
            written.append(fname)
    cat = content / "catalogo"
    if cat.is_dir():
        for cf in sorted(cat.glob("*.md")):
            if cf.stem == "prodotti" and not ecommerce:
                continue
            dest = data_dir / "catalogo" / cf.name
            if not dest.exists():
                _write(dest, _subst(cf.read_text(encoding="utf-8"), mapping))
                written.append(f"catalogo/{cf.name}")
    return written


def _augment_meta(meta_path: Path, blueprint: str, backend: str, ecommerce: bool) -> None:
    if not meta_path.is_file():
        return
    extra = f"blueprint: {blueprint}\nbackend: {backend}\necommerce: {str(ecommerce).lower()}\n"
    with meta_path.open("a", encoding="utf-8") as f:
        f.write(extra)


def _augment_registry(reg_path: Path, ws_slug: str, blueprint: str, backend: str, ecommerce: bool) -> None:
    if not reg_path.is_file():
        return
    reg = json.loads(reg_path.read_text(encoding="utf-8"))
    for p in reg.get("projects", []):
        if p.get("name") == ws_slug:
            p["type"] = "marketing"
            p["blueprint"] = blueprint
            p["backend"] = backend
            p["ecommerce"] = ecommerce
            tags = p.setdefault("tags", [])
            for t in ("brand", "marketing"):
                if t not in tags:
                    tags.append(t)
    reg_path.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n")


def scaffold_from_blueprint(
    hub_path: Path,
    brand_name: str,
    blueprint_name: str = "marketing-site",
    backend: str = "wp",
    ecommerce: bool = False,
    lead_name: Optional[str] = None,
    provider: str = "claude",
    model: str = "sonnet",
) -> dict:
    """Istanzia un workspace-brand da un blueprint. Ritorna un dict di esito."""
    hub_path = Path(hub_path)
    bp = load_blueprint(blueprint_name, hub_path)
    bp_dir = resolve_blueprint_dir(blueprint_name, hub_path)

    if backend not in bp.get("backends", ["wp"]):
        return {"ok": False, "error": f"backend '{backend}' non supportato (ammessi: {bp.get('backends')})"}

    ws_type = bp.get("workspace_type", "marketing")
    full_groups = bp.get("tool_groups_by_backend", {}).get(backend, "cms,analytics,social")

    # 1) base workspace (lead = responsabile)
    base = scaffold_workspace(
        hub_path=hub_path,
        name=brand_name,
        responsabile_name=lead_name or f"anja-{_slugify(brand_name)}",
        role_description=f"Workspace marketing del brand {brand_name} (blueprint {blueprint_name}, backend {backend}).",
        ws_type=ws_type,
        responsabile_provider=provider,
        responsabile_model=model,
    )
    if not base.get("ok"):
        return base

    ws_root = Path(base["path"])
    anjawiki = ws_root / ".anjawiki"
    ws_slug = base["slug"]
    lead_slug = base["responsabile_slug"]
    mapping = {"{WS}": ws_slug, "{BRAND}": brand_name, "{LEAD}": lead_slug}
    agents_dir = anjawiki / "agents"
    lead_role = bp.get("lead_role", "lead")

    # 2) pod: arricchisci il lead + materializza gli specialisti + .mcp.json per-scope
    created_agents = []
    for role in bp.get("pod", []):
        tmpl = bp_dir / "agents" / f"{role}.json"
        if not tmpl.is_file():
            continue
        is_lead = role == lead_role
        adir = agents_dir / (lead_slug if is_lead else role)
        cfg = _apply_agent_template(adir, tmpl, mapping, is_lead=is_lead, backend=backend)
        role_groups = _groups_for_agent(cfg.get("allowed_tools", []), full_groups)
        extra = tuple(cfg.get("mcp_servers", []))
        _write_mcp_json(adir, ws_slug, ws_root, anjawiki, hub_path, role_groups, extra)
        created_agents.append(adir.name)

    # 3) vault schema → .secrets.env.example
    schema = _subst((bp_dir / "vault.schema.env").read_text(encoding="utf-8"), mapping)
    _write(anjawiki / ".secrets.env.example", schema)

    # 3b) routine template → <ws>/.anjawiki/routines/
    routines = _scaffold_routines(bp_dir, anjawiki, mapping)

    # 3c) contenuto brand: triade (ESPERTO/BRAND/PIANO) + catalogo skeleton → <ws>/data/
    content = _scaffold_content(bp_dir, ws_root, mapping, ecommerce)

    # 4) <ws_root>/.mcp.json (chat scope=project: lead/workspace, tutti i group del backend)
    _write_mcp_json(ws_root, ws_slug, ws_root, anjawiki, hub_path, full_groups)

    # 5) meta.yaml + registry
    _augment_meta(anjawiki / "meta.yaml", blueprint_name, backend, ecommerce)
    _augment_registry(hub_path / "config" / "projects.json", ws_slug, blueprint_name, backend, ecommerce)

    return {
        "ok": True,
        "slug": ws_slug,
        "path": str(ws_root),
        "lead": lead_slug,
        "agents": created_agents,
        "blueprint": blueprint_name,
        "backend": backend,
        "ecommerce": ecommerce,
        "tool_groups": full_groups,
        "vault_example": str(anjawiki / ".secrets.env.example"),
        "shared_skill": bp.get("shared_skill"),
        "routines": routines,
        "content": content,
    }


def resync_marketing_mcp(hub_path: Path) -> dict:
    """Rigenera tutti i `.mcp.json` (workspace + agenti) dei workspace marketing dal
    template corrente. Da lanciare quando cambia il server (path, env, group, set hub).
    """
    hub_path = Path(hub_path)
    reg_path = hub_path / "config" / "projects.json"
    if not reg_path.is_file():
        return {"ok": False, "error": "projects.json assente"}
    reg = json.loads(reg_path.read_text(encoding="utf-8"))
    updated = []
    for p in reg.get("projects", []):
        if p.get("type") != "marketing" and p.get("blueprint") != "marketing-site":
            continue
        ws_root = Path(p["location"]["path"])
        anjawiki = ws_root / ".anjawiki"
        if not anjawiki.is_dir():
            continue
        ws_slug = p["name"]
        full_groups = "analytics,social" if p.get("backend") == "swerpi" else "cms,analytics,social"
        _write_mcp_json(ws_root, ws_slug, ws_root, anjawiki, hub_path, full_groups)
        agents_dir = anjawiki / "agents"
        if agents_dir.is_dir():
            for adir in sorted(agents_dir.iterdir()):
                cfg_path = adir / "config.json"
                if not cfg_path.is_file():
                    continue
                try:
                    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                role_groups = _groups_for_agent(cfg.get("allowed_tools", []), full_groups)
                extra = tuple(cfg.get("mcp_servers", []))
                _write_mcp_json(adir, ws_slug, ws_root, anjawiki, hub_path, role_groups, extra)
        updated.append(ws_slug)
    return {"ok": True, "updated_workspaces": updated, "count": len(updated)}


# ----------------------------------------------------------------------
# F-BlueprintForge Step A — validazione deterministica (pre-scaffold)
# ----------------------------------------------------------------------

_DUMMY_MAPPING = {"{WS}": "demo-ws", "{BRAND}": "Demo Brand", "{LEAD}": "anja-demo-ws"}


def validate_blueprint(name: str, hub_path: Optional[Path] = None) -> dict:
    """Schema-check completo di un blueprint (hub o built-in) SENZA istanziarlo.

    Ritorna {ok, origin, errors: [...], warnings: [...]}: `errors` = lo scaffold
    fallirebbe o produrrebbe un workspace rotto; `warnings` = funziona ma manca
    qualcosa di consigliato. È il contratto reale di `scaffold_from_blueprint`.
    """
    errors: list[str] = []
    warnings: list[str] = []

    try:
        bp_dir = resolve_blueprint_dir(name, hub_path)
    except BlueprintError as e:
        return {"ok": False, "origin": None, "errors": [str(e)], "warnings": []}
    origin = ("hub" if (hub_path and bp_dir.is_relative_to(Path(hub_path).resolve()))
              else "builtin")

    # --- blueprint.json
    try:
        bp = json.loads((bp_dir / "blueprint.json").read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "origin": origin,
                "errors": [f"blueprint.json non parseabile: {e}"], "warnings": []}

    if bp.get("name") != bp_dir.name:
        warnings.append(f"name '{bp.get('name')}' ≠ nome directory '{bp_dir.name}' "
                        "(la galleria usa name: tienili allineati)")
    for field in ("description", "workspace_type", "version"):
        if not bp.get(field):
            warnings.append(f"blueprint.json: campo '{field}' mancante o vuoto")

    backends = bp.get("backends")
    if not isinstance(backends, list) or not backends:
        errors.append("blueprint.json: 'backends' deve essere una lista non vuota")
        backends = []
    default_backend = bp.get("default_backend", "")
    if backends and default_backend not in backends:
        errors.append(f"'default_backend' ({default_backend!r}) non è in backends {backends}")
    tgb = bp.get("tool_groups_by_backend") or {}
    for b in backends:
        if b not in tgb:
            warnings.append(f"tool_groups_by_backend: manca la chiave '{b}' "
                            "(fallback 'cms,analytics,social')")

    pod = bp.get("pod")
    if not isinstance(pod, list) or not pod:
        errors.append("blueprint.json: 'pod' deve essere una lista di ruoli non vuota")
        pod = []
    lead_role = bp.get("lead_role", "lead")
    if pod and lead_role not in pod:
        errors.append(f"'lead_role' ({lead_role!r}) non è nel pod {pod}")

    # --- agents/<role>.json (lo scaffold SALTA in silenzio i template mancanti:
    # qui è un errore, altrimenti il pod nasce monco)
    for role in pod:
        tmpl = bp_dir / "agents" / f"{role}.json"
        if not tmpl.is_file():
            errors.append(f"agents/{role}.json mancante (il ruolo verrebbe saltato)")
            continue
        try:
            cfg = json.loads(_subst(tmpl.read_text(encoding="utf-8"), _DUMMY_MAPPING))
        except Exception as e:
            errors.append(f"agents/{role}.json non parseabile dopo i placeholder: {e}")
            continue
        if not cfg.get("name"):
            errors.append(f"agents/{role}.json: campo 'name' mancante")
        if not cfg.get("role"):
            warnings.append(f"agents/{role}.json: campo 'role' vuoto (l'agente non sa cosa fa)")
        if not cfg.get("allowed_tools"):
            warnings.append(f"agents/{role}.json: 'allowed_tools' vuoto (nessun tool MCP)")

    # --- vault.schema.env (lo scaffold lo legge INCONDIZIONATAMENTE: senza, crash)
    vault = bp_dir / "vault.schema.env"
    if not vault.is_file():
        errors.append("vault.schema.env mancante (lo scaffold fallirebbe)")

    # --- routines/*.yaml
    routines_dir = bp_dir / "routines"
    if routines_dir.is_dir():
        try:
            import yaml  # noqa: PLC0415
        except Exception:
            yaml = None
            warnings.append("pyyaml non disponibile: routine non validate")
        if yaml:
            for f in sorted(routines_dir.glob("*.yaml")):
                try:
                    yaml.safe_load(_subst(f.read_text(encoding="utf-8"), _DUMMY_MAPPING))
                except Exception as e:
                    errors.append(f"routines/{f.name}: YAML non valido: {e}")

    # --- content/ (opzionale, ma la triade è consigliata)
    content = bp_dir / "content"
    if content.is_dir():
        for fname in ("ESPERTO.md", "BRAND.md", "PIANO.md"):
            if not (content / fname).is_file():
                warnings.append(f"content/{fname} mancante (il brand nasce senza)")
    else:
        warnings.append("content/ assente: il workspace nasce senza triade brand")

    return {"ok": not errors, "origin": origin, "errors": errors, "warnings": warnings}
