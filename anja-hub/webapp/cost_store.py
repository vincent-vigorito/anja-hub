"""Cost store (M-CostObservability): persistenza token+costo per feature/provider/giorno.

SQLite WAL, stesso pattern di notification_bus.py — append-only, ispezionabile a mano.
Una riga per chiamata LLM tracciata. `summary()` aggrega per le viste; `today_spend()`
+ `check_budget()` alimentano i budget cap.

    record(hub, provider=, model=, feature=, input_tokens=, output_tokens=)
       → prezza via pricing.cost_of (se cost_usd non fornito) e inserisce.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pricing

FEATURES = ("chat", "coding", "routine", "heartbeat", "dialectic",
            "commitment", "judge", "summarize", "ingest", "other")


def db_path(hub_path: Path) -> Path:
    return Path(hub_path) / "data" / "costs.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def get_conn(hub_path: Path) -> sqlite3.Connection:
    p = db_path(hub_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), isolation_level=None)   # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS costs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            day TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            feature TEXT NOT NULL DEFAULT 'other',
            scope TEXT NOT NULL DEFAULT 'hub',
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL NOT NULL DEFAULT 0,
            priced INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_costs_day ON costs(day)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_costs_feature ON costs(feature)")


def record(hub_path: Path, *, provider: str = "", model: str = "", feature: str = "other",
           input_tokens: int = 0, output_tokens: int = 0, scope: str = "hub",
           cost_usd: Optional[float] = None) -> dict:
    """Registra una chiamata LLM. Se cost_usd è None lo calcola via pricing.
    Best-effort: non solleva mai (il tracking non deve rompere il path LLM)."""
    try:
        priced = True
        if cost_usd is None:
            cost_usd, priced = pricing.cost_of(model, input_tokens, output_tokens,
                                               pricing.load_pricing(hub_path))
        rec = {
            "ts": _now(), "day": _today(), "provider": provider or "", "model": model or "",
            "feature": feature or "other", "scope": scope or "hub",
            "input_tokens": int(input_tokens or 0), "output_tokens": int(output_tokens or 0),
            "cost_usd": float(cost_usd or 0), "priced": 1 if priced else 0,
        }
        conn = get_conn(hub_path)
        conn.execute(
            "INSERT INTO costs (ts,day,provider,model,feature,scope,input_tokens,output_tokens,cost_usd,priced) "
            "VALUES (:ts,:day,:provider,:model,:feature,:scope,:input_tokens,:output_tokens,:cost_usd,:priced)",
            rec)
        conn.close()
        return rec
    except Exception:
        return {}


def record_usage_event(hub_path: Path, ev: dict, *, feature: str, scope: str = "hub",
                       provider: str = "") -> dict:
    """Adatta l'evento `{type:"usage", input_tokens, output_tokens, model, cost_usd?}`
    già emesso da claude_chat/llm_router. Usa cost_usd se presente (es. opencode)."""
    model = ev.get("model", "")
    return record(
        hub_path, provider=provider or ev.get("provider", "") or pricing.provider_of(model),
        model=model, feature=feature, scope=scope,
        input_tokens=ev.get("input_tokens", 0), output_tokens=ev.get("output_tokens", 0),
        cost_usd=ev.get("cost_usd"),
    )


def today_spend(hub_path: Path, feature: Optional[str] = None) -> float:
    try:
        conn = get_conn(hub_path)
        if feature:
            row = conn.execute("SELECT COALESCE(SUM(cost_usd),0) AS s FROM costs WHERE day=? AND feature=?",
                               (_today(), feature)).fetchone()
        else:
            row = conn.execute("SELECT COALESCE(SUM(cost_usd),0) AS s FROM costs WHERE day=?",
                               (_today(),)).fetchone()
        conn.close()
        return round(float(row["s"]), 6)
    except Exception:
        return 0.0


def summary(hub_path: Path, days: int = 7) -> dict:
    """Viste per la UI: totale oggi, breakdown per provider/feature (range), trend per giorno."""
    try:
        conn = get_conn(hub_path)
        today = _today()
        start = time.strftime("%Y-%m-%d", time.localtime(time.time() - (days - 1) * 86400))
        today_total = conn.execute("SELECT COALESCE(SUM(cost_usd),0) AS s FROM costs WHERE day=?",
                                   (today,)).fetchone()["s"]
        by_provider = [dict(r) for r in conn.execute(
            "SELECT provider, COALESCE(SUM(cost_usd),0) AS cost, SUM(input_tokens+output_tokens) AS tokens "
            "FROM costs WHERE day>=? GROUP BY provider ORDER BY cost DESC", (start,))]
        by_feature = [dict(r) for r in conn.execute(
            "SELECT feature, COALESCE(SUM(cost_usd),0) AS cost, SUM(input_tokens+output_tokens) AS tokens "
            "FROM costs WHERE day>=? GROUP BY feature ORDER BY cost DESC", (start,))]
        by_day = [dict(r) for r in conn.execute(
            "SELECT day, COALESCE(SUM(cost_usd),0) AS cost FROM costs WHERE day>=? GROUP BY day ORDER BY day", (start,))]
        unpriced = conn.execute("SELECT COUNT(*) AS n FROM costs WHERE priced=0 AND day>=?",
                                (start,)).fetchone()["n"]
        conn.close()
        return {
            "today": round(float(today_total), 4),
            "range_days": days,
            "by_provider": by_provider,
            "by_feature": by_feature,
            "by_day": by_day,
            "unpriced_calls": unpriced,
            "budgets": check_all_budgets(hub_path),
        }
    except Exception as e:
        return {"today": 0, "error": str(e), "by_provider": [], "by_feature": [], "by_day": []}


# -- budget cap ------------------------------------------------------------
def _budgets_path(hub_path: Path) -> Path:
    return Path(hub_path) / "config" / "budgets.json"


def load_budgets(hub_path: Path) -> dict:
    import json
    p = _budgets_path(hub_path)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"daily": {}}


def set_budget(hub_path: Path, feature: str, cap: Optional[float]) -> dict:
    import json
    b = load_budgets(hub_path)
    daily = b.setdefault("daily", {})
    if cap is None:
        daily.pop(feature, None)
    else:
        daily[feature] = float(cap)
    p = _budgets_path(hub_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(b, indent=2) + "\n", encoding="utf-8")
    return b


def check_budget(hub_path: Path, feature: str) -> dict:
    """{ok, feature_spent, feature_cap, total_spent, total_cap}. ok=False se uno dei cap
    è superato. `_total` nel config è il cap su tutta la spesa giornaliera."""
    daily = load_budgets(hub_path).get("daily", {})
    f_cap = daily.get(feature)
    t_cap = daily.get("_total")
    f_spent = today_spend(hub_path, feature)
    t_spent = today_spend(hub_path)
    ok = (f_cap is None or f_spent < f_cap) and (t_cap is None or t_spent < t_cap)
    return {"ok": ok, "feature": feature, "feature_spent": f_spent, "feature_cap": f_cap,
            "total_spent": t_spent, "total_cap": t_cap}


def check_all_budgets(hub_path: Path) -> list:
    daily = load_budgets(hub_path).get("daily", {})
    out = []
    for feature, cap in daily.items():
        spent = today_spend(hub_path) if feature == "_total" else today_spend(hub_path, feature)
        out.append({"feature": feature, "cap": cap, "spent": round(spent, 4),
                    "over": spent >= cap})
    return out
