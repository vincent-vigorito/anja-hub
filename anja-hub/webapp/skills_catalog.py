"""skills_catalog.py — Hub-level skill catalog (progressive disclosure).

Discovers SKILL.md files con frontmatter YAML.
Multi-source: bundled plugin / hub / user-global / workspace (.anjawiki/skills).
Progressive disclosure a 3 livelli:
  Level 0: `list_skills()`     → nome + description + category + tag (~3k tok max)
  Level 1: `load_skill(name)`  → SKILL.md body completo + frontmatter
  Level 2: `load_skill_file(name, path)` → file dentro references/scripts/templates

Sources di default (label scope allineate a convenzione UI esistente):
  - plugin:anja-hub         → <ANJAHUB_ROOT>/anja-hub/skills/         (read-only)
  - plugin:anja-routines    → <ANJAHUB_ROOT>/anja-routines/skills/    (read-only)
  - plugin:anjadev          → ~/.claude/plugins/marketplaces/anjadev/skills/ (read-only)
  - user-global             → ~/.anja/skills/                          (writable, primary)
  - user-global             → ~/.claude/skills/                        (read-only legacy, CC native)
  - hub                     → <hub>/skills/                            (writable)
  - project:<name>          → <hub>/workspaces/<name>/.anjawiki/skills/ (writable, primary)
  - project:<name>          → <hub>/workspaces/<name>/.claude/skills/  (read-only legacy, CC native)

Filtri runtime:
  - `platforms: [macos, linux]`     → skill nascosta su altre piattaforme
  - `requires_tools: [mcp__x__*]`   → skill nascosta se nessuno di questi tool è disponibile
  - `fallback_for_tools: [mcp__y]`  → skill nascosta se almeno uno di questi tool è disponibile

Dep: PyYAML (già nella requirements della webapp).
"""

from __future__ import annotations

import os
import platform as _platform
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable, Optional

import yaml


_CURRENT_PLATFORM = _platform.system().lower()
_PLATFORM_ALIAS = {"darwin": "macos", "linux": "linux", "windows": "windows"}
_PLATFORM = _PLATFORM_ALIAS.get(_CURRENT_PLATFORM, _CURRENT_PLATFORM)

_ANJAHUB_ROOT = Path(
    os.environ.get("ANJAHUB_ROOT") or Path(__file__).resolve().parents[2]
)


@dataclass
class SkillSource:
    scope: str
    path: Path
    writable: bool = True
    label: str = ""


@dataclass
class SkillInfo:
    name: str
    description: str = ""
    version: str = ""
    category: str = ""
    tags: list = field(default_factory=list)
    platforms: list = field(default_factory=list)
    requires_tools: list = field(default_factory=list)
    fallback_for_tools: list = field(default_factory=list)
    config: list = field(default_factory=list)
    required_env: list = field(default_factory=list)
    path: str = ""
    scope: str = ""
    writable: bool = True
    size_bytes: int = 0
    has_references: bool = False
    has_scripts: bool = False
    has_templates: bool = False


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter. Returns (meta, body)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    raw = text[3:end].strip()
    try:
        meta = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(meta, dict):
        return {}, text
    body = text[end + 4:].lstrip("\n")
    return meta, body


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _info_from_skill_md(skill_md: Path, source: SkillSource) -> Optional[SkillInfo]:
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    meta, _ = _parse_frontmatter(text)
    name = str(meta.get("name") or skill_md.parent.name).strip()
    if not name:
        return None
    desc = str(meta.get("description") or "").strip()
    desc_line = desc.split("\n")[0].strip()
    if len(desc_line) > 200:
        desc_line = desc_line[:197] + "..."
    skill_dir = skill_md.parent
    return SkillInfo(
        name=name,
        description=desc_line,
        version=str(meta.get("version") or ""),
        category=str(meta.get("category") or ""),
        tags=_as_list(meta.get("tags")),
        platforms=_as_list(meta.get("platforms")),
        requires_tools=_as_list(meta.get("requires_tools")),
        fallback_for_tools=_as_list(meta.get("fallback_for_tools")),
        config=_as_list(meta.get("config")),
        required_env=_as_list(meta.get("required_env")),
        path=str(skill_md),
        scope=source.scope,
        writable=source.writable,
        size_bytes=skill_md.stat().st_size,
        has_references=(skill_dir / "references").is_dir(),
        has_scripts=(skill_dir / "scripts").is_dir(),
        has_templates=(skill_dir / "templates").is_dir(),
    )


def _scan_source(source: SkillSource) -> list[SkillInfo]:
    if not source.path.is_dir():
        return []
    out = []
    for sub in sorted(source.path.iterdir()):
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        skill_md = sub / "SKILL.md"
        if not skill_md.is_file():
            continue
        info = _info_from_skill_md(skill_md, source)
        if info:
            out.append(info)
    return out


def default_sources(hub_path: Optional[Path] = None) -> list[SkillSource]:
    """Sources di default. Ordine = precedenza dedup (first wins).

    `user-global` e `project:<name>` coprono due path ciascuno (anja-native
    primario, CC-native legacy). Stesso scope label intenzionalmente — la UI
    non distingue, e in scrittura usiamo il path anja-native.
    """
    home = Path.home()
    sources: list[SkillSource] = [
        SkillSource("plugin:anja-hub",
                    _ANJAHUB_ROOT / "anja-hub" / "skills",
                    writable=False, label="anja-hub plugin"),
        SkillSource("plugin:anja-routines",
                    _ANJAHUB_ROOT / "anja-routines" / "skills",
                    writable=False, label="anja-routines plugin"),
        SkillSource("plugin:anjadev",
                    home / ".claude" / "plugins" / "marketplaces" / "anjadev" / "skills",
                    writable=False, label="anjadev plugin"),
        SkillSource("user-global",
                    home / ".anja" / "skills",
                    writable=True, label="user-global (anja)"),
        SkillSource("user-global",
                    home / ".claude" / "skills",
                    writable=False, label="user-global (cc, legacy)"),
    ]

    if hub_path:
        sources.append(SkillSource("hub",
                                   hub_path / "skills",
                                   writable=True, label="hub"))
        ws_root = hub_path / "workspaces"
        if ws_root.is_dir():
            for ws in sorted(ws_root.iterdir()):
                target = ws.resolve() if ws.is_symlink() else ws
                if not target.is_dir():
                    continue
                sources.append(SkillSource(
                    f"project:{ws.name}",
                    target / ".anjawiki" / "skills",
                    writable=True,
                    label=f"ws {ws.name} (anja)",
                ))
                sources.append(SkillSource(
                    f"project:{ws.name}",
                    target / ".claude" / "skills",
                    writable=False,
                    label=f"ws {ws.name} (cc, legacy)",
                ))

    return sources


def resolve_writable_skill_dir(
    scope: str,
    name: str,
    hub_path: Optional[Path] = None,
) -> Optional[Path]:
    """Per write: resolve la dir destinazione per (scope, name).

    Ritorna None se scope non writable (plugin:* è bundled, read-only).
    """
    if scope == "user-global":
        return Path.home() / ".anja" / "skills" / name
    if scope == "hub":
        if not hub_path:
            return None
        return hub_path / "skills" / name
    if scope.startswith("project:"):
        if not hub_path:
            return None
        proj_name = scope.split(":", 1)[1]
        ws_dir = hub_path / "workspaces" / proj_name
        target = ws_dir.resolve() if ws_dir.is_symlink() else ws_dir
        if not target.is_dir():
            return None
        return target / ".anjawiki" / "skills" / name
    return None


def _passes_filters(
    info: SkillInfo,
    available_tools: Optional[set[str]],
    apply_platform_filter: bool,
) -> bool:
    if apply_platform_filter and info.platforms and _PLATFORM not in info.platforms:
        return False
    if available_tools is not None:
        if info.requires_tools and not any(t in available_tools for t in info.requires_tools):
            return False
        if info.fallback_for_tools and any(t in available_tools for t in info.fallback_for_tools):
            return False
    return True


def list_skills(
    hub_path: Optional[Path] = None,
    sources: Optional[Iterable[SkillSource]] = None,
    available_tools: Optional[set[str]] = None,
    apply_platform_filter: bool = True,
) -> list[SkillInfo]:
    """Discover skills da multi-source con filtri runtime (platforms, requires_tools, fallback_for_tools).

    Dedup per name: precedenza secondo ordine sources (workspace > user > hub > bundled).
    """
    srcs = list(sources) if sources is not None else default_sources(hub_path)
    found: dict[str, SkillInfo] = {}
    for src in srcs:
        for info in _scan_source(src):
            if not _passes_filters(info, available_tools, apply_platform_filter):
                continue
            found.setdefault(info.name, info)
    return sorted(found.values(), key=lambda x: x.name)


def load_skill(
    name: str,
    hub_path: Optional[Path] = None,
    sources: Optional[Iterable[SkillSource]] = None,
) -> Optional[dict]:
    """Level 1: ritorna SkillInfo as dict + 'content' del SKILL.md.

    Niente platform/tool filter qui — se l'agent chiede esplicitamente per nome,
    glielo serviamo (es. l'utente sa cosa fa).
    """
    skills = list_skills(hub_path, sources=sources, apply_platform_filter=False)
    info = next((s for s in skills if s.name == name), None)
    if not info:
        return None
    out = asdict(info)
    try:
        out["content"] = Path(info.path).read_text(encoding="utf-8")
    except Exception as e:
        out["error"] = str(e)
    return out


def load_skill_file(
    name: str,
    file_path: str,
    hub_path: Optional[Path] = None,
    sources: Optional[Iterable[SkillSource]] = None,
) -> Optional[str]:
    """Level 2: file in references/scripts/templates. Path relativo alla skill dir."""
    skills = list_skills(hub_path, sources=sources, apply_platform_filter=False)
    info = next((s for s in skills if s.name == name), None)
    if not info:
        return None
    skill_dir = Path(info.path).parent.resolve()
    candidate = (skill_dir / file_path).resolve()
    try:
        candidate.relative_to(skill_dir)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    try:
        return candidate.read_text(encoding="utf-8")
    except Exception:
        return None


def format_catalog_for_prompt(skills: list[SkillInfo]) -> str:
    """Level 0 catalog compatto per system prompt injection."""
    if not skills:
        return ""
    lines = [
        "# Available skills (workflows)",
        "<!-- Level 0 catalog. Use `skill.load(name)` for full body, "
        "`skill.read_file(name, path)` for references. -->",
        "",
    ]
    by_scope: dict[str, list[SkillInfo]] = {}
    for s in skills:
        by_scope.setdefault(s.scope, []).append(s)
    for scope, items in sorted(by_scope.items()):
        lines.append(f"## {scope}")
        for s in items:
            cat = f" [{s.category}]" if s.category else ""
            ver = f" v{s.version}" if s.version else ""
            lines.append(f"- **`{s.name}`**{cat}{ver} — {s.description}")
        lines.append("")
    return "\n".join(lines)


def list_skills_as_dicts(
    hub_path: Optional[Path] = None,
    available_tools: Optional[set[str]] = None,
) -> list[dict]:
    """Convenience per API JSON (es. endpoint webapp)."""
    return [asdict(s) for s in list_skills(hub_path, available_tools=available_tools)]


# ============================================================
# Skill bundles: N skill + instruction wrapper sotto un slug
# ============================================================

@dataclass
class BundleInfo:
    name: str
    description: str = ""
    skills: list = field(default_factory=list)
    instruction: str = ""
    path: str = ""


def list_bundles(hub_path: Optional[Path] = None) -> list[BundleInfo]:
    """Discover bundle yaml in <hub>/skill-bundles/."""
    if not hub_path:
        return []
    bdir = hub_path / "skill-bundles"
    if not bdir.is_dir():
        return []
    out: list[BundleInfo] = []
    candidates = sorted(list(bdir.glob("*.yaml")) + list(bdir.glob("*.yml")))
    for f in candidates:
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        name = str(data.get("name") or f.stem).strip()
        if not name:
            continue
        out.append(BundleInfo(
            name=name,
            description=str(data.get("description") or "").strip(),
            skills=list(data.get("skills") or []),
            instruction=str(data.get("instruction") or "").strip(),
            path=str(f),
        ))
    return out


def load_bundle(name: str, hub_path: Optional[Path] = None) -> Optional[dict]:
    """Carica bundle + body di tutte le skill referenziate."""
    bundle = next((b for b in list_bundles(hub_path) if b.name == name), None)
    if not bundle:
        return None
    skills_data = []
    missing = []
    for s_name in bundle.skills:
        sd = load_skill(s_name, hub_path)
        if sd and sd.get("content"):
            skills_data.append({"name": sd["name"], "content": sd["content"]})
        else:
            missing.append(s_name)
    return {
        "name": bundle.name,
        "description": bundle.description,
        "instruction": bundle.instruction,
        "path": bundle.path,
        "skills": skills_data,
        "missing": missing,
    }


def list_bundles_as_dicts(hub_path: Optional[Path] = None) -> list[dict]:
    return [asdict(b) for b in list_bundles(hub_path)]


# ============================================================
# Setup wizard: config keys + required_env
# ============================================================

_SKILLS_CONFIG_PATH = Path.home() / ".anja" / "skills-config.yaml"
_SKILLS_ENV_PATH = Path.home() / ".anja" / ".env"


def _load_skills_config() -> dict:
    if not _SKILLS_CONFIG_PATH.is_file():
        return {}
    try:
        data = yaml.safe_load(_SKILLS_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _save_skills_config(data: dict) -> None:
    _SKILLS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SKILLS_CONFIG_PATH.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _read_env_file(path: Path) -> dict:
    if not path.is_file():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _write_env_file(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for k, v in data.items():
        if any(c in str(v) for c in (" ", "#", "\"", "'")):
            lines.append(f'{k}="{v}"')
        else:
            lines.append(f"{k}={v}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def skill_setup_status(name: str, hub_path: Optional[Path] = None) -> dict:
    """Setup wizard: ritorna stato config/env per una skill.

    Output:
      {
        skill: {name, description, version, ...},
        config: [{key, default, description, prompt, current_value, set}],
        required_env: [{name, prompt, help, required_for, current_value, set}],
        ready: bool,  # tutto risolto
      }

    "current_value" è oscurato per env vars (mostra solo "***" se set).
    """
    data = load_skill(name, hub_path)
    if not data:
        return {"error": f"skill '{name}' not found"}

    cfg_store = _load_skills_config()
    skill_cfg = cfg_store.get(name, {})
    env_store = {**os.environ, **_read_env_file(_SKILLS_ENV_PATH)}

    config_items = []
    for item in data.get("config") or []:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if not key:
            continue
        current = skill_cfg.get(key)
        default = item.get("default")
        config_items.append({
            "key": key,
            "default": default,
            "description": item.get("description", ""),
            "prompt": item.get("prompt", f"Value for {key}"),
            "current_value": current if current is not None else default,
            "set": current is not None,
        })

    env_items = []
    for item in data.get("required_env") or []:
        if not isinstance(item, dict):
            continue
        var = item.get("name")
        if not var:
            continue
        present = bool(env_store.get(var))
        env_items.append({
            "name": var,
            "prompt": item.get("prompt", f"Enter {var}"),
            "help": item.get("help", ""),
            "required_for": item.get("required_for", "full functionality"),
            "current_value": "***" if present else None,
            "set": present,
        })

    ready = all(c["set"] or c["default"] is not None for c in config_items) and all(e["set"] for e in env_items)

    return {
        "skill": {
            "name": data["name"],
            "description": data["description"],
            "version": data["version"],
            "category": data["category"],
        },
        "config": config_items,
        "required_env": env_items,
        "ready": ready,
    }


def skill_setup_apply(
    name: str,
    config_values: Optional[dict] = None,
    env_values: Optional[dict] = None,
    hub_path: Optional[Path] = None,
) -> dict:
    """Applica valori submessi dal wizard. Aggiorna skills-config.yaml + .env.

    Validazione: i nomi key/var devono essere dichiarati nella skill.
    Ignora silenziosamente value vuoti (treat as 'leave default / not set').
    """
    data = load_skill(name, hub_path)
    if not data:
        return {"error": f"skill '{name}' not found"}

    declared_keys = {item.get("key") for item in (data.get("config") or []) if isinstance(item, dict)}
    declared_env = {item.get("name") for item in (data.get("required_env") or []) if isinstance(item, dict)}

    applied_config = []
    applied_env = []

    if config_values:
        store = _load_skills_config()
        skill_cfg = store.setdefault(name, {})
        for k, v in config_values.items():
            if k not in declared_keys:
                continue
            if v is None or v == "":
                skill_cfg.pop(k, None)
                continue
            skill_cfg[k] = v
            applied_config.append(k)
        _save_skills_config(store)

    if env_values:
        env_store = _read_env_file(_SKILLS_ENV_PATH)
        for k, v in env_values.items():
            if k not in declared_env:
                continue
            if v is None or v == "":
                env_store.pop(k, None)
                continue
            env_store[k] = v
            applied_env.append(k)
        _write_env_file(_SKILLS_ENV_PATH, env_store)

    return {
        "status": "applied",
        "skill": name,
        "config_applied": applied_config,
        "env_applied": applied_env,
        "env_file": str(_SKILLS_ENV_PATH),
        "config_file": str(_SKILLS_CONFIG_PATH),
    }


if __name__ == "__main__":
    import sys
    hub_arg = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    skills = list_skills(hub_arg)
    print(f"Found {len(skills)} skills (platform={_PLATFORM}):\n")
    print(format_catalog_for_prompt(skills))
