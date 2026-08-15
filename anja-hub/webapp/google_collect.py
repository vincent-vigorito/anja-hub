"""google_collect.py — collector reale Google Search Console + GA4 → metrics.db.

Tier 1. Riusa l'OAuth user-token (con refresh_token) come il prototipo
anja-marketer: HTTP raw via `google.auth` AuthorizedSession (niente googleapiclient
né SDK Google — già disponibili google-auth + google-auth-oauthlib nell'hub).
Scrive nello schema di metrics_io (gsc_daily / gsc_queries / ga_daily / ads_daily).

Token: file formato "authorized_user" (token, refresh_token, client_id,
client_secret, token_uri, scopes), in <scope>/.anjawiki/google-token.json con
fallback <hub>/.anjawiki/google-token.json. Property id dai connettori Google
(GSC_SITE, GA4_PROPERTY_ID). L'access token viene rinnovato in automatico dal
refresh_token e ri-persistito a fine raccolta.
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from urllib.parse import quote

import metrics_io


def _write_token_secure(path: Path, text: str) -> None:
    """Scrive un token OAuth in modo atomico e 0600 (niente finestra world-readable né
    file corrotto): il refresh persiste l'access token rinfrescato — non deve mai 0644."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass

SCOPES = [
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/content",
]
GSC_URL = "https://www.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
GA_URL = "https://analyticsdata.googleapis.com/v1beta/properties/{pid}:runReport"
# Merchant API (la Content API for Shopping muore il 18/08/2026)
MERCHANT_PRODUCTS_URL = "https://merchantapi.googleapis.com/products/v1/accounts/{acc}/products"
MERCHANT_REPORTS_URL = "https://merchantapi.googleapis.com/reports/v1/accounts/{acc}/reports:search"
TOKEN_NAME = "google-token.json"


def find_token(scope_dir: Path | None, hub_dir: Path | None) -> Path | None:
    """Token del workspace, fallback all'hub. None se assente."""
    for d in (scope_dir, hub_dir):
        if d:
            p = Path(d) / TOKEN_NAME
            if p.is_file():
                return p
    return None


def _session(token_path: Path):
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.credentials import Credentials
    # NB: niente override di scope al load — il refresh chiederebbe a Google
    # anche scope MAI concessi al token (invalid_scope su tutto, trovato
    # aggiungendo content per Merchant). Gli scope nuovi valgono solo per il
    # consenso (google_oauth.SCOPES): qui il token vive con i suoi.
    creds = Credentials.from_authorized_user_file(str(token_path))
    return AuthorizedSession(creds), creds


# ---- GSC --------------------------------------------------------------------

def _gsc_query(session, site_url, start, end, dimensions, row_limit=25000, start_row=0):
    body = {"startDate": start, "endDate": end, "dimensions": dimensions,
            "rowLimit": min(row_limit, 25000), "startRow": start_row, "type": "web"}
    r = session.post(GSC_URL.format(site=quote(site_url, safe="")), json=body, timeout=60)
    r.raise_for_status()
    out = []
    for row in r.json().get("rows", []):
        impr = int(row.get("impressions", 0) or 0)
        clicks = int(row.get("clicks", 0) or 0)
        out.append({"keys": row.get("keys", []), "clicks": clicks, "impressions": impr,
                    "ctr": (clicks / impr) if impr else 0.0,
                    "position": round(float(row.get("position", 0.0) or 0.0), 1)})
    return out


def collect_gsc(session, site_url, start, end, conn, site) -> tuple[int, int]:
    daily = _gsc_query(session, site_url, start, end, ["date"])
    for r in daily:
        conn.execute("INSERT OR REPLACE INTO gsc_daily VALUES(?,?,?,?,?,?)",
                     (site, r["keys"][0], r["clicks"], r["impressions"], r["ctr"], r["position"]))
    nq, start_row = 0, 0
    while True:
        rows = _gsc_query(session, site_url, start, end, ["date", "query"], 25000, start_row)
        for r in rows:
            conn.execute("INSERT OR REPLACE INTO gsc_queries VALUES(?,?,?,?,?,?,?)",
                         (site, r["keys"][0], r["keys"][1], r["clicks"], r["impressions"],
                          r["ctr"], r["position"]))
        nq += len(rows)
        if len(rows) < 25000:
            break
        start_row += 25000
    np, start_row = 0, 0
    while True:
        rows = _gsc_query(session, site_url, start, end, ["date", "page"], 25000, start_row)
        for r in rows:
            conn.execute("INSERT OR REPLACE INTO gsc_pages VALUES(?,?,?,?,?,?,?)",
                         (site, r["keys"][0], r["keys"][1], r["clicks"], r["impressions"],
                          r["ctr"], r["position"]))
        np += len(rows)
        if len(rows) < 25000:
            break
        start_row += 25000
    return len(daily), nq, np


# ---- GA4 --------------------------------------------------------------------

def _ga_date(s: str) -> str:
    s = str(s)
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else s


def _ga_report(session, pid, start, end, dims, metrics, limit=100000):
    pid = str(pid).replace("properties/", "")
    body = {"dateRanges": [{"startDate": start, "endDate": end}],
            "dimensions": [{"name": d} for d in dims],
            "metrics": [{"name": m} for m in metrics], "limit": limit}
    r = session.post(GA_URL.format(pid=pid), json=body, timeout=60)
    r.raise_for_status()
    data = r.json()
    out = []
    for row in data.get("rows", []):
        keys = [c.get("value") for c in row.get("dimensionValues", [])]
        mv = row.get("metricValues", [])
        rec = {"keys": keys}
        for i, m in enumerate(metrics):
            rec[m] = mv[i].get("value") if i < len(mv) else 0
        out.append(rec)
    return out


def collect_ga(session, pid, start, end, conn, site) -> tuple[int, int]:
    rows = _ga_report(session, pid, start, end, ["date", "sessionDefaultChannelGroup"],
                      ["sessions", "activeUsers", "keyEvents", "totalRevenue"])
    for r in rows:
        conn.execute("INSERT OR REPLACE INTO ga_daily VALUES(?,?,?,?,?,?,?)",
                     (site, _ga_date(r["keys"][0]), r["keys"][1], int(float(r["sessions"] or 0)),
                      int(float(r["activeUsers"] or 0)), int(float(r["keyEvents"] or 0)),
                      float(r["totalRevenue"] or 0)))
    arows = _ga_report(session, pid, start, end, ["date", "sessionCampaignName"],
                       ["advertiserAdCost", "advertiserAdClicks", "totalRevenue"])
    na = 0
    for r in arows:
        cost = float(r.get("advertiserAdCost", 0) or 0)
        if cost <= 0:
            continue
        conn.execute("INSERT OR REPLACE INTO ads_daily VALUES(?,?,?,?,?,?,?,?)",
                     (site, _ga_date(r["keys"][0]), r["keys"][1], cost, 0,
                      int(float(r.get("advertiserAdClicks", 0) or 0)), 0,
                      float(r.get("totalRevenue", 0) or 0)))
        na += 1
    return len(rows), na


# ---- Merchant ---------------------------------------------------------------

def _merchant_products(session, account_id):
    """Prodotti processati con status/issues (products_v1, paginata)."""
    out, token = [], ""
    while True:
        params = {"pageSize": 250}
        if token:
            params["pageToken"] = token
        r = session.get(MERCHANT_PRODUCTS_URL.format(acc=account_id),
                        params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        out.extend(data.get("products", []))
        token = data.get("nextPageToken", "")
        if not token:
            break
    return out


def _merchant_report(session, account_id, query):
    """reports:search (MQL) — la condizione su `date` nel WHERE è obbligatoria."""
    out, token = [], ""
    while True:
        body = {"query": query, "pageSize": 1000}
        if token:
            body["pageToken"] = token
        r = session.post(MERCHANT_REPORTS_URL.format(acc=account_id),
                         json=body, timeout=60)
        r.raise_for_status()
        data = r.json()
        out.extend(data.get("results", []))
        token = data.get("nextPageToken", "")
        if not token:
            break
    return out


def collect_merchant(session, account_id, start, end, conn, site) -> tuple[int, int, int]:
    """Snapshot prodotti+issues (datato oggi, STORICIZZATO: le date passate
    restano — servono a datare le disapprovazioni) + performance listing
    giornaliera e per prodotto, con split organico/ads e conversioni."""
    today = datetime.date.today().isoformat()
    products = _merchant_products(session, account_id)
    # idempotente sul giorno: ripulisce solo lo snapshot odierno
    conn.execute("DELETE FROM merchant_products WHERE site=? AND date=?", (site, today))
    conn.execute("DELETE FROM merchant_issues WHERE site=? AND date=?", (site, today))
    n_issues = 0
    for p in products:
        attrs = p.get("productAttributes") or {}
        status_obj = p.get("productStatus") or {}
        issues = [i for i in (status_obj.get("itemLevelIssues") or [])
                  if isinstance(i, dict)]
        severities = {i.get("severity", "") for i in issues}
        status = ("disapproved" if "DISAPPROVED" in severities
                  else "demoted" if "DEMOTED" in severities else "ok")
        price = attrs.get("price") or {}
        try:
            amount = round(int(price.get("amountMicros", 0) or 0) / 1e6, 2) or None
        except (TypeError, ValueError):
            amount = None
        conn.execute("INSERT OR REPLACE INTO merchant_products VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (site, today, p.get("offerId", ""),
                      str(attrs.get("title", ""))[:200], attrs.get("link", ""),
                      amount, price.get("currencyCode", ""),
                      str(attrs.get("availability", "")),
                      str(attrs.get("brand", ""))[:100],
                      status, len(issues)))
        for i in issues:
            conn.execute("INSERT OR REPLACE INTO merchant_issues VALUES(?,?,?,?,?,?,?,?,?)",
                         (site, today, p.get("offerId", ""), i.get("code", ""),
                          i.get("severity", ""), i.get("description", ""),
                          i.get("detail", ""), i.get("attribute", ""),
                          i.get("documentation", "")))
            n_issues += 1

    def _method(v):
        return str(v.get("marketingMethod") or "").lower() or "?"

    def _perf(v):
        impr = int(v.get("impressions", 0) or 0)
        clicks = int(v.get("clicks", 0) or 0)
        ctr = (float(v.get("clickThroughRate", 0) or 0)
               or ((clicks / impr) if impr else 0.0))
        cv = v.get("conversionValue") or {}
        # conversionValue è un Price {amountMicros, currencyCode}
        try:
            value = round(int(cv.get("amountMicros", 0) or 0) / 1e6, 2) \
                if isinstance(cv, dict) else float(cv or 0)
        except (TypeError, ValueError):
            value = 0.0
        return (clicks, impr, ctr, float(v.get("conversions", 0) or 0), value)

    query = ("SELECT date, marketing_method, clicks, impressions, "
             "click_through_rate, conversions, conversion_value "
             "FROM product_performance_view "
             f"WHERE date BETWEEN '{start}' AND '{end}'")
    nd = 0
    for row in _merchant_report(session, account_id, query):
        v = row.get("productPerformanceView") or {}
        d = v.get("date") or {}
        if not isinstance(d, dict) or not d.get("year"):
            continue
        date_s = f"{d['year']:04d}-{d.get('month', 1):02d}-{d.get('day', 1):02d}"
        clicks, impr, ctr, conv, val = _perf(v)
        conn.execute("INSERT OR REPLACE INTO merchant_daily VALUES(?,?,?,?,?,?,?,?)",
                     (site, date_s, _method(v), clicks, impr, ctr, conv, val))
        nd += 1
    # performance per prodotto sull'intera finestra (aggregato mobile `days` gg)
    query_p = ("SELECT offer_id, title, marketing_method, clicks, impressions, "
               "click_through_rate, conversions, conversion_value "
               "FROM product_performance_view "
               f"WHERE date BETWEEN '{start}' AND '{end}'")
    for row in _merchant_report(session, account_id, query_p):
        v = row.get("productPerformanceView") or {}
        oid = v.get("offerId", "")
        if not oid:
            continue
        clicks, impr, ctr, conv, val = _perf(v)
        conn.execute("INSERT OR REPLACE INTO merchant_product_perf VALUES(?,?,?,?,?,?,?,?,?)",
                     (site, oid, _method(v), str(v.get("title", ""))[:200],
                      clicks, impr, ctr, conv, val))
    return len(products), n_issues, nd


# ---- orchestratore ----------------------------------------------------------

def collect(db_path: Path, token_file: Path, *, gsc_site: str = "",
            ga_property: str = "", merchant_account: str = "",
            days: int = 90, replace: bool = True) -> dict:
    """Raccolta reale GSC+GA+Merchant → metrics.db. `replace` svuota le tabelle
    prima (refresh pulito: niente residui demo/vecchi). Ritorna {ok, gsc_daily,
    gsc_queries, ga_daily, ads_daily, merchant_*, range, errors:[...]}."""
    session, creds = _session(token_file)
    end = datetime.date.today() - datetime.timedelta(days=3)   # lag dati Google
    start = end - datetime.timedelta(days=days - 1)
    s, e = start.isoformat(), end.isoformat()
    site = (gsc_site or ga_property or "site").strip()
    conn = metrics_io._conn(Path(db_path))
    out = {"ok": True, "gsc_daily": 0, "gsc_queries": 0, "gsc_pages": 0, "ga_daily": 0,
           "ads_daily": 0, "merchant_products": 0, "merchant_issues": 0,
           "merchant_daily": 0, "range": [s, e], "errors": []}
    try:
        if replace:
            # NB: merchant_products/merchant_issues NON si svuotano — sono
            # snapshot datati e lo storico serve a datare le disapprovazioni
            # (collect_merchant ripulisce solo la data odierna).
            for t in ("gsc_daily", "gsc_queries", "gsc_pages", "ga_daily", "ads_daily",
                      "merchant_daily", "merchant_product_perf"):
                conn.execute(f"DELETE FROM {t}")
        if gsc_site:
            try:
                out["gsc_daily"], out["gsc_queries"], out["gsc_pages"] = collect_gsc(session, gsc_site, s, e, conn, site)
            except Exception as ex:  # noqa: BLE001
                out["errors"].append(f"GSC: {ex}")
        if ga_property:
            try:
                out["ga_daily"], out["ads_daily"] = collect_ga(session, ga_property, s, e, conn, site)
            except Exception as ex:  # noqa: BLE001
                out["errors"].append(f"GA4: {ex}")
        if merchant_account:
            try:
                token_scopes = json.loads(Path(token_file).read_text()).get("scopes") or []
            except Exception:  # noqa: BLE001
                token_scopes = []
            if token_scopes and "https://www.googleapis.com/auth/content" not in token_scopes:
                out["errors"].append(
                    "Merchant: il token Google non ha lo scope content — "
                    "ricollega Google dai Connettori per autorizzarlo")
            else:
                try:
                    (out["merchant_products"], out["merchant_issues"],
                     out["merchant_daily"]) = collect_merchant(
                        session, merchant_account, s, e, conn, site)
                except Exception as ex:  # noqa: BLE001
                    out["errors"].append(f"Merchant: {ex}")
        conn.commit()
        try:
            _write_token_secure(Path(token_file), creds.to_json())   # persisti l'access token rinfrescato (0600 atomico)
        except OSError:
            pass
    finally:
        conn.close()
    out["ok"] = not out["errors"]
    return out
