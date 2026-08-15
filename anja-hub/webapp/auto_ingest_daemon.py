"""auto_ingest_daemon.py — file-change watcher per progetti registrati (Fase 13+).

Pattern polling stdlib (no fswatch dependency): ogni 30s controlla mtime dei file
whitelisted nei progetti registrati con auto_ingest attivo. Quando rileva cambiamenti
significativi:
  1. Aggiorna `.auto_ingest_state.json` (mtimes tracking)
  2. Scrive entry in `wiki/log.md` (`[date] auto-detect | files...`)
  3. Aggiorna `.pending_ingest.json` (queue di file da ingest)
  4. Emette notification (webapp + telegram) via callback

Lo step "ingest reale" (LLM call con anja-ingest workflow) viene triggerato
dall'utente con click su button webapp o auto (mode='active') via subprocess CC CLI.

Stdlib only.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Awaitable, Optional


DEFAULT_POLL_INTERVAL_SEC = 30
DEFAULT_WHITELIST = [
    "*.md", "*.mdx",
    "README*", "CHANGELOG*", "CONTRIBUTING*", "ARCHITECTURE*",
    "docs/**", "doc/**",
    "*.txt",  # plain text docu
]
DEFAULT_EXCLUDE_DIRS = {
    ".git", ".anjawiki", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".cache", ".pytest_cache", ".mypy_cache",
    "target", ".idea", ".vscode", "site-packages", ".tox", ".nox",
}
STATE_FILENAME = ".auto_ingest_state.json"
PENDING_FILENAME = ".pending_ingest.json"
CONFIG_FILENAME = "auto_ingest.json"


def _load_project_config(project_root: Path) -> dict:
    """Read `<project>/.anjawiki/auto_ingest.json` config.

    Default (se assente):
      {enabled: false, mode: 'passive', poll_interval_sec: 30,
       whitelist: DEFAULT_WHITELIST, exclude_dirs: DEFAULT_EXCLUDE_DIRS}
    """
    f = project_root / ".anjawiki" / CONFIG_FILENAME
    if not f.is_file():
        return {
            "enabled": False,
            "mode": "passive",  # 'off' | 'passive' (notify only) | 'active' (auto ingest)
            "poll_interval_sec": DEFAULT_POLL_INTERVAL_SEC,
            "whitelist": list(DEFAULT_WHITELIST),
            "exclude_dirs": list(DEFAULT_EXCLUDE_DIRS),
            "notify_telegram": False,
            "notify_telegram_chat_id": None,
        }
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {"enabled": False, "_error": "config parse failed"}


def _save_project_config(project_root: Path, cfg: dict) -> None:
    f = project_root / ".anjawiki" / CONFIG_FILENAME
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _load_state(project_root: Path) -> dict:
    f = project_root / ".anjawiki" / STATE_FILENAME
    if not f.is_file():
        return {"mtimes": {}, "last_poll": 0}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {"mtimes": {}, "last_poll": 0}


def _save_state(project_root: Path, state: dict) -> None:
    f = project_root / ".anjawiki" / STATE_FILENAME
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_pending(project_root: Path) -> dict:
    f = project_root / ".anjawiki" / PENDING_FILENAME
    if not f.is_file():
        return {"files": [], "last_updated": 0}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {"files": [], "last_updated": 0}


def _save_pending(project_root: Path, pending: dict) -> None:
    f = project_root / ".anjawiki" / PENDING_FILENAME
    f.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")


def _matches_whitelist(rel_path: str, whitelist: list) -> bool:
    for pat in whitelist:
        if fnmatch.fnmatch(rel_path, pat):
            return True
        # Match patterns with **/ prefix
        if "**" in pat:
            base = pat.replace("/**", "")
            if rel_path.startswith(base + "/"):
                return True
    return False


def _scan_project(project_root: Path, cfg: dict, state: dict) -> list[dict]:
    """Scan filesystem looking for changed files (vs last state mtimes).

    Returns list of {path, action, mtime, size} where action in (new|modified|deleted).
    """
    whitelist = cfg.get("whitelist", DEFAULT_WHITELIST)
    exclude_dirs = set(cfg.get("exclude_dirs", DEFAULT_EXCLUDE_DIRS))
    old_mtimes = state.get("mtimes", {})
    new_mtimes: dict = {}
    changes: list[dict] = []

    for p in project_root.rglob("*"):
        # Skip excluded dirs early
        try:
            rel = p.relative_to(project_root)
        except ValueError:
            continue
        parts = rel.parts
        if any(part in exclude_dirs or part.startswith(".") for part in parts[:-1]):
            continue
        if not p.is_file():
            continue
        if p.name.startswith("."):
            continue
        rel_str = str(rel).replace("\\", "/")
        if not _matches_whitelist(rel_str, whitelist):
            continue
        try:
            mt = p.stat().st_mtime
        except Exception:
            continue
        new_mtimes[rel_str] = mt
        old_mt = old_mtimes.get(rel_str)
        if old_mt is None:
            changes.append({"path": rel_str, "action": "new", "mtime": mt, "size": p.stat().st_size})
        elif mt > old_mt + 0.5:  # safety: solo se cambio sensibile
            changes.append({"path": rel_str, "action": "modified", "mtime": mt, "size": p.stat().st_size})

    # Detect deletions
    for old_path in old_mtimes:
        if old_path not in new_mtimes:
            changes.append({"path": old_path, "action": "deleted", "mtime": 0, "size": 0})

    state["mtimes"] = new_mtimes
    state["last_poll"] = time.time()
    return changes


def _append_log(project_root: Path, changes: list[dict]) -> None:
    """Append entry in `<project>/.anjawiki/wiki/log.md`."""
    log_path = project_root / ".anjawiki" / "wiki" / "log.md"
    if not log_path.is_file():
        return
    today = datetime.now().strftime("%Y-%m-%d")
    new_count = sum(1 for c in changes if c["action"] == "new")
    mod_count = sum(1 for c in changes if c["action"] == "modified")
    del_count = sum(1 for c in changes if c["action"] == "deleted")
    summary_parts = []
    if new_count: summary_parts.append(f"{new_count} new")
    if mod_count: summary_parts.append(f"{mod_count} modified")
    if del_count: summary_parts.append(f"{del_count} deleted")
    summary = ", ".join(summary_parts)
    file_list = ", ".join(c["path"] for c in changes[:5])
    if len(changes) > 5:
        file_list += f", +{len(changes) - 5} altri"
    entry = f"\n## [{today}] auto-detect | {summary} ({file_list})\n"
    try:
        with log_path.open("a", encoding="utf-8") as fp:
            fp.write(entry)
    except Exception as e:
        print(f"[auto_ingest] log append error: {e}")


def _update_pending(project_root: Path, changes: list[dict]) -> dict:
    """Aggiunge i file changed alla pending queue. Returns updated pending dict."""
    pending = _load_pending(project_root)
    existing = {f["path"]: f for f in pending.get("files", [])}
    for c in changes:
        if c["action"] == "deleted":
            existing.pop(c["path"], None)
        else:
            existing[c["path"]] = {
                "path": c["path"],
                "action": c["action"],
                "detected_at": time.time(),
            }
    pending["files"] = list(existing.values())
    pending["last_updated"] = time.time()
    _save_pending(project_root, pending)
    return pending


# =================================================================
# Daemon
# =================================================================

class AutoIngestDaemon:
    """Singleton daemon che monitora tutti i progetti registrati con auto_ingest enabled."""

    def __init__(self, projects_provider: Callable[[], list],
                 on_changes: Optional[Callable[[str, list, dict], Awaitable[None]]] = None):
        """
        projects_provider: callable returns lista projects context (con location.path)
        on_changes: async callback(project_name, changes, config) chiamato a ogni detect
        """
        self.projects_provider = projects_provider
        self.on_changes = on_changes
        self.running = False
        self.task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self.last_poll_at: Optional[float] = None
        self.last_changes_count = 0

    def status(self) -> dict:
        return {
            "running": self.running,
            "last_poll_at": self.last_poll_at,
            "last_changes_count": self.last_changes_count,
        }

    async def start(self):
        if self.running:
            return
        self._stop_event.clear()
        self.running = True
        self.task = asyncio.create_task(self._loop(), name="auto-ingest-daemon")
        print("[auto_ingest] daemon started")

    async def stop(self):
        if not self.running:
            return
        self._stop_event.set()
        if self.task:
            try:
                await asyncio.wait_for(self.task, timeout=5)
            except asyncio.TimeoutError:
                self.task.cancel()
        self.running = False
        print("[auto_ingest] daemon stopped")

    async def _loop(self):
        while not self._stop_event.is_set():
            try:
                await self._poll_all()
            except Exception as e:
                print(f"[auto_ingest] loop error: {type(e).__name__}: {e}")
            # Sleep configurable: minimo tra i poll_interval dei progetti, default 30s
            await asyncio.sleep(DEFAULT_POLL_INTERVAL_SEC)

    async def _poll_all(self):
        self.last_poll_at = time.time()
        try:
            projects = self.projects_provider()
        except Exception as e:
            print(f"[auto_ingest] projects_provider error: {e}")
            return
        total_changes = 0
        for p in projects:
            name = p.get("name", "")
            loc = p.get("location") or {}
            if loc.get("kind") != "local":
                continue
            path_str = loc.get("path")
            if not path_str:
                continue
            project_root = Path(path_str).resolve()
            if not project_root.is_dir():
                continue
            cfg = _load_project_config(project_root)
            if not cfg.get("enabled"):
                continue
            try:
                state = _load_state(project_root)
                changes = _scan_project(project_root, cfg, state)
                if changes:
                    _save_state(project_root, state)
                    _append_log(project_root, changes)
                    _update_pending(project_root, changes)
                    total_changes += len(changes)
                    print(f"[auto_ingest] {name}: {len(changes)} change(s) detected")
                    if self.on_changes:
                        try:
                            await self.on_changes(name, changes, cfg)
                        except Exception as e:
                            print(f"[auto_ingest] on_changes callback error: {e}")
                else:
                    # Salva stato anche se nessun cambio (per primo scan)
                    _save_state(project_root, state)
            except Exception as e:
                print(f"[auto_ingest] scan {name} error: {type(e).__name__}: {e}")
        self.last_changes_count = total_changes
