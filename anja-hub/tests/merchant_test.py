#!/usr/bin/env python3
"""merchant_test.py — F-MerchantAPI: unit test collector + audit join, senza rete.
Sessione finta con payload reali della Merchant API (products_v1 + reports_v1)."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))

import audit_io
import google_collect
import metrics_io

FAILED = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name} {detail if not cond else ''}")
    if not cond:
        FAILED.append(name)


class FakeResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


class FakeSession:
    """Risponde con payload canned in base all'URL (products vs reports)."""

    def get(self, url, params=None, **kw):
        assert "/products/v1/accounts/acc-1/products" in url
        return FakeResp({"products": [
            {"offerId": "sku-1",
             "productAttributes": {"title": "Enzimatic Bio 5kg",
                                   "link": "https://shop.example.it/enzimatic-bio/",
                                   "price": {"amountMicros": "159000000",
                                             "currencyCode": "EUR"},
                                   "availability": "in stock",
                                   "brand": "Acme Clean"},
             "productStatus": {"itemLevelIssues": [
                 {"code": "image_link_missing", "severity": "DISAPPROVED",
                  "description": "Immagine mancante", "detail": "Aggiungi image_link",
                  "attribute": "image_link", "documentation": "https://support.google.com/x"},
             ]}},
            {"offerId": "sku-2",
             "productAttributes": {"title": "Descaler CIP",
                                   "link": "https://shop.example.it/descaler-cip/"},
             "productStatus": {"itemLevelIssues": []}},
        ]})

    def post(self, url, json=None, **kw):
        assert "/reports/v1/accounts/acc-1/reports:search" in url
        assert "WHERE date BETWEEN" in json["query"]
        assert "marketing_method" in json["query"]
        if "offer_id" in json["query"]:   # report per prodotto
            return FakeResp({"results": [
                {"productPerformanceView": {"offerId": "sku-1",
                                            "title": "Enzimatic Bio 5kg",
                                            "marketingMethod": "ADS",
                                            "clicks": "19", "impressions": "630",
                                            "clickThroughRate": 0.0302,
                                            "conversions": "2",
                                            "conversionValue": {"amountMicros": "129500000", "currencyCode": "EUR"}}},
            ]})
        return FakeResp({"results": [
            {"productPerformanceView": {"date": {"year": 2026, "month": 8, "day": 10},
                                        "marketingMethod": "ORGANIC",
                                        "clicks": "12", "impressions": "340",
                                        "clickThroughRate": 0.0353}},
            {"productPerformanceView": {"date": {"year": 2026, "month": 8, "day": 10},
                                        "marketingMethod": "ADS",
                                        "clicks": "5", "impressions": "100",
                                        "clickThroughRate": 0.05,
                                        "conversions": "1", "conversionValue": {"amountMicros": "50000000", "currencyCode": "EUR"}}},
            {"productPerformanceView": {"date": {"year": 2026, "month": 8, "day": 11},
                                        "marketingMethod": "ORGANIC",
                                        "clicks": "7", "impressions": "290",
                                        "clickThroughRate": 0.0241}},
        ]})


with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "metrics.db"

    # migrazione: db "vecchio" con merchant_daily di prima generazione
    import sqlite3
    old = sqlite3.connect(str(db))
    old.execute("CREATE TABLE gsc_daily(site TEXT, date TEXT, clicks INTEGER, "
                "impressions INTEGER, ctr REAL, position REAL, PRIMARY KEY(site, date))")
    old.execute("CREATE TABLE merchant_daily(site TEXT, date TEXT, clicks INTEGER, "
                "impressions INTEGER, ctr REAL, PRIMARY KEY(site, date))")
    old.commit()
    old.close()
    conn = metrics_io._conn(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(merchant_daily)")}
    check("migrazione: merchant_daily rigenerata con marketing_method",
          "marketing_method" in cols and "conversion_value" in cols, str(cols))

    # storico: uno snapshot vecchio NON deve sparire alla raccolta nuova
    conn.execute("INSERT INTO merchant_products VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                 ("shop.example.it", "2026-07-01", "sku-old", "Vecchio", "", None,
                  "", "", "", "disapproved", 2))
    conn.commit()

    np, ni, nd = google_collect.collect_merchant(
        FakeSession(), "acc-1", "2026-08-01", "2026-08-11", conn, "shop.example.it")
    conn.commit()
    check("collect: 2 prodotti, 1 issue, 3 righe daily", (np, ni, nd) == (2, 1, 3),
          str((np, ni, nd)))
    check("storico snapshot conservato (fix #3)",
          conn.execute("SELECT COUNT(*) n FROM merchant_products "
                       "WHERE date='2026-07-01'").fetchone()["n"] == 1)

    r = conn.execute("SELECT status, issues, price, currency, availability, brand "
                     "FROM merchant_products WHERE offer_id='sku-1'").fetchone()
    check("prodotto: status + attributi (fix #5)",
          r["status"] == "disapproved" and r["price"] == 159.0
          and r["currency"] == "EUR" and r["availability"] == "in stock"
          and r["brand"] == "Acme Clean", dict(r) if r else "None")
    r = conn.execute("SELECT * FROM merchant_issues WHERE offer_id='sku-1'").fetchone()
    check("issue persistita con codice e doc",
          r["code"] == "image_link_missing" and r["severity"] == "DISAPPROVED"
          and r["documentation"].startswith("https://"), dict(r) if r else "None")

    r = conn.execute("SELECT clicks, conversions, conversion_value FROM merchant_daily "
                     "WHERE date='2026-08-10' AND marketing_method='ads'").fetchone()
    check("daily con split method + conversioni (fix #1+#2)",
          r and r["clicks"] == 5 and r["conversions"] == 1.0
          and r["conversion_value"] == 50.0, dict(r) if r else "None")
    r = conn.execute("SELECT title, clicks, conversion_value FROM merchant_product_perf "
                     "WHERE offer_id='sku-1' AND marketing_method='ads'").fetchone()
    check("performance per prodotto con valore", r and r["clicks"] == 19
          and r["conversion_value"] == 129.5, dict(r) if r else "None")

    # dashboard: blocco merchant con by_method, serie aggregata e top per valore
    dash = metrics_io.dashboard(db)
    mer = (dash or {}).get("merchant")
    check("dashboard.merchant presente", bool(mer), str(dash.get("exists")))
    check("dashboard: by_method organico+ads",
          mer and {m["method"] for m in mer["by_method"]} == {"organic", "ads"},
          str((mer or {}).get("by_method")))
    dates = [s["date"] for s in (mer or {}).get("series", [])]
    check("dashboard: serie una riga per data", len(dates) == len(set(dates)), str(dates))
    check("dashboard: top_products per valore",
          mer and mer["top_products"]
          and mer["top_products"][0]["offer_id"] == "sku-1"
          and mer["top_products"][0]["value"] == 129.5, str((mer or {}).get("top_products")))
    check("dashboard: disapproved_products con motivo",
          mer and mer["feed"]["disapproved_products"]
          and mer["feed"]["disapproved_products"][0]["offer_id"] == "sku-1"
          and "Immagine" in mer["feed"]["disapproved_products"][0]["reasons"],
          str((mer or {}).get("feed", {}).get("disapproved_products")))
    conn.close()

    # audit join: per link normalizzato (senza slash finale)
    m = audit_io._merchant_by_link(db)
    check("audit join per link normalizzato",
          m.get("https://shop.example.it/enzimatic-bio", {}).get("status") == "disapproved"
          and m.get("https://shop.example.it/descaler-cip", {}).get("issues") == 0,
          str(m))

    # token vecchio (senza scope content) + merchant configurato → errore parlante
    import json as _json
    tok = Path(tmp) / "google-token.json"
    tok.write_text(_json.dumps({
        "token": "x", "refresh_token": "y",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "c", "client_secret": "s",
        "scopes": ["https://www.googleapis.com/auth/webmasters.readonly",
                   "https://www.googleapis.com/auth/analytics.readonly"]}))
    res = google_collect.collect(db, tok, merchant_account="acc-1", replace=False)
    check("scope mancante → errore parlante senza chiamate",
          any("scope content" in e for e in res["errors"])
          and res["merchant_products"] == 0, str(res["errors"]))

    # _finalize conta i flagged
    rows = [{"gsc": {"clicks": 0, "impressions": 0, "position": 0}, "revenue_proxy": 0,
             "scores": {"seo": 50, "eeat": 50, "geo": 50, "avg": 50},
             "merchant": {"status": "disapproved", "issues": 1}},
            {"gsc": {"clicks": 0, "impressions": 0, "position": 0}, "revenue_proxy": 0,
             "scores": {"seo": 50, "eeat": 50, "geo": 50, "avg": 50},
             "merchant": None}]
    import datetime
    out = audit_io._finalize(rows, "shop.example.it", "products", datetime.date.today())
    check("summary.merchant_flagged", out["summary"]["merchant_flagged"] == 1,
          str(out["summary"]))

print("=" * 44)
if FAILED:
    print(f"FAILED: {FAILED}")
    sys.exit(1)
print("ALL PASS")
