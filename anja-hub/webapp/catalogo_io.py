"""catalogo_io.py — catalogo contenuti del sito (workspace marketing).

Legge `<ws>/data/catalogo/{articoli,pagine,prodotti}.md` — tabelle markdown
RIGENERABILI (ID | Titolo | Slug | Stato | URL) prodotte dal sync col CMS.
Le rende strutturate per la vista Catalogo del workspace. Decodifica le entità
HTML nei titoli (es. `&#8217;` → ').

NB: distinto dal "Marketplace" (galleria blueprint, hub-level). Questo è
l'inventario dei contenuti DI QUESTO sito.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

_KINDS = ("articoli", "pagine", "prodotti")


def _clean(s: str) -> str:
    return html.unescape(re.sub(r"`", "", s or "")).strip()


def _parse_table(text: str) -> list[dict]:
    items, seen_header = [], False
    for ln in text.splitlines():
        if "|" not in ln:
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        joined = " ".join(cells).lower()
        if "titolo" in joined and "slug" in joined:
            seen_header = True
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        if not seen_header:
            continue
        items.append({
            "id": cells[0].strip(),
            "title": _clean(cells[1]) or "(senza titolo)",
            "slug": _clean(cells[2]),
            "status": cells[3].strip().lower(),
            "url": cells[4].strip(),
        })
    return items


def _generated(text: str) -> str:
    m = re.search(r"[Gg]enerato\s+([\d-]+(?:\s+[\d:]+)?)", text)
    return m.group(1) if m else ""


def content_stats(catalogo_dir: Path) -> dict:
    """Conteggi sui contenuti del sito (per la sezione Contenuti delle Statistiche):
    totale + pubblicati + per-tipo. Stato 'publish' = pubblicato."""
    cat = read_catalogo(catalogo_dir)
    out = {"total": 0, "published": 0, "by_kind": {}}
    # 'publish/future' = WP; 'pubblicato/live/online' = SwerpiCommerce (vocabolario it)
    _PUB = {"publish", "future", "pubblicato", "live", "online"}
    for kind, items in cat.get("kinds", {}).items():
        pub = sum(1 for it in items if str(it.get("status", "")).strip().lower() in _PUB)
        out["by_kind"][kind] = {"total": len(items), "published": pub}
        out["total"] += len(items)
        out["published"] += pub
    out["draft"] = out["total"] - out["published"]
    out["generated"] = cat.get("generated", "")
    return out


def read_catalogo(catalogo_dir: Path) -> dict:
    """Ritorna {exists, generated, kinds:{articoli:[...],pagine:[...],prodotti:[...]}}.
    Include solo i kind con un file presente."""
    d = Path(catalogo_dir)
    out = {"exists": d.is_dir(), "generated": "", "kinds": {}}
    if not d.is_dir():
        return out
    for kind in _KINDS:
        f = d / f"{kind}.md"
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        out["kinds"][kind] = _parse_table(text)
        if not out["generated"]:
            out["generated"] = _generated(text)
    return out
