"""social_collect.py — collector social account-level (IG + FB page) → metrics.db.

social_daily: serie giornaliere per canale — reach, profile_views, nuovi
follower (IG, solo ultimi ~28gg per limite API), engagement pagina FB — più
lo SNAPSHOT follower del giorno di raccolta (non backfillabile: la curva si
accumula raccogliendo, come lo storico Merchant).
social_audience: demografia follower IG (età/genere/città), snapshot datato.

Ogni metrica viaggia in una chiamata separata: Meta depreca senza preavviso
(v. reach FB per-post) e una metrica morta non deve uccidere le altre.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import httpx

import metrics_io

GRAPH = "https://graph.facebook.com/v19.0"
_IG_WINDOW = 30    # l'API IG accetta max ~30gg per chiamata
_FB_WINDOW = 90


def _upsert(conn, site, date, channel, **cols):
    conn.execute(
        "INSERT INTO social_daily(site, date, channel) VALUES(?,?,?) "
        "ON CONFLICT(site, date, channel) DO NOTHING", (site, date, channel))
    sets = ", ".join(f"{k}=?" for k in cols)
    conn.execute(
        f"UPDATE social_daily SET {sets} WHERE site=? AND date=? AND channel=?",
        (*cols.values(), site, date, channel))


def _series(client, token, node, metric, since, until, window, errors, label):
    """GET {node}/insights?metric=<uno>&period=day, a finestre → {date: value}."""
    out: dict[str, int] = {}
    cur = since
    while cur <= until:
        chunk_end = min(cur + datetime.timedelta(days=window - 1), until)
        try:
            r = client.get(f"{GRAPH}/{node}/insights", params={
                "metric": metric, "period": "day",
                "since": cur.isoformat(), "until": chunk_end.isoformat(),
                "access_token": token}, timeout=30)
            data = r.json()
            if r.status_code >= 400:
                raise RuntimeError((data.get("error") or {}).get("message",
                                                                 f"HTTP {r.status_code}"))
            for m in data.get("data", []):
                for v in m.get("values", []):
                    day = str(v.get("end_time", ""))[:10]
                    if day:
                        out[day] = int(v.get("value", 0) or 0)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{label}: {e}")
            return out
        cur = chunk_end + datetime.timedelta(days=1)
    return out


def _ig_audience(client, token, ig_user_id, errors):
    """follower_demographics per breakdown (età/genere/città) → righe."""
    rows = []
    for dim in ("age", "gender", "city"):
        try:
            r = client.get(f"{GRAPH}/{ig_user_id}/insights", params={
                "metric": "follower_demographics", "period": "lifetime",
                "metric_type": "total_value", "breakdown": dim,
                "access_token": token}, timeout=30)
            data = r.json()
            if r.status_code >= 400:
                raise RuntimeError((data.get("error") or {}).get("message",
                                                                 f"HTTP {r.status_code}"))
            for m in data.get("data", []):
                for bd in (m.get("total_value") or {}).get("breakdowns", []):
                    for res in bd.get("results", []):
                        val = " · ".join(res.get("dimension_values") or ["?"])
                        rows.append((dim, val, int(res.get("value", 0) or 0)))
        except Exception as e:  # noqa: BLE001
            errors.append(f"IG demografia {dim}: {e}")
    return rows


def collect(db_path: Path, page_token: str, page_id: str = "",
            ig_user_id: str = "", *, days: int = 90, site: str = "",
            client: httpx.Client | None = None) -> dict:
    """Raccolta account-level → social_daily + social_audience.
    Ritorna {ok, ig_days, fb_days, audience_rows, errors:[...]}."""
    site = site or (ig_user_id or page_id or "social")
    today = datetime.date.today()
    since = today - datetime.timedelta(days=days - 1)
    out = {"ok": True, "ig_days": 0, "fb_days": 0, "audience_rows": 0, "errors": []}
    own_client = client is None
    c = client or httpx.Client(timeout=30.0)
    conn = metrics_io._conn(Path(db_path))
    try:
        if ig_user_id:
            reach = _series(c, page_token, ig_user_id, "reach", since, today,
                            _IG_WINDOW, out["errors"], "IG reach")
            # follower_count: l'API lo limita agli ultimi ~30gg
            nf_since = max(since, today - datetime.timedelta(days=28))
            newf = _series(c, page_token, ig_user_id, "follower_count", nf_since,
                           today, _IG_WINDOW, out["errors"], "IG follower_count")
            for day in sorted(set(reach) | set(newf)):
                _upsert(conn, site, day, "instagram",
                        reach=reach.get(day), new_followers=newf.get(day))
                out["ig_days"] += 1
            # profile_views richiede metric_type=total_value → totale ultimi
            # 28gg come snapshot sul giorno di raccolta
            try:
                r = c.get(f"{GRAPH}/{ig_user_id}/insights", params={
                    "metric": "profile_views", "period": "day",
                    "metric_type": "total_value",
                    "since": nf_since.isoformat(), "until": today.isoformat(),
                    "access_token": page_token}, timeout=30)
                data = r.json()
                if r.status_code >= 400:
                    raise RuntimeError((data.get("error") or {}).get(
                        "message", f"HTTP {r.status_code}"))
                for m in data.get("data", []):
                    tv = (m.get("total_value") or {}).get("value")
                    if tv is not None:
                        _upsert(conn, site, today.isoformat(), "instagram",
                                profile_views=int(tv or 0))
            except Exception as e:  # noqa: BLE001
                out["errors"].append(f"IG profile_views: {e}")
            # snapshot follower totali (curva accumulata raccogliendo)
            try:
                r = c.get(f"{GRAPH}/{ig_user_id}", params={
                    "fields": "followers_count", "access_token": page_token},
                    timeout=30)
                fc = int((r.json() or {}).get("followers_count", 0) or 0)
                if fc:
                    _upsert(conn, site, today.isoformat(), "instagram", followers=fc)
            except Exception as e:  # noqa: BLE001
                out["errors"].append(f"IG followers_count: {e}")
            # demografia (snapshot odierno)
            rows = _ig_audience(c, page_token, ig_user_id, out["errors"])
            if rows:
                conn.execute("DELETE FROM social_audience WHERE site=? AND date=? "
                             "AND channel='instagram'", (site, today.isoformat()))
                for dim, val, n in rows:
                    conn.execute("INSERT OR REPLACE INTO social_audience "
                                 "VALUES(?,?,?,?,?,?)",
                                 (site, today.isoformat(), "instagram", dim, val, n))
                out["audience_rows"] = len(rows)

        if page_id:
            # gli insights di pagina vogliono il PAGE token: derivalo dal token
            # user (pages_show_list) — fallback al token com'è
            ptok = page_token
            try:
                r = c.get(f"{GRAPH}/{page_id}", params={
                    "fields": "access_token", "access_token": page_token},
                    timeout=30)
                if r.status_code == 200:
                    ptok = (r.json() or {}).get("access_token") or page_token
            except Exception as e:  # noqa: BLE001
                out["errors"].append(f"FB page token: {e}")
            # follower: la metrica page_fans è morta → campo della pagina
            try:
                r = c.get(f"{GRAPH}/{page_id}", params={
                    "fields": "followers_count,fan_count",
                    "access_token": page_token}, timeout=30)
                j = r.json() or {}
                fc = int(j.get("followers_count") or j.get("fan_count") or 0)
                if fc:
                    _upsert(conn, site, today.isoformat(), "facebook",
                            followers=fc)
            except Exception as e:  # noqa: BLE001
                out["errors"].append(f"FB followers: {e}")
            eng = _series(c, ptok, page_id, "page_post_engagements", since,
                          today, _FB_WINDOW, out["errors"], "FB engagement")
            freach = _series(c, ptok, page_id, "page_impressions_unique",
                             since, today, _FB_WINDOW, out["errors"], "FB reach")
            for day in sorted(set(eng) | set(freach)):
                _upsert(conn, site, day, "facebook",
                        interactions=eng.get(day), reach=freach.get(day))
                out["fb_days"] += 1
        conn.commit()
    finally:
        conn.close()
        if own_client:
            c.close()
    # errori parziali = raccolta comunque utile: ok resta True se c'è QUALCHE dato
    out["ok"] = bool(out["ig_days"] or out["fb_days"]) or not out["errors"]
    return out
