#!/usr/bin/env python3
"""E2E Fase 4 ASP — git-sessione sulla webapp vera: turno su progetto git →
worktree isolato → diff.ready → review via endpoint → merge nel base."""
import asyncio
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8765"
WS = "ws://127.0.0.1:8765/api/chat"
CONV = f"asp-git-{int(time.time())}"
REPO = Path(os.environ.get("ANJA_HUB", "")) / "asplab-repo"
R: dict[str, str] = {}


def post(path, payload):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read())


def git(args):
    return subprocess.run(["git"] + args, cwd=str(REPO), capture_output=True,
                          text=True).stdout.strip()


async def main():
    import websockets
    async with websockets.connect(WS, max_size=8 * 1024 * 1024) as ws:
        assert json.loads(await ws.recv())["type"] == "active_streams_snapshot"
        print(f"conv={CONV}")
        await ws.send(json.dumps({
            "message": "Crea con il tool Write il file hello.py con contenuto "
                       "print('ciao asp') e aggiorna README.md aggiungendo la "
                       "riga 'modificato dalla sessione'. Solo questo.",
            "conversation_id": CONV, "scope": "project:asplab",
            "model": "haiku", "provider": "claude", "asp_mode": "auto",
        }))
        events, t0 = [], time.time()
        while True:
            ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=300))
            events.append(ev)
            et = ev.get("type")
            if et in ("tool_use", "diff.ready", "text", "done", "error"):
                extra = ""
                if et == "diff.ready":
                    extra = f" files={[f['path'] for f in ev.get('files', [])]} +{ev.get('additions')}/-{ev.get('deletions')}"
                print(f"  [{et} +{time.time()-t0:4.1f}s]{extra}")
            if et in ("done", "error"):
                break

    diff_evs = [e for e in events if e.get("type") == "diff.ready"]
    R["diff_ready_emesso"] = ("PASS" if diff_evs
                              and {f["path"] for f in diff_evs[0]["files"]}
                              >= {"hello.py"} else f"FAIL {diff_evs}")
    done_idx = next(i for i, e in enumerate(events) if e.get("type") == "done")
    R["diff_prima_del_done"] = ("PASS" if diff_evs
                                and events.index(diff_evs[0]) < done_idx else "FAIL")

    # il base repo è INTATTO (lavoro nel worktree)
    R["base_intatto"] = ("PASS" if not (REPO / "hello.py").exists()
                         and git(["rev-parse", "--abbrev-ref", "HEAD"]) == "main"
                         else "FAIL")

    # review: patch via endpoint
    d = get(f"/api/session/diff?conv_id={CONV}")
    R["patch_review"] = ("PASS" if "ciao asp" in d.get("patch", "")
                         and "hello.py" in d.get("patch", "") else "FAIL")

    # merge esplicito → il base riceve i cambi, branch pulito
    res = post("/api/session/merge", {"conv_id": CONV, "decision": "merge"})
    R["merge_ok"] = "PASS" if res.get("ok") else f"FAIL {res}"
    R["base_aggiornato"] = ("PASS" if (REPO / "hello.py").is_file()
                            and "modificato dalla sessione" in (REPO / "README.md").read_text()
                            else "FAIL")
    branch_gone = subprocess.run(
        ["git", "rev-parse", "--verify", f"anja/{CONV}"], cwd=str(REPO),
        capture_output=True).returncode != 0
    R["cleanup_branch"] = "PASS" if branch_gone else "FAIL"

    print("\n" + "=" * 46)
    failed = 0
    for k, v in R.items():
        ok = v.startswith("PASS")
        failed += 0 if ok else 1
        print(f"  {'✅' if ok else '❌'} {k}: {v}")
    print("=" * 46)
    sys.exit(1 if failed else 0)


asyncio.run(main())
