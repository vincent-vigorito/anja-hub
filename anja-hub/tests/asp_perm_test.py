#!/usr/bin/env python3
"""asp_perm_test.py — Fase 2 ASP: unit test del motore permessi (no LLM).
Policy 3 livelli, learn_allow, pending requests, timeout, canonical_target."""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))

import asp_permissions as ap

FAILED = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name} {detail if not cond else ''}")
    if not cond:
        FAILED.append(name)


async def main():
    with tempfile.TemporaryDirectory() as tmp:
        ap.configure(tmp)
        store = ap.get_store()

        # canonical_target
        check("target Bash = command",
              ap.canonical_target("Bash", {"command": "git status"}) == "git status")
        check("target Write = file_path",
              ap.canonical_target("Write", {"file_path": "/a/b.txt", "content": "x"})
              == "/a/b.txt")

        # policy: nessuna regola → ask (None)
        check("no regole → ask", store.evaluate("hub", "Bash", "rm -rf /") is None)

        # learn_allow Bash = comando esatto
        store.learn_allow("hub", "Bash", "git status", by="test")
        check("learned allow match esatto",
              store.evaluate("hub", "Bash", "git status") == "allow")
        check("learned allow non generalizza",
              store.evaluate("hub", "Bash", "git push") is None)
        check("learned allow scoped",
              store.evaluate("project:x", "Bash", "git status") is None)

        # learn_allow path = prefisso directory
        store.learn_allow("hub", "Write", "/tmp/reports/a.txt", by="test")
        check("learned path copre la dir",
              store.evaluate("hub", "Write", "/tmp/reports/b.txt") == "allow")
        check("learned path non esce dalla dir",
              store.evaluate("hub", "Write", "/etc/passwd") is None)

        # deny statico vince su allow
        data = store._load()
        data["rules"].append({"scope": "*", "tool": "Bash",
                              "pattern": "git status", "action": "deny",
                              "source": "static"})
        store.path.write_text(__import__("json").dumps(data), encoding="utf-8")
        check("deny vince su allow",
              store.evaluate("hub", "Bash", "git status") == "deny")
        check("scope * matcha ovunque",
              store.evaluate("project:y", "Bash", "git status") == "deny")

        # pending: create/resolve
        rid, fut = ap.pending.create("conv-1", "hub", "Write", "/x", {})
        check("latest_for_conv", ap.pending.latest_for_conv("conv-1") == rid)
        meta = ap.pending.resolve(rid, "allow", by="test")
        check("resolve ritorna meta", meta and meta["tool"] == "Write")
        check("future risolto", (await fut)["decision"] == "allow")
        check("doppio resolve → None",
              ap.pending.resolve(rid, "deny") is None)

        # pending: timeout consumer-side
        rid2, fut2 = ap.pending.create("conv-2", "hub", "Bash", "ls", {})
        try:
            await asyncio.wait_for(fut2, timeout=0.2)
            check("timeout scatta", False)
        except asyncio.TimeoutError:
            ap.pending.drop(rid2)
            check("timeout scatta", True)
        check("drop pulisce", ap.pending.latest_for_conv("conv-2") is None)

    print("=" * 44)
    if FAILED:
        print(f"FAILED: {FAILED}")
        sys.exit(1)
    print("ALL PASS")


asyncio.run(main())
