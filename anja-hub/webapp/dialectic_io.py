"""dialectic_io.py — I/O helpers per Dialectic Memory (Fase 14).

Schema `<slug>-dialectic.md`:
    ---
    slug: vincent
    scope: hub | project:<name>
    updated: ISO8601
    ---

    # Rolling observations

    ## Active
    - [N sightings · last YYYY-MM-DD · sessions: A,B,C] <text> #<tag>

    ## Promoted to USER.md
    - YYYY-MM-DD → "<text>" (reason: N sightings, M sessions)

    ## Decayed
    - YYYY-MM-DD → "<text>" (no sightings in N turns)

    ## Never promote
    - <text>

Stdlib only.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional


SECTION_HEADERS = ("Active", "Promoted to USER.md", "Decayed", "Never promote")
_OBS_RE = re.compile(
    r"^-\s*\[(\d+)\s+sightings?\s*·\s*last\s+([\d-]+)(?:\s*·\s*sessions?:\s*([^\]]+))?\]\s+(.+?)(?:\s+#(\w[\w-]*))?\s*$"
)


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def empty_dialectic(slug: str, scope: str) -> dict:
    return {
        "frontmatter": {"slug": slug, "scope": scope, "updated": _now_iso()},
        "active": [],         # [{text, tag, sightings, last_seen, sessions: [session_id, ...]}]
        "promoted": [],       # [{date, text, reason}]
        "decayed": [],        # [{date, text, reason}]
        "never_promote": [],  # [text, ...]
    }


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Estrae frontmatter YAML semplice (key: value per riga). Ritorna (dict, rest)."""
    fm = {}
    if not text.startswith("---\n"):
        return fm, text
    end = text.find("\n---", 4)
    if end < 0:
        return fm, text
    block = text[4:end].strip()
    for line in block.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    rest = text[end + 4 :].lstrip("\n")
    return fm, rest


def _serialize_frontmatter(fm: dict) -> str:
    out = ["---"]
    for k, v in fm.items():
        out.append(f"{k}: {v}")
    out.append("---")
    return "\n".join(out) + "\n"


def _split_sections(body: str) -> dict:
    """Spezza il body in sezioni per ## header."""
    sections: dict[str, list[str]] = {}
    current = None
    for line in body.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            current = m.group(1).strip()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def _parse_active(section_text: str) -> list[dict]:
    items: list[dict] = []
    for line in section_text.splitlines():
        line = line.strip()
        if not line or not line.startswith("-"):
            continue
        m = _OBS_RE.match(line)
        if not m:
            continue
        sightings = int(m.group(1))
        last_seen = m.group(2)
        sessions_raw = (m.group(3) or "").strip()
        text = m.group(4).strip()
        tag = m.group(5) or ""
        sessions = [s.strip() for s in sessions_raw.split(",") if s.strip()] if sessions_raw else []
        items.append({
            "text": text,
            "tag": tag,
            "sightings": sightings,
            "last_seen": last_seen,
            "sessions": sessions,
        })
    return items


def _parse_dated_list(section_text: str) -> list[dict]:
    """Parsa righe tipo: - YYYY-MM-DD → "text" (reason)"""
    items: list[dict] = []
    pat = re.compile(r'^-\s*([\d-]+)\s*→\s*"([^"]+)"\s*(?:\(([^)]*)\))?\s*$')
    for line in section_text.splitlines():
        line = line.strip()
        if not line.startswith("-"):
            continue
        m = pat.match(line)
        if not m:
            continue
        items.append({
            "date": m.group(1),
            "text": m.group(2),
            "reason": (m.group(3) or "").strip(),
        })
    return items


def _parse_simple_list(section_text: str) -> list[str]:
    """Parsa righe tipo: - <text>"""
    out: list[str] = []
    for line in section_text.splitlines():
        line = line.strip()
        if not line.startswith("-"):
            continue
        rest = line[1:].strip()
        if rest:
            out.append(rest)
    return out


def read_dialectic(path: Path) -> dict:
    """Legge file dialectic, ritorna dict strutturato."""
    if not path or not path.is_file():
        return empty_dialectic(slug="unknown", scope="hub")
    raw = path.read_text(encoding="utf-8", errors="replace")
    fm, body = _parse_frontmatter(raw)
    sections = _split_sections(body)
    return {
        "frontmatter": fm,
        "active": _parse_active(sections.get("Active", "")),
        "promoted": _parse_dated_list(sections.get("Promoted to USER.md", "")),
        "decayed": _parse_dated_list(sections.get("Decayed", "")),
        "never_promote": _parse_simple_list(sections.get("Never promote", "")),
    }


def _fmt_active(obs: dict) -> str:
    sightings = int(obs.get("sightings", 1))
    last_seen = obs.get("last_seen") or _today()
    sessions = obs.get("sessions") or []
    text = obs.get("text", "").strip()
    tag = obs.get("tag", "").strip()
    sessions_part = f" · sessions: {','.join(sessions[:5])}" if sessions else ""
    tag_part = f" #{tag}" if tag else ""
    return f"- [{sightings} sightings · last {last_seen}{sessions_part}] {text}{tag_part}"


def _fmt_dated(item: dict) -> str:
    date = item.get("date", _today())
    text = item.get("text", "").replace('"', "'")
    reason = item.get("reason", "")
    reason_part = f" ({reason})" if reason else ""
    return f'- {date} → "{text}"{reason_part}'


def write_dialectic(path: Path, data: dict) -> None:
    """Serializza data → file md."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = dict(data.get("frontmatter") or {})
    fm["updated"] = _now_iso()
    if "slug" not in fm:
        fm["slug"] = "unknown"
    if "scope" not in fm:
        fm["scope"] = "hub"
    lines = [_serialize_frontmatter(fm)]
    lines.append("# Rolling observations\n")

    # Active
    lines.append("## Active")
    active = data.get("active") or []
    if not active:
        lines.append("_(none)_")
    else:
        # Sort by sightings desc + last_seen desc
        active_sorted = sorted(
            active,
            key=lambda o: (int(o.get("sightings", 1)), o.get("last_seen", "")),
            reverse=True,
        )
        for o in active_sorted:
            lines.append(_fmt_active(o))
    lines.append("")

    # Promoted
    lines.append("## Promoted to USER.md")
    promoted = data.get("promoted") or []
    if not promoted:
        lines.append("_(none)_")
    else:
        for p in sorted(promoted, key=lambda x: x.get("date", ""), reverse=True):
            lines.append(_fmt_dated(p))
    lines.append("")

    # Decayed
    lines.append("## Decayed")
    decayed = data.get("decayed") or []
    if not decayed:
        lines.append("_(none)_")
    else:
        for d in sorted(decayed, key=lambda x: x.get("date", ""), reverse=True)[:50]:  # cap
            lines.append(_fmt_dated(d))
    lines.append("")

    # Never promote
    lines.append("## Never promote")
    never = data.get("never_promote") or []
    if not never:
        lines.append("_(none)_")
    else:
        for n in never:
            lines.append(f"- {n}")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


# ====================================================================
# Operations
# ====================================================================

def _fuzzy_match(text: str, active: list, embedder, threshold: float):
    """Ritorna l'observation attiva semanticamente più simile a `text` con cosine >=
    threshold, altrimenti None. Embed on-the-fly (le active sono poche). Graceful: se
    l'embedding fallisce ritorna None → si ricade sul match esatto/nuovo."""
    texts = [o.get("text", "") for o in active]
    if not texts:
        return None
    try:
        from embeddings import cosine
        vecs = embedder([text] + texts)
    except Exception:
        return None
    if not vecs or len(vecs) != len(texts) + 1:
        return None
    qv, ovs = vecs[0], vecs[1:]
    best, best_sim = None, threshold
    for o, v in zip(active, ovs):
        s = cosine(qv, v)
        if s >= best_sim:
            best, best_sim = o, s
    return best


def add_observation(path: Path, text: str, tag: str = "", session_id: str = "",
                    slug: str = "", scope: str = "hub",
                    embedder=None, sim_threshold: float = 0.90) -> dict:
    """Upsert observation: se text già presente, incrementa sightings + aggiorna last_seen + append session.

    Match per text esatto (case-insensitive, trim). Se `embedder` è fornito (opt-in),
    fa anche un FUZZY match semantico: una riformulazione dello stesso concetto rinforza
    l'observation esistente invece di crearne una quasi-duplicata. Graceful senza embedder.

    ⚠ Soglia CONSERVATIVA (0.90): validazione reale su text-embedding-3-small mostra che
    una preferenza OPPOSTA sullo stesso tema ("risposte brevi" vs "dettagliate" ≈ 0.88) è
    più simile di una vera parafrasi (≈ 0.81) — l'embedding cattura il tema, non l'equivalenza.
    Soglia alta = solo riformulazioni quasi-identiche (mai fondere contraddizioni). Un dedup
    di parafrasi profonde richiederebbe un LLM-judge di conferma (non ancora implementato).
    """
    if not text or not text.strip():
        return {}
    if path.is_file():
        data = read_dialectic(path)
        if not data.get("frontmatter"):
            data["frontmatter"] = {"slug": slug, "scope": scope}
    else:
        data = empty_dialectic(slug=slug or "unknown", scope=scope)

    # Anti-pattern guard
    never = [n.lower().strip() for n in (data.get("never_promote") or [])]
    if text.lower().strip() in never:
        return {"skipped": True, "reason": "in never_promote"}

    text_norm = text.strip()
    active = data.get("active") or []
    found = None
    for o in active:
        if o.get("text", "").strip().lower() == text_norm.lower():
            found = o
            break
    # fuzzy semantico (opt-in): nessun match esatto → cerca una riformulazione affine
    if found is None and embedder is not None and active:
        found = _fuzzy_match(text_norm, active, embedder, sim_threshold)

    today = _today()
    if found:
        found["sightings"] = int(found.get("sightings", 1)) + 1
        found["last_seen"] = today
        if session_id and session_id not in (found.get("sessions") or []):
            found.setdefault("sessions", []).append(session_id)
        if tag and not found.get("tag"):
            found["tag"] = tag
        op = "reinforced"
    else:
        active.append({
            "text": text_norm,
            "tag": tag,
            "sightings": 1,
            "last_seen": today,
            "sessions": [session_id] if session_id else [],
        })
        data["active"] = active
        op = "new"

    write_dialectic(path, data)
    return {"op": op, "text": text_norm}


def decay_observations(path: Path, max_age_days: int = 50) -> int:
    """Sposta observations vecchie (last_seen > max_age_days) da active a decayed.

    Returns count moved.
    """
    if not path.is_file():
        return 0
    data = read_dialectic(path)
    active = data.get("active") or []
    today = datetime.now().date()
    moved = 0
    new_active = []
    decayed = list(data.get("decayed") or [])
    for o in active:
        try:
            ls = datetime.strptime(o.get("last_seen", ""), "%Y-%m-%d").date()
            age = (today - ls).days
        except Exception:
            age = 0
        if age > max_age_days and int(o.get("sightings", 1)) < 3:
            decayed.append({
                "date": _today(),
                "text": o.get("text", ""),
                "reason": f"no sightings in {age} days",
            })
            moved += 1
        else:
            new_active.append(o)
    if moved:
        data["active"] = new_active
        data["decayed"] = decayed
        write_dialectic(path, data)
    return moved


def find_user_files(hub_path: Path, project_path: Optional[Path], slug: str) -> dict:
    """Risolve i path canonici per HOT + DETAIL + DIALECTIC su hub + project.

    Returns: {
      hub_hot, hub_detail, hub_dialectic, hub_dir,
      project_hot, project_detail, project_dialectic, project_dir
    }
    Project keys are None se project_path is None.
    """
    out = {
        "hub_dir": hub_path / "users" if hub_path else None,
        "hub_hot": (hub_path / "users" / f"{slug}.md") if hub_path else None,
        "hub_detail": (hub_path / "users" / f"{slug}-detail.md") if hub_path else None,
        "hub_dialectic": (hub_path / "users" / f"{slug}-dialectic.md") if hub_path else None,
        "project_dir": None,
        "project_hot": None,
        "project_detail": None,
        "project_dialectic": None,
    }
    if project_path:
        users_dir = project_path / ".anjawiki" / "users"
        out["project_dir"] = users_dir
        out["project_hot"] = users_dir / f"{slug}.md"
        out["project_detail"] = users_dir / f"{slug}-detail.md"
        out["project_dialectic"] = users_dir / f"{slug}-dialectic.md"
    return out


def top_active(path: Path, n: int = 5) -> list[dict]:
    """Ritorna top-N observations attive (per sightings desc), per context injection."""
    if not path or not path.is_file():
        return []
    data = read_dialectic(path)
    active = data.get("active") or []
    return sorted(active, key=lambda o: int(o.get("sightings", 1)), reverse=True)[:n]


def format_active_for_context(observations: list[dict]) -> str:
    """Formatta una lista di obs per iniezione in context (compatto, no metadata)."""
    if not observations:
        return ""
    lines = []
    for o in observations:
        text = o.get("text", "").strip()
        tag = o.get("tag", "")
        sightings = int(o.get("sightings", 1))
        marker = "⚪" if sightings == 1 else ("🔵" if sightings == 2 else "🟢")
        tag_part = f" [{tag}]" if tag else ""
        lines.append(f"- {marker} {text}{tag_part}")
    return "\n".join(lines)


def add_to_never_promote(path: Path, text: str, slug: str = "", scope: str = "hub") -> None:
    if not text or not text.strip():
        return
    if path.is_file():
        data = read_dialectic(path)
    else:
        data = empty_dialectic(slug, scope)
    never = data.get("never_promote") or []
    if text.strip() not in never:
        never.append(text.strip())
        data["never_promote"] = never
    # Rimuovi anche da active se presente
    active = data.get("active") or []
    data["active"] = [o for o in active if o.get("text", "").lower().strip() != text.lower().strip()]
    write_dialectic(path, data)


def remove_from_never_promote(path: Path, text: str) -> None:
    if not path.is_file():
        return
    data = read_dialectic(path)
    never = data.get("never_promote") or []
    data["never_promote"] = [n for n in never if n.strip().lower() != text.strip().lower()]
    write_dialectic(path, data)


def restore_decayed(path: Path, text: str) -> bool:
    if not path.is_file():
        return False
    data = read_dialectic(path)
    decayed = data.get("decayed") or []
    target = None
    for d in decayed:
        if d.get("text", "").strip().lower() == text.strip().lower():
            target = d
            break
    if not target:
        return False
    data["decayed"] = [d for d in decayed if d is not target]
    active = data.get("active") or []
    active.append({
        "text": target["text"],
        "tag": "",
        "sightings": 1,
        "last_seen": _today(),
        "sessions": [],
    })
    data["active"] = active
    write_dialectic(path, data)
    return True


def revert_promoted(path: Path, text: str, user_md_path: Optional[Path] = None) -> bool:
    """Sposta da Promoted a Never promote. Rimuove riga da USER.md se path fornito."""
    if not path.is_file():
        return False
    data = read_dialectic(path)
    promoted = data.get("promoted") or []
    found = None
    for p in promoted:
        if p.get("text", "").strip().lower() == text.strip().lower():
            found = p
            break
    if not found:
        return False
    data["promoted"] = [p for p in promoted if p is not found]
    never = data.get("never_promote") or []
    if text.strip() not in never:
        never.append(text.strip())
    data["never_promote"] = never
    write_dialectic(path, data)
    # Rimuovi da USER.md
    if user_md_path and user_md_path.is_file():
        try:
            content = user_md_path.read_text(encoding="utf-8")
            # Match riga con marker auto-promoted + text
            pattern = re.compile(
                r"<!-- auto-promoted [\d-]+ -->\s*\n.*?" + re.escape(text) + r".*?\n",
                re.DOTALL,
            )
            new_content = pattern.sub("", content)
            if new_content != content:
                user_md_path.write_text(new_content, encoding="utf-8")
        except Exception as e:
            print(f"[dialectic_io] revert user.md error: {e}")
    return True


if __name__ == "__main__":
    # Smoke test
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tf:
        p = Path(tf.name)
    print(f"Test path: {p}")
    add_observation(p, "preferisce risposte schematiche", tag="style", session_id="s1", slug="test")
    add_observation(p, "preferisce risposte schematiche", tag="style", session_id="s2", slug="test")
    add_observation(p, "frustrato da modal di conferma", tag="ux", session_id="s1", slug="test")
    d = read_dialectic(p)
    print(json.dumps(d, indent=2, ensure_ascii=False, default=str))
    print(f"\nFile content:\n{p.read_text()}")
    p.unlink()
