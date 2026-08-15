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
    """Info pubblicamente sicure su Claude auth per UI. No token leakage."""
    sub_active = has_claude_subscription()
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
