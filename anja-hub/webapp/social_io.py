"""social_io.py — post social pubblicati + engagement (Performance social organica).

Sorgente dei post: la sezione "Log pubblicazioni" del `PIANO.md` (Data | Canale |
Contenuto | Link), da cui prendiamo i post Instagram/Facebook con il loro
riferimento (permalink IG / post-id FB) → servono per Meta Insights.

L'engagement (reach/like/commenti) raccolto da Meta vive in
`<ws>/data/social_insights.json` (mappa ref→metriche), aggiornato dal collector
`meta_insights`. read_social() unisce post + engagement per la UI.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_SOCIAL_CHANNELS = ("instagram", "facebook", "linkedin")


def _clean(s: str) -> str:
    return re.sub(r"~~|\*\*|`", "", s or "").strip()


def parse_social_log(text: str) -> list[dict]:
    """Estrae i post social dalla sezione 'Log pubblicazioni' del PIANO."""
    posts, in_log, seen_header = [], False, False
    for ln in text.splitlines():
        if ln.lstrip().startswith("##"):
            in_log = "log pubblicazioni" in ln.lower()
            seen_header = False
            continue
        if not in_log or "|" not in ln:
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        joined = " ".join(cells).lower()
        if "data" in joined and "canale" in joined and "link" in joined:
            seen_header = True
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        if not seen_header:
            continue
        date, channel, content, link = cells[0], cells[1], cells[2], cells[3]
        ch = channel.strip().lower()
        if ch not in _SOCIAL_CHANNELS:
            continue
        posts.append({
            "date": date.strip(), "channel": ch, "title": _clean(content),
            "ref": link.strip(), "ref_key": _ref_key(link.strip()),
        })
    return posts


def _ref_key(ref: str) -> str:
    """Chiave stabile per il ref (shortcode IG o post-id FB)."""
    m = re.search(r"instagram\.com/p/([A-Za-z0-9_-]+)", ref)
    if m:
        return "ig:" + m.group(1)
    m = re.search(r"(\d{6,}_\d+)", ref)
    if m:
        return "fb:" + m.group(1)
    return ref[:64]


def _insights_path(data_dir: Path) -> Path:
    return Path(data_dir) / "social_insights.json"


def load_insights(data_dir: Path) -> dict:
    p = _insights_path(data_dir)
    if not p.is_file():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_insights(data_dir: Path, insights: dict) -> None:
    p = _insights_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(insights, ensure_ascii=False, indent=2), encoding="utf-8")


def read_social(data_dir: Path) -> dict:
    """Post social del log + engagement raccolto (merge). Per la UI."""
    piano = Path(data_dir) / "PIANO.md"
    posts = parse_social_log(piano.read_text(encoding="utf-8")) if piano.is_file() else []
    insights = load_insights(data_dir)
    for p in posts:
        m = insights.get(p["ref_key"]) or {}
        p["reach"] = m.get("reach")
        p["likes"] = m.get("likes")
        p["comments"] = m.get("comments")
        p["clicks"] = m.get("clicks")
        p["saves"] = m.get("saves")
        p["shares"] = m.get("shares")
    posts.sort(key=lambda p: p["date"], reverse=True)
    collected = sum(1 for p in posts if p.get("reach") is not None or p.get("likes") is not None)
    return {"posts": posts, "total": len(posts), "collected": collected,
            "updated_at": insights.get("_updated_at", "")}
