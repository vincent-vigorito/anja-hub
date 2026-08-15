"""
file.py — scrittura su filesystem.

Config:
    type: file
    path: "/abs/or/relative/path.md"
    mode: append | overwrite             # default overwrite
    template: "## {date}\n\n{body}"      # opzionale wrapper

Path relativi sono risolti rispetto al hub.
"""

from datetime import datetime
from pathlib import Path


def _render(body: str, template: str) -> str:
    return (
        template
        .replace("{body}", body)
        .replace("{date}", datetime.now().strftime("%Y-%m-%d"))
        .replace("{datetime}", datetime.now().isoformat())
    )


def write_file(cfg: dict, body: str, hub: Path) -> dict:
    raw_path = cfg.get("path")
    if not raw_path:
        return {"status": "failed", "details": "missing 'path'"}
    p = Path(raw_path).expanduser()
    if not p.is_absolute():
        p = hub / p
    # Confina all'hub: niente path assoluti fuori, niente ../ che escono — una routine
    # manomessa/injection non deve scrivere su ~/.zshrc o ~/.ssh (F-Sec-RoutineFilePathConfine).
    try:
        p.resolve().relative_to(hub.resolve())
    except ValueError:
        return {"status": "failed", "details": f"path fuori dall'hub non consentito: {raw_path}"}

    p.parent.mkdir(parents=True, exist_ok=True)

    template = cfg.get("template")
    text = _render(body, template) if template else body

    mode = cfg.get("mode", "overwrite")
    if mode == "append":
        sep = "\n\n---\n\n" if p.exists() and p.stat().st_size > 0 else ""
        with p.open("a", encoding="utf-8") as f:
            f.write(sep + text + ("\n" if not text.endswith("\n") else ""))
    else:
        p.write_text(text, encoding="utf-8")

    return {"status": "success", "details": f"{mode} → {p}"}


def write_hub_page(cfg: dict, body: str, hub: Path) -> dict:
    """Scrive in <hub>/cross/analysis/<slug>.md."""
    slug = cfg.get("slug")
    if not slug:
        return {"status": "failed", "details": "missing 'slug'"}
    target = hub / "cross" / "analysis" / f"{slug}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    header = f"---\nsource: anja-routine\ndate: {datetime.now().isoformat()}\n---\n\n"
    target.write_text(header + body, encoding="utf-8")
    return {"status": "success", "details": f"wrote {target.relative_to(hub)}"}
