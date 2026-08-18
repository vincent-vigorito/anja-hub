#!/usr/bin/env python3
"""mcp_marketing_server.py — MCP server `anja_marketing` (F-MarketingVertical Fase 0).

Espone i tool di gestione sito/marketing per UN brand (= il workspace attivo).
Credenziali dal vault a 2 livelli (marketing/vault.py): vault del brand
(`.secrets.env`) + connettori agency (Google OAuth). Nessun `wp_use_site`:
lo scope È il sito (un server per workspace, scopizzato via env nel .mcp.json
del workspace). Vedi anja-marketing-workspace-design.md §3/§6.

Tool-group via env `ANJA_TOOL_GROUPS` (default "cms,analytics,social"):
  - cms:       wp_site_info, wp_list/get/create/update/delete_content,
               wp_get/set_seo, wp_list/create_term
  - analytics: gsc_list_properties, gsc_query, ga_list_properties, ga_report, ads_check, ads_report
               (read-only; query/report PINNED ai resource-ID del brand)
  - social:    meta_check, meta_publish_fb, meta_publish_ig

Avvio (debug):
  ANJA_MARKETING_VAULT=<ws>/.anjawiki/.secrets.env \
  ANJA_GOOGLE_CONNECTORS=<hub>/config/connectors \
  python3.12 scripts/mcp_marketing_server.py
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Any

# Il package `marketing` è accanto a questo script (scripts/marketing/).
sys.path.insert(0, str(Path(__file__).resolve().parent))
# `audit_io` / `metrics_io` vivono nella webapp dell'hub (per il tool audit).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "webapp"))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from marketing import vault  # noqa: E402
from marketing.wp_client import WordPressClient  # noqa: E402

mcp = FastMCP("anja_marketing")

GROUPS = {
    g.strip()
    for g in os.environ.get("ANJA_TOOL_GROUPS", "cms,analytics,social").split(",")
    if g.strip()
}


def _maybe(group: str):
    """Registra il tool solo se il group è attivo in ANJA_TOOL_GROUPS."""
    def deco(fn):
        return mcp.tool()(fn) if group in GROUPS else fn
    return deco


def _maybe_any(*groups: str):
    """Registra il tool se ALMENO uno dei group è attivo."""
    def deco(fn):
        return mcp.tool()(fn) if any(g in GROUPS for g in groups) else fn
    return deco


# ----------------------------------------------------------------------
# Diagnostica (sempre attiva)
# ----------------------------------------------------------------------

@mcp.tool()
async def marketing_status() -> dict[str, Any]:
    """Stato del marketing server per il brand corrente: scope, tool-group attivi, presenza delle chiavi nel vault (booleani, mai i valori) e connettore Google. Usalo come primo check."""
    backend = vault.backend()
    if backend == "swerpi":
        cms_keys = ["SWERPICOMMERCE_BASE_URL", "SWERPICOMMERCE_API_ID",
                    "SWERPICOMMERCE_API_SECRET", "SWERPICOMMERCE_BEARER_AUTH"]
    else:
        cms_keys = ["WP_BASE_URL", "WP_USERNAME", "WP_APP_PASSWORD"]
    keys = cms_keys + ["META_ACCESS_TOKEN", "META_PAGE_ID", "META_IG_USER_ID",
                       "GA4_PROPERTY_ID", "GSC_SITE", "MERCHANT_ACCOUNT_ID",
                       "GOOGLE_ADS_CUSTOMER_ID", "GOOGLE_ADS_DEVELOPER_TOKEN"]
    return {
        "scope": vault.scope(),
        "backend": backend or "wp (assunto)",
        "tool_groups": sorted(GROUPS),
        "vault_path": os.environ.get("ANJA_MARKETING_VAULT", "<unset>"),
        "vault_keys_present": {k: bool(vault.get(k)) for k in keys},
        "connectors_dir": str(vault.connectors_dir()),
        "google_oauth_token_present": (vault.connectors_dir() / "gsc-token.json").is_file(),
    }


# ----------------------------------------------------------------------
# CMS — WordPress (un solo brand dal vault)
# ----------------------------------------------------------------------

_wp: WordPressClient | None = None


def get_wp() -> WordPressClient:
    global _wp
    if _wp is None:
        base_url, username, app_password = vault.wp_config()
        _wp = WordPressClient(base_url, username, app_password)
    return _wp


@_maybe("cms")
async def wp_site_info() -> dict[str, Any]:
    """Verifica la connessione al sito WordPress del brand: info sito + utente autenticato (ruoli inclusi). Primo controllo CMS."""
    client = get_wp()
    return {
        "site": await client.site_info(),
        "authenticated_user": await client.current_user(),
    }


@_maybe("cms")
async def wp_list_content(
    post_type: str,
    search: str | None = None,
    status: str | None = None,
    page: int = 1,
    per_page: int = 10,
    orderby: str | None = None,
    order: str | None = None,
) -> list[dict[str, Any]]:
    """Elenca articoli o pagine WordPress con ricerca e filtri (campi sintetici).

    Args:
        post_type: "posts" (articoli) o "pages" (pagine).
        search: testo da cercare in titolo/contenuto.
        status: publish, draft, pending, private, future; più valori separati da virgola.
        page: pagina dei risultati.
        per_page: risultati per pagina (max 100).
        orderby: date, modified, title, slug.
        order: asc o desc.
    """
    return await get_wp().list_content(
        post_type=post_type, search=search, status=status,
        page=page, per_page=per_page, orderby=orderby, order=order,
    )


@_maybe("cms")
async def wp_get_content(post_type: str, content_id: int) -> dict[str, Any]:
    """Recupera un singolo articolo o pagina con il contenuto completo (HTML sorgente). Usalo SEMPRE prima di modificare.

    Args:
        post_type: "posts" o "pages".
        content_id: ID del contenuto.
    """
    return await get_wp().get_content(post_type, content_id)


@_maybe("cms")
async def wp_create_content(
    post_type: str,
    title: str,
    content: str | None = None,
    status: str = "draft",
    slug: str | None = None,
    excerpt: str | None = None,
    date: str | None = None,
    categories: list[int] | None = None,
    tags: list[int] | None = None,
    parent: int | None = None,
) -> dict[str, Any]:
    """Crea un nuovo articolo o pagina. Di default come bozza (draft).

    Args:
        post_type: "posts" o "pages".
        title: titolo del contenuto.
        content: corpo in HTML (h2/h3, p, ul, ol, a, img...).
        status: draft, publish, pending, private, future (default: draft).
        slug: slug URL (se omesso WordPress lo genera dal titolo).
        excerpt: riassunto, funge anche da meta description di fallback.
        date: data pubblicazione ISO 8601 (per status=future).
        categories: ID delle categorie (solo posts).
        tags: ID dei tag (solo posts).
        parent: ID della pagina genitore (solo pages).
    """
    payload: dict[str, Any] = {"title": title, "status": status}
    for key, value in [
        ("content", content), ("slug", slug), ("excerpt", excerpt), ("date", date),
        ("categories", categories), ("tags", tags), ("parent", parent),
    ]:
        if value is not None:
            payload[key] = value
    return _summary(await get_wp().create_content(post_type, payload))


@_maybe("cms")
async def wp_update_content(
    post_type: str,
    content_id: int,
    title: str | None = None,
    content: str | None = None,
    status: str | None = None,
    slug: str | None = None,
    excerpt: str | None = None,
    date: str | None = None,
    categories: list[int] | None = None,
    tags: list[int] | None = None,
    parent: int | None = None,
) -> dict[str, Any]:
    """Aggiorna un articolo o pagina esistente. Passa solo i campi da modificare. ATTENZIONE: content sostituisce integralmente il corpo — recupera prima l'HTML con wp_get_content.

    Args:
        post_type: "posts" o "pages".
        content_id: ID del contenuto da aggiornare.
        title: nuovo titolo.
        content: nuovo corpo HTML (sostituisce tutto).
        status: draft, publish, pending, private, future.
        slug: nuovo slug URL.
        excerpt: nuovo riassunto.
        date: data pubblicazione ISO 8601.
        categories: ID categorie (solo posts).
        tags: ID tag (solo posts).
        parent: ID pagina genitore (solo pages).
    """
    payload: dict[str, Any] = {}
    for key, value in [
        ("title", title), ("content", content), ("status", status), ("slug", slug),
        ("excerpt", excerpt), ("date", date), ("categories", categories),
        ("tags", tags), ("parent", parent),
    ]:
        if value is not None:
            payload[key] = value
    if not payload:
        raise ValueError("Nessun campo da aggiornare specificato")
    return _summary(await get_wp().update_content(post_type, content_id, payload))


@_maybe("cms")
async def wp_delete_content(post_type: str, content_id: int, force: bool = False) -> dict[str, Any]:
    """Elimina un articolo o pagina. Default = cestino (recuperabile); force=true = definitivo. Richiede SEMPRE conferma esplicita dell'utente.

    Args:
        post_type: "posts" o "pages".
        content_id: ID del contenuto.
        force: true = eliminazione definitiva (default: false, cestino).
    """
    result = await get_wp().delete_content(post_type, content_id, force=force)
    return _summary(result) if isinstance(result, dict) else result


@_maybe("cms")
async def wp_list_terms(
    taxonomy: str,
    search: str | None = None,
    per_page: int = 50,
) -> list[dict[str, Any]]:
    """Elenca categorie o tag del sito (id, nome, slug, conteggio). Usalo per trovare gli ID da assegnare.

    Args:
        taxonomy: "categories" o "tags".
        search: filtro sul nome.
        per_page: max risultati.
    """
    return await get_wp().list_terms(taxonomy=taxonomy, search=search, per_page=per_page)


@_maybe("cms")
async def wp_create_term(
    taxonomy: str,
    name: str,
    description: str | None = None,
    parent: int | None = None,
) -> dict[str, Any]:
    """Crea una nuova categoria o tag.

    Args:
        taxonomy: "categories" o "tags".
        name: nome del termine.
        description: descrizione opzionale.
        parent: ID categoria genitore (solo categories).
    """
    return await get_wp().create_term(
        taxonomy=taxonomy, name=name, description=description, parent=parent
    )


@_maybe("cms")
async def wp_get_seo(content_id: int, include_effective: bool = False) -> dict[str, Any]:
    """Legge i meta SEO (SEOPress) di un articolo/pagina: title, description, keyword target, robots/canonical, social. Valori vuoti = mai impostati (template globali).

    Args:
        content_id: ID dell'articolo o pagina.
        include_effective: se true include la SEO "effettiva" calcolata da SEOPress.
    """
    client = get_wp()
    result: dict[str, Any] = await client.seo_get(content_id)
    if include_effective:
        result["effective"] = await client.seo_effective(content_id)
    return result


@_maybe("cms")
async def wp_set_seo(
    content_id: int,
    title: str | None = None,
    description: str | None = None,
    target_keywords: list[str] | None = None,
    canonical: str | None = None,
    noindex: bool | None = None,
    nofollow: bool | None = None,
    fb_title: str | None = None,
    fb_description: str | None = None,
    x_title: str | None = None,
    x_description: str | None = None,
) -> dict[str, Any]:
    """Imposta i meta SEO (SEOPress). Passa solo i campi da modificare. Dopo la scrittura rilegge i meta salvati per verifica.

    Args:
        content_id: ID dell'articolo o pagina.
        title: meta title (~50-60 char, keyword principale all'inizio).
        description: meta description (~140-155 char, con call to action).
        target_keywords: lista di keyword target per l'analisi SEOPress.
        canonical: URL canonico personalizzato.
        noindex: true per escludere dai motori (USARE CON CAUTELA).
        nofollow: true per non far seguire i link.
        fb_title: titolo Open Graph (Facebook).
        fb_description: descrizione Open Graph (Facebook).
        x_title: titolo per X/Twitter.
        x_description: descrizione per X/Twitter.
    """
    return await get_wp().seo_apply(
        content_id,
        title=title, description=description, target_keywords=target_keywords,
        canonical=canonical, noindex=noindex, nofollow=nofollow,
        fb_title=fb_title, fb_description=fb_description,
        x_title=x_title, x_description=x_description,
    )


# ----------------------------------------------------------------------
# ANALYTICS — Google Search Console + GA4 (read-only, pinned al brand)
# ----------------------------------------------------------------------

_gsc = None
_ga = None
_merchant = None


def get_gsc():
    global _gsc
    if _gsc is None:
        from marketing.gsc_client import GSCClient
        _gsc = GSCClient()
    return _gsc


def get_ga():
    global _ga
    if _ga is None:
        from marketing.ga_client import GAClient
        _ga = GAClient()
    return _ga


@_maybe("analytics")
async def gsc_list_properties() -> dict[str, Any]:
    """Elenca le proprietà Google Search Console accessibili dal connettore agency (diagnostica). Per le query usa gsc_query (pinned al brand)."""
    client = get_gsc()
    sites = await asyncio.to_thread(client.list_sites)
    return {"identity": client.service_account_email, "properties": sites}


@_maybe("analytics")
async def gsc_query(
    start_date: str,
    end_date: str,
    dimensions: list[str] | None = None,
    row_limit: int = 100,
    search_type: str = "web",
    query_filter: str | None = None,
    page_filter: str | None = None,
    site_url: str | None = None,
) -> dict[str, Any]:
    """Dati di ricerca GSC del brand (click, impression, CTR %, posizione media). La proprietà è PINNED al brand (GSC_SITE del vault); non serve passarla.

    Args:
        start_date: "YYYY-MM-DD" (i dati GSC hanno ~2 giorni di ritardo).
        end_date: "YYYY-MM-DD".
        dimensions: tra "query","page","date","device","country" (default ["query"]).
        row_limit: max righe (default 100, max 25000).
        search_type: "web" (default), "image", "video", "news".
        query_filter: filtra le query che CONTENGONO questo testo.
        page_filter: filtra le pagine il cui URL CONTIENE questo testo.
        site_url: override (deve coincidere con GSC_SITE del brand, altrimenti errore).
    """
    pinned = vault.get("GSC_SITE")
    target = site_url or pinned
    if not target:
        raise ValueError("GSC_SITE non è nel vault del brand e site_url non è stato passato")
    if site_url and pinned and site_url != pinned:
        raise ValueError(
            f"site_url '{site_url}' ≠ GSC_SITE del brand '{pinned}' (pin di isolamento per-workspace)"
        )

    filters = []
    if query_filter:
        filters.append({"dimension": "query", "operator": "contains", "expression": query_filter})
    if page_filter:
        filters.append({"dimension": "page", "operator": "contains", "expression": page_filter})

    return await asyncio.to_thread(
        get_gsc().query, target, start_date, end_date,
        dimensions, row_limit, 0, search_type, filters or None,
    )


@_maybe("analytics")
async def ga_list_properties() -> dict[str, Any]:
    """Elenca le proprietà GA4 accessibili dal connettore agency (diagnostica). Per i report usa ga_report (pinned al brand)."""
    props = await asyncio.to_thread(get_ga().list_properties)
    return {"properties": props, "count": len(props)}


@_maybe("analytics")
async def ga_report(
    start_date: str,
    end_date: str,
    dimensions: list[str] | None = None,
    metrics: list[str] | None = None,
    row_limit: int = 100,
    property_id: str | None = None,
) -> dict[str, Any]:
    """Report GA4 del brand (sessioni, utenti, conversioni, entrate...). La property è PINNED al brand (GA4_PROPERTY_ID del vault); non serve passarla.

    Args:
        start_date: "YYYY-MM-DD" o relativo ("28daysAgo", "yesterday").
        end_date: "YYYY-MM-DD" o relativo.
        dimensions: es. ["sessionDefaultChannelGroup"] (default), ["landingPage"], ["pagePath"], ["date"], ["sessionSourceMedium"], ["deviceCategory"], ["country"].
        metrics: es. ["sessions","activeUsers","keyEvents"] (default); altri: newUsers, screenPageViews, averageSessionDuration, bounceRate, totalRevenue, purchaseRevenue.
        row_limit: max righe (default 100).
        property_id: override (deve coincidere con GA4_PROPERTY_ID del brand, altrimenti errore).
    """
    pinned = vault.get("GA4_PROPERTY_ID")
    target = property_id or pinned
    if not target:
        raise ValueError("GA4_PROPERTY_ID non è nel vault del brand e property_id non è stato passato")
    if property_id and pinned and property_id.removeprefix("properties/") != pinned.removeprefix("properties/"):
        raise ValueError(
            f"property_id '{property_id}' ≠ GA4_PROPERTY_ID del brand '{pinned}' (pin di isolamento)"
        )
    return await asyncio.to_thread(
        get_ga().run_report, target, start_date, end_date, dimensions, metrics, row_limit,
    )


def get_merchant():
    global _merchant
    if _merchant is None:
        from marketing.merchant_client import MerchantClient
        _merchant = MerchantClient()
    return _merchant


def _merchant_account() -> str:
    acc = vault.get("MERCHANT_ACCOUNT_ID")
    if not acc:
        raise ValueError("MERCHANT_ACCOUNT_ID non è nel vault del brand (Connettori → Google)")
    return str(acc).strip()


def _mql_date(s: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", (s or "").strip()):
        raise ValueError(f"data non valida: {s!r} (atteso YYYY-MM-DD)")
    return s.strip()


@_maybe("analytics")
async def merchant_status() -> dict[str, Any]:
    """Salute del feed Google Merchant del brand: prodotti totali, disapprovati/demoted e conteggio issues. Primo check quando si parla di Google Shopping. L'account è PINNED al brand (MERCHANT_ACCOUNT_ID del vault)."""
    acc = _merchant_account()
    products = await asyncio.to_thread(get_merchant().list_products, acc)
    by_status: dict[str, int] = {}
    n_issues = 0
    for p in products:
        by_status[p["status"]] = by_status.get(p["status"], 0) + 1
        n_issues += len(p["issues"])
    return {"account_id": acc, "products": len(products),
            "by_status": by_status, "issues_total": n_issues}


@_maybe("analytics")
async def merchant_issues(severity: str = "", top: int = 50) -> dict[str, Any]:
    """Issues dei prodotti sul feed Google Merchant (disapprovazioni/demozioni Shopping) con codice, motivo, attributo e link alla doc. Usalo quando i prodotti non compaiono su Google o per l'audit del feed.

    Args:
        severity: filtro opzionale "DISAPPROVED" | "DEMOTED" | "NOT_IMPACTED" (default tutte).
        top: max prodotti con issues riportati (default 50).
    """
    acc = _merchant_account()
    products = await asyncio.to_thread(get_merchant().list_products, acc)
    sev = severity.strip().upper()
    order = {"disapproved": 0, "demoted": 1, "ok": 2}
    rows = []
    for p in products:
        issues = [i for i in p["issues"] if not sev or i["severity"] == sev]
        if issues:
            rows.append({"offer_id": p["offer_id"], "title": p["title"],
                         "link": p["link"], "status": p["status"],
                         "issues": issues})
    rows.sort(key=lambda r: order.get(r["status"], 3))
    return {"account_id": acc, "count": len(rows), "products": rows[: max(1, top)]}


@_maybe("analytics")
async def merchant_report(
    start_date: str,
    end_date: str,
    group_by_product: bool = False,
    top: int = 100,
) -> dict[str, Any]:
    """Performance dei listing Google Shopping del brand (clic, impression, CTR — incluse le free listings). L'account è PINNED al brand (MERCHANT_ACCOUNT_ID del vault).

    Args:
        start_date: "YYYY-MM-DD".
        end_date: "YYYY-MM-DD".
        group_by_product: True = righe per prodotto (offer_id + title); False = totali giornalieri.
        top: max righe (default 100).
    """
    acc = _merchant_account()
    s, e = _mql_date(start_date), _mql_date(end_date)
    fields = ("offer_id, title, marketing_method, clicks, impressions, "
              "click_through_rate, conversions, conversion_value"
              if group_by_product
              else "date, marketing_method, clicks, impressions, "
                   "click_through_rate, conversions, conversion_value")
    q = (f"SELECT {fields} FROM product_performance_view "
         f"WHERE date BETWEEN '{s}' AND '{e}'")
    raw = await asyncio.to_thread(get_merchant().search_report, acc, q, max(1, top))
    rows = []
    for r in raw:
        v = r.get("productPerformanceView") or {}
        d = v.get("date")
        if isinstance(d, dict):
            v["date"] = f"{d.get('year', 0):04d}-{d.get('month', 0):02d}-{d.get('day', 0):02d}"
        cv = v.get("conversionValue")
        if isinstance(cv, dict):   # Price {amountMicros, currencyCode}
            v["conversionValue"] = round(int(cv.get("amountMicros", 0) or 0) / 1e6, 2)
        rows.append(v)
    return {"account_id": acc, "count": len(rows), "rows": rows}


# ----------------------------------------------------------------------
# GOOGLE ADS — Ads API nativa (GAQL), sola lettura
# ----------------------------------------------------------------------

_ads = None


def get_ads():
    global _ads
    if _ads is None:
        from marketing.ads_client import AdsClient
        _ads = AdsClient()
    return _ads


def _ads_gaql_where(start_date: str, end_date: str) -> str:
    return f"segments.date BETWEEN '{_mql_date(start_date)}' AND '{_mql_date(end_date)}'"


def _ads_row(r: dict) -> dict:
    """Appiattisce una riga searchStream in un dict leggibile (costi in valuta)."""
    seg, met = r.get("segments") or {}, r.get("metrics") or {}
    out: dict[str, Any] = {}
    for key in ("campaign", "adGroup", "adGroupCriterion", "searchTermView"):
        if key in r:
            out.update({f"{key}.{k}": v for k, v in (r[key] or {}).items() if k != "resourceName"})
    if seg.get("date"):
        out["date"] = seg["date"]
    if "costMicros" in met:
        out["cost"] = round(int(met.get("costMicros", 0) or 0) / 1e6, 2)
    for k in ("impressions", "clicks", "conversions", "conversionsValue", "ctr", "averageCpc"):
        if k in met:
            v = met[k]
            out[k] = round(int(v) / 1e6, 2) if k == "averageCpc" else v
    return out


@_maybe("analytics")
async def ads_check() -> dict[str, Any]:
    """Verifica la connessione Google Ads del brand: developer token, account raggiungibili, customer configurato. Primo check prima dei report."""
    client = get_ads()
    try:
        accessible = await asyncio.to_thread(client.list_accessible_customers)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc),
                "hint": "developer token 'test access' legge solo account di test: per dati reali "
                        "serve 'basic access' (Google Ads → Tools → API Center)"}
    cid = ""
    try:
        cid = client.customer_id()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "accessible_customers": accessible, "error": str(exc)}
    return {"ok": True, "customer_id": cid, "accessible_customers": accessible,
            "customer_reachable": cid in accessible or bool(client._headers.get("login-customer-id"))}


@_maybe("analytics")
async def ads_report(
    start_date: str,
    end_date: str,
    level: str = "campaign",
    top: int = 100,
) -> dict[str, Any]:
    """Performance Google Ads del brand (dati NATIVI: spesa, impression, click, CTR, CPC medio, conversioni e valore). L'account è PINNED al brand (GOOGLE_ADS_CUSTOMER_ID del vault).

    Args:
        start_date: "YYYY-MM-DD".
        end_date: "YYYY-MM-DD".
        level: "campaign" (default) | "ad_group" | "keyword" | "search_term" (query reali degli utenti) | "daily" (totali per giorno).
        top: max righe (default 100), ordinate per spesa decrescente.
    """
    client = get_ads()
    cid = client.customer_id()
    where = _ads_gaql_where(start_date, end_date)
    metrics = ("metrics.cost_micros, metrics.impressions, metrics.clicks, metrics.ctr, "
               "metrics.average_cpc, metrics.conversions, metrics.conversions_value")
    queries = {
        "campaign": f"SELECT campaign.name, campaign.status, campaign.advertising_channel_type, {metrics} "
                    f"FROM campaign WHERE {where} AND metrics.impressions > 0 ORDER BY metrics.cost_micros DESC",
        "ad_group": f"SELECT campaign.name, ad_group.name, ad_group.status, {metrics} "
                    f"FROM ad_group WHERE {where} AND metrics.impressions > 0 ORDER BY metrics.cost_micros DESC",
        "keyword": f"SELECT campaign.name, ad_group.name, ad_group_criterion.keyword.text, "
                   f"ad_group_criterion.keyword.match_type, ad_group_criterion.quality_info.quality_score, {metrics} "
                   f"FROM keyword_view WHERE {where} AND metrics.impressions > 0 ORDER BY metrics.cost_micros DESC",
        "search_term": f"SELECT campaign.name, search_term_view.search_term, search_term_view.status, {metrics} "
                       f"FROM search_term_view WHERE {where} AND metrics.impressions > 0 ORDER BY metrics.cost_micros DESC",
        "daily": f"SELECT segments.date, {metrics} FROM customer WHERE {where} ORDER BY segments.date",
    }
    gaql = queries.get(level)
    if not gaql:
        raise ValueError(f"level non valido: {level} (ammessi: {sorted(queries)})")
    gaql += f" LIMIT {max(1, min(int(top), 1000))}"
    raw = await asyncio.to_thread(client.search, cid, gaql)
    rows = [_ads_row(r) for r in raw]
    total_cost = round(sum(r.get("cost", 0) for r in rows), 2)
    return {"customer_id": cid, "level": level, "count": len(rows),
            "total_cost": total_cost, "rows": rows}


# ----------------------------------------------------------------------
# SOCIAL — Meta (FB + IG) organico
# ----------------------------------------------------------------------

def _get_meta():
    from marketing.meta_client import MetaClient
    return MetaClient.from_vault()


@_maybe("social")
async def meta_check() -> dict[str, Any]:
    """Verifica la connessione Meta del brand: identità token, pagina FB e account Instagram collegato. Primo check prima di pubblicare."""
    client = _get_meta()
    try:
        return await asyncio.to_thread(client.discover)
    finally:
        client.close()


@_maybe("social")
async def meta_publish_fb(
    message: str,
    link: str | None = None,
    image_url: str | None = None,
    scheduled_unix: int | None = None,
) -> dict[str, Any]:
    """Pubblica un post sulla pagina Facebook del brand. ATTENZIONE: pubblica DAVVERO — solo con approvazione esplicita dell'utente nella conversazione.

    Args:
        message: testo del post.
        link: URL da allegare come anteprima (alternativo a image_url).
        image_url: URL PUBBLICO di un'immagine → post con foto (caption=message).
        scheduled_unix: timestamp UNIX futuro → post programmato (10 min - 75 giorni).
    """
    client = _get_meta()
    try:
        if image_url:
            res = await asyncio.to_thread(client.fb_photo, image_url, message, scheduled_unix)
        else:
            res = await asyncio.to_thread(client.fb_post, message, link, scheduled_unix)
        return {"published": scheduled_unix is None, "result": res}
    finally:
        client.close()


@_maybe("social")
async def meta_publish_ig(caption: str, image_urls: list[str]) -> dict[str, Any]:
    """Pubblica su Instagram Business del brand: 1 immagine = post, 2-10 = carosello. Immagini DA URL pubblici. ATTENZIONE: pubblica DAVVERO, IG non supporta la programmazione via API — solo con approvazione esplicita."""
    client = _get_meta()
    try:
        if len(image_urls) == 1:
            res = await asyncio.to_thread(client.ig_post, image_urls[0], caption)
        else:
            res = await asyncio.to_thread(client.ig_carousel, image_urls, caption)
        return {"published": True, "result": res}
    finally:
        client.close()


@_maybe("social")
async def social_kit_build(
    campaign: str,
    slides: list,
    hero_image: str | None = None,
    readme: str | None = None,
) -> dict[str, Any]:
    """Genera un KIT SOCIAL completo in `files/social/<campaign>/`: carosello (slide PNG + JPG
    per IG), PDF LinkedIn, hero, README e media-urls.json. Stile grafico del brand.

    Args:
        campaign: nome breve della campagna (slug, es. "firewall-pmi").
        slides: lista di slide. Ogni slide è un dict:
            {"kicker": "SEZIONE", "title": "Claim\\ngrande", "sub": "sottotitolo",
             "body": "paragrafo", "items": [["punto","dettaglio"], ...],
             "title_size": 88, "accent": "white"|"green", "scorri": true}.
            `title` = claim grande; `body` = paragrafo; `items` = lista numerata.
            L'ultima slide è la CTA (scorri=false automatico). Tieni i testi brevi
            (in caso di overflow arriva un warning nella risposta).
        hero_image: path di un'immagine hero (es. da anja_images) → copiata come hero.png.
        readme: copy markdown (testi per canale, orari, UTM) → README.md.
    """
    import os
    import re as _re
    camp = _re.sub(r"[^a-z0-9]+", "-", str(campaign).lower()).strip("-") or "kit"
    files_root = os.environ.get("ANJA_FILES_ROOT") or os.environ.get("ANJA_ROOT", ".")
    out_dir = Path(files_root) / "files" / "social" / camp
    brand = (vault.get("WP_BASE_URL", "") or "").replace("https://", "").replace("http://", "").strip("/") or "brand"
    from marketing import social_kit
    return await asyncio.to_thread(social_kit.build_kit, out_dir, slides, brand, hero_image, readme)


@_maybe_any("cms", "social")
async def wp_upload_media(file_path: str, alt_text: str | None = None, title: str | None = None) -> dict[str, Any]:
    """Carica un'immagine nella libreria media WordPress e ritorna l'URL PUBBLICO. Serve a Instagram
    (pubblica solo da URL pubblici): carica qui le slide prima di `meta_publish_ig`.

    Args:
        file_path: path locale dell'immagine (png/jpg/webp).
        alt_text: testo alternativo (accessibilità/SEO).
        title: titolo del media.
    """
    media = await get_wp().upload_media(file_path, alt_text=alt_text, title=title)
    url = media.get("source_url")
    if not url and isinstance(media.get("guid"), dict):
        url = media["guid"].get("rendered")
    return {"id": media.get("id"), "url": url, "title": title}


# ----------------------------------------------------------------------
# Audit — scoring deterministico dei prodotti (alimenta l'analisi dell'agente)
# ----------------------------------------------------------------------

def _metrics_db() -> Path:
    """`<ws>/data/metrics.db` derivato dal vault del brand (ANJA_MARKETING_VAULT)."""
    vp = os.environ.get("ANJA_MARKETING_VAULT", "")
    if vp:
        return Path(vp).resolve().parent.parent / "data" / "metrics.db"
    return Path("data/metrics.db")


@_maybe_any("cms", "analytics")
async def audit_products(top: int = 25) -> dict[str, Any]:
    """Audit SEO / E-E-A-T / GEO dei prodotti del brand — DETERMINISTICO (no LLM, no
    token, ripetibile): per ogni prodotto WooCommerce calcola tre score 0-100 (SEO,
    E-E-A-T, GEO), incrocia con Search Console e ordina per priority (potenziale × gap)
    + quick-win. Usalo PRIMA di analizzare a mano: ti dice QUALI prodotti sistemare e in
    che ordine, con i `signals` (h2/h3, faq, dosaggi, SDS, pdf…) per leggere il gap. Poi
    approfondisci i top con wp_get_content e proponi i fix con wp_update_content / wp_set_seo.

    Args:
        top: quanti prodotti (i più prioritari) restituire in dettaglio; il summary è su tutti.
    """
    import audit_io
    vals = {k: (vault.get(k) or "") for k in ("WP_BASE_URL", "WP_USERNAME", "WP_APP_PASSWORD")}
    res = await asyncio.to_thread(audit_io.audit, vals, _metrics_db(), limit=300)
    if not res.get("ok"):
        return {"error": res.get("error", "audit fallito")}
    prods = res["products"][:max(1, top)]
    return {
        "site": res["site"], "count": res["count"], "summary": res["summary"],
        "showing_top": len(prods),
        "products": [{
            "name": p["name"], "url": p["permalink"], "scores": p["scores"],
            "priority": p["priority"], "quick_win": p["quick_win"],
            "gsc": p["gsc"], "signals": p["text"],
        } for p in prods],
    }


@_maybe_any("cms", "analytics")
async def audit_content(kind: str = "posts", top: int = 25) -> dict[str, Any]:
    """Audit SEO / E-E-A-T / GEO dei CONTENUTI editoriali — DETERMINISTICO (no LLM).
    kind: 'posts' (articoli) o 'pages' (pagine). Qui l'E-E-A-T è editoriale (profondità,
    fonti esterne, citazioni, freschezza, dati, firma), non e-commerce. Stesso uso di
    audit_products: triage per priority, poi approfondisci e sistemi i top con
    wp_get_content / wp_update_content. Solo backend WordPress/Woo (su swerpi
    ritorna un errore che indirizza alla CLI).

    Args:
        kind: 'posts' (articoli) | 'pages' (pagine).
        top: quanti contenuti (i più prioritari) in dettaglio; il summary è su tutti.
    """
    if vault.backend() == "swerpi":
        return {"error": "backend swerpi: audit_content legge da WordPress e qui non si applica. "
                         "Per i contenuti usa la CLI `swerpicommerce-pp-cli` in Bash "
                         "(articles list / pages list, credenziali già nel vault del workspace).",
                "backend": "swerpi"}
    if kind not in ("posts", "pages"):
        return {"error": "kind deve essere 'posts' o 'pages'"}
    import audit_io
    vals = {k: (vault.get(k) or "") for k in ("WP_BASE_URL", "WP_USERNAME", "WP_APP_PASSWORD")}
    res = await asyncio.to_thread(audit_io.audit, vals, _metrics_db(), kind=kind, limit=300)
    if not res.get("ok"):
        return {"error": res.get("error", "audit fallito")}
    items = res["products"][:max(1, top)]
    return {
        "site": res["site"], "kind": res["kind"], "count": res["count"], "summary": res["summary"],
        "showing_top": len(items),
        "items": [{
            "name": p["name"], "url": p["permalink"], "scores": p["scores"],
            "priority": p["priority"], "quick_win": p["quick_win"],
            "gsc": p["gsc"], "signals": p["text"],
        } for p in items],
    }


def _summary(item: dict[str, Any]) -> dict[str, Any]:
    """Riduce la risposta di create/update/delete ai campi utili."""
    title = item.get("title")
    if isinstance(title, dict):
        title = title.get("raw") or title.get("rendered")
    return {
        "id": item.get("id"),
        "title": title,
        "slug": item.get("slug"),
        "status": item.get("status"),
        "type": item.get("type"),
        "link": item.get("link"),
        "date": item.get("date"),
        "modified": item.get("modified"),
    }


if __name__ == "__main__":
    mcp.run()  # trasporto stdio
