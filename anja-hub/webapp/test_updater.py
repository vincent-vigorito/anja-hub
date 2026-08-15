"""test_updater.py — versioning + migration runner + apply flow. Nessun server."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import updater

PASS, FAIL = 0, 0
def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ✓ {name}")
    else:
        FAIL += 1; print(f"  ✗ {name}")


def main():
    # --- versioning puro ---
    print("[versioning]")
    check("current_version non vuota", bool(updater.current_version()))
    check("is_newer 0.20.0 > 0.19.9", updater.is_newer("0.20.0", "0.19.9"))
    check("is_newer 1.0.0 > 0.99.99", updater.is_newer("1.0.0", "0.99.99"))
    check("non newer se uguale", not updater.is_newer("0.20.0", "0.20.0"))
    check("suffisso -dev ignorato", not updater.is_newer("0.20.0-dev", "0.20.0"))

    with tempfile.TemporaryDirectory() as tmp:
        hub = Path(tmp) / "hub"
        (hub / "config").mkdir(parents=True)
        (hub / "config.json").write_text(json.dumps({"mode": "personal", "default_user": "v"}))

        # --- hub version get/set (merge non distruttivo) ---
        print("\n[hub version]")
        check("hub_version iniziale None", updater.hub_version(hub) is None)
        updater.set_hub_version(hub, "0.19.0")
        check("hub_version letta", updater.hub_version(hub) == "0.19.0")
        cfg = json.loads((hub / "config.json").read_text())
        check("set preserva il resto del config", cfg.get("mode") == "personal" and cfg.get("default_user") == "v")

        # --- check: version_behind ---
        print("\n[check]")
        info = updater.check(hub)
        check("code_version presente", bool(info["code_version"]))
        check("version_behind True (hub 0.19 < code)", info["version_behind"] is True)

        # --- migration runner reale (0001_baseline) ---
        print("\n[migration runner]")
        pend = updater.pending_migrations(hub)
        check("0001_baseline pending", "0001_baseline" in pend)
        r = updater.run_migrations(hub)
        check("run ok", r["ok"])
        check("baseline applicata", "0001_baseline" in r["applied"])
        check("backups/ creata dalla migrazione", (hub / "backups").is_dir())
        check("state file scritto", (hub / updater.MIGRATIONS_STATE).is_file())
        # idempotenza
        r2 = updater.run_migrations(hub)
        check("seconda run non riapplica", r2["ok"] and r2["applied"] == [])
        check("nessuna pending dopo apply", updater.pending_migrations(hub) == [])

    # --- runner con migrazioni FITTIZIE: ordine + fail-safe ---
    with tempfile.TemporaryDirectory() as tmp:
        hub = Path(tmp) / "hub2"
        (hub / "config").mkdir(parents=True)
        order = []
        # inietta migrazioni finte nel discovery
        import types
        m2 = types.ModuleType("m2"); m2.up = lambda h, o=order: o.append("0002")
        m3bad = types.ModuleType("m3");
        def _boom(h):
            order.append("0003"); raise RuntimeError("boom")
        m3bad.up = _boom
        m4 = types.ModuleType("m4"); m4.up = lambda h, o=order: o.append("0004")
        orig = updater._discover_migrations
        updater._discover_migrations = lambda: [("0002_a", m2), ("0003_bad", m3bad), ("0004_c", m4)]
        try:
            print("\n[runner: ordine + fail-safe]")
            res = updater.run_migrations(hub)
            check("si ferma alla migrazione che fallisce", res["ok"] is False and res["failed"] == "0003_bad")
            check("0002 applicata prima del fallimento", "0002_a" in res["applied"])
            check("0004 NON applicata (dopo il fallimento)", "0004_c" not in res["applied"])
            check("ordine rispettato", order == ["0002", "0003"])
            applied_ids = {a["id"] for a in updater.applied_migrations(hub)}
            check("0003_bad NON marcata applicata (fail-safe)", "0003_bad" not in applied_ids)
            check("0002_a marcata applicata", "0002_a" in applied_ids)
        finally:
            updater._discover_migrations = orig

    # --- apply end-to-end: backup pre-update + migrazioni + bump ---
    with tempfile.TemporaryDirectory() as tmp:
        hub = Path(tmp) / "hub3"
        (hub / "config").mkdir(parents=True)
        (hub / "config.json").write_text(json.dumps({"mode": "personal"}))
        (hub / "SOUL.md").write_text("soul")
        print("\n[apply end-to-end]")
        res = updater.apply(hub, backup=True)
        check("apply ok", res["ok"])
        check("backup pre-update creato", res["backup"]["ok"] and "pre-update" in res["backup"]["archive"])
        check("migrazioni applicate", "0001_baseline" in res["migrations"]["applied"])
        check("code_version bumpata a current", updater.hub_version(hub) == updater.current_version())
        check("archivio backup su disco", (hub / "backups").is_dir() and any((hub/"backups").glob("*.tar.gz")))

    print(f"\n{'='*40}\nPASS={PASS} FAIL={FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
