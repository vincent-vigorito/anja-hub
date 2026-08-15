"""audit_io.py — audit SEO / E-E-A-T / GEO dei prodotti (Tier 2).

Porta la logica di scoring di anja-marketer scripts/audit_products.py: per ogni
prodotto WooCommerce calcola tre score 0-100 (SEO, E-E-A-T, GEO) sul contenuto,
incrocia con GSC (gsc_pages da metrics.db, 90gg) per click/impression/posizione,
e calcola una priority (potenziale × gap) + quick-win CTR.

Prodotti: WooCommerce REST (/wp-json/wc/v3/products) con Application Password dal
vault. Meta SEO: SEOPress (/wp-json/seopress/v1/...) se disponibile. Vendite:
`total_sales × price` del prodotto come proxy del potenziale revenue (l'hub non
ha il collector wc_product_daily del prototipo).
"""

from __future__ import annotations

import datetime
import html
import math
import re
from pathlib import Path

import httpx

GSC_DAYS = 90


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s or "")


def _clamp(v: float, lo: int = 0, hi: int = 100) -> int:
    return int(max(lo, min(hi, v)))


def analyze_text(desc_html: str, short_html: str, base_host: str) -> dict:
    desc_html = desc_html or ""
    text = _strip_html(desc_html)
    low = text.lower()
    bold_chunks = re.findall(r"<(?:strong|b)>(.*?)</(?:strong|b)>", desc_html, re.I | re.S)
    bold_len = sum(len(_strip_html(c)) for c in bold_chunks)
    fp = re.search(r"<p[^>]*>(.*?)</p>", desc_html, re.I | re.S)
    # link interni: href relativi o verso lo stesso host
    hrefs = re.findall(r'<a\s[^>]*href="([^"]+)"', desc_html, re.I)
    internal = sum(1 for h in hrefs if h.startswith("/") or (base_host and base_host in h))
    external = sum(1 for h in hrefs if h.startswith("http") and base_host and base_host not in h)
    return {
        "words": len(text.split()),
        "h2": len(re.findall(r"<h2", desc_html, re.I)),
        "h3": len(re.findall(r"<h3", desc_html, re.I)),
        "tables": len(re.findall(r"<table", desc_html, re.I)),
        "lists": len(re.findall(r"<[uo]l\b", desc_html, re.I)),
        "bold_ratio": round(bold_len / max(1, len(text)), 2),
        "faq": bool(re.search(r"domande frequenti|faq", low))
               or bool(re.search(r"<h[23][^>]*>[^<]*\?", desc_html, re.I)),
        "internal_links": internal,
        "external_links": external,
        "blockquotes": len(re.findall(r"<blockquote", desc_html, re.I)),
        "has_data": bool(re.search(r"\d+\s?%|\b\d{2,}\b", text)),
        "pdf_links": len(re.findall(r'href="[^"]+\.pdf', desc_html, re.I)),
        "first_paragraph_chars": len(_strip_html(fp.group(1))) if fp else 0,
        "has_short_description": bool(_strip_html(short_html or "").strip()),
        "has_dosaggi": bool(re.search(r"dosagg|dosare|dosi\b|diluizion|diluire|gr?\.\s*per|g/m|ml/|per m³|per mq|al \d+%", low)),
        "has_composizione": bool(re.search(r"composizione|contiene\b", low)),
        "mentions_sds": bool(re.search(r"scheda di sicurezza|scheda tecnica", low)),
        "made_in_italy": bool(re.search(r"made in italy|realizzat\w+ (?:interamente )?in italia|prodott\w+ in italia", low)),
    }


def score_seo(slug: str, alt_missing: int, t: dict, meta: dict) -> int:
    s, mt, md = 0, meta.get("title", ""), meta.get("description", "")
    if mt:
        s += 15
    if 40 <= len(mt) <= 70:
        s += 10
    if md:
        s += 15
    if 120 <= len(md) <= 165:
        s += 10
    if len(slug or "") <= 75:
        s += 5
    if t["h2"] + t["h3"] >= 2:
        s += 15
    if t["internal_links"] >= 1:
        s += 10
    if t["has_short_description"]:
        s += 10
    s += _clamp(10 - 5 * alt_missing, 0, 10)
    return _clamp(s)


def score_eeat(rating_count: int, t: dict) -> int:
    s = 0
    if t["has_dosaggi"]:
        s += 25
    if t["has_composizione"]:
        s += 15
    if t["mentions_sds"]:
        s += 10
    if t["pdf_links"] >= 1:
        s += 15
    if rating_count >= 1:
        s += 15
    if rating_count >= 3:
        s += 5
    if t["made_in_italy"]:
        s += 5
    if t["words"] >= 250:
        s += 10
    return _clamp(s)


def score_geo(t: dict) -> int:
    s = 0
    h = t["h2"] + t["h3"]
    s += 25 if h >= 3 else 15 if h == 2 else 8 if h == 1 else 0
    if t["tables"] >= 1:
        s += 20
    if t["faq"]:
        s += 20
    if t["lists"] >= 1:
        s += 10
    if t["first_paragraph_chars"] >= 60:
        s += 10
    br = t["bold_ratio"]
    s += 15 if br < 0.4 else 7 if br < 0.6 else 0
    return _clamp(s)


def _days_since(iso: str) -> int | None:
    try:
        d = datetime.datetime.fromisoformat((iso or "").replace("Z", "")).date()
        return (datetime.date.today() - d).days
    except (ValueError, TypeError):
        return None


def score_eeat_editorial(t: dict, fresh_days: int | None, has_author: bool) -> int:
    """E-E-A-T per i contenuti editoriali (articoli/pagine): profondità, fonti,
    citazioni, freschezza, dati, firma — diverso dai segnali e-commerce."""
    s = 0
    w = t["words"]
    s += 25 if w >= 1200 else 15 if w >= 600 else 8 if w >= 300 else 0   # profondità
    s += 20 if t["external_links"] >= 2 else 10 if t["external_links"] >= 1 else 0  # fonti
    if t["blockquotes"] >= 1:
        s += 10                                                           # citazioni
    if fresh_days is not None:
        s += 20 if fresh_days <= 180 else 10 if fresh_days <= 365 else 0  # freschezza
    if t["has_data"]:
        s += 10                                                           # dati/numeri
    if has_author:
        s += 10                                                           # firma
    return _clamp(s)


def _gsc_pages(db_path: Path, start_date: str) -> dict:
    p = Path(db_path)
    if not p.is_file():
        return {}
    import metrics_io
    c = metrics_io._conn(p)   # garantisce lo schema (gsc_pages incluso anche su db vecchi)
    try:
        rows = c.execute(
            """SELECT page, SUM(clicks) clicks, SUM(impressions) impr,
                      SUM(position*impressions)/MAX(1.0, SUM(impressions)) pos
               FROM gsc_pages WHERE date >= ? GROUP BY page""", (start_date,)).fetchall()
        return {(r["page"] or "").rstrip("/"): {
            "clicks": r["clicks"] or 0, "impressions": r["impr"] or 0,
            "position": round(r["pos"], 1) if r["pos"] else 0.0} for r in rows}
    finally:
        c.close()


def _merchant_by_link(db_path: Path) -> dict:
    """Ultimo snapshot Merchant (status feed + n. issues) per link normalizzato —
    stesso join per permalink usato per gsc_pages. Vuoto se mai raccolto."""
    p = Path(db_path)
    if not p.is_file():
        return {}
    import metrics_io
    c = metrics_io._conn(p)
    try:
        last = c.execute("SELECT MAX(date) d FROM merchant_products").fetchone()
        if not last or not last["d"]:
            return {}
        rows = c.execute(
            "SELECT offer_id, link, status, issues FROM merchant_products WHERE date = ?",
            (last["d"],)).fetchall()
        return {(r["link"] or "").rstrip("/"): {
            "offer_id": r["offer_id"], "status": r["status"],
            "issues": r["issues"] or 0} for r in rows if (r["link"] or "").strip()}
    finally:
        c.close()


def _norm_log(v: float, mx: float) -> float:
    return math.log1p(v) / math.log1p(mx) if mx > 0 else 0.0


def _fetch_items(client: httpx.Client, base: str, kind: str, limit: int) -> list[dict]:
    """Prodotti (WooCommerce) o articoli/pagine (WP v2), pubblicati, paginati."""
    if kind == "products":
        path, params = "/wp-json/wc/v3/products", {"per_page": 100, "status": "publish", "_nocache": "1"}
    else:  # posts | pages
        path = f"/wp-json/wp/v2/{kind}"
        params = {"per_page": 100, "status": "publish",
                  "_fields": "id,slug,link,title,content,excerpt,date,modified,author"}
    out = []
    for page in range(1, 20):
        params["page"] = page
        r = client.get(f"{base}{path}", params=params)
        if r.status_code != 200:
            break
        batch = r.json()
        if not isinstance(batch, list) or not batch:
            break
        out.extend(batch)
        if len(out) >= limit or len(batch) < 100:
            break
    return out[:limit]


def _rendered(field) -> str:
    """Estrae .rendered dai campi WP v2 ({rendered: ...}) o ritorna la stringa."""
    if isinstance(field, dict):
        return field.get("rendered", "") or ""
    return field or ""


def _seo_meta(client: httpx.Client, base: str, pid: int) -> dict:
    try:
        r = client.get(f"{base}/wp-json/seopress/v1/posts/{pid}/title-description-metas", timeout=15)
        if r.status_code == 200:
            d = r.json()
            return {"title": d.get("title", "") or "", "description": d.get("description", "") or ""}
    except httpx.HTTPError:
        pass
    return {"title": "", "description": ""}


def audit(vault_values: dict, db_path: Path, *, kind: str = "products", limit: int = 300) -> dict:
    """Audit dei contenuti pubblicati. `kind`: 'products' (WooCommerce, E-E-A-T
    e-commerce) | 'posts' | 'pages' (editoriale, E-E-A-T autore/fonti/freschezza).
    Ritorna {ok, site, kind, date, count, products:[...], summary, error?} per priority."""
    if kind not in ("products", "posts", "pages"):
        return {"ok": False, "error": f"kind non valido: {kind}"}
    base = (vault_values.get("WP_BASE_URL") or "").strip().rstrip("/")
    user = (vault_values.get("WP_USERNAME") or "").strip()
    pw = (vault_values.get("WP_APP_PASSWORD") or "").strip()
    if not (base and user and pw):
        # backend swerpi: stesse formule, fetch dall'API v2 del tenant
        if (vault_values.get("SWERPICOMMERCE_BASE_URL") or "").strip():
            return _audit_swerpi(vault_values, db_path, kind=kind, limit=limit)
        return {"ok": False, "error": "credenziali CMS mancanti nei Connettori "
                                      "(WordPress WP_* oppure SwerpiCommerce SWERPICOMMERCE_*)"}

    host = re.sub(r"^https?://", "", base).split("/")[0]
    today = datetime.date.today()
    gsc = _gsc_pages(db_path, (today - datetime.timedelta(days=GSC_DAYS)).isoformat())
    merch = _merchant_by_link(db_path) if kind == "products" else {}

    rows = []
    try:
        with httpx.Client(timeout=30.0, auth=(user, pw), follow_redirects=True) as c:
            items = _fetch_items(c, base, kind, limit)
            if not items:
                what = "prodotti WooCommerce" if kind == "products" else ("articoli" if kind == "posts" else "pagine")
                return {"ok": False, "error": f"nessun contenuto trovato ({what}). WP_BASE_URL corretto / tipo attivo?"}
            for p in items:
                if kind == "products":
                    name = html.unescape(_strip_html(p.get("name", ""))).strip()
                    t = analyze_text(p.get("description", ""), p.get("short_description", ""), host)
                    permalink = p.get("permalink", "")
                    alt_missing = sum(1 for im in (p.get("images") or []) if not (im.get("alt") or "").strip())
                    eeat = score_eeat(int(p.get("rating_count", 0) or 0), t)
                    revenue = float(p.get("price") or 0) * int(p.get("total_sales", 0) or 0)
                else:
                    name = html.unescape(_strip_html(_rendered(p.get("title")))).strip()
                    t = analyze_text(_rendered(p.get("content")), _rendered(p.get("excerpt")), host)
                    permalink = p.get("link", "")
                    alt_missing = 0
                    eeat = score_eeat_editorial(t, _days_since(p.get("modified", "")), bool(p.get("author")))
                    revenue = 0.0
                meta = _seo_meta(c, base, p["id"])
                seo, geo = score_seo(p.get("slug", ""), alt_missing, t, meta), score_geo(t)
                avg = round((seo + eeat + geo) / 3)
                g = gsc.get((permalink or "").rstrip("/"), {"clicks": 0, "impressions": 0, "position": 0.0})
                rows.append({
                    "id": p["id"], "name": name, "permalink": permalink,
                    "revenue_proxy": round(revenue, 2),
                    "scores": {"seo": seo, "eeat": eeat, "geo": geo, "avg": avg},
                    "text": t, "gsc": g, "meta_title_len": len(meta["title"]),
                    "meta_description_len": len(meta["description"]),
                    "merchant": merch.get((permalink or "").rstrip("/")),
                })
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"errore WordPress: {e}"}

    return _finalize(rows, host, kind, today)


def _finalize(rows: list[dict], host: str, kind: str, today: datetime.date) -> dict:
    """Priority (potenziale × gap) + quick-win + summary — comune a tutti i backend."""
    max_imp = max((r["gsc"]["impressions"] for r in rows), default=0)
    max_clk = max((r["gsc"]["clicks"] for r in rows), default=0)
    max_rev = max((r["revenue_proxy"] for r in rows), default=0)
    for r in rows:
        potential = (0.5 * _norm_log(r["gsc"]["impressions"], max_imp)
                     + 0.35 * _norm_log(r["revenue_proxy"], max_rev)
                     + 0.15 * _norm_log(r["gsc"]["clicks"], max_clk))
        r["priority"] = round(100 * potential * (1 - r["scores"]["avg"] / 100), 1)
        imp, pos, clk = r["gsc"]["impressions"], r["gsc"]["position"], r["gsc"]["clicks"]
        r["quick_win"] = bool(imp >= 100 and 0 < pos <= 12 and (clk / max(1, imp)) < 0.02)

    rows.sort(key=lambda r: r["priority"], reverse=True)
    summary = {
        "count": len(rows),
        "avg_seo": round(sum(r["scores"]["seo"] for r in rows) / max(1, len(rows))),
        "avg_eeat": round(sum(r["scores"]["eeat"] for r in rows) / max(1, len(rows))),
        "avg_geo": round(sum(r["scores"]["geo"] for r in rows) / max(1, len(rows))),
        "quick_wins": sum(1 for r in rows if r["quick_win"]),
        "with_gsc": sum(1 for r in rows if r["gsc"]["impressions"] > 0),
        "merchant_flagged": sum(1 for r in rows
                                if (r.get("merchant") or {}).get("status")
                                in ("disapproved", "demoted")),
    }
    return {"ok": True, "site": host, "kind": kind, "date": today.isoformat(),
            "count": len(rows), "products": rows, "summary": summary}


# ----------------------------------------------------------------------
# SwerpiCommerce (backend swerpi) — stesse formule, fetch API v2
# ----------------------------------------------------------------------

_SWERPI_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AnjaHub-audit/0.20"}
_SWERPI_PUB = {"pubblicato", "live", "online", "publish"}
_SWERPI_RESOURCE = {"products": "products", "posts": "articles", "pages": "pages"}


def _swerpi_token(client: httpx.Client, base: str, vals: dict) -> str:
    r = client.post(f"{base}/auth/token", json={
        "api_id": vals.get("SWERPICOMMERCE_API_ID", ""),
        "api_secret": vals.get("SWERPICOMMERCE_API_SECRET", "")}, timeout=25.0)
    r.raise_for_status()
    j = r.json()
    dd = j.get("data") or {}
    return (((dd.get("data") or {}).get("token") if isinstance(dd.get("data"), dict)
             else dd.get("token")) or j.get("token") or "")


def _swerpi_fetch(client: httpx.Client, base: str, resource: str, headers: dict, limit: int) -> list[dict]:
    """Letture paginate; envelope `.results.data`, `.results` nudo o `.data` (varia per risorsa)."""
    out: list[dict] = []
    for page in range(1, 20):
        r = client.get(f"{base}/{resource}", params={"per_page": 100, "page": page}, headers=headers)
        if r.status_code != 200:
            break
        j = r.json()
        res = j.get("results")
        batch = (res.get("data") if isinstance(res, dict) else res) or j.get("data") or []
        if not isinstance(batch, list) or not batch:
            break
        out.extend(batch)
        if len(out) >= limit or len(batch) < 100:
            break
    return out[:limit]


def _audit_swerpi(vault_values: dict, db_path: Path, *, kind: str, limit: int) -> dict:
    base = (vault_values.get("SWERPICOMMERCE_BASE_URL") or "").strip().rstrip("/")
    bearer = (vault_values.get("SWERPICOMMERCE_BEARER_AUTH") or "").strip()
    host = re.sub(r"^https?://", "", base).split("/")[0]
    today = datetime.date.today()
    gsc = _gsc_pages(db_path, (today - datetime.timedelta(days=GSC_DAYS)).isoformat())
    merch = _merchant_by_link(db_path) if kind == "products" else {}
    resource = _SWERPI_RESOURCE[kind]

    rows = []
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True, headers=_SWERPI_UA) as c:
            tok = bearer or _swerpi_token(c, base, vault_values)
            if not tok:
                return {"ok": False, "error": "auth SwerpiCommerce fallita: token non emesso"}
            hdr = {"Authorization": f"Bearer {tok}"}
            site_root = base[:-len("/api/v2")] if base.endswith("/api/v2") else base
            items = _swerpi_fetch(c, base, resource, hdr, limit)
            if not items:
                return {"ok": False, "error": f"nessun contenuto trovato ({resource}) sul tenant SwerpiCommerce"}
            for p in items:
                stato = str(p.get("stato") or p.get("status")
                            or ("pubblicato" if resource == "pages" else "")).strip().lower()
                if stato and stato not in _SWERPI_PUB:
                    continue
                name = html.unescape(_strip_html(str(p.get("titolo") or p.get("title") or p.get("name") or ""))).strip()
                t = analyze_text(str(p.get("contenuto") or p.get("content") or ""),
                                 str(p.get("descrizione_breve") or p.get("excerpt") or ""), host)
                # NB `url_diretto` è un FLAG boolean, non l'URL → costruisci dallo slug
                permalink = p.get("url") or p.get("permalink") or ""
                if not isinstance(permalink, str):
                    permalink = ""
                _slug = p.get("slug") or ""
                if not permalink and _slug:
                    permalink = (f"{site_root}/blog/{_slug}/" if resource == "articles"
                                 else f"{site_root}/{_slug}/")
                meta = {"title": p.get("meta_title") or "", "description": p.get("meta_description") or ""}
                if resource == "products":
                    eeat = score_eeat(int(p.get("rating_count", 0) or 0), t)
                else:
                    eeat = score_eeat_editorial(
                        t, _days_since(str(p.get("updated_at") or p.get("data_pubblicazione") or "")),
                        bool(p.get("autore") or p.get("author")))
                seo, geo = score_seo(p.get("slug", ""), 0, t, meta), score_geo(t)
                avg = round((seo + eeat + geo) / 3)
                g = gsc.get((permalink or "").rstrip("/"), {"clicks": 0, "impressions": 0, "position": 0.0})
                rows.append({
                    "id": p.get("id"), "name": name, "permalink": permalink,
                    "revenue_proxy": 0.0,
                    "scores": {"seo": seo, "eeat": eeat, "geo": geo, "avg": avg},
                    "text": t, "gsc": g, "meta_title_len": len(meta["title"]),
                    "meta_description_len": len(meta["description"]),
                    "merchant": merch.get((permalink or "").rstrip("/")),
                })
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"errore SwerpiCommerce: {e}"}

    return _finalize(rows, host, kind, today)
