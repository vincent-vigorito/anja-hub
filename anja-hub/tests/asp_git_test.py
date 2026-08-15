#!/usr/bin/env python3
"""asp_git_test.py — Fase 4 ASP: unit test git-sessione su repo temporaneo.
Worktree isolato, finalize/summary, merge nel base, discard, conflitti."""

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))

import asp_git

FAILED = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name} {detail if not cond else ''}")
    if not cond:
        FAILED.append(name)


def git(args, cwd):
    return subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True,
                          text=True, check=True).stdout.strip()


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    hub = tmp / "hub"
    hub.mkdir()
    asp_git.configure(hub)

    # repo base con un commit su main
    repo = tmp / "repo"
    repo.mkdir()
    git(["init", "-b", "main"], repo)
    git(["-c", "user.name=T", "-c", "user.email=t@t", "commit",
         "--allow-empty", "-m", "init"], repo)
    (repo / "app.py").write_text("print('v1')\n")
    git(["add", "-A"], repo)
    git(["-c", "user.name=T", "-c", "user.email=t@t", "commit", "-m", "v1"], repo)

    check("repo git riconosciuto", asp_git.is_git_repo(repo))
    check("non-repo → None", asp_git.prepare(hub, "x") is None)

    # prepare: worktree + branch, utente resta su main
    ctx = asp_git.prepare(repo, "conv-a")
    check("prepare crea ctx", ctx is not None and ctx["branch"] == "anja/conv-a")
    check("worktree esiste", Path(ctx["worktree"]).is_dir())
    check("utente resta su main",
          git(["rev-parse", "--abbrev-ref", "HEAD"], repo) == "main")
    check("prepare idempotente",
          asp_git.prepare(repo, "conv-a")["worktree"] == ctx["worktree"])
    check("ctx ricostruibile", asp_git.get_ctx("conv-a")["branch"] == "anja/conv-a")

    # turno: modifica nel worktree → finalize
    wt = Path(ctx["worktree"])
    (wt / "app.py").write_text("print('v2')\n")
    (wt / "nuovo.txt").write_text("ciao\n")
    summary = asp_git.finalize_turn(ctx)
    check("finalize committa e riassume",
          summary and summary["commits"] == 1
          and {f["path"] for f in summary["files"]} == {"app.py", "nuovo.txt"},
          str(summary))
    check("base intatto prima del merge",
          (repo / "app.py").read_text() == "print('v1')\n"
          and not (repo / "nuovo.txt").exists())
    patch = asp_git.full_patch(ctx)
    check("patch contiene i cambi", "v2" in patch and "nuovo.txt" in patch)

    # merge esplicito nel base
    res = asp_git.merge(ctx)
    check("merge ok", res.get("ok"), str(res))
    check("base aggiornato", (repo / "app.py").read_text() == "print('v2')\n"
          and (repo / "nuovo.txt").exists())
    check("cleanup: branch e meta rimossi",
          asp_git.get_ctx("conv-a") is None
          and subprocess.run(["git", "rev-parse", "--verify", "anja/conv-a"],
                             cwd=repo, capture_output=True).returncode != 0)

    # discard
    ctx2 = asp_git.prepare(repo, "conv-b")
    (Path(ctx2["worktree"]) / "scarto.txt").write_text("x\n")
    asp_git.finalize_turn(ctx2)
    res = asp_git.discard(ctx2)
    check("discard ok", res.get("ok") and not (repo / "scarto.txt").exists()
          and asp_git.get_ctx("conv-b") is None)

    # conflitto: merge fallisce pulito, branch conservato
    ctx3 = asp_git.prepare(repo, "conv-c")
    (Path(ctx3["worktree"]) / "app.py").write_text("print('sessione')\n")
    asp_git.finalize_turn(ctx3)
    (repo / "app.py").write_text("print('base-divergente')\n")
    git(["add", "-A"], repo)
    git(["-c", "user.name=T", "-c", "user.email=t@t", "commit", "-m", "div"], repo)
    res = asp_git.merge(ctx3)
    check("conflitto → errore pulito", not res.get("ok") and "conserva" in res.get("error", ""),
          str(res))
    check("branch conservato dopo conflitto",
          asp_git.get_ctx("conv-c") is not None)
    check("base non sporco dopo abort",
          git(["status", "--porcelain"], repo) == "")

print("=" * 44)
if FAILED:
    print(f"FAILED: {FAILED}")
    sys.exit(1)
print("ALL PASS")
