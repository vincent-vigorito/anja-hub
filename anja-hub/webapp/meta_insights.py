"""meta_insights.py — collector Meta Insights (reach/like/commenti dei post social).

Riempie l'engagement della tabella "Performance social organica" chiamando la
Graph API di Meta col token del vault. FB: post-id diretto → reactions/comments/
shares + reach (insights). IG: lo shortcode del permalink va risolto in media-id
(lista media dell'IG user), poi like/comments/reach.

Robusto per-post: un errore su un post non blocca gli altri. Ritorna le metriche
raccolte + eventuali errori, senza inventare numeri.
"""

from __future__ import annotations

import datetime
import re

import httpx

GRAPH = "https://graph.facebook.com/v19.0"


def _first_insight(payload: dict, metric: str = None):
    data = (payload or {}).get("data") or []
    if not data:
        return None
    values = data[0].get("values") or []
    return values[0].get("value") if values else None


def _fb_metrics(client: httpx.Client, token: str, post_id: str) -> dict:
    r = client.get(f"{GRAPH}/{post_id}", params={
        "fields": "reactions.summary(total_count),comments.summary(total_count),shares",
        "access_token": token}, timeout=20.0)
    r.raise_for_status()
    d = r.json()
    out = {
        "likes": (d.get("reactions") or {}).get("summary", {}).get("total_count"),
        "comments": (d.get("comments") or {}).get("summary", {}).get("total_count"),
        "shares": (d.get("shares") or {}).get("count"),
        # reach/impression FB: deprecato dalla Graph API attuale (non è un permesso) →
        # resta None di proposito, il reach FB si guarda in Business Suite.
        "reach": None,
        "clicks": None,
    }
    ci = client.get(f"{GRAPH}/{post_id}/insights",
                    params={"metric": "post_clicks", "access_token": token}, timeout=20.0)
    if ci.status_code == 200:
        out["clicks"] = _first_insight(ci.json())
    return out


def _ig_media_map(client: httpx.Client, token: str, ig_user_id: str) -> dict:
    """shortcode → media_id, dalla lista media dell'IG business account (paginata)."""
    out, url = {}, f"{GRAPH}/{ig_user_id}/media"
    params = {"fields": "id,permalink", "limit": 100, "access_token": token}
    for _ in range(20):
        r = client.get(url, params=params, timeout=20.0)
        r.raise_for_status()
        j = r.json()
        for m in j.get("data", []):
            sc = re.search(r"/p/([A-Za-z0-9_-]+)", m.get("permalink", ""))
            if sc:
                out[sc.group(1)] = m["id"]
        nxt = (j.get("paging") or {}).get("next")
        if not nxt:
            break
        url, params = nxt, None
    return out


def _ig_metrics(client: httpx.Client, token: str, media_id: str) -> dict:
    d = client.get(f"{GRAPH}/{media_id}",
                   params={"fields": "like_count,comments_count", "access_token": token}, timeout=20.0).json()
    out = {"likes": d.get("like_count"), "comments": d.get("comments_count"),
           "reach": None, "clicks": None, "saves": None, "shares": None}
    # saved/shares non valgono per tutti i media type: combinata con fallback
    ri = client.get(f"{GRAPH}/{media_id}/insights",
                    params={"metric": "reach,saved,shares", "access_token": token},
                    timeout=20.0)
    if ri.status_code != 200:
        ri = client.get(f"{GRAPH}/{media_id}/insights",
                        params={"metric": "reach", "access_token": token}, timeout=20.0)
    if ri.status_code == 200:
        payload = ri.json()
        for m in payload.get("data", []):
            key = {"reach": "reach", "saved": "saves", "shares": "shares"}.get(m.get("name"))
            vals = m.get("values") or [{}]
            if key:
                out[key] = vals[0].get("value")
    return out


def collect(posts: list[dict], token: str, ig_user_id: str = "") -> dict:
    """Fetcha l'engagement per i `posts` (con ref_key fb:/ig:). Ritorna
    {ok, insights:{ref_key:{...}}, collected, errors:[...]}."""
    if not token:
        return {"ok": False, "error": "META_ACCESS_TOKEN mancante nel vault"}
    insights, errors, collected = {}, [], 0
    with httpx.Client() as client:
        ig_map = {}
        need_ig = any((p.get("ref_key") or "").startswith("ig:") for p in posts)
        if need_ig and ig_user_id:
            try:
                ig_map = _ig_media_map(client, token, ig_user_id)
            except httpx.HTTPError as e:
                errors.append(f"lista media IG: {e}")
        for p in posts:
            key = p.get("ref_key") or ""
            try:
                if key.startswith("fb:"):
                    insights[key] = _fb_metrics(client, token, key[3:])
                    collected += 1
                elif key.startswith("ig:"):
                    mid = ig_map.get(key[3:])
                    if not mid:
                        errors.append(f"IG {key[3:]}: media non trovato")
                        continue
                    insights[key] = _ig_metrics(client, token, mid)
                    collected += 1
            except httpx.HTTPStatusError as e:
                errors.append(f"{key}: HTTP {e.response.status_code}")
            except httpx.HTTPError as e:
                errors.append(f"{key}: {e}")
    insights["_updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    return {"ok": True, "insights": insights, "collected": collected, "errors": errors}
