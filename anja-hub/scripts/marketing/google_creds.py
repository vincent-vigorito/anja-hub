"""Credenziali Google condivise (connettore agency) per GSC + GA4 (+ Ads).

Un solo token OAuth utente per tutte le API Google in lettura. La directory dei
connettori è risolta da `ANJA_GOOGLE_CONNECTORS` (fallback `<hub>/config/connectors`),
non più dalla root di progetto. Priorità: OAuth utente > service account.

Riadattato da anja-marketer (rimosso il riferimento a config.PROJECT_ROOT).
"""

from __future__ import annotations

import os
from pathlib import Path

from google.auth.credentials import Credentials
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as UserCredentials


def _connectors_dir() -> Path:
    env = os.environ.get("ANJA_GOOGLE_CONNECTORS")
    if env:
        return Path(env).expanduser().resolve()
    hub = os.environ.get("ANJA_HUB") or os.environ.get("ANJA_ROOT") or "."
    return (Path(hub) / "config" / "connectors").resolve()


CREDENTIALS_DIR = _connectors_dir()
TOKEN_PATH = CREDENTIALS_DIR / "gsc-token.json"
OAUTH_CLIENT_PATH = CREDENTIALS_DIR / "gsc-oauth-client.json"
SA_PATH_DEFAULT = CREDENTIALS_DIR / "gsc-service-account.json"

# Ambiti usati: riautorizzare il token quando se ne aggiungono di nuovi.
SCOPES = [
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/analytics.edit",  # key events, filtri (Admin API write)
    "https://www.googleapis.com/auth/adwords",
    "https://www.googleapis.com/auth/content",  # Merchant API
]


class GoogleAuthError(Exception):
    """Credenziali Google mancanti o non valide."""


def sa_path() -> Path:
    return Path(os.environ.get("GSC_CREDENTIALS", "") or SA_PATH_DEFAULT)


def load_credentials() -> tuple[Credentials, str]:
    """Restituisce (credenziali, modalità) — modalità: "oauth" | "service_account"."""
    if TOKEN_PATH.is_file():
        # Niente override di scope al load: il refresh chiederebbe a Google anche
        # scope mai concessi a QUESTO token (invalid_scope su tutte le API).
        # SCOPES resta la lista da concedere quando si (ri)genera il token.
        return (
            UserCredentials.from_authorized_user_file(str(TOKEN_PATH)),
            "oauth",
        )
    path = sa_path()
    if path.is_file():
        return (
            service_account.Credentials.from_service_account_file(str(path), scopes=SCOPES),
            "service_account",
        )
    raise GoogleAuthError(
        f"Nessuna credenziale Google nel connettore agency ({CREDENTIALS_DIR}). "
        f"Attesi: gsc-token.json (OAuth) oppure {path.name} (service account)."
    )
