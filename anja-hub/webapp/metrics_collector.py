"""metrics_collector.py — refresh delle metriche (GSC/GA → metrics.db).

Cablaggio del bottone "Aggiorna" delle Statistiche. La raccolta reale (GSC + GA4)
gira in `google_collect` via OAuth user-token; serve il token Google in
<scope>/.anjawiki/google-token.json (fallback hub) + le property nei Connettori
Google (GSC_SITE, GA4_PROPERTY_ID). Senza token, riporta lo stato senza scrivere.
"""

from __future__ import annotations

from pathlib import Path


def connection_status(vault_values: dict, token_present: bool = False) -> list[dict]:
    """Stato per-sorgente: configured (property impostata) + connected (property +
    token OAuth Google presente)."""
    gsc = (vault_values.get("GSC_SITE") or "").strip()
    ga4 = (vault_values.get("GA4_PROPERTY_ID") or "").strip()
    ads = (vault_values.get("GOOGLE_ADS_CUSTOMER_ID") or "").strip()
    merchant = (vault_values.get("MERCHANT_ACCOUNT_ID") or "").strip()
    meta_ads = bool((vault_values.get("META_ADS_TOKEN") or "").strip()
                    and (vault_values.get("META_AD_ACCOUNT_ID") or "").strip())
    social = bool((vault_values.get("META_ACCESS_TOKEN") or "").strip()
                  and ((vault_values.get("META_PAGE_ID") or "").strip()
                       or (vault_values.get("META_IG_USER_ID") or "").strip()))
    return [
        {"key": "gsc", "label": "Search Console", "configured": bool(gsc), "connected": bool(gsc and token_present)},
        {"key": "ga", "label": "Analytics GA4", "configured": bool(ga4), "connected": bool(ga4 and token_present)},
        {"key": "ads", "label": "Google Ads", "configured": bool(ads), "connected": bool(ads and token_present)},
        {"key": "merchant", "label": "Google Merchant", "configured": bool(merchant), "connected": bool(merchant and token_present)},
        {"key": "meta_ads", "label": "Meta Ads", "configured": meta_ads, "connected": meta_ads},
        {"key": "social", "label": "Social (FB/IG)", "configured": social, "connected": social},
    ]


def _collect_social(db_path: Path, vault_values: dict, days: int, result: dict) -> dict:
    """Raccolta social account-level (IG/FB) + merge nel result."""
    tok = (vault_values.get("META_ACCESS_TOKEN") or "").strip()
    page = (vault_values.get("META_PAGE_ID") or "").strip()
    ig = (vault_values.get("META_IG_USER_ID") or "").strip()
    if not (tok and (page or ig)):
        return result
    import social_collect
    s = social_collect.collect(Path(db_path), tok, page, ig, days=days)
    result["collected"] = result.get("collected", 0) + s["ig_days"] + s["fb_days"]
    extra = (f" Social: {s['ig_days']} gg IG · {s['fb_days']} gg FB · "
             f"{s['audience_rows']} righe demografia.")
    if s["errors"]:
        extra += " ⚠️ " + "; ".join(s["errors"][:3])
        if not s["ok"]:
            result["ok"] = False
    result["note"] = (result.get("note") or "").rstrip() + extra
    return result


def _collect_meta(db_path: Path, vault_values: dict, days: int, result: dict) -> dict:
    """Raccolta Meta Ads (indipendente dal token Google) + merge nel result.
    Va chiamata DOPO google_collect: il suo replace svuota tutta ads_daily."""
    tok = (vault_values.get("META_ADS_TOKEN") or "").strip()
    acc = (vault_values.get("META_AD_ACCOUNT_ID") or "").strip()
    if not (tok and acc):
        return result
    import meta_ads_collect
    m = meta_ads_collect.collect(Path(db_path), tok, acc, days=days)
    result["collected"] = result.get("collected", 0) + m["rows"]
    extra = f" Meta Ads: {m['rows']} righe."
    if m["errors"]:
        extra += " ⚠️ " + "; ".join(m["errors"])
        result["ok"] = False
    result["note"] = (result.get("note") or "").rstrip() + extra
    return result


def refresh(db_path: Path, vault_values: dict, *, scope_dir: Path | None = None,
            hub_dir: Path | None = None, days: int = 90) -> dict:
    """Raccolta reale GSC+GA → metrics.db via google_collect. Senza token o senza
    property configurata, riporta lo stato senza scrivere. Ritorna {ok, collected,
    sources, note, detail?}."""
    import google_collect
    token = google_collect.find_token(scope_dir, hub_dir)
    sources = connection_status(vault_values, token_present=bool(token))
    gsc = (vault_values.get("GSC_SITE") or "").strip()
    ga4 = (vault_values.get("GA4_PROPERTY_ID") or "").strip()
    merchant = (vault_values.get("MERCHANT_ACCOUNT_ID") or "").strip()

    if not token:
        configured = [s["label"] for s in sources if s["configured"]]
        return _collect_social(db_path, vault_values, days, _collect_meta(db_path, vault_values, days, {
            "ok": True, "collected": 0, "sources": sources, "note": (
                "Sorgenti configurate (" + (", ".join(configured) if configured else "nessuna") +
                ") ma manca il token OAuth Google: caricalo in <workspace>/.anjawiki/google-token.json "
                "(o a livello hub).")}))
    if not (gsc or ga4 or merchant):
        return _collect_social(db_path, vault_values, days, _collect_meta(db_path, vault_values, days, {
            "ok": True, "collected": 0, "sources": sources,
            "note": "Token OAuth presente ma nessuna property configurata "
                    "(GSC_SITE / GA4_PROPERTY_ID / MERCHANT_ACCOUNT_ID nei Connettori Google)."}))

    res = google_collect.collect(Path(db_path), token, gsc_site=gsc, ga_property=ga4,
                                 merchant_account=merchant, days=days)
    collected = (res["gsc_daily"] + res["gsc_queries"] + res.get("gsc_pages", 0)
                 + res["ga_daily"] + res["ads_daily"]
                 + res.get("merchant_products", 0) + res.get("merchant_daily", 0))
    note = (f"Raccolti {res['gsc_daily']} gg GSC · {res['gsc_queries']} query · "
            f"{res.get('gsc_pages', 0)} pagine · {res['ga_daily']} righe GA · "
            f"{res['ads_daily']} righe Ads ({res['range'][0]} → {res['range'][1]}).")
    if merchant:
        note += (f" Merchant: {res.get('merchant_products', 0)} prodotti · "
                 f"{res.get('merchant_issues', 0)} issues · "
                 f"{res.get('merchant_daily', 0)} gg listing.")
    if res["errors"]:
        note += " ⚠️ " + "; ".join(res["errors"])
    return _collect_social(db_path, vault_values, days, _collect_meta(db_path, vault_values, days, {
        "ok": res["ok"], "collected": collected, "sources": sources,
        "note": note, "detail": res}))
