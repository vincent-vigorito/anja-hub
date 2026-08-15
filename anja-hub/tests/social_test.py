#!/usr/bin/env python3
"""social_test.py — collector social account-level: unit test senza rete."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))

import metrics_io
import social_collect

FAILED = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name} {detail if not cond else ''}")
    if not cond:
        FAILED.append(name)


class FakeResp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._p = payload

    def json(self):
        return self._p


class FakeClient:
    """IG: reach/profile_views/follower_count ok, followers_count ok, demografia
    ok. FB: page_fans ok, page_impressions_unique DEPRECATO (errore soft)."""

    def get(self, url, params=None, timeout=None):
        params = params or {}
        metric = params.get("metric", "")
        fields = params.get("fields", "")
        if url.endswith("/ig-9/insights"):
            if metric == "follower_demographics":
                bd = params.get("breakdown")
                results = {"age": [{"dimension_values": ["25-34"], "value": 480}],
                           "gender": [{"dimension_values": ["F"], "value": 700}],
                           "city": [{"dimension_values": ["Milano, Lombardia"], "value": 130}]}
                return FakeResp({"data": [{"total_value": {"breakdowns": [
                    {"results": results[bd]}]}}]})
            if metric == "profile_views":
                assert params.get("metric_type") == "total_value"
                return FakeResp({"data": [{"name": "profile_views",
                                           "total_value": {"value": 84}}]})
            vals = {"reach": 250, "follower_count": 3}
            return FakeResp({"data": [{
                "name": metric, "period": "day",
                "values": [{"value": vals.get(metric, 0),
                            "end_time": params["since"] + "T07:00:00+0000"}]}]})
        if url.endswith("/ig-9"):
            return FakeResp({"followers_count": 1234})
        if url.endswith("/page-7"):
            if "access_token" in fields:
                return FakeResp({"access_token": "ptok-page"})
            return FakeResp({"followers_count": 890})
        if url.endswith("/page-7/insights"):
            assert params.get("access_token") == "ptok-page", "serve il PAGE token"
            if metric == "page_impressions_unique":
                return FakeResp({"error": {"message": "(#100) deprecated metric"}}, 400)
            vals = {"page_post_engagements": 44}
            return FakeResp({"data": [{
                "name": metric, "period": "day",
                "values": [{"value": vals.get(metric, 0),
                            "end_time": params["since"] + "T07:00:00+0000"}]}]})
        raise AssertionError(f"url inattesa: {url}")


with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "metrics.db"
    res = social_collect.collect(db, "tok", "page-7", "ig-9", days=10,
                                 client=FakeClient())
    check("collect ok con errore FB soft (metrica deprecata)",
          res["ok"] and res["ig_days"] >= 1 and res["fb_days"] >= 1
          and any("FB reach" in e for e in res["errors"]), str(res))
    check("demografia raccolta", res["audience_rows"] == 3, str(res))

    conn = metrics_io._conn(db)
    r = conn.execute("SELECT * FROM social_daily WHERE channel='instagram' "
                     "ORDER BY date LIMIT 1").fetchone()
    check("IG daily: reach in serie", r and r["reach"] == 250,
          dict(r) if r else "None")
    import datetime
    today = datetime.date.today().isoformat()
    r = conn.execute("SELECT followers, profile_views FROM social_daily "
                     "WHERE channel='instagram' AND date=?", (today,)).fetchone()
    check("snapshot IG: follower + profile_views (total_value)",
          r and r["followers"] == 1234 and r["profile_views"] == 84,
          dict(r) if r else "None")
    r = conn.execute("SELECT interactions, reach FROM social_daily "
                     "WHERE channel='facebook' AND interactions IS NOT NULL "
                     "ORDER BY date LIMIT 1").fetchone()
    check("FB daily: engagement col page token, reach NULL (deprecato)",
          r and r["interactions"] == 44 and r["reach"] is None,
          dict(r) if r else "None")
    r = conn.execute("SELECT followers FROM social_daily WHERE channel='facebook' "
                     "AND date=?", (today,)).fetchone()
    check("snapshot follower FB dal campo pagina (page_fans è morto)",
          r and r["followers"] == 890, dict(r) if r else "None")
    r = conn.execute("SELECT value, count FROM social_audience "
                     "WHERE dimension='city'").fetchone()
    check("audience città", r and r["value"].startswith("Milano") and r["count"] == 130)

    # dashboard: blocco social con followers e audience
    conn.execute("INSERT OR REPLACE INTO gsc_daily VALUES('s','2026-08-01',1,10,0.1,5.0)")
    conn.commit()
    conn.close()
    dash = metrics_io.dashboard(db)
    soc = (dash or {}).get("social")
    check("dashboard.social presente", bool(soc), str(dash.get("exists")))
    check("dashboard: followers + audience",
          soc and soc["followers"].get("instagram") == 1234
          and soc["audience"]["age"][0]["value"] == "25-34", str(soc)[:120])
    check("dashboard: kpis con confronto periodo",
          soc and "ig_reach" in soc["kpis"] and "fb_interactions" in soc["kpis"]
          and soc["kpis"]["ig_reach"]["value"] >= 0
          and "delta" in soc["kpis"]["ig_reach"], str((soc or {}).get("kpis")))

print("=" * 44)
if FAILED:
    print(f"FAILED: {FAILED}")
    sys.exit(1)
print("ALL PASS")
