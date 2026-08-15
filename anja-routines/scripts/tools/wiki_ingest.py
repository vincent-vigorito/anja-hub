"""
wiki_ingest.py — scrive output di una routine come "raw" in un progetto registrato
nel hub, opzionalmente invocando /anja-ingest dopo la write.

Config:
    type: wiki_ingest
    target_project: research-engine
    raw_subdir: news-daily
    auto_ingest: true                 # opzionale, default false
    filename: "{name}-{date}.md"      # opzionale, default: <routine>-<date>.md
"""

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional


def _resolve_project(name: str, hub: Path) -> Optional[Path]:
    """Stessa logica del runner ma duplicata qui per non avere import circolari pesanti."""
    reg = hub / "registry" / "hub.json"
    if reg.is_file():
        try:
            data = json.loads(reg.read_text(encoding="utf-8"))
            for proj in data.get("projects", []):
                if proj.get("name") == name:
                    p = Path(proj["path"]).expanduser()
                    if p.is_dir():
                        return p
        except Exception:
            pass
    pl = hub / "projects" / name
    if pl.is_dir():
        return pl.resolve()
    return None


def ingest(cfg: dict, body: str, hub: Path) -> dict:
    target = cfg.get("target_project")
    if not target:
        return {"status": "failed", "details": "missing target_project"}
    proj = _resolve_project(target, hub)
    if proj is None:
        return {"status": "failed", "details": f"project '{target}' not found"}

    raw_subdir = cfg.get("raw_subdir", "routines")
    raw_dir = proj / ".anjawiki" / "raw" / raw_subdir
    raw_dir.mkdir(parents=True, exist_ok=True)

    date = datetime.now().strftime("%Y-%m-%d")
    fname_tpl = cfg.get("filename", "{date}.md")
    fname = fname_tpl.replace("{date}", date)
    out = raw_dir / fname

    # se file esiste già nello stesso giorno → aggiungi suffisso ora
    if out.exists():
        out = raw_dir / fname.replace(".md", f"-{datetime.now().strftime('%H%M%S')}.md")

    header = f"---\nsource: anja-routine\ndate: {datetime.now().isoformat()}\n---\n\n"
    out.write_text(header + body, encoding="utf-8")

    details = f"wrote {out.relative_to(proj)}"

    if cfg.get("auto_ingest"):
        # invoca /anja-ingest via claude se disponibile (best-effort, non blocca)
        # MVP: solo nota nel log; integrazione full quando avremo CLI claude
        details += " (auto_ingest: pending — invocare manualmente /anja-ingest)"

    return {"status": "success", "details": details}
