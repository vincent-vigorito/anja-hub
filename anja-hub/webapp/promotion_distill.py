"""promotion_distill.py — promozione dialectic → USER.md HOT (Fase 14).

Workflow:
  1. Read dialectic Active observations
  2. Filtra candidates: sightings>=MIN_SIGHTINGS + len(sessions)>=MIN_SESSIONS
  3. (Opzionale) LLM judge: "è preferenza stabile?"
  4. Promotion: append a USER.md body con marker `<!-- auto-promoted YYYY-MM-DD -->`
  5. Move observation a sezione `## Promoted to USER.md` del dialectic
  6. Cross-workspace distillation: pattern condivisi project N + project M → hub dialectic

Triggered da:
  - manuale via endpoint /api/dialectic/promote (force singolo)
  - automatico ogni N session-end (cron)
  - cross-workspace pass via endpoint
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import dialectic_io as dio


MIN_SIGHTINGS = 3
MIN_SESSIONS = 3
PROMOTION_MARKER_PREFIX = "<!-- auto-promoted "


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def list_candidates(dialectic_path: Path,
                     min_sightings: int = MIN_SIGHTINGS,
                     min_sessions: int = MIN_SESSIONS) -> list[dict]:
    """Ritorna lista observations che soddisfano i criteri stretti."""
    if not dialectic_path.is_file():
        return []
    data = dio.read_dialectic(dialectic_path)
    candidates = []
    for o in data.get("active", []):
        sightings = int(o.get("sightings", 1))
        sessions = o.get("sessions") or []
        if sightings >= min_sightings and len(set(sessions)) >= min_sessions:
            candidates.append({
                "text": o.get("text", ""),
                "tag": o.get("tag", ""),
                "sightings": sightings,
                "sessions": len(set(sessions)),
                "last_seen": o.get("last_seen", ""),
            })
    return candidates


def _format_promotion_line(text: str, tag: str = "") -> str:
    """Riga da appendere a USER.md body."""
    today = _today()
    tag_part = f" `#{tag}`" if tag else ""
    return f"{PROMOTION_MARKER_PREFIX}{today} -->\n- {text}{tag_part}\n"


def _ensure_user_md(user_md_path: Path, slug: str) -> None:
    """Crea USER.md vuoto con frontmatter minimo se assente."""
    if user_md_path.is_file():
        return
    user_md_path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        f"---\nslug: {slug}\ntype: user\ncreated: {_today()}\nupdated: {_today()}\n---\n\n"
        f"# {slug}\n\n"
        f"## Preferences\n\n"
        f"_(auto-populated by dialectic promotion)_\n"
    )
    user_md_path.write_text(content, encoding="utf-8")


def _append_to_user_md(user_md_path: Path, text: str, tag: str = "", slug: str = "") -> bool:
    """Append observation a USER.md body con marker auto-promoted.

    Returns True se appended, False se già presente.
    """
    _ensure_user_md(user_md_path, slug or "user")
    try:
        current = user_md_path.read_text(encoding="utf-8")
    except Exception:
        return False
    # Dedup: se la stringa è già nel file (case-insensitive match), skip
    if text.lower().strip() in current.lower():
        return False
    line = _format_promotion_line(text, tag)
    # Append in fondo
    if not current.endswith("\n"):
        current += "\n"
    current += "\n" + line
    user_md_path.write_text(current, encoding="utf-8")
    return True


def promote_observation(dialectic_path: Path, user_md_path: Path, text: str,
                         slug: str = "") -> dict:
    """Promuove una singola observation: append a USER.md + move a sezione Promoted.

    Args:
        text: testo dell'observation in Active (match case-insensitive)
    """
    if not dialectic_path.is_file():
        return {"ok": False, "reason": "dialectic file missing"}
    data = dio.read_dialectic(dialectic_path)
    target = None
    for o in data.get("active", []):
        if o.get("text", "").strip().lower() == text.strip().lower():
            target = o
            break
    if not target:
        return {"ok": False, "reason": "observation not in active"}

    # Append a USER.md
    appended = _append_to_user_md(
        user_md_path, target["text"], target.get("tag", ""),
        slug=data.get("frontmatter", {}).get("slug", slug),
    )
    if not appended:
        return {"ok": False, "reason": "already in USER.md (dedup)"}

    # Move from active to promoted
    data["active"] = [o for o in data.get("active", []) if o is not target]
    promoted = data.get("promoted") or []
    promoted.append({
        "date": _today(),
        "text": target["text"],
        "reason": f"{target.get('sightings', 1)} sightings, {len(set(target.get('sessions') or []))} sessions",
    })
    data["promoted"] = promoted
    dio.write_dialectic(dialectic_path, data)
    return {"ok": True, "text": target["text"], "tag": target.get("tag", "")}


async def llm_judge_promotion(candidate: dict, user_md_text: str = "") -> bool:
    """Optional LLM judge: 'è preferenza stabile?' (haiku call).

    Default: True (skip LLM se non vuoi cost overhead). Lo lascio implementato ma
    chiamato solo se PROMOTION_LLM_JUDGE=True.
    """
    try:
        from claude_agent_sdk import ClaudeAgentOptions, query
    except ImportError:
        return True
    prompt = (
        f"Observation candidate: \"{candidate.get('text')}\" "
        f"(tag: {candidate.get('tag', '')}, "
        f"{candidate.get('sightings')} sightings across {candidate.get('sessions')} sessions)\n\n"
        f"USER profile so far:\n{user_md_text[:1500]}\n\n"
        f"Domanda: questa observation rappresenta una PREFERENZA STABILE / pattern collaborativo "
        f"che merita di essere promosso nel profilo permanente, OPPURE è un contesto transiente?\n\n"
        f"Rispondi SOLO con 'PROMOTE' o 'SKIP' su una riga, senza spiegazioni."
    )
    full_text = ""
    try:
        options = ClaudeAgentOptions(
            system_prompt="Sei un giudice severo. Promuovi solo pattern stabili e personali. Default è SKIP.",
            model="haiku",
            permission_mode="bypassPermissions",
            allowed_tools=[],
        )
        async for message in query(prompt=prompt, options=options):
            content = getattr(message, "content", None)
            if isinstance(content, str):
                full_text += content
            elif content:
                for block in content:
                    text = getattr(block, "text", None)
                    if text:
                        full_text += text
        return "PROMOTE" in full_text.upper()
    except Exception as e:
        print(f"[promotion] LLM judge error: {e}. Defaulting to SKIP.")
        return False


async def distill_promotions(dialectic_path: Path, user_md_path: Path,
                              use_llm_judge: bool = False, slug: str = "") -> dict:
    """Esegue distillation pass: trova candidates → (opzionale LLM judge) → promuovi.

    Returns: {promoted: [...], skipped: [...], errors: []}
    """
    report = {"promoted": [], "skipped": [], "errors": []}
    candidates = list_candidates(dialectic_path)
    if not candidates:
        return report

    user_md_text = ""
    if user_md_path.is_file():
        try:
            user_md_text = user_md_path.read_text(encoding="utf-8")
        except Exception:
            pass

    for cand in candidates:
        if use_llm_judge:
            judged = await llm_judge_promotion(cand, user_md_text)
            if not judged:
                report["skipped"].append({"text": cand["text"], "reason": "LLM judged unstable"})
                continue
        result = promote_observation(dialectic_path, user_md_path, cand["text"], slug=slug)
        if result.get("ok"):
            report["promoted"].append(result)
        else:
            report["skipped"].append({"text": cand["text"], "reason": result.get("reason")})

    print(f"[promotion] distill: promoted={len(report['promoted'])} skipped={len(report['skipped'])}")
    return report


def cross_workspace_distill(hub_path: Path, projects: list, user_slug: str,
                              min_projects_agreed: int = 2) -> dict:
    """Cross-workspace: pattern presenti in >=N project dialectics → promosso a hub dialectic.

    Args:
        projects: list di project context (con location.path) — solo local
        min_projects_agreed: numero minimo di project che devono avere la stessa observation
    """
    report = {"distilled": [], "errors": []}
    obs_count: dict[str, dict] = {}  # text_lower → {text, tag, projects: [name, ...]}

    for p in projects:
        loc = p.get("location") or {}
        if loc.get("kind") != "local":
            continue
        pname = p.get("name", "")
        proot = Path(loc.get("path", "")).resolve()
        dpath = proot / ".anjawiki" / "users" / f"{user_slug}-dialectic.md"
        if not dpath.is_file():
            continue
        data = dio.read_dialectic(dpath)
        for o in data.get("active", []):
            t = o.get("text", "").strip()
            if not t:
                continue
            key = t.lower()
            entry = obs_count.setdefault(key, {"text": t, "tag": o.get("tag", ""), "projects": []})
            if pname not in entry["projects"]:
                entry["projects"].append(pname)

    hub_dialectic = hub_path / "users" / f"{user_slug}-dialectic.md"
    for key, entry in obs_count.items():
        if len(entry["projects"]) >= min_projects_agreed:
            result = dio.add_observation(
                hub_dialectic,
                text=entry["text"],
                tag=entry["tag"] or "cross",
                session_id=f"cross-{','.join(entry['projects'][:3])}",
                slug=user_slug,
                scope="hub",
            )
            if result.get("op") == "new":
                report["distilled"].append({
                    "text": entry["text"],
                    "projects": entry["projects"],
                })

    print(f"[promotion] cross-workspace: distilled={len(report['distilled'])}")
    return report
