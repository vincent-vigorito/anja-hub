"""google_ads_collect.py — Google Ads API (REST + GAQL) → ads_daily.

Dati NATIVI di Google Ads per campagna (spesa, impression, click, conversioni,
valore conversioni), scritti in ads_daily con campagna prefissata "gads:".
Sostituisce la stima via GA4 (advertiserAdCost) che resta come fallback
quando la Ads API non è configurata.

Requisiti (tutti nei Connettori):
- token OAuth Google con scope https://www.googleapis.com/auth/adwords
  (ricollega Google dopo l'aggiunta dello scope)
- GOOGLE_ADS_DEVELOPER_TOKEN (hub-level, Settings → Integrations): token
  "test access" legge solo account di test → per dati reali serve "basic
  access" (approvazione Google, gratuita)
- GOOGLE_ADS_CUSTOMER_ID (workspace) e, se l'account è sotto un manager,
  GOOGLE_ADS_LOGIN_CUSTOMER_ID (hub-level)

API: https://googleads.googleapis.com/v22/customers/{id}/googleAds:searchStream
Il collector cancella e riscrive SOLO le proprie righe (prefisso gads:).
"""

from __future__ import annotations

import datetime
from pathlib import Path

import metrics_io

API_BASE = "https://googleads.googleapis.com/v22"
PREFIX = "gads:"


def _digits(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


class GoogleAdsError(Exception):
    pass


def _search_stream(session, customer_id: str, gaql: str, dev_token: str,
                   login_customer_id: str = "") -> list[dict]:
    """Esegue una query GAQL. Se l'account non è raggiungibile via MCC
    (USER_PERMISSION_DENIED) ritenta senza login-customer-id."""
    headers = {"developer-token": dev_token}
    if login_customer_id:
        headers["login-customer-id"] = login_customer_id
    url = f"{API_BASE}/customers/{customer_id}/googleAds:searchStream"
    r = session.post(url, headers=headers, json={"query": gaql}, timeout=90)
    if r.status_code == 403 and login_customer_id and "USER_PERMISSION_DENIED" in r.text:
        headers.pop("login-customer-id")
        r = session.post(url, headers=headers, json={"query": gaql}, timeout=90)
    if r.status_code >= 400:
        try:
            err = r.json()
            if isinstance(err, list):
                err = err[0]
            msg = (err.get("error") or {}).get("message") or r.text[:300]
            # dettaglio Ads (es. DEVELOPER_TOKEN_NOT_APPROVED) quando c'è
            for d in (err.get("error") or {}).get("details") or []:
                for e in d.get("errors") or []:
                    if e.get("message"):
                        msg = e["message"]
        except Exception:
            msg = r.text[:300]
        raise GoogleAdsError(f"HTTP {r.status_code}: {msg}")
    rows: list[dict] = []
    for batch in r.json() or []:
        rows.extend(batch.get("results") or [])
    return rows


def collect(db_path: Path, session, customer_id: str, dev_token: str, *,
            login_customer_id: str = "", days: int = 90,
            site: str = "") -> dict:
    """Spesa/click/impression/conversioni per campagna e giorno → ads_daily.
    Ritorna {ok, ads_daily, campaigns, range, error?}."""
    cid = _digits(customer_id)
    if not cid:
        return {"ok": False, "error": "GOOGLE_ADS_CUSTOMER_ID non valido"}
    if not dev_token:
        return {"ok": False, "error": "GOOGLE_ADS_DEVELOPER_TOKEN mancante (Settings → Integrations)"}
    end = datetime.date.today() - datetime.timedelta(days=1)
    start = end - datetime.timedelta(days=days - 1)
    gaql = (
        "SELECT segments.date, campaign.name, campaign.status, "
        "metrics.cost_micros, metrics.impressions, metrics.clicks, "
        "metrics.conversions, metrics.conversions_value "
        "FROM campaign "
        f"WHERE segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}' "
        "AND metrics.impressions > 0"
    )
    try:
        rows = _search_stream(session, cid, gaql, dev_token, _digits(login_customer_id))
    except GoogleAdsError as e:
        return {"ok": False, "error": str(e)}
    site = site or cid
    conn = metrics_io._conn(Path(db_path))
    try:
        conn.execute("DELETE FROM ads_daily WHERE campaign LIKE ?", (PREFIX + "%",))
        n, campaigns = 0, set()
        for r in rows:
            seg, camp, met = r.get("segments") or {}, r.get("campaign") or {}, r.get("metrics") or {}
            name = PREFIX + (camp.get("name") or "?")[:120]
            campaigns.add(name)
            conn.execute("INSERT OR REPLACE INTO ads_daily VALUES(?,?,?,?,?,?,?,?)", (
                site, seg.get("date", ""), name,
                int(met.get("costMicros", 0) or 0) / 1e6,
                int(met.get("impressions", 0) or 0),
                int(met.get("clicks", 0) or 0),
                float(met.get("conversions", 0) or 0),
                float(met.get("conversionsValue", 0) or 0),
            ))
            n += 1
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "ads_daily": n, "campaigns": len(campaigns),
            "range": [start.isoformat(), end.isoformat()]}
