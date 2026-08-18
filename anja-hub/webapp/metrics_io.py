"""metrics_io.py — metriche del workspace marketing (F1c statistiche/dashboard).

Fonte: `<workspace>/data/metrics.db` (SQLite), schema sottoinsieme di quello del
prototipo anja-marketer (`db.py`) → compatibile con un futuro collector GSC/GA/Ads
(F1a connettori). Finché il collector non esiste, `seed_demo()` popola dati
realistici a bassa trazione per dimostrare la dashboard; la UI mostra empty-state
quando il db è assente/vuoto.

Modello hub: 1 sito = 1 workspace → il db ha un solo `site`, le read aggregano
tutto (niente filtro per site). La colonna `site` resta per fedeltà di import.

Insight (stesse definizioni del marketer `dashboard/queries.py`):
  - quick-win: query a pos 8–20 con impression ≥8 (spinta facile)
  - anomalie CTR: pos ≤7 ma 0 click (≥5 imp) → meta da ottimizzare
  - movers: Δ posizione media periodo corrente vs precedente (≥1)
"""

from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS gsc_daily(
  site TEXT, date TEXT, clicks INTEGER, impressions INTEGER, ctr REAL, position REAL,
  PRIMARY KEY(site, date));
CREATE TABLE IF NOT EXISTS gsc_queries(
  site TEXT, date TEXT, query TEXT, clicks INTEGER, impressions INTEGER, ctr REAL, position REAL,
  PRIMARY KEY(site, date, query));
CREATE TABLE IF NOT EXISTS gsc_pages(
  site TEXT, date TEXT, page TEXT, clicks INTEGER, impressions INTEGER, ctr REAL, position REAL,
  PRIMARY KEY(site, date, page));
CREATE TABLE IF NOT EXISTS ga_daily(
  site TEXT, date TEXT, channel TEXT, sessions INTEGER, active_users INTEGER, key_events INTEGER, revenue REAL,
  PRIMARY KEY(site, date, channel));
CREATE TABLE IF NOT EXISTS ads_daily(
  site TEXT, date TEXT, campaign TEXT, spend REAL, impressions INTEGER, clicks INTEGER, conversions REAL, revenue REAL,
  PRIMARY KEY(site, date, campaign));
CREATE TABLE IF NOT EXISTS wc_orders_daily(
  site TEXT, date TEXT, orders INTEGER, revenue REAL, net_revenue REAL, tax REAL, shipping REAL,
  discount REAL, items INTEGER, new_customers INTEGER,
  PRIMARY KEY(site, date));
CREATE TABLE IF NOT EXISTS wc_order_products(
  site TEXT, product_id INTEGER, name TEXT, sku TEXT, quantity INTEGER, revenue REAL, orders INTEGER,
  period_start TEXT, period_end TEXT,
  PRIMARY KEY(site, product_id));
CREATE TABLE IF NOT EXISTS wc_orders(
  site TEXT, order_id INTEGER, date TEXT, status TEXT, total REAL, net REAL, items INTEGER,
  customer_id INTEGER, new_customer INTEGER, payment TEXT, city TEXT, region TEXT, country TEXT, company TEXT,
  PRIMARY KEY(site, order_id));
CREATE TABLE IF NOT EXISTS ads_terms(
  site TEXT, campaign TEXT, term TEXT, status TEXT, spend REAL, impressions INTEGER, clicks INTEGER,
  conversions REAL, revenue REAL, period_start TEXT, period_end TEXT,
  PRIMARY KEY(site, campaign, term));
CREATE TABLE IF NOT EXISTS merchant_daily(
  site TEXT, date TEXT, marketing_method TEXT, clicks INTEGER, impressions INTEGER, ctr REAL,
  conversions REAL, conversion_value REAL,
  PRIMARY KEY(site, date, marketing_method));
CREATE TABLE IF NOT EXISTS merchant_products(
  site TEXT, date TEXT, offer_id TEXT, title TEXT, link TEXT,
  price REAL, currency TEXT, availability TEXT, brand TEXT, status TEXT, issues INTEGER,
  PRIMARY KEY(site, date, offer_id));
CREATE TABLE IF NOT EXISTS merchant_issues(
  site TEXT, date TEXT, offer_id TEXT, code TEXT, severity TEXT, description TEXT, detail TEXT, attribute TEXT, documentation TEXT,
  PRIMARY KEY(site, date, offer_id, code));
CREATE TABLE IF NOT EXISTS merchant_product_perf(
  site TEXT, offer_id TEXT, marketing_method TEXT, title TEXT, clicks INTEGER, impressions INTEGER, ctr REAL,
  conversions REAL, conversion_value REAL,
  PRIMARY KEY(site, offer_id, marketing_method));
CREATE TABLE IF NOT EXISTS social_daily(
  site TEXT, date TEXT, channel TEXT, followers INTEGER, new_followers INTEGER,
  reach INTEGER, profile_views INTEGER, interactions INTEGER,
  PRIMARY KEY(site, date, channel));
CREATE TABLE IF NOT EXISTS social_audience(
  site TEXT, date TEXT, channel TEXT, dimension TEXT, value TEXT, count INTEGER,
  PRIMARY KEY(site, date, channel, dimension, value));
"""


def _migrate(c: sqlite3.Connection) -> None:
    """Migrazioni. Le tabelle merchant_* di prima generazione (senza
    marketing_method/conversioni) cambiano PK → drop-and-recreate: i dati
    sono ricostruibili dal collector alla prossima raccolta."""
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(merchant_daily)")}
        if cols and "marketing_method" not in cols:
            for t in ("merchant_daily", "merchant_products", "merchant_issues",
                      "merchant_product_perf"):
                c.execute(f"DROP TABLE IF EXISTS {t}")
    except sqlite3.Error:
        pass


def _conn(db_path: Path) -> sqlite3.Connection:
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p))
    c.row_factory = sqlite3.Row
    _migrate(c)
    c.executescript(_SCHEMA)
    return c


def _anchor_date(c: sqlite3.Connection) -> str:
    """Ultima data con dati (le metriche sono spesso stale di qualche giorno)."""
    row = c.execute("SELECT MAX(date) d FROM gsc_daily").fetchone()
    if row and row["d"]:
        return row["d"]
    row = c.execute("SELECT MAX(date) d FROM ga_daily").fetchone()
    if row and row["d"]:
        return row["d"]
    return datetime.date.today().isoformat()


def _shift(iso: str, days: int) -> str:
    return (datetime.date.fromisoformat(iso) + datetime.timedelta(days=days)).isoformat()


def _gsc_agg(c, start, end) -> dict:
    r = c.execute(
        """SELECT COALESCE(SUM(clicks),0) clicks, COALESCE(SUM(impressions),0) impr,
                  CASE WHEN SUM(impressions)>0 THEN SUM(position*impressions)/SUM(impressions) END pos
           FROM gsc_daily WHERE date BETWEEN ? AND ?""", (start, end)).fetchone()
    clicks, impr = r["clicks"] or 0, r["impr"] or 0
    return {
        "clicks": clicks,
        "impressions": impr,
        "ctr": round(clicks / impr * 100, 2) if impr else 0.0,
        "position": round(r["pos"], 1) if r["pos"] is not None else None,
    }


def _ga_agg(c, start, end) -> dict:
    r = c.execute(
        """SELECT COALESCE(SUM(sessions),0) s, COALESCE(SUM(active_users),0) u,
                  COALESCE(SUM(key_events),0) k, COALESCE(SUM(revenue),0) rev
           FROM ga_daily WHERE date BETWEEN ? AND ?""", (start, end)).fetchone()
    return {"sessions": r["s"], "users": r["u"], "conversions": r["k"], "revenue": round(r["rev"], 2)}


def _ads_agg(c, start, end) -> dict:
    r = c.execute(
        """SELECT COALESCE(SUM(spend),0) sp, COALESCE(SUM(conversions),0) cv,
                  COALESCE(SUM(revenue),0) rev, COALESCE(SUM(clicks),0) cl,
                  COALESCE(SUM(impressions),0) im
           FROM ads_daily WHERE date BETWEEN ? AND ?""", (start, end)).fetchone()
    spend, clicks, imps, conv = round(r["sp"], 2), r["cl"], r["im"], r["cv"]
    return {
        "spend": spend, "conversions": round(conv, 1), "clicks": clicks,
        "impressions": imps, "revenue": round(r["rev"], 2),
        "roas": round(r["rev"] / spend, 2) if spend else None,
        # metriche native (0 quando la sorgente è la stima GA4: impressions vuote)
        "ctr": round(clicks / imps * 100, 2) if imps else None,
        "cpc": round(spend / clicks, 2) if clicks else None,
        "cpa": round(spend / conv, 2) if conv else None,
    }


def _sales_agg(c, start, end) -> dict:
    r = c.execute(
        """SELECT COALESCE(SUM(orders),0) o, COALESCE(SUM(revenue),0) rev, COALESCE(SUM(net_revenue),0) net,
                  COALESCE(SUM(items),0) it, COALESCE(SUM(new_customers),0) nc
           FROM wc_orders_daily WHERE date BETWEEN ? AND ?""", (start, end)).fetchone()
    o, rev = r["o"], round(r["rev"], 2)
    return {"orders": o, "revenue": rev, "net_revenue": round(r["net"], 2), "items": r["it"],
            "new_customers": r["nc"], "aov": round(rev / o, 2) if o else None,
            "returning_share": round((o - r["nc"]) / o * 100, 1) if o else None}


def _sales_block(c, days, series_days, kpi) -> dict | None:
    """Ordini WooCommerce (dato di cassa). None se il collector non ha mai scritto.
    Anchor PROPRIO (ultimo ordine, tipicamente ieri): l'anchor Google è indietro
    di ~3 giorni e taglierebbe le vendite più recenti."""
    try:
        row = c.execute("SELECT MAX(date) d FROM wc_orders_daily").fetchone()
    except Exception:
        return None
    if not row or not row["d"]:
        return None
    anchor = row["d"]
    cur_start = _shift(anchor, -(days - 1))
    prev_end = _shift(anchor, -days)
    prev_start = _shift(anchor, -(2 * days - 1))
    ser_start = _shift(anchor, -(series_days - 1))
    cur, prev = _sales_agg(c, cur_start, anchor), _sales_agg(c, prev_start, prev_end)
    ads = _ads_agg(c, cur_start, anchor)
    prods = [dict(r) for r in c.execute(
        """SELECT product_id, name, sku, quantity, revenue, orders FROM wc_order_products
           ORDER BY revenue DESC LIMIT 25""")]
    tot = sum(p["revenue"] for p in prods) or 1
    for p in prods:
        p["share"] = round(p["revenue"] / tot * 100, 1)
    geo = [dict(r) for r in c.execute(
        """SELECT COALESCE(NULLIF(region,''),'?') region, COUNT(*) orders, ROUND(SUM(total),2) revenue
           FROM wc_orders WHERE date BETWEEN ? AND ? GROUP BY region ORDER BY revenue DESC LIMIT 12""",
        (cur_start, anchor))]
    pay = [dict(r) for r in c.execute(
        """SELECT COALESCE(NULLIF(payment,''),'?') payment, COUNT(*) orders, ROUND(SUM(total),2) revenue
           FROM wc_orders WHERE date BETWEEN ? AND ? GROUP BY payment ORDER BY orders DESC LIMIT 8""",
        (cur_start, anchor))]
    b2b = c.execute("""SELECT SUM(CASE WHEN company<>'' THEN 1 ELSE 0 END), COUNT(*)
                       FROM wc_orders WHERE date BETWEEN ? AND ?""", (cur_start, anchor)).fetchone()
    return {
        "anchor": anchor,
        "kpis": {
            "orders": kpi(cur, prev, "orders"),
            "revenue": kpi(cur, prev, "revenue"),
            "aov": kpi(cur, prev, "aov"),
            "new_customers": kpi(cur, prev, "new_customers"),
            "items": kpi(cur, prev, "items"),
            "net_revenue": kpi(cur, prev, "net_revenue"),
        },
        "returning_share": cur["returning_share"],
        "b2b_share": round(b2b[0] / b2b[1] * 100, 1) if b2b and b2b[1] else None,
        # ROAS di cassa: fatturato Woo / spesa ads del periodo (l'unico ROAS onesto)
        "cash_roas": round(cur["revenue"] / ads["spend"], 2) if ads["spend"] else None,
        "ads_spend": ads["spend"],
        "series": _series(c, "wc_orders_daily", ("orders", "revenue"), ser_start, anchor),
        "top_products": prods,
        "geo": geo,
        "payments": pay,
    }


def _ads_campaigns(c, start, end) -> list[dict]:
    """Righe per campagna nel periodo, ordinate per spesa. Il prefisso indica
    la sorgente: gads: (Google Ads API), meta: (Meta), nessuno (stima GA4)."""
    rows = c.execute(
        """SELECT campaign, SUM(spend) sp, SUM(impressions) im, SUM(clicks) cl,
                  SUM(conversions) cv, SUM(revenue) rev
           FROM ads_daily WHERE date BETWEEN ? AND ? GROUP BY campaign
           ORDER BY sp DESC LIMIT 50""", (start, end)).fetchall()
    out = []
    for r in rows:
        name = r["campaign"] or "?"
        src = "google" if name.startswith("gads:") else ("meta" if name.startswith("meta:") else "ga4")
        sp, cl, im, cv = round(r["sp"] or 0, 2), r["cl"] or 0, r["im"] or 0, r["cv"] or 0
        out.append({
            "campaign": name.split(":", 1)[1] if ":" in name else name, "source": src,
            "spend": sp, "impressions": im, "clicks": cl, "conversions": round(cv, 1),
            "revenue": round(r["rev"] or 0, 2),
            "ctr": round(cl / im * 100, 2) if im else None,
            "cpc": round(sp / cl, 2) if cl else None,
            "cpa": round(sp / cv, 2) if cv else None,
            "roas": round((r["rev"] or 0) / sp, 2) if sp else None,
        })
    return out


def _ads_terms(c, limit: int = 40) -> dict:
    """Search terms nativi (ultimo snapshot del collector): top per spesa +
    'wasted' = costano senza convertire (candidati negative keyword)."""
    try:
        rows = c.execute(
            """SELECT campaign, term, status, spend, impressions, clicks, conversions, revenue,
                      period_start, period_end
               FROM ads_terms ORDER BY spend DESC""").fetchall()
    except Exception:
        return {"exists": False}
    if not rows:
        return {"exists": False}
    items = [{"campaign": (r["campaign"] or "").split(":", 1)[-1], "term": r["term"],
              "status": r["status"] or "", "spend": round(r["spend"] or 0, 2),
              "impressions": r["impressions"] or 0, "clicks": r["clicks"] or 0,
              "conversions": round(r["conversions"] or 0, 1),
              "revenue": round(r["revenue"] or 0, 2)} for r in rows]
    wasted = [t for t in items if t["spend"] >= 1 and t["conversions"] == 0]
    wasted.sort(key=lambda t: t["spend"], reverse=True)
    return {
        "exists": True,
        "period": [rows[0]["period_start"], rows[0]["period_end"]],
        "count": len(items),
        "top": items[:limit],
        "wasted": wasted[:limit],
        "wasted_spend": round(sum(t["spend"] for t in wasted), 2),
        "total_spend": round(sum(t["spend"] for t in items), 2),
    }


def _delta(cur, prev):
    if cur is None or prev is None:
        return None
    return round(cur - prev, 2)


def _quick_wins(c, start, end, limit=15) -> list[dict]:
    rows = c.execute(
        """SELECT query, SUM(clicks) clicks, SUM(impressions) impr,
                  SUM(position*impressions)/SUM(impressions) pos
           FROM gsc_queries WHERE date BETWEEN ? AND ?
           GROUP BY query HAVING impr >= 8 AND pos BETWEEN 8 AND 20
           ORDER BY impr DESC LIMIT ?""", (start, end, limit)).fetchall()
    return [{"query": r["query"], "clicks": r["clicks"], "impressions": r["impr"],
             "position": round(r["pos"], 1)} for r in rows]


def _ctr_anomalies(c, start, end, limit=15) -> list[dict]:
    rows = c.execute(
        """SELECT query, SUM(clicks) clicks, SUM(impressions) impr,
                  SUM(position*impressions)/SUM(impressions) pos
           FROM gsc_queries WHERE date BETWEEN ? AND ?
           GROUP BY query HAVING impr >= 5 AND pos <= 7 AND SUM(clicks) = 0
           ORDER BY impr DESC LIMIT ?""", (start, end, limit)).fetchall()
    return [{"query": r["query"], "impressions": r["impr"], "position": round(r["pos"], 1)} for r in rows]


def _movers(c, cur_start, cur_end, prev_start, prev_end, limit=8):
    def period(s, e):
        return {r["query"]: r["pos"] for r in c.execute(
            """SELECT query, SUM(position*impressions)/SUM(impressions) pos
               FROM gsc_queries WHERE date BETWEEN ? AND ?
               GROUP BY query HAVING SUM(impressions) > 0""", (s, e))}
    cur, prev = period(cur_start, cur_end), period(prev_start, prev_end)
    moved = []
    for q, cpos in cur.items():
        if q in prev:
            delta = prev[q] - cpos          # >0 = salita (posizione migliorata)
            if abs(delta) >= 1:
                moved.append({"query": q, "position": round(cpos, 1),
                              "prev": round(prev[q], 1), "delta": round(delta, 1)})
    up = sorted((m for m in moved if m["delta"] > 0), key=lambda x: -x["delta"])[:limit]
    down = sorted((m for m in moved if m["delta"] < 0), key=lambda x: x["delta"])[:limit]
    return up, down


def _ga_channels(c, start, end) -> list[dict]:
    rows = c.execute(
        """SELECT channel, SUM(sessions) sessions, SUM(active_users) users,
                  SUM(key_events) conv, SUM(revenue) revenue
           FROM ga_daily WHERE date BETWEEN ? AND ?
           GROUP BY channel ORDER BY sessions DESC""", (start, end)).fetchall()
    return [{"channel": r["channel"], "sessions": r["sessions"], "users": r["users"],
             "conversions": r["conv"], "revenue": round(r["revenue"] or 0, 2)} for r in rows]


def _series(c, table, cols, start, end) -> list[dict]:
    sel = ", ".join(f"COALESCE(SUM({k}),0) {k}" for k in cols)
    rows = c.execute(
        f"SELECT date, {sel} FROM {table} WHERE date BETWEEN ? AND ? GROUP BY date ORDER BY date",
        (start, end)).fetchall()
    return [dict(r) for r in rows]


def has_data(db_path: Path) -> bool:
    p = Path(db_path)
    if not p.is_file():
        return False
    c = _conn(p)
    try:
        n = c.execute("SELECT COUNT(*) n FROM gsc_daily").fetchone()["n"]
        return n > 0
    finally:
        c.close()


def _social_agg(c, start, end) -> dict:
    r = c.execute(
        """SELECT COALESCE(SUM(CASE WHEN channel='instagram' THEN reach END),0) ig_reach,
                  COALESCE(SUM(CASE WHEN channel='facebook' THEN interactions END),0) fb_int
           FROM social_daily WHERE date BETWEEN ? AND ?""", (start, end)).fetchone()
    return {"ig_reach": r["ig_reach"] or 0, "fb_interactions": r["fb_int"] or 0}


def _social_block(c, ser_start, days=28):
    """Blocco social account-level per la dashboard (None se mai raccolto)."""
    # niente limite superiore: i dati social non hanno il lag di 3gg di GSC
    # (l'anchor della dashboard taglierebbe i giorni più recenti)
    rows = [dict(r) for r in c.execute(
        """SELECT date, channel, followers, new_followers, reach, profile_views,
                  interactions FROM social_daily
           WHERE date >= ? ORDER BY date""", (ser_start,))]
    if not rows:
        return None
    followers = {}
    for ch in ("instagram", "facebook"):
        r = c.execute(
            """SELECT followers FROM social_daily
               WHERE channel=? AND followers IS NOT NULL
               ORDER BY date DESC LIMIT 1""", (ch,)).fetchone()
        if r and r["followers"]:
            followers[ch] = r["followers"]
    aud_date = c.execute("SELECT MAX(date) d FROM social_audience").fetchone()
    audience = {}
    if aud_date and aud_date["d"]:
        for dim in ("age", "gender", "city"):
            audience[dim] = [dict(r) for r in c.execute(
                """SELECT value, count FROM social_audience
                   WHERE date=? AND dimension=? ORDER BY count DESC LIMIT 5""",
                (aud_date["d"], dim))]
    # KPI col confronto periodo precedente — finestre relative a OGGI
    # (i social non hanno il lag di 3gg dell'anchor GSC)
    today = datetime.date.today()
    cur_s = (today - datetime.timedelta(days=days - 1)).isoformat()
    prev_e = (today - datetime.timedelta(days=days)).isoformat()
    prev_s = (today - datetime.timedelta(days=2 * days - 1)).isoformat()
    cur, prev = _social_agg(c, cur_s, today.isoformat()), _social_agg(c, prev_s, prev_e)
    kpis = {k: {"value": cur[k], "prev": prev[k],
                "delta": _delta(cur[k], prev[k]), "better": "up"}
            for k in ("ig_reach", "fb_interactions")}
    return {"series": rows, "followers": followers, "audience": audience,
            "kpis": kpis,
            "audience_date": aud_date["d"] if aud_date else None}


def _merchant_agg(c, start, end) -> dict:
    r = c.execute(
        """SELECT COALESCE(SUM(clicks),0) clicks, COALESCE(SUM(impressions),0) impr,
                  COALESCE(SUM(conversions),0) conv, COALESCE(SUM(conversion_value),0) val
           FROM merchant_daily WHERE date BETWEEN ? AND ?""", (start, end)).fetchone()
    clicks, impr = r["clicks"] or 0, r["impr"] or 0
    return {"clicks": clicks, "impressions": impr,
            "ctr": round(clicks / impr * 100, 2) if impr else 0.0,
            "conversions": round(r["conv"] or 0, 1),
            "conversion_value": round(r["val"] or 0, 2)}


def _merchant_methods(c, start, end) -> list:
    """Split organico/ads (marketing_method) sul periodo."""
    return [dict(r) for r in c.execute(
        """SELECT marketing_method method, COALESCE(SUM(clicks),0) clicks,
                  COALESCE(SUM(impressions),0) impressions,
                  ROUND(COALESCE(SUM(conversion_value),0), 2) value
           FROM merchant_daily WHERE date BETWEEN ? AND ?
           GROUP BY marketing_method ORDER BY clicks DESC""", (start, end))]


def _merchant_feed(c) -> dict | None:
    """Ultimo snapshot del feed Shopping: salute prodotti + top issues."""
    last = c.execute("SELECT MAX(date) d FROM merchant_products").fetchone()
    if not last or not last["d"]:
        return None
    d = last["d"]
    st = {r["status"]: r["n"] for r in c.execute(
        "SELECT status, COUNT(*) n FROM merchant_products WHERE date=? GROUP BY status",
        (d,))}
    top = [dict(r) for r in c.execute(
        """SELECT code, severity, description, COUNT(*) n FROM merchant_issues
           WHERE date=? GROUP BY code, severity, description
           ORDER BY CASE severity WHEN 'DISAPPROVED' THEN 0
                                  WHEN 'DEMOTED' THEN 1 ELSE 2 END, n DESC
           LIMIT 10""", (d,))]
    disapproved = [dict(r) for r in c.execute(
        """SELECT p.offer_id, p.title, p.link, p.issues,
                  COALESCE(GROUP_CONCAT(i.description, ' · '), '') reasons
           FROM merchant_products p
           LEFT JOIN merchant_issues i
             ON i.date = p.date AND i.offer_id = p.offer_id
                AND i.severity = 'DISAPPROVED'
           WHERE p.date = ? AND p.status = 'disapproved'
           GROUP BY p.offer_id ORDER BY p.issues DESC LIMIT 10""", (d,))]
    return {"date": d, "products": sum(st.values()), "ok": st.get("ok", 0),
            "demoted": st.get("demoted", 0),
            "disapproved": st.get("disapproved", 0), "top_issues": top,
            "disapproved_products": disapproved}


def dashboard(db_path: Path, days: int = 28, series_days: int = 90) -> dict:
    """Payload completo per la vista Statistiche (KPI + serie + insight)."""
    p = Path(db_path)
    if not p.is_file():
        return {"exists": False}
    c = _conn(p)
    try:
        if c.execute("SELECT COUNT(*) n FROM gsc_daily").fetchone()["n"] == 0 \
           and c.execute("SELECT COUNT(*) n FROM ga_daily").fetchone()["n"] == 0 \
           and c.execute("SELECT COUNT(*) n FROM merchant_daily").fetchone()["n"] == 0 \
           and c.execute("SELECT COUNT(*) n FROM wc_orders_daily").fetchone()["n"] == 0:
            return {"exists": False}

        anchor = _anchor_date(c)
        cur_start = _shift(anchor, -(days - 1))
        prev_end = _shift(anchor, -days)
        prev_start = _shift(anchor, -(2 * days - 1))
        ser_start = _shift(anchor, -(series_days - 1))

        gsc, gsc_prev = _gsc_agg(c, cur_start, anchor), _gsc_agg(c, prev_start, prev_end)
        ga, ga_prev = _ga_agg(c, cur_start, anchor), _ga_agg(c, prev_start, prev_end)
        ads, ads_prev = _ads_agg(c, cur_start, anchor), _ads_agg(c, prev_start, prev_end)
        up, down = _movers(c, cur_start, anchor, prev_start, prev_end)
        mfeed = _merchant_feed(c)
        mer, mer_prev = (_merchant_agg(c, cur_start, anchor),
                         _merchant_agg(c, prev_start, prev_end))

        def kpi(cur, prev, key, better="up"):
            return {"value": cur[key], "prev": prev[key], "delta": _delta(cur[key], prev[key]), "better": better}

        return {
            "exists": True,
            "anchor": anchor,
            "range_days": days,
            "kpis": {
                "clicks": kpi(gsc, gsc_prev, "clicks"),
                "impressions": kpi(gsc, gsc_prev, "impressions"),
                "ctr": kpi(gsc, gsc_prev, "ctr"),
                "position": kpi(gsc, gsc_prev, "position", better="down"),
                "sessions": kpi(ga, ga_prev, "sessions"),
                "conversions": kpi(ga, ga_prev, "conversions"),
                "spend": kpi(ads, ads_prev, "spend", better="down"),
                "roas": kpi(ads, ads_prev, "roas"),
            },
            "has_ads": ads["spend"] > 0,
            "gsc_series": _series(c, "gsc_daily", ("clicks", "impressions"), ser_start, anchor),
            "ga_series": _series(c, "ga_daily", ("sessions",), ser_start, anchor),
            "ads_series": _series(c, "ads_daily", ("spend", "revenue"), ser_start, anchor),
            "quick_wins": _quick_wins(c, cur_start, anchor),
            "ctr_anomalies": _ctr_anomalies(c, cur_start, anchor),
            "movers_up": up,
            "movers_down": down,
            "ga_channels": _ga_channels(c, cur_start, anchor),
            # Google Ads nativo: KPI completi, campagne, search terms
            "ads": {
                "source": ("google" if c.execute(
                    "SELECT 1 FROM ads_daily WHERE campaign LIKE 'gads:%' LIMIT 1").fetchone()
                           else ("ga4" if ads["spend"] > 0 else "")),
                "kpis": {
                    "spend": kpi(ads, ads_prev, "spend", better="down"),
                    "conversions": kpi(ads, ads_prev, "conversions"),
                    "revenue": kpi(ads, ads_prev, "revenue"),
                    "roas": kpi(ads, ads_prev, "roas"),
                    "cpa": kpi(ads, ads_prev, "cpa", better="down"),
                    "ctr": kpi(ads, ads_prev, "ctr"),
                    "cpc": kpi(ads, ads_prev, "cpc", better="down"),
                    "clicks": kpi(ads, ads_prev, "clicks"),
                },
                "campaigns": _ads_campaigns(c, cur_start, anchor),
                "terms": _ads_terms(c),
            },
            # Ordini WooCommerce (dato di cassa) — presente solo se raccolto
            "sales": _sales_block(c, days, series_days, kpi),
            # Social account-level (IG/FB) — presente solo se raccolto
            "social": _social_block(c, ser_start, days),
            # Google Shopping — presente solo se il connettore Merchant ha raccolto
            "merchant": ({
                "feed": mfeed,
                "kpis": {
                    "clicks": kpi(mer, mer_prev, "clicks"),
                    "impressions": kpi(mer, mer_prev, "impressions"),
                    "ctr": kpi(mer, mer_prev, "ctr"),
                    "conversions": kpi(mer, mer_prev, "conversions"),
                    "conversion_value": kpi(mer, mer_prev, "conversion_value"),
                },
                "by_method": _merchant_methods(c, cur_start, anchor),
                # serie aggregata sui method (righe per-method → una per data)
                "series": [dict(r) for r in c.execute(
                    """SELECT date, SUM(clicks) clicks, SUM(impressions) impressions
                       FROM merchant_daily WHERE date BETWEEN ? AND ?
                       GROUP BY date ORDER BY date""", (ser_start, anchor))],
                "top_products": [dict(r) for r in c.execute(
                    """SELECT offer_id, title, SUM(clicks) clicks,
                              SUM(impressions) impressions,
                              ROUND(COALESCE(SUM(conversions),0), 1) conversions,
                              ROUND(COALESCE(SUM(conversion_value),0), 2) value
                       FROM merchant_product_perf GROUP BY offer_id
                       ORDER BY value DESC, clicks DESC LIMIT 10""")],
            } if (mfeed or mer["impressions"] or mer["clicks"]) else None),
        }
    finally:
        c.close()


# --- seeder demo (finché non c'è il collector F1a) --------------------------

def seed_demo(db_path: Path, site: str = "example.com", days: int = 90) -> dict:
    """Popola metrics.db con ~`days` giorni di dati demo realistici a bassa
    trazione (click ~0, ~60 imp/sett, pos ~13, con quick-win, mover e
    anomalie CTR distribuiti sulle query finte)."""
    c = _conn(db_path)
    try:
        for t in ("gsc_daily", "gsc_queries", "ga_daily", "ads_daily"):
            c.execute(f"DELETE FROM {t}")

        today = datetime.date.today()
        # query: (testo, pos_base, drift_su_90gg, imp_giornaliere_medie, ctr_decimale)
        queries = [
            ("zaino trekking 40 litri", 15.5, -3.5, 2.2, 0.0),          # quick-win + mover up
            ("zaino impermeabile viaggio", 8.5, -4.0, 0.7, 0.0),        # mover up
            ("scarpe trail running uomo", 9.0, 4.5, 0.9, 0.0),          # mover down (8→13)
            ("bastoncini trekking carbonio", 13.0, 3.5, 0.8, 0.02),     # mover down
            ("giacca antivento leggera", 9.5, 4.0, 0.4, 0.0),           # mover down (scivola)
            ("borraccia termica 1 litro", 12.0, -3.5, 0.5, 0.0),        # mover up
            ("tenda 2 posti ultraleggera", 6.0, -0.5, 1.1, 0.0),        # pos<=7, 0 click → anomalia CTR
            ("sacco a pelo invernale", 5.5, 0.3, 0.9, 0.0),             # anomalia CTR
            ("kit pronto soccorso escursione", 4.5, -1.0, 1.4, 0.06),   # ben posizionato, click
            ("lampada frontale ricaricabile", 16.0, -3.5, 0.6, 0.0),    # mover up
            ("guanti trekking impermeabili", 18.0, 0.5, 0.5, 0.0),
            ("fornello campeggio gas", 11.0, 3.5, 0.4, 0.0),            # mover down
        ]
        channels = [("Direct", 0.5), ("Referral", 0.2), ("Organic Search", 0.25), ("Social", 0.05)]

        for i in range(days):
            d = (today - datetime.timedelta(days=days - 1 - i)).isoformat()
            frac = i / max(days - 1, 1)        # 0 → 1 nel tempo (drift lineare)
            day_clicks = day_impr = 0
            day_pos_w = 0.0
            for q, pos0, drift, impd, ctr in queries:
                # impression deterministiche, leggero ciclo settimanale
                imp = max(0, round(impd * (0.6 + 0.8 * ((i % 7) / 6))))
                if imp == 0:
                    continue
                pos = max(1.0, pos0 + drift * frac)
                clk = round(imp * ctr)
                c.execute(
                    "INSERT OR REPLACE INTO gsc_queries VALUES(?,?,?,?,?,?,?)",
                    (site, d, q, clk, imp, (clk / imp) if imp else 0.0, pos))
                day_clicks += clk
                day_impr += imp
                day_pos_w += pos * imp
            if day_impr:
                c.execute("INSERT OR REPLACE INTO gsc_daily VALUES(?,?,?,?,?,?)",
                          (site, d, day_clicks, day_impr,
                           day_clicks / day_impr, day_pos_w / day_impr))
            # GA: traffico piccolo, organico in lenta crescita
            base = 1 + round(3 * frac)
            for ch, share in channels:
                s = max(0, round(base * share * (0.5 + (i % 5) / 4)))
                if s == 0 and ch != "Direct":
                    continue
                conv = 1 if (ch == "Organic Search" and i > days - 10 and i % 6 == 0) else 0
                c.execute("INSERT OR REPLACE INTO ga_daily VALUES(?,?,?,?,?,?,?)",
                          (site, d, ch, s, s, conv, 0.0))
        c.commit()
        n_q = c.execute("SELECT COUNT(*) n FROM gsc_queries").fetchone()["n"]
        n_d = c.execute("SELECT COUNT(*) n FROM gsc_daily").fetchone()["n"]
        return {"ok": True, "site": site, "gsc_daily": n_d, "gsc_queries": n_q, "anchor": _anchor_date(c)}
    finally:
        c.close()


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "metrics.db"
    print(seed_demo(Path(target)))
