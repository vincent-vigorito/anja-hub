"""meta_ads_collect.py — collector Meta Ads insights → metrics.db (ads_daily).

Insights giornalieri per campagna (act_<id>/insights, time_increment=1):
spend/impressions/clicks + conversioni purchase (actions) e revenue
(action_values), scritti in ads_daily con campagna prefissata "meta:" —
spesa e ROAS del tab Ads diventano Google+Meta senza toccare la UI.

Il collector cancella e riscrive SOLO le proprie righe (prefisso meta:) ed è
ordinato DOPO google_collect, il cui replace svuota l'intera ads_daily.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import httpx

import metrics_io

GRAPH = "https://graph.facebook.com/v19.0"
# omni_purchase aggrega già le varianti: ordine di preferenza per NON sommare
# due volte lo stesso acquisto.
_PURCHASE_PRIORITY = ("omni_purchase", "purchase",
                      "offsite_conversion.fb_pixel_purchase")


def _pick(entries) -> float:
    vals = {e.get("action_type"): float(e.get("value", 0) or 0)
            for e in (entries or []) if isinstance(e, dict)}
    for k in _PURCHASE_PRIORITY:
        if k in vals:
            return vals[k]
    return 0.0


def collect(db_path: Path, token: str, ad_account: str, *, days: int = 90,
            site: str = "", client: httpx.Client | None = None) -> dict:
    """Raccolta Meta Ads → ads_daily. Ritorna {ok, rows, errors:[...]}."""
    aid = ad_account if ad_account.startswith("act_") else f"act_{ad_account}"
    site = site or aid
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days - 1)
    out = {"ok": True, "rows": 0, "errors": []}
    own_client = client is None
    c = client or httpx.Client(timeout=30.0)
    conn = metrics_io._conn(Path(db_path))
    try:
        conn.execute("DELETE FROM ads_daily WHERE campaign LIKE 'meta:%'")
        url = f"{GRAPH}/{aid}/insights"
        params = {
            "level": "campaign", "time_increment": 1,
            "fields": "campaign_name,spend,impressions,clicks,actions,action_values",
            "time_range": json.dumps({"since": start.isoformat(),
                                      "until": end.isoformat()}),
            "limit": 500, "access_token": token,
        }
        while True:
            r = c.get(url, params=params)
            data = r.json()
            if r.status_code >= 400:
                err = (data.get("error") or {}).get("message", f"HTTP {r.status_code}")
                out["errors"].append(f"Meta Ads: {err}")
                break
            for row in data.get("data", []):
                conn.execute(
                    "INSERT OR REPLACE INTO ads_daily VALUES(?,?,?,?,?,?,?,?)",
                    (site, row.get("date_start", ""),
                     "meta:" + (row.get("campaign_name") or "?")[:120],
                     float(row.get("spend", 0) or 0),
                     int(row.get("impressions", 0) or 0),
                     int(row.get("clicks", 0) or 0),
                     _pick(row.get("actions")),
                     _pick(row.get("action_values"))))
                out["rows"] += 1
            nxt = (data.get("paging") or {}).get("next")
            if not nxt:
                break
            url, params = nxt, None   # `next` è un URL completo (token incluso)
        conn.commit()
    finally:
        conn.close()
        if own_client:
            c.close()
    out["ok"] = not out["errors"]
    return out
