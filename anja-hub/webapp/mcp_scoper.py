"""mcp_scoper.py — decide quali MCP server attivare per ogni chat (Fase 7u, M-Cx 2).

Tre tier complementari:

- **Tier 0 CORE** (sempre on): MCP infrastrutturali che il PA non può non avere
  (`anja_memory` di default — contiene anche i tool task.schedule_one_shot,
  user.update, sessions.list, ecc.).

- **Tier 1 SCOPE** (auto da contesto chat):
    * scope=hub      → solo Tier 0
    * scope=agent    → Tier 0 + agent_config["mcp_servers"]
    * scope=project  → Tier 0 + project's `.mcp.json` keys

- **Tier 2 ON-DEMAND** (3 meccanismi che si sommano):
    * keyword pre-routing: regex su user_prompt → MCP corrispondenti
    * manual UI override:  caller passa `active_mcps` (persisted in conversation)
    * meta-tool `mcp.activate(name)`: gestito a runtime, fuori scope qui

Manifest opzionale: `<hub>/config/mcp_tiers.json` per override del default.

Stdlib only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

DEFAULT_MANIFEST = {
    # Tier 0 = server SEMPRE montati, per nome reale di entry `.mcp.json`. Vuoto di
    # default: `anja_memory` arriva già da tier1 (hub: lista esplicita; project: dal
    # `.mcp.json` del workspace; agent: da `mcp_servers` della config). I vecchi nomi
    # logici `anja_memory_core`/`anja_skills` non esistevano in nessun `.mcp.json` e
    # venivano scartati come "unavailable" (F-ConnectorUX residuo, chiuso 2026-08-19).
    # Un hub può forzare server always-on in <hub>/config/mcp_tiers.json.
    "tier0": [],
    "keyword_map": {
        # Pattern keyword → server MCP. Regex case-insensitive.
        # External tools — F-CLI-Media: immagini/video via CLI giv (Bash),
        # niente più server MCP anja_images/anja_videos negli agent.
        r"\b(email|gmail|inbox|posta|mail|newsletter|invia.*mail|send.*mail|messaggio.*email)\b": "anja_mail",
        r"\b(calendar|calendario|appuntamento|meeting|evento|event\b|invite)\b": "google_calendar",
        r"\b(drive|file.*google|condividi.*doc|google.*doc)\b": "google_drive",
        r"\b(browser|naviga|screenshot|click.*pagina|web.*scrape|crawl)\b": "playwright",
        r"\b(stripe|payment|invoice|fattura|subscription)\b": "stripe",
        # Fase 17 — anja_code
        r"\b(analizza|analisi|calcola|elabora|parse|parsa|csv|json|xlsx|script|python|esegui)\b": "anja_code",
        r"\b(statistiche|metrics|grafico|chart|aggregate)\b": "anja_code",
        # Fase 20 — anja_office
        r"\b(report|word|docx|excel|spreadsheet|presentazione|slides?|powerpoint|pptx|pdf)\b": "anja_office",
        r"\b(genera.*documento|crea.*report|esporta.*excel|genera.*pdf|fammi.*slides)\b": "anja_office",
        # Hub management (create/modify/delete routine/agent/workspace/goal/skill)
        # NON passa più via anja_hub_ops keyword routing — è gestito da `hub_api`
        # (REST wrapper, sempre attivo in scope=hub via tier1). Anja chiama
        # direttamente gli endpoint /api/* e questo design è cross-provider.
        # WS — Web research skills (DuckDuckGo + SerpAPI). Trigger su intent di ricerca web/news/info.
        r"\b(cerca(re)? (info|informaz|online|sul web|su google|news|notizie)|trova(re)? (info|notizie|news|qualcosa)|google[r]? (questa|questo|info)|cosa dicono|ultim(e|i) (news|notizie)|search.* (web|online|google)|web search|research (su|on)|ricerca (web|online))\b": "anja_memory",
        # F-AnjadevCoreSplit — piano di lavoro degli agent: kanban/goals/workspace/tasks/
        # agents/pp vivono nel server REALE `anja_hub_runtime` (anja-hub/scripts/
        # mcp_hub_runtime.py). I vecchi nomi logici (anja_kanban, anja_goals, …) non
        # matchavano nessuna entry `.mcp.json` e venivano scartati.
        r"\b(kanban|task\b|tasks\b|todo|ricorda.*di|aggiungi.*lista|cosa.*devo|cosa.*c'è.*da.*fare|backlog|board)\b": "anja_hub_runtime",
        r"\b(goal|goals|obiettivo|obiettivi|target|deadline|judge|verdict|valuta.*goal|progress.*goal|raggiungere|riflessione|reflection|on.track|drift)\b": "anja_hub_runtime",
        r"\b(workspace|ufficio|files.*hub|files.*workspace|scripts.*hub|crea.*workspace|nuovo workspace)\b": "anja_hub_runtime",
        r"\b(soul|personalit[àa]|stile.*anja|come.*ti.*comporti)\b": "anja_memory",
        r"\b(schedula|ricontrolla|tra.*\d+.*min|tra.*\d+.*ore|domani.*alle|verifica.*tra|in.*\d+.*minuti)\b": "anja_hub_runtime",
        r"(?:^|\W)@\w[\w-]*": "anja_hub_runtime",
        r"\b(agent\.list|elenca.*agent|delega|delegare|chiedi.*a.*@)\b": "anja_hub_runtime",
        # Fase P-CLI — Printing Press catalog discovery (gruppo pp del runtime)
        r"\b(integra(re|zione)?|integrare|integration|nuovo.*mcp|nuovo.*cli|nuovo.*connettore|connettore|wrap(pa(re)?)?.*api|printing.press|pp-cli|cli-architect)\b": "anja_hub_runtime",
        r"\b(stripe|notion|github|linear|slack|airtable|zapier|gmail.*api|google.*search.*console|gsc|google.*calendar|drive\b|hubspot|salesforce|paypal|shopify)\b": "anja_hub_runtime",
    },
    # Native Claude SDK tools (Read/Edit/Bash/Grep/...) ON di default solo per project.
    "native_tools_default": {
        "hub": False,
        "agent": False,
        "project": True,
    },
}


def load_manifest(hub_path: Path) -> dict:
    """Load <hub>/config/mcp_tiers.json se presente, fallback DEFAULT_MANIFEST."""
    manifest_path = hub_path / "config" / "mcp_tiers.json"
    if not manifest_path.is_file():
        return dict(DEFAULT_MANIFEST)
    try:
        user_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        merged = dict(DEFAULT_MANIFEST)
        merged.update(user_manifest)
        return merged
    except Exception:
        return dict(DEFAULT_MANIFEST)


def _read_mcp_json_servers(root: Path) -> list[str]:
    """Estrai chiavi mcpServers da `<root>/.mcp.json`. Vuoto se assente/malformato."""
    f = root / ".mcp.json"
    if not f.is_file():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return list((data.get("mcpServers") or {}).keys())
    except Exception:
        return []


def _keyword_match_servers(prompt: str, keyword_map: dict) -> list[str]:
    """Ritorna i server i cui pattern keyword matchano nel prompt utente."""
    if not prompt:
        return []
    matches = []
    low = prompt.lower()
    for pattern, server in keyword_map.items():
        try:
            if re.search(pattern, low, re.IGNORECASE):
                if server not in matches:
                    matches.append(server)
        except re.error:
            continue
    return matches


# =================================================================
# Main entry
# =================================================================

def scope_mcps(
    hub_path: Path,
    scope_kind: str,
    target_name: Optional[str] = None,
    cwd: Optional[Path] = None,
    user_prompt: str = "",
    active_mcps: Optional[list[str]] = None,
    agent_config: Optional[dict] = None,
    manifest: Optional[dict] = None,
) -> tuple[list[str], dict]:
    """Decide MCP server list per la chat corrente.

    Returns (server_names, meta) where meta explica perché ognuno è incluso.
    `cwd` = root di scope (project path, agent dir, hub path) — usato per leggere
    project `.mcp.json`.
    """
    if manifest is None:
        manifest = load_manifest(hub_path)

    tier0 = list(manifest.get("tier0", []))
    keyword_map = manifest.get("keyword_map", {})

    tier1: list[str] = []
    tier1_source = ""
    if scope_kind == "agent" and agent_config:
        tier1 = list(agent_config.get("mcp_servers") or [])
        tier1_source = "agent_config.mcp_servers"
    elif scope_kind == "project" and cwd:
        # Project: leggi .mcp.json del progetto, filtra fuori i server già in Tier 0
        # (es. anja_memory definito anche nel project .mcp.json).
        project_servers = _read_mcp_json_servers(cwd)
        tier1 = [s for s in project_servers if s not in tier0]
        tier1_source = f"{cwd}/.mcp.json"
    elif scope_kind == "hub":
        # scope=hub: includi sempre `hub_api` (REST wrapper, ~150 tok) + `anja_memory`
        # (skill.load + wiki/memory/sessions/soul/user, sempre serve in chat hub).
        # Anja gestisce tutto l'hub via REST API senza caricare 25k token di
        # tool schemas. Skill `hub-admin` caricabile on-demand per il dettaglio.
        tier1 = ["hub_api", "anja_memory"]
        tier1_source = "hub_scope:rest_bridge+memory"

    # Keyword routing per scope agent/project (toolset task-specifico).
    # Per hub: keyword_map ancora attivo (image/video/code/office/...) ma NON per
    # hub_ops (gestione hub passa via hub_api REST, no più scopo per il routing).
    tier2_keyword = _keyword_match_servers(user_prompt, keyword_map)
    tier2_manual = list(active_mcps or [])

    # Merge preserving order, deduplicato
    final: list[str] = []
    reasons: dict[str, str] = {}
    for s in tier0:
        if s not in final:
            final.append(s)
            reasons[s] = "tier0:core"
    for s in tier1:
        if s not in final:
            final.append(s)
            reasons[s] = f"tier1:{tier1_source}"
    for s in tier2_keyword:
        if s not in final:
            final.append(s)
            reasons[s] = "tier2:keyword"
    for s in tier2_manual:
        if s not in final:
            final.append(s)
            reasons[s] = "tier2:manual"

    # Filtro: tieni solo server effettivamente disponibili nel `.mcp.json` dell'hub
    # O dello scope corrente (un hub appena scaffoldato ha un catalogo minimo, ma il
    # workspace/agent può dichiarare server propri — es. anja_marketing dal blueprint).
    available = set(_read_mcp_json_servers(hub_path))
    if cwd is not None:
        available |= set(_read_mcp_json_servers(Path(cwd)))
    if available:
        kept = [s for s in final if s in available]
        dropped = [s for s in final if s not in available]
        final = kept
    else:
        dropped = []

    meta = {
        "scope_kind": scope_kind,
        "target_name": target_name,
        "tier0": tier0,
        "tier1": tier1,
        "tier1_source": tier1_source,
        "tier2_keyword": tier2_keyword,
        "tier2_manual": tier2_manual,
        "final": final,
        "reasons": reasons,
        "dropped_unavailable": dropped,
    }
    return final, meta


def native_tools_enabled(
    hub_path: Path,
    scope_kind: str,
    override: Optional[bool] = None,
    manifest: Optional[dict] = None,
) -> bool:
    """Decidi se i native Claude SDK tools (Read/Edit/Bash/...) sono ON.

    `override` (caller-supplied) vince se non None.
    Altrimenti: dal manifest `native_tools_default[scope_kind]`.
    """
    if override is not None:
        return bool(override)
    if manifest is None:
        manifest = load_manifest(hub_path)
    defaults = manifest.get("native_tools_default", {})
    return bool(defaults.get(scope_kind, False))


# =================================================================
# CLI debug
# =================================================================

def _main():
    import argparse

    ap = argparse.ArgumentParser(description="Test MCPScoper.")
    ap.add_argument("--hub", required=True, help="Hub path (per leggere manifest e .mcp.json hub)")
    ap.add_argument("--scope-kind", default="hub", choices=["hub", "project", "agent"])
    ap.add_argument("--target-name", default=None)
    ap.add_argument("--cwd", default=None, help="Cwd per project (.mcp.json del progetto)")
    ap.add_argument("--prompt", default="")
    ap.add_argument("--active-mcps", default="", help="Comma-separated list di manual override")
    ap.add_argument("--agent-config", default=None, help="Path config.json agent")
    args = ap.parse_args()

    hub_path = Path(args.hub)
    cwd = Path(args.cwd) if args.cwd else None
    active = [s.strip() for s in args.active_mcps.split(",") if s.strip()]
    agent_cfg = None
    if args.agent_config:
        agent_cfg = json.loads(Path(args.agent_config).read_text(encoding="utf-8"))

    final, meta = scope_mcps(
        hub_path=hub_path,
        scope_kind=args.scope_kind,
        target_name=args.target_name,
        cwd=cwd,
        user_prompt=args.prompt,
        active_mcps=active,
        agent_config=agent_cfg,
    )
    print(json.dumps(meta, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    _main()
