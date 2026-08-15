"""session_mirror.py — mirror webapp/Telegram conversations into hub sessions journal (P1).

Trasforma ogni conversation gestita dalla webapp/Telegram in un session file canonico
nel filesystem (`<hub>/sessions/<date>/<id>.md` o equivalente per agent/project), così
da renderle visibili a:
  - `sessions.list` MCP tool
  - `anja-aggregate-sessions`
  - context_loader WARM (richiama session passate in nuove chat)
  - hook session_end pattern (compatibile)

Rate-limited (30s) per evitare write storm su typing fast / vocali rapidi.
Forza override su compact (writes summary).

Stdlib only.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# Rate limiting (in-memory; ok per single-process uvicorn)
_LAST_MIRROR_TS: dict[str, float] = {}
DEFAULT_RATE_LIMIT_SEC = 30


def _slugify(s: str, maxlen: int = 40) -> str:
    s = re.sub(r"[^\w-]+", "-", s.lower()).strip("-")
    return s[:maxlen] or "untitled"


def _stable_short_id(conv_id: str) -> str:
    """Hash deterministico per conv_id → 4 char hex (stabile cross-process)."""
    import hashlib
    return hashlib.md5(conv_id.encode("utf-8")).hexdigest()[:4]


def _fmt_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")


def _compose_session_filename(conv_id: str, started_ts: float) -> str:
    """Format: <HHMMSS>-<source>-<short_id>.md (stabile per conv_id)."""
    hh = datetime.fromtimestamp(started_ts).strftime("%H%M%S")
    short = _stable_short_id(conv_id)
    if conv_id.startswith("telegram-"):
        cid = conv_id.split("-", 1)[1][:12]
        return f"{hh}-telegram-{cid}-{short}.md"
    if conv_id.startswith("voice-"):
        return f"{hh}-webapp-voice-{short}.md"
    return f"{hh}-webapp-{short}.md"


def _resolve_target_dir(hub_path: Path, scope: str, started_ts: float,
                       projects: Optional[list] = None) -> Optional[Path]:
    """Risolvi la dir di destinazione del session file in base allo scope.

    - scope='hub' o vuoto → <hub>/sessions/<date>/
    - scope='agent:<name>' → <hub>/agents/<name>/sessions/<date>/
    - scope='project:<name>' → <project_root>/.anjawiki/wiki/sessions/<date>/
    """
    date_str = datetime.fromtimestamp(started_ts).strftime("%Y-%m-%d")
    if not scope or scope == "hub":
        return hub_path / "sessions" / date_str
    if scope.startswith("agent:"):
        agent_name = scope.split(":", 1)[1]
        return hub_path / "agents" / agent_name / "sessions" / date_str
    if scope.startswith("project:"):
        proj_name = scope.split(":", 1)[1]
        if projects:
            for p in projects:
                if p.get("name") == proj_name:
                    loc = (p.get("location") or {})
                    if loc.get("kind") == "local" and loc.get("path"):
                        return Path(loc["path"]) / ".anjawiki" / "wiki" / "sessions" / date_str
        return None
    return hub_path / "sessions" / date_str


def _extract_started_ts(conv_data: dict) -> float:
    """Best-effort started timestamp dalla conv. Fallback: now."""
    # Telegram messages don't have per-message timestamp; use compacted_at se presente,
    # altrimenti file mtime non disponibile qui. Usa first message implicit (now).
    # Per coerenza: usiamo `started_at` se presente, altrimenti `compacted_at`, else now
    for key in ("started_at", "compacted_at", "created_at"):
        v = conv_data.get(key)
        if v:
            try:
                return float(v)
            except Exception:
                pass
    return time.time()


def _build_body(conv_data: dict) -> tuple[str, dict]:
    """Costruisce body markdown del session file + statistiche.

    Returns (body, stats) dove stats ha messages_user/messages_assistant counts.
    """
    msgs = conv_data.get("messages") or []
    n_user = sum(1 for m in msgs if m.get("role") == "user")
    n_asst = sum(1 for m in msgs if m.get("role") in ("claude", "assistant"))

    # Summary: usa compact_summary se presente, altrimenti primi 300 char user msg
    summary = conv_data.get("compact_summary") or ""
    if not summary:
        for m in msgs:
            if m.get("role") == "user":
                summary = (m.get("content") or "")[:300]
                break

    lines = []
    lines.append("## Summary")
    lines.append("")
    lines.append(summary or "_(no summary yet)_")
    lines.append("")
    lines.append("## Conversation")
    lines.append("")
    for m in msgs[-50:]:  # cap a ultimi 50 msg nel body per non gonfiare file
        role = m.get("role", "?")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        label = {"user": "USER", "claude": "ANJA", "assistant": "ANJA", "system": "SYSTEM"}.get(role, role.upper())
        lines.append(f"### [{label}]")
        lines.append("")
        lines.append(content)
        lines.append("")
    body = "\n".join(lines)

    return body, {"messages_user": n_user, "messages_assistant": n_asst}


def _build_frontmatter(conv_data: dict, conv_id: str, scope: str, source: str,
                      started_ts: float, file_id: str, stats: dict) -> str:
    fm = ["---"]
    fm.append(f"id: {file_id}")
    fm.append(f"type: session")
    fm.append(f"source: {source}")
    fm.append(f"conv_id: {conv_id}")
    if conv_id.startswith("telegram-"):
        cid = conv_id.split("-", 1)[1]
        try:
            fm.append(f"chat_id: {int(cid)}")
        except ValueError:
            pass

    fm.append(f"scope: {scope or 'hub'}")
    if scope and scope.startswith(("agent:", "project:")):
        fm.append(f"target: {scope.split(':', 1)[1]}")
    fm.append(f"date: {datetime.fromtimestamp(started_ts).strftime('%Y-%m-%d')}")
    fm.append(f"started: {_fmt_iso(started_ts)}")
    fm.append(f"updated: {_fmt_iso(time.time())}")
    if conv_data.get("provider"):
        fm.append(f"provider: {conv_data['provider']}")
    if conv_data.get("model"):
        fm.append(f"model: {conv_data['model']}")
    fm.append(f"messages_user: {stats['messages_user']}")
    fm.append(f"messages_assistant: {stats['messages_assistant']}")
    if conv_data.get("last_usage"):
        u = conv_data["last_usage"]
        fm.append(f"last_input_tokens: {u.get('input_tokens', 0)}")
        fm.append(f"context_window: {u.get('context_window', 0)}")
    if conv_data.get("compacted_at"):
        fm.append(f"compacted_at: {_fmt_iso(float(conv_data['compacted_at']))}")
        fm.append(f"compacted_from_count: {conv_data.get('compacted_from_count', 0)}")
    fm.append(f"agent: {source}")  # generic source tag (compat con CC schema 'agent: cli-claude')
    fm.append("---")
    return "\n".join(fm)


def mirror_conversation(conv_id: str, conv_data: dict, hub_path: Path,
                        projects: Optional[list] = None,
                        force: bool = False) -> Optional[Path]:
    """Scrive/aggiorna il session file per una conversation.

    Rate-limited a 30s per conv_id (skip se force=False e già scritto recente).
    Force=True dopo compact_conversation o a hang-up call.

    Returns path scritto o None se skip.
    """
    if not conv_data:
        return None

    # Rate limit
    now = time.time()
    if not force:
        last = _LAST_MIRROR_TS.get(conv_id, 0)
        if (now - last) < DEFAULT_RATE_LIMIT_SEC:
            return None

    # Source detection
    if conv_id.startswith("telegram-"):
        source = "telegram"
    elif conv_id.startswith("voice-"):
        source = "webapp-voice"
    else:
        source = "webapp"

    started_ts = _extract_started_ts(conv_data)
    scope = conv_data.get("scope", "hub")
    target_dir = _resolve_target_dir(hub_path, scope, started_ts, projects)
    if not target_dir:
        return None

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"[session_mirror] mkdir failed: {e}")
        return None

    filename = _compose_session_filename(conv_id, started_ts)
    target_path = target_dir / filename
    file_id = filename.rsplit(".", 1)[0]

    body, stats = _build_body(conv_data)
    fm = _build_frontmatter(conv_data, conv_id, scope, source, started_ts, file_id, stats)
    full = fm + "\n\n# Session " + file_id + "\n\n" + body + "\n"

    try:
        target_path.write_text(full, encoding="utf-8")
        _LAST_MIRROR_TS[conv_id] = now
        return target_path
    except Exception as e:
        print(f"[session_mirror] write failed: {e}")
        return None


def mirror_from_file(conv_id: str, webapp_dir: Path, hub_path: Path,
                    projects: Optional[list] = None,
                    force: bool = False) -> Optional[Path]:
    """Wrapper: carica conv da `<webapp>/conversations/{id}.json` e mirra."""
    conv_path = webapp_dir / "conversations" / f"{conv_id}.json"
    if not conv_path.is_file():
        return None
    try:
        conv_data = json.loads(conv_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return mirror_conversation(conv_id, conv_data, hub_path, projects, force=force)
