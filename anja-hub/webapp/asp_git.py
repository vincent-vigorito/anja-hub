"""asp_git.py — F-AgentSessions Fase 4: git-sessione (design §7).

Il principio Cursor che adottiamo: da qualunque canale si rivede un DIFF, non
uno stato di file. Quando il cwd della sessione è un repo git e il flag
ANJA_ASP_GIT=1 è attivo:

  - la sessione lavora in un WORKTREE isolato con branch dedicato
    (`anja/<conv>`): l'utente non viene mai spostato dal suo branch, e più
    sessioni possono lavorare in parallelo sullo stesso repo;
  - a fine turno i cambi vengono committati sul branch sessione e il manager
    emette `diff.ready` (files, +/-) PRIMA del done;
  - il merge nel branch di partenza è SEMPRE un atto esplicito
    (`merge.approve` da UI/TG) — niente merge silenzioso; discard = butta
    branch e worktree.

Stateless: il contesto si ricostruisce da <hub>/asp-worktrees/<conv>.json
(robusto ai restart). Stdlib only (subprocess).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

_hub_path: Optional[Path] = None

_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")

# Autore dei commit di sessione (override per-comando, niente config globale)
_GIT_ID = ["-c", "user.name=Anja (ASP)", "-c", "user.email=asp@anja.local"]

MAX_PATCH_BYTES = int(os.environ.get("ANJA_ASP_GIT_MAX_PATCH", str(200 * 1024)))


def configure(hub_path) -> None:
    global _hub_path
    _hub_path = Path(hub_path)


def enabled() -> bool:
    return (os.environ.get("ANJA_ASP_ENABLED") == "1"
            and os.environ.get("ANJA_ASP_GIT") == "1")


def _safe(conv_id: str) -> str:
    return _SAFE_RE.sub("_", (conv_id or "").strip())[:80].strip("._") or "_anon"


def _run(args: list, cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True,
                          text=True, timeout=60, check=check)


def is_git_repo(cwd: Path) -> bool:
    try:
        r = _run(["rev-parse", "--is-inside-work-tree"], cwd, check=False)
        return r.returncode == 0 and r.stdout.strip() == "true"
    except Exception:
        return False


def _meta_path(conv_id: str) -> Path:
    return _hub_path / "asp-worktrees" / f"{_safe(conv_id)}.json"


def get_ctx(conv_id: str) -> Optional[dict]:
    try:
        return json.loads(_meta_path(conv_id).read_text(encoding="utf-8"))
    except Exception:
        return None


def prepare(base_cwd: Path, conv_id: str) -> Optional[dict]:
    """Worktree + branch per la conversazione (lazy, idempotente).
    None se il cwd non è un repo git, HEAD è detached, o qualcosa fallisce —
    in quel caso la sessione lavora sul cwd normale (nessuna git-sessione)."""
    if _hub_path is None or not is_git_repo(base_cwd):
        return None
    try:
        base_ref = _run(["rev-parse", "--abbrev-ref", "HEAD"], base_cwd).stdout.strip()
        if not base_ref or base_ref == "HEAD":
            return None   # detached: niente base su cui fare review/merge
        safe = _safe(conv_id)
        branch = f"anja/{safe}"
        wt_dir = _hub_path / "asp-worktrees" / safe / "repo"

        ctx = get_ctx(conv_id)
        if ctx and Path(ctx["worktree"]).is_dir():
            return ctx

        wt_dir.parent.mkdir(parents=True, exist_ok=True)
        branch_exists = _run(["rev-parse", "--verify", "--quiet", branch],
                             base_cwd, check=False).returncode == 0
        if wt_dir.is_dir():
            _run(["worktree", "remove", "--force", str(wt_dir)], base_cwd, check=False)
        if branch_exists:
            _run(["worktree", "add", str(wt_dir), branch], base_cwd)
        else:
            _run(["worktree", "add", str(wt_dir), "-b", branch, base_ref], base_cwd)

        ctx = {"base": str(Path(base_cwd).resolve()), "worktree": str(wt_dir),
               "branch": branch, "base_ref": base_ref, "conv_id": conv_id,
               "created": time.strftime("%Y-%m-%d %H:%M:%S")}
        _meta_path(conv_id).write_text(json.dumps(ctx, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
        print(f"[asp-git] worktree pronto conv={conv_id} branch={branch} base={base_ref}")
        return ctx
    except Exception as e:
        print(f"[asp-git] WARN prepare {conv_id}: {e}")
        return None


def finalize_turn(ctx: dict) -> Optional[dict]:
    """Commit dei cambi del turno sul branch sessione + summary diff vs base.
    None se non c'è nulla da rivedere (nessun commit oltre la base)."""
    try:
        wt = Path(ctx["worktree"])
        _run(["add", "-A"], wt)
        staged = _run(["diff", "--cached", "--quiet"], wt, check=False)
        if staged.returncode != 0:
            _run(_GIT_ID + ["commit", "-m", "asp: turno di sessione"], wt)

        rng = f"{ctx['base_ref']}...HEAD"
        commits = int(_run(["rev-list", "--count",
                            f"{ctx['base_ref']}..HEAD"], wt).stdout.strip() or "0")
        if commits == 0:
            return None
        files, add_tot, del_tot = [], 0, 0
        for line in _run(["diff", "--numstat", rng], wt).stdout.splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                a = int(parts[0]) if parts[0].isdigit() else 0
                d = int(parts[1]) if parts[1].isdigit() else 0
                files.append({"path": parts[2], "additions": a, "deletions": d})
                add_tot += a
                del_tot += d
        if not files:
            return None
        return {"branch": ctx["branch"], "base_ref": ctx["base_ref"],
                "commits": commits, "files": files[:100],
                "additions": add_tot, "deletions": del_tot}
    except Exception as e:
        print(f"[asp-git] WARN finalize {ctx.get('conv_id')}: {e}")
        return None


def full_patch(ctx: dict) -> str:
    try:
        out = _run(["diff", f"{ctx['base_ref']}...HEAD"],
                   Path(ctx["worktree"])).stdout
        if len(out.encode()) > MAX_PATCH_BYTES:
            out = out[:MAX_PATCH_BYTES] + "\n... [diff troncato]\n"
        return out
    except Exception as e:
        return f"(errore diff: {e})"


def _cleanup(ctx: dict) -> None:
    base = Path(ctx["base"])
    _run(["worktree", "remove", "--force", ctx["worktree"]], base, check=False)
    _run(["branch", "-D", ctx["branch"]], base, check=False)
    _meta_path(ctx["conv_id"]).unlink(missing_ok=True)


def merge(ctx: dict, message: str = "") -> dict:
    """Merge --no-ff del branch sessione nel base_ref, nel repo BASE.
    In caso di conflitto: abort, il branch resta per risoluzione manuale."""
    base = Path(ctx["base"])
    try:
        cur = _run(["rev-parse", "--abbrev-ref", "HEAD"], base).stdout.strip()
        if cur != ctx["base_ref"]:
            return {"ok": False,
                    "error": f"il repo base è su '{cur}', attesa '{ctx['base_ref']}': "
                             f"torna sul branch di partenza per il merge"}
        msg = message or f"anja: merge sessione {ctx['conv_id']}"
        r = _run(_GIT_ID + ["merge", "--no-ff", "-m", msg, ctx["branch"]],
                 base, check=False)
        if r.returncode != 0:
            _run(["merge", "--abort"], base, check=False)
            return {"ok": False,
                    "error": f"merge fallito (conflitti?): {r.stderr.strip()[:300]} — "
                             f"branch {ctx['branch']} conservato"}
        sha = _run(["rev-parse", "--short", "HEAD"], base).stdout.strip()
        _cleanup(ctx)
        return {"ok": True, "merged_commit": sha, "into": ctx["base_ref"]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def discard(ctx: dict) -> dict:
    try:
        _cleanup(ctx)
        return {"ok": True, "discarded": ctx["branch"]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
