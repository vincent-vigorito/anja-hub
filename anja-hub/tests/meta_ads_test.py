#!/usr/bin/env python3
"""meta_ads_test.py — collector Meta Ads: unit test senza rete (client finto)."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))

import meta_ads_collect
import metrics_io

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
    """Due pagine di insights (paging.next) + parsing actions/action_values."""

    def __init__(self):
        self.calls = 0

    def get(self, url, params=None):
        self.calls += 1
        if self.calls == 1:
            assert "act_123/insights" in url and params["level"] == "campaign"
            return FakeResp({"data": [
                {"campaign_name": "Post: Black Friday", "date_start": "2026-08-10",
                 "spend": "12.50", "impressions": "3400", "clicks": "88",
                 "actions": [{"action_type": "purchase", "value": "3"},
                             {"action_type": "omni_purchase", "value": "2"},
                             {"action_type": "link_click", "value": "80"}],
                 "action_values": [{"action_type": "omni_purchase", "value": "129.90"}]},
            ], "paging": {"next": "https://graph.facebook.com/next-page"}})
        assert url == "https://graph.facebook.com/next-page" and params is None
        return FakeResp({"data": [
            {"campaign_name": "Post: Rapid Kal", "date_start": "2026-08-11",
             "spend": "0", "impressions": "150", "clicks": "2"},
        ]})


with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "metrics.db"
    conn = metrics_io._conn(db)
    # riga Google preesistente (da GA4) + vecchia riga meta da rimpiazzare
    conn.execute("INSERT INTO ads_daily VALUES(?,?,?,?,?,?,?,?)",
                 ("site", "2026-08-01", "PMax Acme", 30.0, 0, 40, 2.0, 90.0))
    conn.execute("INSERT INTO ads_daily VALUES(?,?,?,?,?,?,?,?)",
                 ("act_123", "2026-07-01", "meta:Vecchia", 5.0, 100, 3, 0, 0))
    conn.commit()
    conn.close()

    res = meta_ads_collect.collect(db, "tok", "123", days=30, client=FakeClient())
    check("collect ok con paginazione", res["ok"] and res["rows"] == 2, str(res))

    conn = metrics_io._conn(db)
    r = conn.execute("SELECT * FROM ads_daily WHERE campaign='meta:Post: Black Friday'").fetchone()
    check("riga meta con spend/click", r and r["spend"] == 12.5 and r["clicks"] == 88,
          dict(r) if r else "None")
    check("conversioni: omni_purchase preferito (no doppio conteggio)",
          r["conversions"] == 2.0 and r["revenue"] == 129.9, dict(r) if r else "")
    check("prefisso meta: su tutte le righe nuove",
          conn.execute("SELECT COUNT(*) n FROM ads_daily WHERE campaign LIKE 'meta:%'")
          .fetchone()["n"] == 2)
    check("vecchie righe meta rimpiazzate",
          conn.execute("SELECT COUNT(*) n FROM ads_daily WHERE campaign='meta:Vecchia'")
          .fetchone()["n"] == 0)
    check("righe Google intatte",
          conn.execute("SELECT COUNT(*) n FROM ads_daily WHERE campaign='PMax Acme'")
          .fetchone()["n"] == 1)
    conn.close()

    # errore API → soft, ok=False, niente eccezioni
    class ErrClient:
        def get(self, url, params=None):
            return FakeResp({"error": {"message": "Invalid OAuth access token."}}, 401)

    res = meta_ads_collect.collect(db, "bad", "123", client=ErrClient())
    check("errore API → soft error parlante",
          not res["ok"] and "OAuth" in res["errors"][0], str(res))

print("=" * 44)
if FAILED:
    print(f"FAILED: {FAILED}")
    sys.exit(1)
print("ALL PASS")
