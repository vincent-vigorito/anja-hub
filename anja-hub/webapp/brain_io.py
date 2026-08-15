"""brain_io.py — Brain personale/condiviso (F3): store di note .md libere.

Obsidian-interno: note `.md` flat con titolo (frontmatter `title:` o prima `# H1`)
e link `[[slug]]`. DISTINTO dalla wiki-ingest (`.anjawiki/wiki/`, sintesi di fonti
con schema entity/concept/source): qui sono APPUNTI dell'utente, scritti a mano.

Storage (simmetrico):
  - personale → `<hub>/users/<slug>-brain/*.md`   (scope user:<slug>, solo l'utente)
  - condiviso → `<hub>/brain/*.md`                 (scope hub, target del "promuovi")

Promozione: copia personale → condiviso con provenienza; NON cancella l'originale
(la promozione è una decisione esplicita dell'utente, niente leak automatico).
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_FM_TITLE = re.compile(r"^title:\s*(.+?)\s*$", re.M)
_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.M)


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").strip().lower()).strip("-")
    return s[:80] or "nota"


def _split_frontmatter(text: str) -> tuple[str, str]:
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            return text[4:end], text[end + 4:].lstrip("\n")
    return "", text


def _title_of(text: str, slug: str) -> str:
    fm, body = _split_frontmatter(text)
    m = _FM_TITLE.search(fm)
    if m:
        return m.group(1).strip().strip("\"'")
    m = _H1_RE.search(body)
    return m.group(1).strip() if m else slug


def _links_of(text: str) -> list[str]:
    # `[[Target]]` o `[[Target|alias]]` → slug del target
    return sorted({slugify(m.group(1).split("|")[0]) for m in _LINK_RE.finditer(text)})


def _excerpt(text: str, n: int = 150) -> str:
    _, body = _split_frontmatter(text)
    plain = re.sub(r"\s+", " ", re.sub(r"[#>*`\[\]]", "", body)).strip()
    return plain[:n]


def _read(f: Path) -> str:
    return f.read_text(encoding="utf-8", errors="replace")


def _mtime(f: Path) -> str:
    return datetime.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")


def list_notes(brain_dir: Path) -> list[dict]:
    d = Path(brain_dir)
    if not d.is_dir():
        return []
    notes = []
    for f in d.glob("*.md"):
        text = _read(f)
        notes.append({
            "slug": f.stem, "title": _title_of(text, f.stem),
            "excerpt": _excerpt(text), "links": _links_of(text), "mtime": _mtime(f),
        })
    incoming: dict[str, int] = {}
    for n in notes:
        for l in n["links"]:
            incoming[l] = incoming.get(l, 0) + 1
    for n in notes:
        n["backlinks"] = incoming.get(n["slug"], 0)
    notes.sort(key=lambda n: n["mtime"], reverse=True)
    return notes


def read_note(brain_dir: Path, slug: str) -> dict | None:
    d = Path(brain_dir)
    if not _SLUG_RE.match(slug or ""):
        return None
    f = d / f"{slug}.md"
    if not f.is_file():
        return None
    text = _read(f)
    _, body = _split_frontmatter(text)
    backlinks = []
    for other in d.glob("*.md"):
        if other.stem == slug:
            continue
        ot = _read(other)
        if slug in _links_of(ot):
            backlinks.append({"slug": other.stem, "title": _title_of(ot, other.stem)})
    return {
        "slug": slug, "title": _title_of(text, slug), "body": body,
        "links": _links_of(text),
        "backlinks": sorted(backlinks, key=lambda b: b["title"].lower()),
        "mtime": _mtime(f),
    }


def save_note(brain_dir: Path, slug: str, title: str, body: str) -> dict:
    title = (title or "").strip().replace("\n", " ") or (slug or "Senza titolo")
    if not _SLUG_RE.match(slug or ""):
        slug = slugify(slug or title)
    if not _SLUG_RE.match(slug):
        return {"ok": False, "error": "slug non valido"}
    d = Path(brain_dir)
    d.mkdir(parents=True, exist_ok=True)
    text = f"---\ntitle: {title}\n---\n\n{(body or '').strip()}\n"
    (d / f"{slug}.md").write_text(text, encoding="utf-8")
    return {"ok": True, "slug": slug, "title": title}


def delete_note(brain_dir: Path, slug: str) -> dict:
    if not _SLUG_RE.match(slug or ""):
        return {"ok": False, "error": "slug non valido"}
    f = Path(brain_dir) / f"{slug}.md"
    if f.is_file():
        f.unlink()
    return {"ok": True, "slug": slug}


SEM_MIN = 0.28   # cosine minima perché una nota conti come match semantico (tarata sul
                 # baseline reale di text-embedding-3-small: correlato ≈0.37, non-corr ≈0.24)
SEM_CAP = 300    # oltre N note salta il semantico (evita re-embed costoso on-the-fly)


def search_notes(brain_dir: Path, q: str, embedder=None) -> list[dict]:
    """Ricerca ranked. Lessicale (default): ogni termine pesa 3 nel titolo + frequenza
    nel corpo. Se `embedder` è fornito → IBRIDA lessicale+semantica fusa via RRF (trova
    anche note senza keyword-match ma affini di significato). Graceful: senza embedder
    o su errore embedding ricade sul solo lessicale."""
    q = (q or "").strip()
    if not q:
        return []
    ql = q.lower()
    terms = [t for t in re.split(r"\s+", ql) if t]
    notes = list_notes(brain_dir)

    # --- ranking lessicale (comportamento storico) ---
    lex_scored = []
    for n in notes:
        text = _read(Path(brain_dir) / f"{n['slug']}.md").lower()
        score = sum((3 if t in n["title"].lower() else 0) + text.count(t) for t in terms)
        if score:
            lex_scored.append((score, n))
    lex_scored.sort(key=lambda x: -x[0])
    lex_order = [n for _, n in lex_scored]

    if embedder is None or not notes or len(notes) > SEM_CAP:
        return lex_order

    # --- layer semantico + fusione RRF ---
    try:
        from embeddings import cosine, rrf_merge
    except Exception:
        return lex_order
    docs = []
    for n in notes:
        body = _excerpt(_read(Path(brain_dir) / f"{n['slug']}.md"), 500)
        docs.append(f"{n['title']} — {body}")
    vecs = embedder([q] + docs)
    if not vecs or len(vecs) != len(docs) + 1:
        return lex_order   # embedding non disponibile → fallback lessicale
    qv, dvs = vecs[0], vecs[1:]
    sem = sorted(((n, cosine(qv, v)) for n, v in zip(notes, dvs)), key=lambda x: x[1], reverse=True)
    sem_order = [n for n, s in sem if s >= SEM_MIN]
    merged = rrf_merge([n["slug"] for n in lex_order], [n["slug"] for n in sem_order])
    by_slug = {n["slug"]: n for n in notes}
    return [by_slug[s] for s in merged if s in by_slug]


def promote(user_brain_dir: Path, shared_brain_dir: Path, slug: str, by: str = "user") -> dict:
    """Copia una nota personale → condivisa (con provenienza). NON cancella l'originale."""
    if not _SLUG_RE.match(slug or ""):
        return {"ok": False, "error": "slug non valido"}
    src = Path(user_brain_dir) / f"{slug}.md"
    if not src.is_file():
        return {"ok": False, "error": "nota non trovata"}
    text = _read(src)
    _, body = _split_frontmatter(text)
    title = _title_of(text, slug)
    dest = Path(shared_brain_dir)
    dest.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    out = f"---\ntitle: {title}\npromoted_from: {by}\npromoted_on: {today}\n---\n\n{body.strip()}\n"
    (dest / f"{slug}.md").write_text(out, encoding="utf-8")
    return {"ok": True, "slug": slug, "title": title}
