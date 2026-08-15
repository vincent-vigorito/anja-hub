#!/usr/bin/env python3
"""Test di validate_blueprint (F-BlueprintForge Step A).

Run: python3 anja-hub/tests/blueprint_validate_test.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))
import blueprint_scaffold  # noqa: E402

PASS, FAIL = 0, 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label} {detail}")


def make_valid(bp_dir: Path) -> None:
    bp_dir.mkdir(parents=True)
    (bp_dir / "blueprint.json").write_text(json.dumps({
        "name": bp_dir.name, "version": "0.1.0", "description": "test",
        "workspace_type": "custom", "backends": ["wp"], "default_backend": "wp",
        "tool_groups_by_backend": {"wp": "cms"},
        "pod": ["lead", "worker"], "lead_role": "lead",
    }))
    agents = bp_dir / "agents"
    agents.mkdir()
    for role, extra in (("lead", {"workspace_lead": True}), ("worker", {})):
        cfg = {"name": "{LEAD}" if role == "lead" else role,
               "role": f"ruolo {role}", "allowed_tools": ["Bash"], **extra}
        (agents / f"{role}.json").write_text(json.dumps(cfg))
    (bp_dir / "vault.schema.env").write_text("EXAMPLE_KEY=\n")
    routines = bp_dir / "routines"
    routines.mkdir()
    (routines / "daily-{WS}.yaml").write_text("name: daily-{WS}\nschedule: '0 9 * * *'\n")
    content = bp_dir / "content"
    content.mkdir()
    for f in ("ESPERTO.md", "BRAND.md", "PIANO.md"):
        (content / f).write_text(f"# {f} di {{BRAND}}\n")


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        hub = Path(td)
        bps = hub / "blueprints"

        print("valid blueprint:")
        make_valid(bps / "legal-office")
        r = blueprint_scaffold.validate_blueprint("legal-office", hub)
        check("ok=True", r["ok"], str(r["errors"]))
        check("origin=hub", r["origin"] == "hub", r["origin"])
        check("zero errors", not r["errors"], str(r["errors"]))

        print("missing agent template:")
        make_valid(bps / "bp-noagent")
        (bps / "bp-noagent" / "agents" / "worker.json").unlink()
        r = blueprint_scaffold.validate_blueprint("bp-noagent", hub)
        check("ok=False", not r["ok"])
        check("errore worker.json", any("worker.json" in e for e in r["errors"]), str(r["errors"]))

        print("missing vault schema:")
        make_valid(bps / "bp-novault")
        (bps / "bp-novault" / "vault.schema.env").unlink()
        r = blueprint_scaffold.validate_blueprint("bp-novault", hub)
        check("ok=False", not r["ok"])
        check("errore vault", any("vault.schema.env" in e for e in r["errors"]), str(r["errors"]))

        print("bad default_backend + lead fuori pod:")
        make_valid(bps / "bp-badmeta")
        m = json.loads((bps / "bp-badmeta" / "blueprint.json").read_text())
        m["default_backend"] = "shopify"
        m["lead_role"] = "boss"
        (bps / "bp-badmeta" / "blueprint.json").write_text(json.dumps(m))
        r = blueprint_scaffold.validate_blueprint("bp-badmeta", hub)
        check("ok=False", not r["ok"])
        check("errore default_backend", any("default_backend" in e for e in r["errors"]), str(r["errors"]))
        check("errore lead_role", any("lead_role" in e for e in r["errors"]), str(r["errors"]))

        print("broken routine yaml:")
        make_valid(bps / "bp-badyaml")
        (bps / "bp-badyaml" / "routines" / "broken.yaml").write_text("a: [b\n")
        r = blueprint_scaffold.validate_blueprint("bp-badyaml", hub)
        check("ok=False", not r["ok"])

        print("blueprint inesistente:")
        r = blueprint_scaffold.validate_blueprint("ghost", hub)
        check("ok=False + errore", not r["ok"] and r["errors"])

        print("builtin (marketing-site) resta valido:")
        r = blueprint_scaffold.validate_blueprint("marketing-site", hub)
        check("ok=True", r["ok"], str(r["errors"]))
        check("origin=builtin", r["origin"] == "builtin", str(r["origin"]))

    print("=" * 44)
    if FAIL:
        print(f"FAIL: {FAIL} (pass {PASS})")
        sys.exit(1)
    print(f"ALL PASS ({PASS})")


if __name__ == "__main__":
    main()
