"""checkpoint.py — F-Proactive-Safety — Git-shadow checkpoints dell'hub.

Rete di sicurezza per la proattività: snapshot del filesystem dell'hub PRIMA di
azioni autonome (routine, turni heartbeat), con rollback. Agnostico al canale di
modifica (REST, MCP anja_memory, SDK Write/Edit) perché git guarda il disco.

Repo git SEPARATO: `<hub>/.anja-checkpoints.git` come git-dir, work-tree = `<hub>`.
Non interferisce con un eventuale `.git` di sviluppo dell'hub (dir diverse).

ESCLUDE sempre `.secrets.env`, i DB SQLite, log e raw/ (binari pesanti).

Stdlib only (subprocess + git).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

# ref ammessi: sha, HEAD, HEAD~1, branch/tag — charset git-safe, MAI un flag (leading '-')
_REF_RE = re.compile(r"^[A-Za-z0-9_./~^@{}-]{1,200}$")

# Cosa NON versionare nello shadow (secrets, DB, log, binari pesanti, lo shadow stesso)
EXCLUDE = [
    ".anja-checkpoints.git/",
    ".secrets.env",
    "*.db", "*.db-wal", "*.db-shm", "*.sqlite", "*.sqlite3",
    "data/",
    "routines/runs/",
    "*.log",
    "raw/",
    "__pycache__/",
]


def _gitdir(hub: Path) -> Path:
    return hub / ".anja-checkpoints.git"


def _run(hub: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["git", "--git-dir", str(_gitdir(hub)), "--work-tree", str(hub), *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def ensure(hub: Path) -> bool:
    """Inizializza lo shadow repo se assente. Ritorna True se l'ha creato ora."""
    gd = _gitdir(hub)
    if gd.exists():
        return False
    _run(hub, "init", "-q")
    _run(hub, "config", "user.email", "anja@local", check=False)
    _run(hub, "config", "user.name", "Anja Checkpoints", check=False)
    _run(hub, "config", "commit.gpgsign", "false", check=False)
    info = gd / "info"
    info.mkdir(parents=True, exist_ok=True)
    (info / "exclude").write_text("\n".join(EXCLUDE) + "\n", encoding="utf-8")
    _run(hub, "add", "-A", check=False)
    _run(hub, "commit", "-q", "-m", "checkpoint: init", check=False)
    return True


def _has_changes(hub: Path) -> bool:
    _run(hub, "add", "-A", check=False)
    st = _run(hub, "status", "--porcelain", check=False)
    return bool(st.stdout.strip())


def _head(hub: Path) -> Optional[str]:
    return _run(hub, "rev-parse", "HEAD", check=False).stdout.strip() or None


def _resolve_ref(hub: Path, ref: str) -> str:
    """Valida `ref` (input esterno) e lo risolve in uno SHA canonico. Anti
    argument-injection: rifiuta i flag e i caratteri non-git, poi usa
    `rev-parse --verify --end-of-options` per fermare il parsing delle opzioni.
    Solleva ValueError se non valido o non risolvibile a un commit."""
    ref = (ref or "").strip()
    if ref.startswith("-") or not _REF_RE.match(ref):
        raise ValueError(f"invalid ref: {ref!r}")
    res = _run(hub, "rev-parse", "--verify", "--end-of-options",
               f"{ref}^{{commit}}", check=False)
    sha = res.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        raise ValueError(f"ref does not resolve to a commit: {ref!r}")
    return sha


def checkpoint(hub: Path, label: str) -> Optional[str]:
    """Crea un checkpoint (commit) se c'è qualcosa di cambiato, altrimenti ritorna
    l'HEAD corrente (lo stato è già un checkpoint valido). Ritorna sempre uno sha."""
    ensure(hub)
    if not _has_changes(hub):
        return _head(hub)
    _run(hub, "add", "-A", check=False)
    _run(hub, "commit", "-q", "-m", label, check=False)
    return _head(hub)


def list_checkpoints(hub: Path, n: int = 30) -> list:
    """Ultimi N checkpoint (sha, ts, label)."""
    ensure(hub)
    out = _run(hub, "log", f"-{int(n)}", "--pretty=%H|%cI|%s", check=False).stdout
    items = []
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            items.append({"sha": parts[0], "short": parts[0][:8], "ts": parts[1], "label": parts[2]})
    return items


def diff_stat(hub: Path, ref: str = "HEAD") -> str:
    """Diff --stat tra ref e lo stato attuale (cosa è cambiato dall'ultimo checkpoint)."""
    ensure(hub)
    sha = _resolve_ref(hub, ref)
    _run(hub, "add", "-A", check=False)
    return _run(hub, "diff", "--stat", sha, check=False).stdout


def restore(hub: Path, ref: str) -> dict:
    """Ripristina i file tracciati allo stato `ref`. Crea un checkpoint pre-restore
    (reversibile). Non sposta HEAD; i file non tracciati restano."""
    ensure(hub)
    sha = _resolve_ref(hub, ref)  # valida + risolve in SHA canonico (anti argument-injection)
    pre = checkpoint(hub, f"pre-restore: snapshot prima di tornare a {sha[:8]}")
    res = _run(hub, "checkout", sha, "--", ".", check=False)
    ok = res.returncode == 0
    return {"restored_to": sha, "pre_restore_checkpoint": pre, "ok": ok,
            "error": (res.stderr.strip() or None) if not ok else None}


def _safe_paths(paths: list[str]) -> list[str]:
    """Valida path relativi all'hub per un checkout mirato: no flag, no traversal."""
    safe = []
    for p in paths:
        p = (p or "").strip().lstrip("/")
        if not p or p.startswith("-") or ".." in p.split("/"):
            raise ValueError(f"invalid path: {p!r}")
        safe.append(p)
    if not safe:
        raise ValueError("no paths")
    return safe


def diff_paths(hub: Path, ref: str, paths: list[str]) -> str:
    """Diff --stat tra `ref` e lo stato attuale, LIMITATO a `paths` (preview undo mirato)."""
    ensure(hub)
    sha = _resolve_ref(hub, ref)
    safe = _safe_paths(paths)
    _run(hub, "add", "-A", check=False)
    return _run(hub, "diff", "--stat", sha, "--", *safe, check=False).stdout


def restore_paths(hub: Path, ref: str, paths: list[str]) -> dict:
    """Ripristina SOLO `paths` allo stato `ref` — undo CHIRURGICO (non tocca il resto
    dell'hub, a differenza di restore()). Crea un checkpoint pre-undo (reversibile).
    `paths` sono relativi all'hub (es. ['users'] per la memoria markdown)."""
    ensure(hub)
    sha = _resolve_ref(hub, ref)          # valida + risolve (anti argument-injection)
    safe = _safe_paths(paths)
    pre = checkpoint(hub, f"pre-undo: {', '.join(safe)} a {sha[:8]}")
    # --no-overlay: rende i path ESATTAMENTE uguali a `sha`, rimuovendo anche i file
    # aggiunti dopo il checkpoint (il checkpoint pre-undo sopra li ha resi tracciati).
    res = _run(hub, "restore", "--source", sha, "--no-overlay", "--worktree", "--", *safe, check=False)
    ok = res.returncode == 0
    return {"restored_to": sha, "paths": safe, "pre_undo_checkpoint": pre,
            "ok": ok, "error": (res.stderr.strip() or None) if not ok else None}


def restore_hard(hub: Path, ref: str) -> dict:
    """Rollback COMPLETO allo stato `ref`: `reset --hard` + `clean -fd`. A differenza di
    restore() RIMUOVE anche i file creati dopo il checkpoint (es. quelli di un coding-run
    rifiutato). I file ignorati (EXCLUDE: secrets/db) NON vengono toccati da `clean -fd`.
    Crea un checkpoint pre-rollback (reversibile). Sposta HEAD dello shadow a `ref`."""
    ensure(hub)
    sha = _resolve_ref(hub, ref)  # valida + risolve (anti argument-injection)
    pre = checkpoint(hub, f"pre-restore-hard: snapshot prima di tornare a {sha[:8]}")
    r1 = _run(hub, "reset", "--hard", sha, check=False)
    r2 = _run(hub, "clean", "-fd", check=False)
    ok = r1.returncode == 0 and r2.returncode == 0
    err = None if ok else ((r1.stderr or r2.stderr or "").strip() or "reset/clean failed")
    return {"restored_to": sha, "pre_restore_checkpoint": pre, "ok": ok, "error": err}
