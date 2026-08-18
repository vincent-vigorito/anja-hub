"""woo_collect.py — ordini WooCommerce (REST wc/v3) → metrics.db.

Il dato di CASSA reale del sito: GA4 sottostima gli ordini (consent mode,
adblock, ordini offline) — su detergenza del 40% (2026-08). Legge con le
stesse credenziali WP del vault (WP_BASE_URL/WP_USERNAME/WP_APP_PASSWORD):
le Application Password WP funzionano anche su wc/v3 se l'utente ha i
permessi shop. Solo backend woo.

Tabelle:
- wc_orders_daily(site,date,orders,revenue,net_revenue,tax,shipping,discount,items,new_customers)
- wc_order_products(site,product_id,name,sku,quantity,revenue,orders,period_start,period_end)
- wc_orders(site,order_id,date,status,total,net,items,customer_id,new_customer,payment,city,region,country,company)

Stati contati come vendita: completed, processing (pagato, in evasione).
Il collector cancella e riscrive solo il proprio periodo.
"""

from __future__ import annotations

import base64
import datetime
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import metrics_io

SALE_STATUSES = ("completed", "processing")
PER_PAGE = 100


class WooError(Exception):
    pass


def _get(base: str, user: str, pw: str, path: str, params: dict) -> tuple[list, dict]:
    url = base.rstrip("/") + "/wp-json/wc/v3/" + path.lstrip("/") + "?" + urllib.parse.urlencode(params)
    tok = base64.b64encode(f"{user}:{pw}".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": "Basic " + tok,
                                               "User-Agent": "anja-hub/woo-collect"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8")), {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:200]
        raise WooError(f"HTTP {e.code} on wc/v3/{path}: {body}") from e
    except Exception as e:  # noqa: BLE001
        raise WooError(f"{type(e).__name__}: {e}") from e


def fetch_orders(base: str, user: str, pw: str, start: datetime.date, end: datetime.date,
                 statuses=SALE_STATUSES, max_pages: int = 50) -> list[dict]:
    """Tutti gli ordini pagati nel periodo (paginazione X-WP-TotalPages)."""
    out: list[dict] = []
    for st in statuses:
        page = 1
        while page <= max_pages:
            rows, hdr = _get(base, user, pw, "orders", {
                "status": st, "per_page": PER_PAGE, "page": page, "orderby": "date", "order": "asc",
                "after": f"{start.isoformat()}T00:00:00", "before": f"{end.isoformat()}T23:59:59",
            })
            out.extend(rows)
            if page >= int(hdr.get("x-wp-totalpages", "1") or 1):
                break
            page += 1
    return out


def collect(db_path: Path, base: str, user: str, pw: str, *, days: int = 90,
            site: str = "") -> dict:
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days - 1)
    try:
        orders = fetch_orders(base, user, pw, start, end)
    except WooError as e:
        return {"ok": False, "error": str(e)}
    site = site or urllib.parse.urlparse(base).netloc

    # clienti visti PRIMA del periodo (per il flag "nuovo"): un giro veloce sugli
    # ordini precedenti (ultimi 2 anni) prende solo customer_id
    seen_before: set[int] = set()
    try:
        prev_start = start - datetime.timedelta(days=730)
        for st in SALE_STATUSES:
            page = 1
            while page <= 30:
                rows, hdr = _get(base, user, pw, "orders", {
                    "status": st, "per_page": PER_PAGE, "page": page, "_fields": "id,customer_id,billing",
                    "after": f"{prev_start.isoformat()}T00:00:00",
                    "before": f"{(start - datetime.timedelta(days=1)).isoformat()}T23:59:59"})
                for o in rows:
                    cid = o.get("customer_id") or 0
                    seen_before.add(cid or ("guest:" + (o.get("billing") or {}).get("email", "")))
                if page >= int(hdr.get("x-wp-totalpages", "1") or 1):
                    break
                page += 1
    except WooError:
        pass   # best-effort: senza storico, new_customers = tutti

    daily: dict[str, list] = {}
    products: dict[int, list] = {}
    order_rows = []
    seen_in_period: set = set()
    for o in orders:
        d = (o.get("date_paid") or o.get("date_created") or "")[:10]
        if not d:
            continue
        total = float(o.get("total") or 0)
        tax = float(o.get("total_tax") or 0)
        ship = float(o.get("shipping_total") or 0)
        disc = float(o.get("discount_total") or 0)
        net = total - tax - ship
        items = sum(int(li.get("quantity") or 0) for li in o.get("line_items") or [])
        cust = o.get("customer_id") or 0
        key = cust or ("guest:" + (o.get("billing") or {}).get("email", ""))
        is_new = key not in seen_before and key not in seen_in_period
        seen_in_period.add(key)
        a = daily.setdefault(d, [0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0])
        a[0] += 1; a[1] += total; a[2] += net; a[3] += tax; a[4] += ship; a[5] += disc; a[6] += items
        a[7] += 1 if is_new else 0
        for li in o.get("line_items") or []:
            pid = int(li.get("product_id") or 0)
            p = products.setdefault(pid, [li.get("name") or "", li.get("sku") or "", 0, 0.0, set()])
            p[2] += int(li.get("quantity") or 0)
            p[3] += float(li.get("total") or 0)
            p[4].add(o.get("id"))
        b = o.get("billing") or {}
        order_rows.append((site, o.get("id"), d, o.get("status"), round(total, 2), round(net, 2), items,
                           cust, 1 if is_new else 0, o.get("payment_method_title") or "",
                           b.get("city") or "", b.get("state") or "", b.get("country") or "",
                           b.get("company") or ""))

    conn = metrics_io._conn(Path(db_path))
    try:
        conn.execute("DELETE FROM wc_orders_daily WHERE site=? AND date BETWEEN ? AND ?",
                     (site, start.isoformat(), end.isoformat()))
        conn.execute("DELETE FROM wc_orders WHERE site=? AND date BETWEEN ? AND ?",
                     (site, start.isoformat(), end.isoformat()))
        conn.execute("DELETE FROM wc_order_products WHERE site=?", (site,))
        for d, a in daily.items():
            conn.execute("INSERT OR REPLACE INTO wc_orders_daily VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (site, d, a[0], round(a[1], 2), round(a[2], 2), round(a[3], 2),
                          round(a[4], 2), round(a[5], 2), a[6], a[7]))
        for pid, p in products.items():
            conn.execute("INSERT OR REPLACE INTO wc_order_products VALUES(?,?,?,?,?,?,?,?,?)",
                         (site, pid, p[0][:200], p[1], p[2], round(p[3], 2), len(p[4]),
                          start.isoformat(), end.isoformat()))
        conn.executemany("INSERT OR REPLACE INTO wc_orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", order_rows)
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "orders": len(orders), "days": len(daily), "products": len(products),
            "revenue": round(sum(a[1] for a in daily.values()), 2),
            "range": [start.isoformat(), end.isoformat()]}
