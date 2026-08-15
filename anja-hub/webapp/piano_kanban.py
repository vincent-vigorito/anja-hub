"""piano_kanban.py — sync del piano editoriale (data/PIANO.md) → card kanban.

UNA direzione: `data/PIANO.md` è la fonte di verità (documento vivo del brand),
il kanban è la board visiva. Idempotente: ogni entry ha una chiave stabile
(`piano_key` in metadata) → il re-sync aggiorna, non duplica. Lo `status` viene
dal PIANO (re-sync ri-applica), tranne le card archiviate a mano (preservate).

Scope card = `workspace:<brand>` → compaiono nel kanban del workspace (il
frontend già filtra per scope).

Parsa due blocchi del PIANO:
  - sez. "Piano editoriale" — tabella blog `| Data | Bozza | Tipo | Target |`
  - sez. "Social" — bullet kit `✅/🔄/⏳/⬜` che referenziano `social/<slug>/`
"""

from __future__ import annotations

import re
from pathlib import Path

import kanban_io

_EMOJI_STATUS = {"✅": "done", "🔄": "running", "⏳": "ready", "⬜": "todo"}


def _clean(s: str) -> str:
    return re.sub(r"~~|\*\*|`", "", s or "").strip()


def _doc_year(text: str, default: int = 2026) -> int:
    m = re.search(r"[Uu]ltimo aggiornamento[:\s]+(\d{4})", text)
    return int(m.group(1)) if m else default


def _parse_date(cell: str, year: int) -> str | None:
    m = re.search(r"(\d{1,2})/(\d{1,2})", cell or "")
    if not m:
        return None
    d, mo = int(m.group(1)), int(m.group(2))
    if not (1 <= d <= 31 and 1 <= mo <= 12):
        return None
    return f"{year:04d}-{mo:02d}-{d:02d}"


def _parse_iso_date(s: str) -> str | None:
    """Estrae una data ISO `YYYY-MM-DD` da una stringa (slug social o riga).
    I kit social usano slug `social/2026-06-15-pulsante-recesso/` (data con
    trattini, non slash → `_parse_date` la mancava). Mese-only `2026-06-...`
    → None (nessun giorno schedulabile)."""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s or "")
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return f"{y:04d}-{mo:02d}-{d:02d}"


def _blog_status(date_cell: str, target_cell: str) -> str:
    s = f"{date_cell} {target_cell}"
    low = s.lower()
    if "~~" in date_cell or "pubblicato" in low or "travasato" in low or "✅" in s:
        return "done"
    if "programmato" in low or "future" in low or "pronto" in low:
        return "ready"
    return "todo"


def parse_piano(text: str, year: int | None = None) -> list[dict]:
    """Estrae le entry editoriali dal PIANO. Ritorna lista di dict
    {key, title, body, tags, status, due_at}."""
    year = year or _doc_year(text)
    lines = text.splitlines()
    entries: list[dict] = []
    seen_keys: set[str] = set()

    # --- blog: tabella sotto la sezione "Piano editoriale" ---
    in_blog = False
    seen_header = False
    for ln in lines:
        if ln.lstrip().startswith("##"):
            in_blog = "piano editoriale" in ln.lower()
            seen_header = False
            continue
        if not in_blog or "|" not in ln:
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        joined = " ".join(cells).lower()
        if "bozza" in joined and "tipo" in joined:
            seen_header = True
            continue
        if set("".join(cells)) <= set("-: "):   # riga separatore |---|---|
            continue
        if not seen_header:
            continue
        data, bozza, tipo, target = cells[0], cells[1], cells[2], cells[3]
        draft = re.search(r"\d{3,}", bozza)
        key_id = draft.group(0) if draft else re.sub(r"[^a-z0-9]+", "-", _clean(tipo).lower())[:24].strip("-")
        if not key_id:
            continue
        key = f"piano:blog:{key_id}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        tipo_c = _clean(tipo) or "articolo"
        entries.append({
            "key": key,
            "title": f"📝 Blog {key_id} — {tipo_c}"[:120],
            "body": f"{_clean(target) or tipo_c}\n\n_Tipo:_ {tipo_c} · _prog._ {_clean(data)}",
            "tags": ["piano", "blog"],
            "status": _blog_status(data, target),
            "due_at": _parse_date(data, year),
        })

    # --- social: bullet kit sotto la sezione "Social" ---
    in_social = False
    for ln in lines:
        if ln.lstrip().startswith("##"):
            in_social = ln.lower().rstrip().endswith("social")
            continue
        if not in_social:
            continue
        m = re.search(r"social/([A-Za-z0-9._-]+)/", ln)
        if not m:
            continue
        slug = m.group(1)
        key = f"piano:social:{slug}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        status = next((v for k, v in _EMOJI_STATUS.items() if k in ln[:8]), "todo")
        entries.append({
            "key": key,
            "title": f"🎨 Social kit — {slug}"[:120],
            "body": _clean(ln.strip().lstrip("-").strip())[:400],
            "tags": ["piano", "social"],
            "status": status,
            "due_at": _parse_date(slug, year),
        })

    return entries


# --- vista Piano editoriale (fonte = PIANO.md, NON il kanban) ---------------
#
# parse_piano_items() restituisce item editoriali (date/title/channel/status/
# keyword) per la vista "Piano editoriale" della webapp, NON card kanban.
# Stessa struttura di parsing di parse_piano (tabella blog + bullet social) ma
# con canale e stato editoriale (idea|brief|bozza|pubblicato|repurposed) espliciti.

_SOCIAL_CHANNELS = ("instagram", "facebook", "linkedin")


def _blog_editorial_status(date_cell: str, target_cell: str) -> str:
    """Deriva lo stato editoriale dalla colonna Data + Target della tabella blog.

    Segnali nel file reale: ~~data~~ + "pubblicato"/"travasato"/✅ = pubblicato;
    "travaso"/"refresh" su id esistente = repurposed; "pubblica"/"programmato" =
    bozza; "brief" = brief; default = idea.
    """
    s = f"{date_cell} {target_cell}".lower()
    # travaso/refresh su un id esistente = repurposed, anche se la data è ~~strike~~
    # (lo strike da solo non basta: "travasato" è un repurpose, non un publish nuovo).
    if "travas" in s or "repurpos" in s:
        return "repurposed"
    if "~~" in date_cell or "pubblicato" in s or "✅" in f"{date_cell} {target_cell}":
        return "pubblicato"
    if "programmato" in s or "pubblica" in s or "pronto" in s or "bozza" in s:
        return "bozza"
    if "brief" in s:
        return "brief"
    return "idea"


def _social_editorial_status(line: str) -> str:
    """Stato editoriale da emoji kit (✅ pubblicato · 🔄 repurposed · ⏳ bozza/pronto · ⬜ idea)."""
    head = line[:8]
    if "✅" in head:
        return "pubblicato"
    if "🔄" in head:
        return "repurposed"
    if "⏳" in head:
        return "bozza"
    return "idea"


def _social_channels(line: str) -> list[str]:
    """Canali di un kit social dal testo del bullet. I kit reali usano le sigle
    `IG` / `FB` (non le parole intere) e pubblicano spesso su più canali insieme
    ('carosello IG + FB') → ritorna la LISTA dei canali rilevati (un item per
    canale nel calendario). Default `[instagram]` se nessun segnale."""
    low = line.lower()
    chans: list[str] = []
    if "instagram" in low or re.search(r"\big\b", low):
        chans.append("instagram")
    if "facebook" in low or re.search(r"\bfb\b", low):
        chans.append("facebook")
    if "linkedin" in low:
        chans.append("linkedin")
    return chans or ["instagram"]


def parse_piano_items(text: str, year: int | None = None) -> list[dict]:
    """Estrae item editoriali dal PIANO per la vista Piano editoriale.

    Ritorna lista di dict {date, title, channel, status, keyword, kind, anchor}:
      - date: ISO `YYYY-MM-DD` o None
      - channel: blog|instagram|facebook|linkedin
      - status: idea|brief|bozza|pubblicato|repurposed
      - keyword: target/sintesi (riga descrittiva)
      - kind: blog|social · anchor: id stabile per il write-back (bozza id / slug)
    """
    year = year or _doc_year(text)
    lines = text.splitlines()
    items: list[dict] = []

    # --- blog: tabella sotto la sezione "Piano editoriale" ---
    in_blog = False
    seen_header = False
    stato_idx = None       # indice colonna "Stato" se presente (override prosa)
    # mapping colonne per nome (default = layout legacy Data|Bozza|Tipo|Target)
    date_idx, bozza_idx, title_idx, target_idx = 0, 1, 2, 3
    for ln in lines:
        if ln.lstrip().startswith("##"):
            low_h = ln.lower()
            # `##` apre/chiude una sezione; `###` è una sotto-sezione (es.
            # "### Settimana 7") e NON deve uscire dal calendario in corso.
            if ln.lstrip().startswith("###"):
                if not in_blog:
                    continue
            else:
                in_blog = ("piano editoriale" in low_h) or ("calendario" in low_h)
            seen_header = False
            stato_idx = None
            date_idx, bozza_idx, title_idx, target_idx = 0, 1, 2, 3
            continue
        if not in_blog or "|" not in ln:
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        joined = " ".join(cells).lower()
        _title_keys = ("bozza", "tipo", "articolo", "pezzo", "post")
        _date_keys = ("data", "giorno")
        if (any(d in joined for d in _date_keys) and any(k in joined for k in _title_keys)) or \
           (("stato" in joined) and any(k in joined for k in ("articolo", "pezzo"))):
            # header: mappa le colonne per nome (supporta sia il layout legacy
            # Data|Bozza|Tipo|Target sia p.es. #|Data|Hub|Articolo|Stato)
            low_cells = [c.lower() for c in cells]

            def _col(keys, default=None):
                for i, c in enumerate(low_cells):
                    if any(k in c for k in keys):
                        return i
                return default

            seen_header = True
            # senza colonna Data/Giorno (es. piano a settimane) l'item resta senza data
            date_idx = _col(("data", "giorno"), None)
            bozza_idx = _col(("bozza",), 1)
            title_idx = _col(("articolo", "pezzo", "tipo", "post"), 2)
            target_idx = _col(("target", "hub", "asset"), 3)
            stato_idx = _col(("stato",))
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        if not seen_header:
            continue
        def _cell(i):
            return cells[i] if i is not None and i < len(cells) else ""
        data, bozza, tipo, target = _cell(date_idx), _cell(bozza_idx), _cell(title_idx), _cell(target_idx)
        tipo_c = _clean(tipo) or "articolo"
        draft = re.search(r"\d{3,}", bozza)
        anchor = draft.group(0) if draft else _clean(tipo_c).lower()[:24]
        title = tipo_c if not draft else f"{tipo_c} (bozza {draft.group(0)})"
        raw_stato = cells[stato_idx] if (stato_idx is not None and stato_idx < len(cells)) else ""
        # senza colonna Stato, un ✅/"pubblicato" in QUALSIASI cella (es. colonne
        # FB/IG dei piani a settimana) vale come pubblicato
        if not _clean(raw_stato) and ("✅" in ln or "pubblicat" in joined):
            raw_stato = "✅ pubblicato"
        stato_c = _clean(raw_stato).lower()
        if "✅" in raw_stato or "pubblicat" in stato_c or "live" in stato_c or "completat" in stato_c:
            status = "pubblicato"
        elif "🟢" in raw_stato or "in corso" in stato_c:
            status = "bozza"
        elif stato_c in ("idea", "brief", "bozza", "repurposed"):
            status = stato_c
        else:
            status = _blog_editorial_status(data, target)
        items.append({
            "date": _parse_date(data, year),
            "title": _clean(title)[:160],
            "channel": "blog",
            "status": status,
            "keyword": _clean(target),
            "kind": "blog",
            "anchor": anchor,
        })

    # --- social: bullet kit sotto la sezione "Social" ---
    # I bullet sono MULTI-RIGA: slug sulla prima riga, ma canale ('IG + FB') e
    # data ('(2026-06-11)') spesso sulla riga di continuazione indentata → unisco
    # ogni bullet nel suo blocco completo prima di parsare.
    for block in _social_bullets(lines):
        m = re.search(r"social/([A-Za-z0-9._-]+)/", block)
        if not m:
            continue
        slug = m.group(1)
        body = _clean(block)
        # data: slug ISO (2026-06-15-...) → blocco (publish "(2026-06-11)") → dd/mm
        date = _parse_iso_date(slug) or _parse_iso_date(block) or _parse_date(block, year)
        status = _social_editorial_status(block)
        for ch in _social_channels(block):
            items.append({
                "date": date,
                "title": f"Kit social — {slug}"[:160],
                "channel": ch,
                "status": status,
                "keyword": body[:200],
                "kind": "social",
                "anchor": slug,
            })

    return items


_BULLET_START = re.compile(r"^\s*([-*]|✅|🔄|⏳|⬜|📌)")


def _social_bullets(lines: list[str]) -> list[str]:
    """Raggruppa la sezione 'Social' in blocchi-bullet (prima riga + righe di
    continuazione indentate unite con spazio). Lo stato/emoji del bullet resta in
    testa al blocco, così `_social_editorial_status` legge i primi char."""
    blocks: list[str] = []
    in_social = False
    cur: list[str] = []

    def flush():
        if cur:
            blocks.append(" ".join(cur).strip())
            cur.clear()

    for ln in lines:
        if ln.lstrip().startswith("##"):
            flush()
            in_social = ln.lower().rstrip().endswith("social")
            continue
        if not in_social:
            continue
        if _BULLET_START.match(ln):
            flush()
            cur.append(ln.strip().lstrip("-*").strip())
        elif ln.strip() and cur:          # continuazione del bullet corrente
            cur.append(ln.strip())
    flush()
    return blocks


# --- write-back: editing stato dalla vista Piano → PIANO.md (F1b) ------------
#
# blog → colonna "Stato" della tabella (la crea on-demand al primo edit, seedando
# le righe esistenti dallo stato derivato); social → emoji in testa al bullet.
# Mirato: tocca solo la riga/cella interessata (tranne la migrazione colonna).

EDITORIAL_STATUSES = ("idea", "brief", "bozza", "pubblicato", "repurposed")
_SOCIAL_STATUS_EMOJI = {
    "pubblicato": "✅", "repurposed": "🔄", "bozza": "⏳", "brief": "⏳", "idea": "⬜",
}


def _row_anchor(cells: list[str]) -> str:
    """Anchor stabile di una riga blog — stessa logica del parser (bozza id / tipo)."""
    draft = re.search(r"\d{3,}", cells[1] if len(cells) > 1 else "")
    if draft:
        return draft.group(0)
    return _clean(cells[2] if len(cells) > 2 else "").lower()[:24]


def _fmt_row(cells: list[str]) -> str:
    return "| " + " | ".join(c.strip() for c in cells) + " |"


def _set_blog_status(lines: list[str], anchor: str, status: str) -> bool:
    """Aggiorna lo stato di una riga blog in-place su `lines`. True se trovata."""
    in_blog = False
    header_i = sep_i = None
    header_cells: list[str] | None = None
    data_rows: list[tuple[int, list[str]]] = []
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("##"):
            if header_i is not None:
                break
            in_blog = "piano editoriale" in ln.lower()
            continue
        if not in_blog:
            continue
        if "|" not in ln:
            if header_i is not None and data_rows and ln.strip() == "":
                break
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        joined = " ".join(cells).lower()
        if header_i is None and "bozza" in joined and "tipo" in joined:
            header_i, header_cells = i, cells
            continue
        if header_i is not None and sep_i is None and set("".join(cells)) <= set("-:"):
            sep_i = i
            continue
        if header_i is not None and sep_i is not None and len(cells) >= 4:
            data_rows.append((i, cells))
    if header_i is None or header_cells is None:
        return False

    target = next((r for r in data_rows if _row_anchor(r[1]) == anchor), None)
    if target is None:
        return False

    stato_idx = next((k for k, c in enumerate(header_cells) if "stato" in c.lower()), None)
    if stato_idx is not None:
        li, cells = target
        while len(cells) <= stato_idx:
            cells.append("")
        cells[stato_idx] = status
        lines[li] = _fmt_row(cells)
    else:
        # migrazione: aggiungi colonna Stato (header + separatore + tutte le righe)
        header_cells.append("Stato")
        lines[header_i] = _fmt_row(header_cells)
        if sep_i is not None:
            sep_cells = [c.strip() for c in lines[sep_i].strip().strip("|").split("|")]
            sep_cells.append("---")
            lines[sep_i] = _fmt_row(sep_cells)
        for li, cells in data_rows:
            if li == target[0]:
                cells.append(status)
            else:
                cells.append(_blog_editorial_status(cells[0], cells[3] if len(cells) > 3 else ""))
            lines[li] = _fmt_row(cells)
    return True


def _set_social_status(lines: list[str], slug: str, status: str) -> bool:
    """Swap dell'emoji in testa al bullet del kit `slug`. True se trovato."""
    emoji = _SOCIAL_STATUS_EMOJI.get(status, "⬜")
    needle = re.compile(r"social/" + re.escape(slug) + r"/")
    in_social = False
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("##"):
            in_social = ln.lower().rstrip().endswith("social")
            continue
        if not in_social or not _BULLET_START.match(ln) or not needle.search(ln):
            continue
        m = re.match(r"^(\s*[-*]\s+)([✅🔄⏳⬜📌]\s*)?(.*)$", ln)
        if m:
            lines[i] = f"{m.group(1)}{emoji} {m.group(3)}"
            return True
    return False


def set_item_status(piano_path: Path, kind: str, anchor: str, status: str) -> dict:
    """Aggiorna lo stato di un item nel PIANO.md (write-back mirato dalla vista Piano)."""
    status = (status or "").strip().lower()
    if status not in EDITORIAL_STATUSES:
        return {"ok": False, "error": f"stato non valido: {status}"}
    piano_path = Path(piano_path)
    if not piano_path.is_file():
        return {"ok": False, "error": "PIANO.md non trovato"}
    text = piano_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if kind == "blog":
        ok = _set_blog_status(lines, anchor, status)
    elif kind == "social":
        ok = _set_social_status(lines, anchor, status)
    else:
        return {"ok": False, "error": f"kind sconosciuto: {kind}"}
    if not ok:
        return {"ok": False, "error": f"item non trovato: {anchor}"}
    new = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    if new != text:
        piano_path.write_text(new, encoding="utf-8")
    return {"ok": True, "kind": kind, "anchor": anchor, "status": status}


def sync_piano_to_kanban(hub_path: Path, ws_slug: str, piano_path: Path) -> dict:
    """Legge PIANO.md, parsa, upserta le card kanban (scope=workspace:<ws_slug>)."""
    piano_path = Path(piano_path)
    if not piano_path.is_file():
        return {"ok": False, "error": "PIANO.md non trovato", "path": str(piano_path)}
    entries = parse_piano(piano_path.read_text(encoding="utf-8"))
    scope = f"workspace:{ws_slug}"
    existing = kanban_io.list_tasks(hub_path, scope=scope, include_archived=True, limit=1000)
    by_key = {(t.get("metadata") or {}).get("piano_key"): t for t in existing if (t.get("metadata") or {}).get("piano_key")}

    created = updated = 0
    for e in entries:
        meta = {"piano_key": e["key"], "source": "PIANO.md"}
        cur = by_key.get(e["key"])
        if cur:
            kanban_io.update_task(hub_path, cur["id"], title=e["title"], body=e["body"],
                                  tags=e["tags"], due_at=e["due_at"], metadata=meta)
            if cur.get("status") not in ("archived", e["status"]):
                kanban_io.update_status(hub_path, cur["id"], e["status"])
            updated += 1
        else:
            kanban_io.create_task(hub_path, title=e["title"], body=e["body"], status=e["status"],
                                  scope=scope, tags=e["tags"], due_at=e["due_at"], metadata=meta)
            created += 1

    return {
        "ok": True, "entries": len(entries), "created": created, "updated": updated,
        "blog": sum(1 for e in entries if "blog" in e["tags"]),
        "social": sum(1 for e in entries if "social" in e["tags"]),
    }
