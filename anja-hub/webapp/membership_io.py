"""membership_io.py — membership per-workspace (F4b slice 3).

Modello deciso (2026-06-20): i membri di un workspace vivono in
`<hub>/workspaces/<ws>.meta.yaml` come lista `members:` (accanto a `responsabile`).
Default FAIL-CLOSED: owner/admin accedono a TUTTI i workspace (sono admin dell'hub);
un member vede SOLO i ws dove il suo slug è in `members[]`; un ws senza `members`
→ visibile solo a owner/admin. Membership BINARIA (nessun ruolo editor/viewer per-ws).

In personal mode il modello è bypassato (role=None → local owner onnipotente).
"""

from __future__ import annotations

from pathlib import Path

import yaml


def _meta_path(hub_path: Path, ws_name: str) -> Path:
    return Path(hub_path) / "workspaces" / f"{ws_name}.meta.yaml"


def _load_meta(hub_path: Path, ws_name: str) -> dict:
    p = _meta_path(hub_path, ws_name)
    if not p.is_file():
        return {}
    try:
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def workspace_members(hub_path: Path, ws_name: str) -> list[str]:
    """Slug dei membri del workspace (lista YAML; tollera anche una stringa CSV)."""
    m = _load_meta(hub_path, ws_name).get("members")
    if isinstance(m, list):
        return [str(x).strip().lower() for x in m if str(x).strip()]
    if isinstance(m, str) and m.strip():
        return [x.strip().lower() for x in m.split(",") if x.strip()]
    return []


def set_workspace_members(hub_path: Path, ws_name: str, members: list[str]) -> list[str]:
    """Sovrascrive la lista membri nel meta.yaml (dedup + lowercase). Lista vuota
    rimuove la chiave `members`. Preserva gli altri campi e il loro ordine."""
    p = _meta_path(hub_path, ws_name)
    if not p.is_file():
        raise ValueError("workspace inesistente (meta.yaml mancante)")
    meta = _load_meta(hub_path, ws_name)
    clean: list[str] = []
    for x in members:
        s = str(x).strip().lower()
        if s and s not in clean:
            clean.append(s)
    if clean:
        meta["members"] = clean
    else:
        meta.pop("members", None)
    p.write_text(yaml.safe_dump(meta, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return clean


def can_access(hub_path: Path, ws_name: str, user_slug: str | None, role: str | None) -> bool:
    """owner/admin → sempre; member → solo se in members[]. role=None (personal) → True."""
    if role is None or role in ("owner", "admin"):
        return True
    if not user_slug:
        return False
    return user_slug in workspace_members(hub_path, ws_name)


def accessible_workspaces(hub_path: Path, ws_names: list[str], user_slug: str | None, role: str | None) -> list[str]:
    """Sottoinsieme di `ws_names` accessibile all'utente (per il filtro registry)."""
    if role is None or role in ("owner", "admin"):
        return list(ws_names)
    if not user_slug:
        return []
    return [w for w in ws_names if user_slug in workspace_members(hub_path, w)]
