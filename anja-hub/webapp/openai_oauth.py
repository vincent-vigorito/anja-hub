"""openai_oauth.py — OAuth subscription auth for OpenAI ChatGPT Plus/Pro (Fase 7v).

Pattern "CLI reuse" (analogo a claude-agent-sdk per Anthropic):
- Codex CLI (`codex login`) salva access_token + refresh_token in `~/.codex/auth.json`
- anja legge quel file e riusa il token per chiamare l'endpoint Codex backend
- Quando token sta per scadere → refresh via OAuth endpoint OpenAI

Endpoint chat: https://chatgpt.com/backend-api/codex/responses (NON api.openai.com)
Schema: OpenAI Responses API (input/instructions, no messages/chat).
Modello disponibile con ChatGPT account: solo `gpt-5.5` (al 2026-05-13).

Stdlib only — niente nuove dep.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional


CODEX_AUTH_PATH = Path("~/.codex/auth.json").expanduser()

# OAuth refresh — il client_id Codex CLI è pubblico (parte del flow PKCE)
# Riusiamo lo stesso che Codex CLI dichiara per coerenza vs OpenAI server.
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"

# Endpoint chat
CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"

# Modelli ammessi via ChatGPT subscription (lista whitelisted da OpenAI)
# Aggiornare quando OpenAI estende. Default solo gpt-5.5 (spike conferma 2026-05-13).
SUPPORTED_MODELS = ["gpt-5.6", "gpt-5.5"]

# Sicurezza: prima di un call, considera token expired se exp < now + EXP_BUFFER_SEC
EXP_BUFFER_SEC = 300  # 5 minuti

# Lock per evitare refresh concorrenti
_refresh_lock = threading.Lock()


# ============================================================
# Read Codex auth.json
# ============================================================

def read_codex_auth() -> Optional[dict]:
    """Read ~/.codex/auth.json. Return dict or None se file assente/malformato."""
    if not CODEX_AUTH_PATH.is_file():
        return None
    try:
        with open(CODEX_AUTH_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def write_codex_auth(data: dict) -> bool:
    """Persist updated tokens back to ~/.codex/auth.json (after refresh).
    Scrittura atomica 0600: tmp aperto O_CREAT|0600 + os.replace → niente finestra
    world-readable (umask) né file corrotto se il processo muore a metà."""
    try:
        tmp = CODEX_AUTH_PATH.with_suffix(".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, CODEX_AUTH_PATH)
        os.chmod(CODEX_AUTH_PATH, 0o600)
        return True
    except Exception:
        return False


def has_codex_auth() -> bool:
    """Quick check: Codex CLI è loggato?"""
    d = read_codex_auth()
    if not d:
        return False
    tokens = d.get("tokens") or {}
    return bool(tokens.get("access_token"))


def codex_auth_summary() -> dict:
    """Info pubblicamente sicure su auth (no token!). Per UI."""
    d = read_codex_auth() or {}
    tokens = d.get("tokens") or {}
    access = tokens.get("access_token") or ""
    return {
        "configured": bool(access),
        "account_id_short": (tokens.get("account_id") or "")[:8] + "..." if tokens.get("account_id") else "",
        "last_refresh": d.get("last_refresh"),
        "exp_unix": _jwt_exp(access) if access else None,
        "expired": _is_expired(access) if access else None,
        "supported_models": SUPPORTED_MODELS,
        "auth_path": str(CODEX_AUTH_PATH),
    }


# ============================================================
# JWT decode (only exp claim — no signature verify)
# ============================================================

def _jwt_exp(token: str) -> Optional[int]:
    """Estrai claim 'exp' da JWT senza verificare firma. Return unix timestamp o None."""
    if not token or token.count(".") < 2:
        return None
    try:
        payload_b64 = token.split(".")[1]
        # Pad base64
        pad = "=" * (-len(payload_b64) % 4)
        decoded = base64.urlsafe_b64decode(payload_b64 + pad)
        claims = json.loads(decoded)
        exp = claims.get("exp")
        return int(exp) if exp else None
    except Exception:
        return None


def _is_expired(token: str) -> bool:
    """True se token expired o scade entro EXP_BUFFER_SEC."""
    exp = _jwt_exp(token)
    if exp is None:
        return True  # safer: assume expired se non leggibile
    return time.time() + EXP_BUFFER_SEC >= exp


# ============================================================
# OAuth refresh
# ============================================================

def refresh_token() -> tuple[bool, str]:
    """Refresh access_token usando refresh_token. Return (success, error_msg)."""
    with _refresh_lock:
        d = read_codex_auth()
        if not d:
            return False, "auth.json not found"
        tokens = d.get("tokens") or {}
        rt = tokens.get("refresh_token")
        if not rt:
            return False, "no refresh_token"

        # Re-check post-lock: se altro thread ha già refreshato, skip
        access = tokens.get("access_token") or ""
        if access and not _is_expired(access):
            return True, ""

        body = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": rt,
            "client_id": CODEX_CLIENT_ID,
            "scope": "openid profile email offline_access",
        }).encode()
        req = urllib.request.Request(
            OAUTH_TOKEN_URL,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "codex_cli_rs/0.124.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                resp = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return False, f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

        new_access = resp.get("access_token")
        new_id = resp.get("id_token")
        new_refresh = resp.get("refresh_token") or rt  # some servers rotate, some don't
        if not new_access:
            return False, "no access_token in response"

        tokens["access_token"] = new_access
        if new_id:
            tokens["id_token"] = new_id
        tokens["refresh_token"] = new_refresh
        d["tokens"] = tokens
        d["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if not write_codex_auth(d):
            return False, "failed to persist refreshed token"
        return True, ""


def get_chatgpt_token() -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Ritorna (access_token, account_id, error) pronto all'uso.

    Refresh automatico se token expired/expiring. None se non configurato.
    """
    d = read_codex_auth()
    if not d:
        return None, None, "auth.json not found"
    tokens = d.get("tokens") or {}
    access = tokens.get("access_token")
    account = tokens.get("account_id")
    if not access:
        return None, None, "no access_token"
    if _is_expired(access):
        ok, err = refresh_token()
        if not ok:
            return None, account, f"refresh failed: {err}"
        # Re-read
        d = read_codex_auth() or {}
        tokens = d.get("tokens") or {}
        access = tokens.get("access_token")
    return access, account, None


# ============================================================
# anja-side enable flag
# ============================================================

def get_config_path(hub_path: Path) -> Path:
    return hub_path / "config" / "openai_oauth.json"


def load_openai_oauth_config(hub_path: Path) -> dict:
    f = get_config_path(hub_path)
    if not f.is_file():
        return {"enabled": False, "use_codex_cli": True}
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        return {
            "enabled": bool(d.get("enabled", False)),
            "use_codex_cli": bool(d.get("use_codex_cli", True)),
        }
    except Exception:
        return {"enabled": False, "use_codex_cli": True}


def save_openai_oauth_config(hub_path: Path, cfg: dict) -> bool:
    f = get_config_path(hub_path)
    f.parent.mkdir(parents=True, exist_ok=True)
    try:
        f.write_text(json.dumps({
            "enabled": bool(cfg.get("enabled", False)),
            "use_codex_cli": bool(cfg.get("use_codex_cli", True)),
        }, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


def is_openai_oauth_enabled(hub_path: Path) -> bool:
    """True se user ha attivato il flag in Settings AND auth.json esiste."""
    return load_openai_oauth_config(hub_path).get("enabled") and has_codex_auth()
