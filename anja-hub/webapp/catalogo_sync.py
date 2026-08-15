"""catalogo_sync.py — rigenera data/catalogo/*.md dal CMS (WordPress REST).

Il "tubo dati" del Catalogo contenuti: legge le credenziali WP dal vault (F2),
chiama la REST API di WordPress (`/wp-json/wp/v2/posts|pages`) e riscrive le
tabelle markdown che `catalogo_io` legge. Read-only sul CMS. I prodotti
(WooCommerce) richiedono le consumer key woo (non nel vault WP) → skip se assenti.

Auth: Application Password WordPress via Basic Auth.
"""

from __future__ import annotations

import datetime
import html
from pathlib import Path

import httpx

_FIELDS = "id,title,slug,status,link"
_STATUSES = "publish,future,draft,pending,private"


def _rows_table(kind_title: str, ws_slug: str, items: list[dict], when: str) -> str:
    lines = [
        f"# Catalogo {kind_title} — {ws_slug}", "",
        f"_Sync {when}. RIGENERABILE dal CMS, non editare a mano._", "",
        "| ID | Titolo | Slug | Stato | URL |", "|---|---|---|---|---|",
    ]
    for it in items:
        t = it.get("title") or {}
        title = html.unescape(t.get("rendered") or t.get("raw") or "").replace("|", "/").replace("\n", " ").strip()
        lines.append(f"| {it.get('id', '')} | {title} | `{it.get('slug', '')}` | {it.get('status', '')} | {it.get('link', '')} |")
    return "\n".join(lines) + "\n"


def _fetch_all(client: httpx.Client, base: str, resource: str, auth) -> list[dict]:
    """Pagina la REST WP (`context=edit` per vedere anche le bozze)."""
    out: list[dict] = []
    page = 1
    while page <= 50:
        r = client.get(
            f"{base.rstrip('/')}/wp-json/wp/v2/{resource}",
            params={"per_page": 100, "page": page, "status": _STATUSES,
                    "_fields": _FIELDS, "context": "edit", "orderby": "id", "order": "desc"},
            auth=auth, timeout=25.0,
        )
        if r.status_code == 400 and page > 1:   # WP: page oltre il totale → 400
            break
        r.raise_for_status()
        batch = r.json()
        if not isinstance(batch, list) or not batch:
            break
        out.extend(batch)
        total_pages = int(r.headers.get("X-WP-TotalPages", "1") or 1)
        if page >= total_pages:
            break
        page += 1
    return out


def sync_catalogo(catalogo_dir: Path, wp_base_url: str, wp_user: str, wp_app_password: str,
                  ws_slug: str = "", ecommerce: bool = False) -> dict:
    """Rigenera articoli.md + pagine.md (+ prodotti.md se ecommerce) da WP.
    Ritorna {ok, counts:{articoli,pagine,...}} oppure {ok:False, error}."""
    base = (wp_base_url or "").strip()
    if not base or not wp_user or not wp_app_password:
        return {"ok": False, "error": "credenziali WordPress mancanti (configura i Connettori)"}
    if not base.startswith(("http://", "https://")):
        base = "https://" + base
    auth = httpx.BasicAuth(wp_user, wp_app_password)
    d = Path(catalogo_dir)
    d.mkdir(parents=True, exist_ok=True)
    when = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    counts = {}
    try:
        with httpx.Client(follow_redirects=True) as client:
            jobs = [("articoli", "posts", "articoli"), ("pagine", "pages", "pagine")]
            for fname, resource, label in jobs:
                items = _fetch_all(client, base, resource, auth)
                (d / f"{fname}.md").write_text(_rows_table(label, ws_slug or "site", items, when), encoding="utf-8")
                counts[fname] = len(items)
            # prodotti: WooCommerce ha la sua API (wc/v3 + consumer key) → non da WP app password.
            # Se il CMS espone il post-type 'product' via wp/v2 lo prendiamo, altrimenti skip.
            if ecommerce:
                try:
                    prods = _fetch_all(client, base, "product", auth)
                    (d / "prodotti.md").write_text(_rows_table("prodotti", ws_slug or "site", prods, when), encoding="utf-8")
                    counts["prodotti"] = len(prods)
                except httpx.HTTPStatusError:
                    counts["prodotti"] = None   # endpoint non disponibile (serve API WooCommerce dedicata)
    except httpx.HTTPStatusError as e:
        return {"ok": False, "error": f"WordPress {e.response.status_code}: verifica URL/credenziali"}
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"connessione WordPress fallita: {e}"}
    return {"ok": True, "counts": counts, "synced_at": when}


# ----------------------------------------------------------------------
# SwerpiCommerce (backend swerpi) — API v2, bearer da api_id+secret
# ----------------------------------------------------------------------

# UA browser-like: Cloudflare davanti ai tenant swerpi blocca gli UA "python-*"
_SWERPI_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AnjaHub-catalogo-sync/0.20"}


def _rows_table_generic(kind_title: str, ws_slug: str, rows: list[dict], when: str) -> str:
    lines = [
        f"# Catalogo {kind_title} — {ws_slug}", "",
        f"_Sync {when}. RIGENERABILE dal CMS, non editare a mano._", "",
        "| ID | Titolo | Slug | Stato | URL |", "|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r.get('id', '')} | {r.get('title', '')} | `{r.get('slug', '')}` | {r.get('status', '')} | {r.get('url', '')} |")
    return "\n".join(lines) + "\n"


def _swerpi_fetch_all(client: httpx.Client, base: str, resource: str, headers: dict) -> list[dict]:
    """Pagina l'API v2: letture in envelope `.results.data` (o `.results` nudo)."""
    out: list[dict] = []
    page = 1
    while page <= 50:
        r = client.get(f"{base}/{resource}", params={"per_page": 100, "page": page},
                       headers=headers, timeout=25.0)
        r.raise_for_status()
        j = r.json()
        res = j.get("results")
        data = (res.get("data") if isinstance(res, dict) else res) or j.get("data") or []
        if not isinstance(data, list) or not data:
            break
        out.extend(data)
        if len(data) < 100:
            break
        page += 1
    return out


def sync_catalogo_swerpi(catalogo_dir: Path, base_url: str, api_id: str, api_secret: str,
                         bearer: str = "", ws_slug: str = "") -> dict:
    """Rigenera articoli/pagine/prodotti.md da un tenant SwerpiCommerce (API v2).
    Bearer dal vault se presente, altrimenti mint da api_id+api_secret."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return {"ok": False, "error": "credenziali SwerpiCommerce mancanti (configura i Connettori)"}
    if not base.startswith(("http://", "https://")):
        base = "https://" + base
    d = Path(catalogo_dir)
    d.mkdir(parents=True, exist_ok=True)
    when = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    counts = {}
    try:
        with httpx.Client(follow_redirects=True, headers=_SWERPI_UA) as client:
            tok = (bearer or "").strip()
            if not tok:
                if not api_id or not api_secret:
                    return {"ok": False, "error": "credenziali SwerpiCommerce mancanti (configura i Connettori)"}
                r = client.post(f"{base}/auth/token",
                                json={"api_id": api_id, "api_secret": api_secret}, timeout=25.0)
                r.raise_for_status()
                j = r.json()
                dd = j.get("data") or {}
                tok = ((dd.get("data") or {}).get("token") if isinstance(dd.get("data"), dict)
                       else dd.get("token")) or j.get("token") or ""
            if not tok:
                return {"ok": False, "error": "auth SwerpiCommerce fallita: token non emesso"}
            headers = {"Authorization": f"Bearer {tok}"}
            site_root = base[:-len("/api/v2")] if base.endswith("/api/v2") else base
            for fname, resource in (("articoli", "articles"), ("pagine", "pages"), ("prodotti", "products")):
                items = _swerpi_fetch_all(client, base, resource, headers)
                rows = []
                for it in items:
                    slug = it.get("slug") or ""
                    url = it.get("url") or it.get("permalink") or ""
                    if not url and slug:
                        url = f"{site_root}/blog/{slug}/" if resource == "articles" else f"{site_root}/{slug}/"
                    title = str(it.get("title") or it.get("name") or it.get("titolo") or "")
                    status = it.get("status") or it.get("stato") or ""
                    if not status and resource == "pages":
                        status = "pubblicato"   # le pages swerpi non hanno stato: se esistono sono live
                    rows.append({
                        "id": it.get("id", ""),
                        "title": title.replace("|", "/").replace("\n", " ").strip()[:140],
                        "slug": slug,
                        "status": status,
                        "url": url,
                    })
                (d / f"{fname}.md").write_text(
                    _rows_table_generic(fname, ws_slug or "site", rows, when), encoding="utf-8")
                counts[fname] = len(rows)
    except httpx.HTTPStatusError as e:
        return {"ok": False, "error": f"SwerpiCommerce {e.response.status_code}: verifica URL/credenziali"}
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"connessione SwerpiCommerce fallita: {e}"}
    return {"ok": True, "counts": counts, "synced_at": when}
