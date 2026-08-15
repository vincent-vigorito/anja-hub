"""script_runtime.py — D2 — Always-on monitor scripts manager.

Scansiona <hub>/scripts/<goal-slug>/*.py e li launchja come subprocess.
Ogni script riceve via env:
  GOAL_SIGNAL_FILE: path al signals.jsonl dove emettere eventi
  GOAL_ID, GOAL_SCOPE, HUB_PATH
  credenziali MCP server da .mcp.json (passate through)

Stato in <hub>/scripts/.runtime_state.json — {script_path: {pid, started_at, restarts}}.
Restart on crash con backoff (max 3 restart in 5 min, poi disabled).

Stdlib only.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional


SCRIPTS_ROOT_NAME = "scripts"
STATE_FILE = ".runtime_state.json"
MAX_RESTARTS_PER_WINDOW = 3
RESTART_WINDOW_SEC = 300


def scripts_root(hub_path: Path) -> Path:
    return hub_path / SCRIPTS_ROOT_NAME


def goal_scripts_dir(hub_path: Path, scope: str, goal_id: str) -> Path:
    """scripts/<scope>/<goal-slug>/ (scope='hub' o 'workspace:<name>')."""
    if scope == "hub":
        return scripts_root(hub_path) / "hub" / goal_id
    if scope.startswith("workspace:"):
        ws = scope.split(":", 1)[1]
        return scripts_root(hub_path) / "workspaces" / ws / goal_id
    return scripts_root(hub_path) / "misc" / goal_id


def signal_file_path(hub_path: Path, scope: str, goal_id: str) -> Path:
    """signals.jsonl vive nella goal dir, scritto dagli script via env GOAL_SIGNAL_FILE."""
    # Import dinamico per evitare circular
    import goal_io
    return goal_io.goal_dir(hub_path, scope, goal_id) / "signals.jsonl"


def _state_path(hub_path: Path) -> Path:
    return scripts_root(hub_path) / STATE_FILE


def _load_state(hub_path: Path) -> dict:
    p = _state_path(hub_path)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(hub_path: Path, state: dict) -> None:
    p = _state_path(hub_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


# Env passato agli script monitor: allowlist esplicita, NON os.environ.copy() — che
# esfiltrerebbe tutte le chiavi API del server (OpenAI/xAI/…) a script .py generici.
# Le credenziali di dominio arrivano dalle env MCP di .mcp.json (sotto), canale dedicato.
_SCRIPT_ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TERM",
                         "USER", "SHELL", "TMPDIR", "TZ")


def _build_script_env(hub_path: Path, scope: str, goal_id: str, script_path: Path) -> dict:
    """Env vars per il subprocess. Least-privilege: allowlist + creds claude + goal-specific
    + env MCP di pertinenza (non l'intero os.environ del server). F-Sec-ScriptEnvAllowlist."""
    env = {k: os.environ[k] for k in _SCRIPT_ENV_ALLOWLIST if k in os.environ}
    for k, v in os.environ.items():
        if k.startswith("ANTHROPIC_") or k.startswith("CLAUDE_"):
            env[k] = v
    env["GOAL_ID"] = goal_id
    env["GOAL_SCOPE"] = scope
    env["GOAL_SIGNAL_FILE"] = str(signal_file_path(hub_path, scope, goal_id))
    env["HUB_PATH"] = str(hub_path)
    env["SCRIPT_LOG_FILE"] = str(script_path.with_suffix(".log"))
    # Inietta env var di tutti gli MCP server configurati in .mcp.json: gli
    # script monitor del workspace ereditano le credenziali del loro dominio
    # (es. API key del provider, webhook secret, ecc.) senza hardcode server-specific.
    mcp_file = hub_path / ".mcp.json"
    if mcp_file.is_file():
        try:
            cfg = json.loads(mcp_file.read_text(encoding="utf-8"))
            for srv_cfg in (cfg.get("mcpServers") or {}).values():
                for k, v in (srv_cfg.get("env") or {}).items():
                    env[k] = str(v)
        except Exception:
            pass
    return env


def list_goal_scripts(hub_path: Path, scope: str, goal_id: str) -> list:
    """Lista script .py nella goal scripts dir."""
    d = goal_scripts_dir(hub_path, scope, goal_id)
    if not d.is_dir():
        return []
    return sorted(d.glob("*.py"))


def list_active_scripts(hub_path: Path) -> list:
    """Restituisce tutti gli script tracciati nello state + status live (pid alive?)."""
    state = _load_state(hub_path)
    out = []
    for path_str, info in state.items():
        pid = info.get("pid", 0)
        alive = _is_pid_alive(pid)
        out.append({
            "path": path_str,
            "filename": Path(path_str).name,
            "goal_id": info.get("goal_id", ""),
            "scope": info.get("scope", ""),
            "pid": pid,
            "alive": alive,
            "started_at": info.get("started_at", ""),
            "restarts": info.get("restarts", 0),
            "disabled": info.get("disabled", False),
            "last_error": info.get("last_error", ""),
        })
    return out


def list_scripts_for_goal(hub_path: Path, scope: str, goal_id: str) -> list:
    """Combina filesystem (script files) + state (running)."""
    files = list_goal_scripts(hub_path, scope, goal_id)
    state = _load_state(hub_path)
    out = []
    for f in files:
        key = str(f)
        info = state.get(key) or {}
        pid = info.get("pid", 0)
        out.append({
            "path": key,
            "filename": f.name,
            "size_bytes": f.stat().st_size,
            "modified": f.stat().st_mtime,
            "pid": pid,
            "alive": _is_pid_alive(pid),
            "started_at": info.get("started_at", ""),
            "restarts": info.get("restarts", 0),
            "disabled": info.get("disabled", False),
            "last_error": info.get("last_error", ""),
            "log_file": str(f.with_suffix(".log")),
        })
    return out


def start_script(hub_path: Path, scope: str, goal_id: str, script_path: Path) -> dict:
    """Lancia uno script in background. Restituisce {ok, pid?, error?}."""
    if not script_path.is_file() or script_path.suffix != ".py":
        return {"ok": False, "error": f"not a .py file: {script_path}"}
    state = _load_state(hub_path)
    key = str(script_path)
    info = state.get(key) or {}

    # Restart window check
    now = time.time()
    restart_history = info.get("restart_history") or []
    restart_history = [t for t in restart_history if now - t < RESTART_WINDOW_SEC]
    if info.get("disabled"):
        return {"ok": False, "error": "script disabled by max restart limit"}
    if len(restart_history) >= MAX_RESTARTS_PER_WINDOW:
        info["disabled"] = True
        info["last_error"] = f"max {MAX_RESTARTS_PER_WINDOW} restarts in {RESTART_WINDOW_SEC}s — DISABLED"
        state[key] = info
        _save_state(hub_path, state)
        try:
            import notification_bus as _nb
            _nb.publish(
                hub_path, source="script", category="error",
                title=f"Script disabled: {script_path.name}",
                body=info["last_error"],
                action={"label": "Reset", "url": f"/#scripts/{scope}/{goal_id}", "type": "navigate"},
                payload={"script": str(script_path), "scope": scope, "goal_id": goal_id},
                scope=scope if scope.startswith("workspace:") else "hub",
            )
        except Exception:
            pass
        return {"ok": False, "error": info["last_error"]}

    # If already alive, no-op
    if _is_pid_alive(info.get("pid", 0)):
        return {"ok": True, "pid": info["pid"], "already_running": True}

    env = _build_script_env(hub_path, scope, goal_id, script_path)
    log_path = script_path.with_suffix(".log")
    try:
        log_f = open(log_path, "ab")
        proc = subprocess.Popen(
            [sys.executable, str(script_path)],
            env=env,
            stdout=log_f, stderr=subprocess.STDOUT,
            cwd=str(script_path.parent),
            start_new_session=True,
        )
        restart_history.append(now)
        info.update({
            "pid": proc.pid,
            "goal_id": goal_id,
            "scope": scope,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "restart_history": restart_history,
            "restarts": info.get("restarts", 0) + (1 if info.get("started_at") else 0),
            "disabled": False,
            "last_error": "",
        })
        state[key] = info
        _save_state(hub_path, state)
        return {"ok": True, "pid": proc.pid}
    except Exception as e:
        info["last_error"] = f"{type(e).__name__}: {e}"
        state[key] = info
        _save_state(hub_path, state)
        return {"ok": False, "error": info["last_error"]}


def stop_script(hub_path: Path, script_path: Path) -> dict:
    state = _load_state(hub_path)
    key = str(script_path)
    info = state.get(key) or {}
    pid = info.get("pid", 0)
    if not _is_pid_alive(pid):
        info["pid"] = 0
        state[key] = info
        _save_state(hub_path, state)
        return {"ok": True, "note": "not running"}
    try:
        os.kill(pid, signal.SIGTERM)
        # Wait up to 3s for clean exit
        for _ in range(15):
            time.sleep(0.2)
            if not _is_pid_alive(pid):
                break
        if _is_pid_alive(pid):
            os.kill(pid, signal.SIGKILL)
        info["pid"] = 0
        state[key] = info
        _save_state(hub_path, state)
        return {"ok": True, "killed": pid}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def reset_script(hub_path: Path, script_path: Path) -> dict:
    """Sblocca uno script disabled per troppi restart."""
    state = _load_state(hub_path)
    key = str(script_path)
    info = state.get(key) or {}
    info["disabled"] = False
    info["restart_history"] = []
    info["last_error"] = ""
    state[key] = info
    _save_state(hub_path, state)
    return {"ok": True}


def read_script_log(hub_path: Path, script_path: Path, tail_lines: int = 100) -> str:
    log_path = script_path.with_suffix(".log")
    if not log_path.is_file():
        return ""
    try:
        with open(log_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block = 4096
            chunks = []
            pos = size
            while pos > 0 and len(b"\n".join(chunks).splitlines()) < tail_lines + 5:
                pos = max(0, pos - block)
                f.seek(pos)
                chunks.insert(0, f.read(block))
                if pos == 0:
                    break
            text = b"".join(chunks).decode("utf-8", errors="replace")
            return "\n".join(text.splitlines()[-tail_lines:])
    except Exception:
        return ""


# ============================================================
# Supervisor loop — run periodically per ensure scripts alive
# ============================================================

async def supervisor_tick(hub_path: Path) -> dict:
    """One tick: scan tutti script tracciati, restart se morto."""
    state = _load_state(hub_path)
    restarted = []
    for path_str, info in list(state.items()):
        if info.get("disabled"):
            continue
        pid = info.get("pid", 0)
        if pid and _is_pid_alive(pid):
            continue
        if not info.get("goal_id"):
            continue
        # Restart
        sp = Path(path_str)
        if not sp.is_file():
            # Script eliminato dal filesystem — rimuovi dal state
            del state[path_str]
            _save_state(hub_path, state)
            continue
        res = start_script(hub_path, info["scope"], info["goal_id"], sp)
        if res.get("ok") and not res.get("already_running"):
            restarted.append(path_str)
            try:
                import notification_bus as _nb
                _nb.publish(
                    hub_path, source="script", category="warn",
                    title=f"Script restarted: {sp.name}",
                    body=f"Crashed and auto-restarted (pid={res.get('pid')})",
                    payload={"script": path_str, "scope": info.get("scope"), "goal_id": info.get("goal_id")},
                    scope=info["scope"] if info.get("scope", "").startswith("workspace:") else "hub",
                )
            except Exception:
                pass
    return {"restarted": restarted, "tracked": len(state)}


async def supervisor_loop(hub_path: Path, tick_sec: int = 30):
    print(f"[script_runtime] supervisor loop start (tick={tick_sec}s, hub={hub_path})", flush=True)
    try:
        while True:
            try:
                res = await supervisor_tick(hub_path)
                if res.get("restarted"):
                    print(f"[script_runtime] restarted: {res['restarted']}", flush=True)
            except Exception as e:
                print(f"[script_runtime] tick error: {e}", flush=True)
            await asyncio.sleep(tick_sec)
    except asyncio.CancelledError:
        print("[script_runtime] supervisor loop cancelled", flush=True)
