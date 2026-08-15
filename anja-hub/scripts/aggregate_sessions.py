#!/usr/bin/env python3
"""
aggregate_sessions.py — aggrega session journal cross-progetto.

Itera tutti i `projects/<name>/wiki/sessions/*.md` registrati nel hub
e genera `sessions/index.md` con timeline cronologica:

  ## YYYY-MM-DD
  - [[<project>/wiki/sessions/<date>]] — **<project>**: <summary>

Eseguito on-demand (`/anja-aggregate-sessions`) o automaticamente dopo
`/anja-sync` se l'integrazione è abilitata.
"""

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
SESSION_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")
SUMMARY_LINE_RE = re.compile(r"^\s*Summary:\s*(.+)$", re.M)


def load_registry(hub_root: Path):
    path = hub_root / "config" / "projects.json"
    if not path.is_file():
        sys.exit(f"ERROR: registry not found: {path}")
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def append_log(hub_root: Path, line: str) -> None:
    log_path = hub_root / "cross" / "log.md"
    if not log_path.is_file():
        return
    with log_path.open("a", encoding="utf-8") as f:
        f.write("\n" + line + "\n")


def extract_summary(text: str, max_chars: int = 120) -> str:
    """Try to extract a 1-line summary from a session file."""
    body = FRONTMATTER_RE.sub("", text, count=1).strip()
    m = SUMMARY_LINE_RE.search(body)
    if m:
        return m.group(1).strip()[:max_chars]
    for line in body.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        return line[:max_chars]
    return "(no summary)"


def collect_sessions(hub_root: Path, registry: dict) -> list:
    sessions = []
    for proj in registry["projects"]:
        name = proj["name"]
        sessions_dir = hub_root / "projects" / name / "wiki" / "sessions"
        if not sessions_dir.is_dir():
            continue
        for f in sorted(sessions_dir.glob("*.md")):
            if not SESSION_DATE_RE.match(f.name):
                continue
            text = f.read_text(encoding="utf-8")
            sessions.append({
                "date": f.stem,
                "project": name,
                "summary": extract_summary(text),
            })
    return sessions


def preserve_created(existing_path: Path) -> str:
    """Preserve `created:` from existing index.md if present."""
    if not existing_path.is_file():
        return date.today().isoformat()
    text = existing_path.read_text(encoding="utf-8")
    m = re.search(r'^\s*created:\s*"?(\d{4}-\d{2}-\d{2})', text, re.M)
    if m:
        return m.group(1)
    return date.today().isoformat()


def render_index(sessions: list, created: str) -> str:
    today = date.today().isoformat()
    lines = [
        "---",
        "title: Sessions Aggregate",
        "type: index",
        f'created: "{created}"',
        f'updated: "{today}"',
        "---",
        "",
        "# Sessions aggregate",
        "",
        "Timeline cross-progetto delle sessioni di lavoro. Aggiornato da `aggregate_sessions.py`.",
        "",
    ]
    if not sessions:
        lines.append("_(nessuna session journal ancora — gli hook SessionStart/SessionEnd di `anja` popolano i `wiki/sessions/` dei singoli progetti quando lavori in essi via Claude Code)_")
        return "\n".join(lines) + "\n"

    grouped = {}
    for s in sessions:
        grouped.setdefault(s["date"], []).append(s)

    for d in sorted(grouped.keys(), reverse=True):
        lines.append(f"## {d}")
        for s in grouped[d]:
            lines.append(f"- [[{s['project']}/wiki/sessions/{s['date']}]] — **{s['project']}**: {s['summary']}")
        lines.append("")

    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser(description="Aggregate session journals across hub projects.")
    p.add_argument("--hub", required=True, help="path to hub directory")
    args = p.parse_args()

    hub = Path(args.hub).resolve()
    if not hub.is_dir():
        sys.exit(f"ERROR: hub not found: {hub}")

    registry = load_registry(hub)
    sessions = collect_sessions(hub, registry)

    out = hub / "sessions" / "index.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    created = preserve_created(out)
    content = render_index(sessions, created)
    out.write_text(content, encoding="utf-8")

    today = date.today().isoformat()
    append_log(
        hub,
        f"## [{today}] cross-rebuild | sessions aggregate ({len(sessions)} sessioni cross-progetto)",
    )

    print(f"[anja-hub] sessions aggregate aggiornato: {out}")
    print(f"  Progetti scansionati: {len(registry['projects'])}")
    print(f"  Sessioni trovate:     {len(sessions)}")
    if sessions:
        dates = sorted({s['date'] for s in sessions})
        print(f"  Range date:           {dates[0]} → {dates[-1]}")


if __name__ == "__main__":
    main()
