#!/usr/bin/env python3
"""Test google_ads_collect con fake session (shape reale searchStream v22).

Run: python3 anja-hub/tests/google_ads_test.py
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))
import google_ads_collect as gac  # noqa: E402
import metrics_io  # noqa: E402

PASS = FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ✓ {label}")
    else:
        FAIL += 1; print(f"  ✗ {label} {detail}")


class FakeResp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status; self._p = payload; self.text = text or str(payload)
    def json(self): return self._p


class FakeSession:
    """Prima chiamata con login-customer-id → 403 USER_PERMISSION_DENIED
    (retry senza MCC), poi 2 batch di risultati."""
    def __init__(self, fail_mcc=False):
        self.calls = []; self.fail_mcc = fail_mcc
    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": dict(headers or {}), "gaql": (json or {}).get("query", "")})
        if self.fail_mcc and "login-customer-id" in (headers or {}):
            return FakeResp(403, [{"error": {"code": 403, "message": "USER_PERMISSION_DENIED"}}],
                            text="USER_PERMISSION_DENIED")
        row = lambda d, c, cost, imp, clk, conv, val: {  # noqa: E731
            "segments": {"date": d}, "campaign": {"name": c, "status": "ENABLED"},
            "metrics": {"costMicros": str(cost), "impressions": str(imp), "clicks": str(clk),
                        "conversions": conv, "conversionsValue": val}}
        return FakeResp(200, [
            {"results": [row("2026-08-10", "PMax Acme", 12_340_000, 1500, 42, 3.0, 210.5),
                         row("2026-08-10", "Search Brand", 2_000_000, 300, 25, 1.0, 60.0)]},
            {"results": [row("2026-08-11", "PMax Acme", 9_990_000, 1200, 30, 2.0, 140.0)]},
        ])


def main():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "metrics.db"
        metrics_io._conn(db).close()   # crea schema
        # pre-esistente: riga meta e riga ga4 devono SOPRAVVIVERE al collect gads
        c = sqlite3.connect(db)
        c.execute("INSERT INTO ads_daily VALUES('acme','2026-08-10','meta:Old',5,10,1,0,0)")
        c.execute("INSERT INTO ads_daily VALUES('acme','2026-08-10','GA4Camp',7,10,1,0,0)")
        c.execute("INSERT INTO ads_daily VALUES('acme','2026-08-01','gads:Stale',1,1,1,0,0)")
        c.commit(); c.close()

        print("collect ok (con retry MCC):")
        sess = FakeSession(fail_mcc=True)
        r = gac.collect(db, sess, "386-143-4233", "devtok", login_customer_id="256-328-9548",
                        days=30, site="acme")
        check("ok", r.get("ok"), str(r))
        check("3 righe", r.get("ads_daily") == 3, str(r))
        check("2 campagne", r.get("campaigns") == 2, str(r))
        check("retry senza MCC (2 chiamate)", len(sess.calls) == 2, str(len(sess.calls)))
        check("2a chiamata senza login-customer-id", "login-customer-id" not in sess.calls[1]["headers"])
        check("customer id senza trattini nell'URL", "/customers/3861434233/" in sess.calls[0]["url"])
        check("GAQL su campaign con date", "FROM campaign" in sess.calls[0]["gaql"] and "segments.date BETWEEN" in sess.calls[0]["gaql"])

        c = sqlite3.connect(db)
        rows = {(d, camp): (sp, imp, clk, conv, rev) for d, camp, sp, imp, clk, conv, rev
                in c.execute("SELECT date,campaign,spend,impressions,clicks,conversions,revenue FROM ads_daily")}
        c.close()
        check("prefisso gads:", ("2026-08-10", "gads:PMax Acme") in rows, str(list(rows)[:5]))
        sp, imp, clk, conv, rev = rows[("2026-08-10", "gads:PMax Acme")]
        check("cost micros → euro", abs(sp - 12.34) < 1e-6, str(sp))
        check("impressions/clicks", imp == 1500 and clk == 42)
        check("conversions/value", conv == 3.0 and rev == 210.5)
        check("riga meta: sopravvive", ("2026-08-10", "meta:Old") in rows)
        check("riga GA4 sopravvive", ("2026-08-10", "GA4Camp") in rows)
        check("gads: stale rimossa", ("2026-08-01", "gads:Stale") not in rows)

        print("errori configurazione:")
        check("customer vuoto", not gac.collect(db, sess, "", "devtok").get("ok"))
        check("dev token vuoto", not gac.collect(db, sess, "123", "").get("ok"))

        print("errore API (dev token non approvato):")
        class Deny(FakeSession):
            def post(self, url, headers=None, json=None, timeout=None):
                return FakeResp(403, [{"error": {"code": 403, "message": "PERMISSION_DENIED", "details": [
                    {"errors": [{"message": "The developer token is not approved."}]}]}}])
        r = gac.collect(db, Deny(), "123", "devtok")
        check("ok=False + messaggio dettaglio", not r["ok"] and "not approved" in r["error"], str(r))

    print("=" * 44)
    if FAIL:
        print(f"FAIL: {FAIL} (pass {PASS})"); sys.exit(1)
    print(f"ALL PASS ({PASS})")


if __name__ == "__main__":
    main()
