"""claude_oauth.py — detect Anthropic Claude Pro/Max subscription auth (Fase 7v.b).

Pattern simmetrico a `openai_oauth.py` ma più snello perché `claude-agent-sdk`
gestisce internamente l'auth: legge subscription credentials da macOS Keychain
(o Linux equivalent) automaticamente.

anja non ha bisogno di leggere/refreshare token Claude — il SDK fa tutto.
Qui esponiamo solo **detection** per UI: "subscription logged in?" yes/no
+ guidance utente su precedenza ANTHROPIC_API_KEY vs subscription.

Stdlib only.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Optional


# macOS Keychain service name usato da Claude Code CLI
KEYCHAIN_SERVICE = "Claude Code-credentials"

# Linux config dir fallback
LINUX_CONFIG_DIRS = [
    Path("~/.config/claude").expanduser(),
    Path("~/.claude").expanduser(),
]


def has_claude_subscription() -> bool:
    """True se Claude CLI è loggato (subscription Pro/Max attivo).

    macOS: probe keychain via `security find-generic-password -s "Claude Code-credentials"`.
    Linux: cerca file credentials in ~/.config/claude/ o ~/.claude/.
    """
    system = platform.system()
    if system == "Darwin":
        return _keychain_has_credentials()
    else:
        return _linux_has_credentials()


def _keychain_has_credentials() -> bool:
    """macOS: usa `security` CLI per probe keychain. Exit code 0 = found.

    Non estraiamo il valore (richiederebbe password user interactive).
    """
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _linux_has_credentials() -> bool:
    """Linux: cerca file credentials/auth in dirs standard."""
    candidates = ["credentials.json", "auth.json", ".credentials.json"]
    for d in LINUX_CONFIG_DIRS:
        if not d.is_dir():
            continue
        for fname in candidates:
            if (d / fname).is_file():
                return True
    return False


def claude_auth_summary() -> dict:
    """Info pubblicamente sicure su Claude auth per UI. No token leakage.
    Fonte di verità: `claude auth status` (il file credenziali può esistere con
    token scaduto — successo sul live 2026-08-17: UI verde, bot sloggato)."""
    cli = cli_auth_status()
    sub_active = cli["logged_in"] if cli.get("cli_installed") else has_claude_subscription()
    # Detect API key presence (in env or known secrets path)
    api_key_set = bool(os.environ.get("ANTHROPIC_API_KEY"))
    system = platform.system()
    storage_hint = ""
    if system == "Darwin":
        storage_hint = f"macOS Keychain: service='{KEYCHAIN_SERVICE}'"
    else:
        storage_hint = " or ".join(str(d) for d in LINUX_CONFIG_DIRS)
    return {
        "subscription_active": sub_active,
        "cli_installed": cli.get("cli_installed", False),
        "account": cli.get("email", ""),
        "subscription": cli.get("subscription", ""),
        "api_key_set": api_key_set,
        "platform": system,
        "storage_hint": storage_hint,
        # Precedence note per UI: claude-agent-sdk usa ANTHROPIC_API_KEY se settata,
        # altrimenti cade su subscription via CLI.
        "precedence": (
            "ANTHROPIC_API_KEY (env) takes precedence over subscription. "
            "Unset API key to force subscription usage."
        ),
    }


# ============================================================
# Login della subscription DALLA UI (F-ConnectorUX): la CLI Claude Code
# stampa un URL OAuth (flow "code=true": il browser mostra un codice da
# incollare) e attende il codice su stdin. Niente callback loopback da
# instradare: funziona anche su hub remoti. Un solo login pendente alla volta.
# ============================================================

import asyncio
import re as _re
import shutil
import time as _time

_LOGIN: dict = {}          # {"proc": Popen, "master_fd": int, "url": str, "started": float}
_URL_RE = _re.compile(r"(https://claude\.com/[^\s\x1b\]]+)")


def cli_auth_status() -> dict:
    """Stato REALE dell'auth via `claude auth status --json`
    (l'esistenza del file credenziali non basta: il token può essere scaduto)."""
    cli = shutil.which("claude")
    if not cli:
        return {"cli_installed": False, "logged_in": False}
    try:
        r = subprocess.run([cli, "auth", "status", "--json"],
                           capture_output=True, text=True, timeout=15)
        data = json.loads(r.stdout or "{}")
        return {"cli_installed": True,
                "logged_in": bool(data.get("loggedIn")),
                "auth_method": data.get("authMethod", ""),
                "email": data.get("email", "") or data.get("account", ""),
                "subscription": data.get("subscriptionType", "") or data.get("plan", "")}
    except Exception as e:
        return {"cli_installed": True, "logged_in": False, "error": f"{type(e).__name__}: {e}"}


def _login_alive() -> bool:
    p = _LOGIN.get("proc")
    return bool(p and p.poll() is None)


def login_start() -> dict:
    """Lancia `claude auth login --claudeai` in uno pseudo-terminale (la CLI
    vuole un TTY) e cattura l'URL OAuth da mostrare all'utente."""
    import os as _os
    import pty
    import select

    cli = shutil.which("claude")
    if not cli:
        return {"ok": False, "error": "Claude Code CLI not installed on the host: "
                                      "npm install -g @anthropic-ai/claude-code"}
    login_cancel()
    master, slave = pty.openpty()
    proc = subprocess.Popen([cli, "auth", "login", "--claudeai"],
                            stdin=slave, stdout=slave, stderr=slave, close_fds=True,
                            env={**_os.environ, "BROWSER": "true", "TERM": "dumb"})
    _os.close(slave)
    buf, url, deadline = "", "", _time.time() + 20
    while _time.time() < deadline and not url:
        r, _, _ = select.select([master], [], [], 0.5)
        if r:
            try:
                chunk = _os.read(master, 4096).decode("utf-8", "replace")
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            m = _URL_RE.search(buf)
            if m:
                url = m.group(1)
        if proc.poll() is not None:
            break
    if not url:
        proc.kill()
        _os.close(master)
        return {"ok": False, "error": "the CLI did not print a login URL",
                "output": buf[-400:]}
    _LOGIN.update({"proc": proc, "master_fd": master, "url": url, "started": _time.time()})
    return {"ok": True, "auth_url": url}


def login_complete(code: str) -> dict:
    """Inoltra alla CLI il codice mostrato dal browser e attende l'esito."""
    import os as _os
    import select

    if not _login_alive():
        return {"ok": False, "error": "no login in progress: click 'Connect' again"}
    code = (code or "").strip()
    if not code:
        return {"ok": False, "error": "paste the code shown by the browser"}
    proc, master = _LOGIN["proc"], _LOGIN["master_fd"]
    try:
        _os.write(master, (code + "\n").encode("utf-8"))
    except OSError as e:
        return {"ok": False, "error": f"cannot send code to CLI: {e}"}
    out, deadline = "", _time.time() + 40
    while _time.time() < deadline:
        r, _, _ = select.select([master], [], [], 0.5)
        if r:
            try:
                chunk = _os.read(master, 4096).decode("utf-8", "replace")
                if chunk:
                    out += chunk
            except OSError:
                break
        if proc.poll() is not None:
            break
    logged = cli_auth_status().get("logged_in", False)
    if logged or proc.poll() == 0:
        login_cancel()
        return {"ok": True, "logged_in": logged}
    # errore: lascia il processo se ancora vivo (l'utente può riprovare il codice)
    tail = _re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", out)[-300:].strip()
    return {"ok": False, "error": tail or "login not completed", "logged_in": False}


def login_cancel() -> None:
    import os as _os
    p = _LOGIN.pop("proc", None)
    fd = _LOGIN.pop("master_fd", None)
    _LOGIN.clear()
    if p and p.poll() is None:
        try:
            p.kill()
        except Exception:
            pass
    if fd is not None:
        try:
            _os.close(fd)
        except OSError:
            pass


def login_pending() -> dict:
    alive = _login_alive()
    if not alive and _LOGIN:
        login_cancel()
    return {"pending": alive, "auth_url": _LOGIN.get("url", "") if alive else ""}
