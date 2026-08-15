"""M-CostObservability — pricing + cost_store + budget cap.

    /opt/homebrew/opt/python@3.12/bin/python3.12 anja-hub/tests/test_cost_store.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))
import pricing       # noqa: E402
import cost_store    # noqa: E402


def main():
    # 1. pricing: match più lungo, famiglie, sconosciuto ---------------------
    c, p = pricing.cost_of("claude-opus-4-8", 1_000_000, 1_000_000)
    assert c == 90.0 and p, (c, p)                          # 15 + 75
    c, p = pricing.cost_of("claude-haiku-4-5", 1_000_000, 0)
    assert c == 1.0 and p, c                                # claude-haiku
    c, p = pricing.cost_of("claude-3-5-haiku-20241022", 1_000_000, 0)
    assert c == 0.80 and p, c                               # match più specifico
    c, p = pricing.cost_of("grok-4-latest", 1_000_000, 0)
    assert c == 5.0 and p, c                                # grok-4 batte grok
    c, p = pricing.cost_of("modello-ignoto", 1_000_000, 1_000_000)
    assert c == 0.0 and not p, (c, p)                       # unpriced
    c, _ = pricing.cost_of("sonnet", 1_000_000, 0)
    assert c == 3.0, c                                      # alias breve (routine usano "sonnet")
    c, _ = pricing.cost_of("opus", 1_000_000, 1_000_000)
    assert c == 90.0, c
    assert pricing.provider_of("claude-opus-4-8") == "anthropic"
    assert pricing.provider_of("grok-4") == "xai"
    print("✓ pricing: opus=90 · haiku=1 · 3-5-haiku=0.80 · grok-4=5 · alias brevi · provider_of · ignoto=unpriced")

    # 2. cost_store: record + spend ----------------------------------------
    hub = Path(tempfile.mkdtemp())
    cost_store.record(hub, provider="anthropic", model="claude-opus-4-8",
                      feature="chat", input_tokens=1_000_000, output_tokens=1_000_000)
    cost_store.record(hub, provider="anthropic", model="claude-sonnet-4-6",
                      feature="coding", input_tokens=1_000_000, output_tokens=0, cost_usd=2.5)
    cost_store.record(hub, provider="xai", model="grok-4", feature="routine",
                      input_tokens=500_000, output_tokens=200_000)
    assert cost_store.today_spend(hub) == round(90.0 + 2.5 + (500_000*5 + 200_000*15)/1e6, 6), cost_store.today_spend(hub)
    assert cost_store.today_spend(hub, "chat") == 90.0
    print("✓ cost_store: record + today_spend (totale + per feature)")

    # 3. summary -----------------------------------------------------------
    s = cost_store.summary(hub, days=7)
    feats = {f["feature"] for f in s["by_feature"]}
    assert feats == {"chat", "coding", "routine"}, feats
    assert any(p["provider"] == "anthropic" for p in s["by_provider"])
    assert s["today"] > 90 and len(s["by_day"]) == 1
    print(f"✓ summary: today=${s['today']} · feature={feats} · provider ok")

    # 4. budget cap --------------------------------------------------------
    cost_store.set_budget(hub, "coding", 10.0)
    assert cost_store.check_budget(hub, "coding")["ok"], "2.5 < 10 → ok"
    cost_store.set_budget(hub, "coding", 1.0)
    assert not cost_store.check_budget(hub, "coding")["ok"], "2.5 >= 1 → over"
    cost_store.set_budget(hub, "_total", 50.0)
    assert not cost_store.check_budget(hub, "chat")["ok"], "totale > 50 → blocca tutto"
    cost_store.set_budget(hub, "_total", None)              # rimozione cap
    cost_store.set_budget(hub, "coding", None)
    assert cost_store.check_budget(hub, "chat")["ok"], "nessun cap → ok"
    print("✓ budget: cap per-feature + _total + rimozione")

    # 5. unpriced tracciato -------------------------------------------------
    cost_store.record(hub, model="modello-ignoto", feature="other",
                      input_tokens=1000, output_tokens=1000)
    assert cost_store.summary(hub)["unpriced_calls"] == 1
    print("✓ unpriced: chiamata senza prezzo conteggiata (segnala tabella da aggiornare)")

    print("\nOK 5/5")


if __name__ == "__main__":
    main()
