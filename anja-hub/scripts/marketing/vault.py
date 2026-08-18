"""vault.py — resolver credenziali a 2 livelli per il marketing workspace.

Livello B — vault del brand (workspace-scoped): file `.secrets.env` puntato da
  `ANJA_MARKETING_VAULT` (= `<ws>/.anjawiki/.secrets.env`). Contiene le credenziali
  proprie del brand (WP_*, META_*) e i resource-ID (GA4_PROPERTY_ID, GSC_SITE, ...).
Livello A — connettori agency (condivisi): directory puntata da
  `ANJA_GOOGLE_CONNECTORS` (fallback `<hub>/config/connectors`). Contiene il token
  Google OAuth dell'agency (gsc-token.json), letto da google_creds.py.

I segreti vivono SOLO nel `.secrets.env`: il `.mcp.json` del workspace inietta via
`env` solo i PATH, mai i valori. Vedi anja-marketing-workspace-design.md §3.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values


class VaultError(Exception):
    """Vault non configurato o chiave obbligatoria mancante."""


_cache: dict[str, str] | None = None


def _load() -> dict[str, str]:
    global _cache
    if _cache is None:
        _cache = {}
        # fallback hub-level PRIMA: le chiavi condivise (developer token Ads,
        # key AI…) vivono in <hub>/.secrets.env; il vault del brand vince.
        hub = os.environ.get("ANJA_HUB", "")
        hub_env = Path(hub) / ".secrets.env" if hub else None
        if hub_env and hub_env.is_file():
            _cache.update({k: (v or "").strip() for k, v in dotenv_values(hub_env).items()})
        path = os.environ.get("ANJA_MARKETING_VAULT", "")
        if path and Path(path).is_file():
            _cache.update({k: (v or "").strip() for k, v in dotenv_values(path).items() if (v or "").strip()})
    return _cache


def scope() -> str:
    return os.environ.get("ANJA_SCOPE", "hub")


def backend() -> str:
    """Backend CMS del workspace da `<ws>/.anjawiki/meta.yaml` ('' se ignoto)."""
    path = os.environ.get("ANJA_MARKETING_VAULT", "")
    if not path:
        return ""
    meta = Path(path).parent / "meta.yaml"
    if not meta.is_file():
        return ""
    for ln in meta.read_text(encoding="utf-8", errors="replace").splitlines():
        if ln.startswith("backend:"):
            return ln.split(":", 1)[1].strip()
    return ""


def get(key: str, default: str | None = None) -> str | None:
    """Valore dal vault del brand; fallback su env; poi default."""
    val = _load().get(key) or os.environ.get(key)
    return val if val else default


def require(key: str) -> str:
    val = get(key)
    if not val:
        raise VaultError(
            f"chiave '{key}' assente nel vault del brand "
            f"(ANJA_MARKETING_VAULT={os.environ.get('ANJA_MARKETING_VAULT', '<unset>')})"
        )
    return val


def wp_config() -> tuple[str, str, str]:
    """(base_url, username, app_password) del brand. Obbligatori per backend WP/Woo."""
    return require("WP_BASE_URL").rstrip("/"), require("WP_USERNAME"), require("WP_APP_PASSWORD")


def connectors_dir() -> Path:
    """Directory dei connettori agency (Google OAuth, ...)."""
    env = os.environ.get("ANJA_GOOGLE_CONNECTORS")
    if env:
        return Path(env).expanduser().resolve()
    hub = os.environ.get("ANJA_HUB") or os.environ.get("ANJA_ROOT") or "."
    return (Path(hub) / "config" / "connectors").resolve()
