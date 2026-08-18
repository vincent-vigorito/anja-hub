#!/usr/bin/env python3
"""Test woo_collect (fake wc/v3) + blocco sales della dashboard.
Run: python3 anja-hub/tests/woo_test.py"""
import datetime, sqlite3, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))
import woo_collect, metrics_io  # noqa: E402

PASS = FAIL = 0
def check(l, c, d=""):
    global PASS, FAIL
    if c: PASS += 1; print(f"  ✓ {l}")
    else: FAIL += 1; print(f"  ✗ {l} {d}")

today = datetime.date.today()
d0, d1 = (today - datetime.timedelta(days=1)).isoformat(), (today - datetime.timedelta(days=2)).isoformat()
def order(oid, d, total, tax, ship, cust, items, company="", status="completed"):
    return {"id": oid, "status": status, "date_paid": f"{d}T10:00:00", "total": str(total), "total_tax": str(tax),
            "shipping_total": str(ship), "discount_total": "0", "customer_id": cust,
            "payment_method_title": "Carta", "billing": {"city": "Roma", "state": "RM", "country": "IT", "company": company, "email": f"c{cust}@x.it"},
            "line_items": [{"product_id": pid, "name": f"Prod {pid}", "sku": f"S{pid}", "quantity": q, "total": str(t)} for pid, q, t in items]}

CALLS = []
def fake_get(base, user, pw, path, params):
    CALLS.append(params)
    if params.get("_fields"):   # storico clienti: cust 7 già visto
        return [{"id": 1, "customer_id": 7, "billing": {"email": "c7@x.it"}}], {"x-wp-totalpages": "1"}
    if params.get("status") == "processing":
        return [order(30, d0, 50, 9, 0, 0, [(3, 1, 41)], status="processing")], {"x-wp-totalpages": "1"}
    return [order(10, d0, 159, 28.67, 0, 7, [(941, 1, 130.33)], company="Autolavaggio X"),
            order(11, d1, 80, 14.4, 5.6, 8, [(941, 1, 30), (5, 2, 30)])], {"x-wp-totalpages": "1"}
woo_collect._get = fake_get

with tempfile.TemporaryDirectory() as td:
    db = Path(td) / "metrics.db"; metrics_io._conn(db).close()
    print("collect:")
    r = woo_collect.collect(db, "https://shop.example.com", "u", "p", days=30, site="shop")
    check("ok", r.get("ok"), str(r)); check("3 ordini (2 completed + 1 processing)", r["orders"] == 3, str(r))
    check("revenue 289", abs(r["revenue"] - 289) < 1e-6, str(r["revenue"]))
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    day = {x["date"]: dict(x) for x in c.execute("SELECT * FROM wc_orders_daily")}
    check("2 giorni", len(day) == 2, str(list(day)))
    check(f"{d0}: 2 ordini, 209 revenue", day[d0]["orders"] == 2 and abs(day[d0]["revenue"] - 209) < 1e-6, str(day.get(d0)))
    check("net = total - tax - ship", abs(day[d0]["net_revenue"] - (159 - 28.67 + 50 - 9)) < 1e-6, str(day[d0]["net_revenue"]))
    check("new_customers: cust7 storico → non nuovo; guest → nuovo", day[d0]["new_customers"] == 1, str(day[d0]))
    prods = {x["product_id"]: dict(x) for x in c.execute("SELECT * FROM wc_order_products")}
    check("prodotto 941 aggregato su 2 ordini", prods[941]["quantity"] == 2 and prods[941]["orders"] == 2 and abs(prods[941]["revenue"] - 160.33) < 1e-6, str(prods.get(941)))
    orders = list(c.execute("SELECT company, new_customer FROM wc_orders ORDER BY order_id"))
    check("company salvata (B2B)", orders[0]["company"] == "Autolavaggio X")
    c.close()
    print("dashboard sales block:")
    # anchor = ieri (gsc vuoto → il dashboard usa oggi-3?): forzo la lettura via metrics_io.dashboard
    dash = metrics_io.dashboard(db, days=28)
    sb = dash.get("sales")
    check("blocco sales presente", bool(sb), str(dash.get("exists")))
    if sb:
        check("kpi orders", sb["kpis"]["orders"]["value"] >= 1, str(sb["kpis"]["orders"]))
        check("top_products ordinati per revenue", sb["top_products"][0]["product_id"] == 941, str(sb["top_products"][:1]))
        check("b2b_share", sb["b2b_share"] is not None, str(sb["b2b_share"]))
        check("cash_roas None senza spesa ads", sb["cash_roas"] is None)

print("=" * 44)
if FAIL: print(f"FAIL: {FAIL} (pass {PASS})"); sys.exit(1)
print(f"ALL PASS ({PASS})")
