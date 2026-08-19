"""grok_oauth.py — Grok Build (SuperGrok) subscription via the official `grok` CLI.

Symmetric to `claude_oauth.py`, NOT to `openai_oauth.py`: anja never touches the
token. The CLI owns auth (`~/.grok/auth.json`, OAuth at auth.x.ai, refreshed by the
CLI itself) and the CLI *is* the chat backend (`grok_cli.py` spawns `grok -p`).
Here: detection for the UI + device-code login driven from Settings.

Not the xAI API key (`provider=xai` via LiteLLM stays as it is).

Stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import select
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

GROK_HOME = Path(os.environ.get("GROK_HOME") or "~/.grok").expanduser()
AUTH_PATH = GROK_HOME / "auth.json"
MODELS_CACHE = GROK_HOME / "models_cache.json"

# Models served to the seat (grok 1.0.5, `grok models`). `grok-build` is a legacy alias.
FALLBACK_MODELS = ["grok-4.6", "grok-4.5"]
FALLBACK_EFFORTS = ["low", "medium", "high"]

_URL_RE = re.compile(r"(https://[^\s\x1b]+user_code=[A-Z0-9-]+)")
_CODE_RE = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b")


def grok_binary() -> Optional[str]:
    """`grok` on PATH, else the managed install `~/.grok/bin/grok` (the service
    user on the live host has no login shell PATH)."""
    found = shutil.which("grok")
    if found:
        return found
    cand = GROK_HOME / "bin" / "grok"
    if cand.is_file() and os.access(cand, os.X_OK):
        return str(cand)
    return None


def has_grok_cli() -> bool:
    return grok_binary() is not None


def grok_cli_version() -> str:
    b = grok_binary()
    if not b:
        return ""
    try:
        r = subprocess.run([b, "--version"], capture_output=True, text=True, timeout=10)
        return (r.stdout or "").strip().splitlines()[0] if r.stdout else ""
    except Exception:
        return ""


def _read_auth_entry() -> Optional[dict]:
    """auth.json is `{ "<issuer>::<client_id>": {key, refresh_token, expires_at, email, ...} }`.
    Returns the first entry (one seat per machine) or None. Never logs the token."""
    try:
        if not AUTH_PATH.is_file():
            return None
        data = json.loads(AUTH_PATH.read_text(encoding="utf-8") or "{}")
    except Exception:
        return None
    if not isinstance(data, dict) or not data:
        return None
    # flat legacy shape
    if "key" in data or "refresh_token" in data:
        return data
    for v in data.values():
        if isinstance(v, dict) and ("key" in v or "refresh_token" in v):
            return v
    return None


def _parse_ts(s: str) -> Optional[float]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def has_grok_session() -> bool:
    """True if the CLI has a usable seat: access token or refresh token present.
    An expired access token with a refresh_token still counts — the CLI refreshes
    on its own at the next `grok -p`."""
    ent = _read_auth_entry()
    if not ent:
        return False
    if ent.get("refresh_token"):
        return True
    if not ent.get("key"):
        return False
    exp = _parse_ts(ent.get("expires_at") or "")
    return exp is None or exp > time.time()


def grok_models() -> list[dict]:
    """Model ids + effort menu from the CLI's catalog cache (`~/.grok/models_cache.json`,
    written at login / catalog refresh). Fallback to the known shortlist."""
    try:
        data = json.loads(MODELS_CACHE.read_text(encoding="utf-8"))
        out = []
        for mid, entry in (data.get("models") or {}).items():
            info = (entry or {}).get("info") or {}
            if info.get("hidden"):
                continue
            efforts = [e.get("id") for e in (info.get("reasoning_efforts") or []) if e.get("id")]
            out.append({
                "id": info.get("id") or mid,
                "name": info.get("name") or mid,
                "efforts": efforts or list(FALLBACK_EFFORTS),
                "default_effort": info.get("reasoning_effort") or "",
                "context_window": info.get("context_window") or 0,
            })
        if out:
            return out
    except Exception:
        pass
    return [{"id": m, "name": m, "efforts": list(FALLBACK_EFFORTS), "default_effort": "", "context_window": 0}
            for m in FALLBACK_MODELS]


def grok_model_ids() -> list[str]:
    return [m["id"] for m in grok_models()]


def grok_auth_summary() -> dict:
    """Safe-for-UI status. Never the token."""
    ent = _read_auth_entry() or {}
    active = has_grok_session()
    exp = ent.get("expires_at") or ""
    uid = str(ent.get("user_id") or "")
    return {
        "cli_installed": has_grok_cli(),
        "cli_path": grok_binary() or "",
        "cli_version": grok_cli_version() if has_grok_cli() else "",
        "session_active": active,
        "email": ent.get("email", "") if active else "",
        "auth_mode": ent.get("auth_mode", "") if active else "",
        "expires_at": exp if active else "",
        "user_id_prefix": (uid[:8] + "…") if (active and uid) else "",
        "auth_path": str(AUTH_PATH),
        "models": grok_models() if active else [],
        "api_key_set": bool(os.environ.get("XAI_API_KEY")),
        "precedence": ("The CLI prefers the signed-in seat over XAI_API_KEY. "
                       "Chats on provider 'grok_cli' use the seat; provider 'xai' uses the API key."),
    }


# ============================================================
# Device-code login from the UI. `grok login --device-auth` prints a URL + a
# user code and polls until the browser confirms — the opposite direction of
# the Claude flow (nothing to paste back). Works on a plain pipe, no PTY.
# One pending login at a time.
# ============================================================

_LOGIN: dict = {}   # {"proc": Popen, "url": str, "code": str, "started": float, "out": str}


def _child_env() -> dict:
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL", "USER", "SHELL", "TMPDIR", "TZ")
           if k in os.environ}
    if os.environ.get("GROK_HOME"):
        env["GROK_HOME"] = os.environ["GROK_HOME"]
    env["TERM"] = "dumb"
    env["GROK_DISABLE_AUTOUPDATER"] = "1"
    return env


def _login_alive() -> bool:
    p = _LOGIN.get("proc")
    return bool(p and p.poll() is None)


def _drain(proc: subprocess.Popen, budget: float, stop_when=None) -> str:
    """Read whatever the child printed within `budget` seconds (non-blocking);
    returns early when `stop_when(out)` is true or the child exits."""
    out, deadline = "", time.time() + budget
    fd = proc.stdout.fileno()
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.3)
        if r:
            try:
                chunk = os.read(fd, 4096).decode("utf-8", "replace")
            except OSError:
                break
            if not chunk:
                break
            out += chunk
            if stop_when and stop_when(out):
                break
        if proc.poll() is not None:
            # flush the tail
            try:
                out += os.read(fd, 65536).decode("utf-8", "replace")
            except OSError:
                pass
            break
    return out


def login_start() -> dict:
    b = grok_binary()
    if not b:
        return {"ok": False, "error": "Grok Build CLI not installed on the host: "
                                      "curl -fsSL https://x.ai/cli/install.sh | bash"}
    login_cancel()
    try:
        proc = subprocess.Popen([b, "login", "--device-auth"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                env=_child_env(), close_fds=True)
    except Exception as e:
        return {"ok": False, "error": f"cannot start grok login: {type(e).__name__}: {e}"}
    out = _drain(proc, 20, stop_when=lambda o: bool(_URL_RE.search(o) and _CODE_RE.search(o)))
    m_url = _URL_RE.search(out)
    m_code = _CODE_RE.search(out)
    if not m_url:
        try:
            proc.kill()
        except Exception:
            pass
        tail = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", out)[-400:].strip()
        return {"ok": False, "error": "the CLI did not print a device-login URL", "output": tail}
    url = m_url.group(1)
    code = m_code.group(1) if m_code else (url.split("user_code=")[-1] if "user_code=" in url else "")
    _LOGIN.update({"proc": proc, "url": url, "code": code, "started": time.time(), "out": out})
    return {"ok": True, "auth_url": url, "user_code": code}


def login_wait(timeout: float = 25.0) -> dict:
    """Wait up to `timeout` s for the pending login to finish. Meant to be polled
    by the UI; returns done=False while the CLI is still waiting for the browser."""
    if not _login_alive():
        # a finished process: was it a success?
        proc = _LOGIN.get("proc")
        if proc is not None and proc.poll() == 0:
            login_cancel()
            return {"ok": True, "done": True, "logged_in": has_grok_session()}
        if proc is not None:
            out = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", _LOGIN.get("out", ""))[-300:].strip()
            login_cancel()
            return {"ok": False, "done": True, "logged_in": has_grok_session(),
                    "error": out or "login process exited"}
        return {"ok": False, "done": True, "logged_in": has_grok_session(),
                "error": "no login in progress"}
    proc = _LOGIN["proc"]
    _LOGIN["out"] = _LOGIN.get("out", "") + _drain(proc, timeout)
    if proc.poll() is None:
        return {"ok": True, "done": False, "auth_url": _LOGIN.get("url", ""), "user_code": _LOGIN.get("code", "")}
    rc = proc.poll()
    out = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", _LOGIN.get("out", ""))[-300:].strip()
    logged = has_grok_session()
    login_cancel()
    if rc == 0 or logged:
        return {"ok": True, "done": True, "logged_in": logged}
    return {"ok": False, "done": True, "logged_in": logged, "error": out or f"grok login exited {rc}"}


def login_pending() -> dict:
    alive = _login_alive()
    if not alive and _LOGIN and _LOGIN.get("proc") is not None and _LOGIN["proc"].poll() == 0:
        login_cancel()
    return {"pending": alive, "auth_url": _LOGIN.get("url", "") if alive else "",
            "user_code": _LOGIN.get("code", "") if alive else "", "logged_in": has_grok_session()}


def login_cancel() -> None:
    p = _LOGIN.pop("proc", None)
    _LOGIN.clear()
    if p and p.poll() is None:
        try:
            p.kill()
        except Exception:
            pass
    if p is not None:
        try:
            p.stdout.close()
        except Exception:
            pass


def logout() -> dict:
    """`grok logout` (clears the cached session); unlink auth.json as fallback."""
    login_cancel()
    b = grok_binary()
    if b:
        try:
            subprocess.run([b, "logout"], capture_output=True, text=True, timeout=20, env=_child_env())
        except Exception:
            pass
    if AUTH_PATH.is_file() and has_grok_session():
        try:
            AUTH_PATH.unlink()
        except Exception as e:
            return {"ok": False, "error": f"cannot remove {AUTH_PATH}: {e}"}
    return {"ok": True, "session_active": has_grok_session()}
