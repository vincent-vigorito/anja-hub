"""Client per la Google Search Console API.

Autenticazione, in ordine di priorità:
1. **OAuth utente** — token in credentials/gsc-token.json (generato una tantum
   con `uv run python scripts/gsc_auth.py`). L'utente è già proprietario delle
   proprietà GSC: nessun utente da aggiungere in Search Console.
2. **Service account** — chiave in credentials/gsc-service-account.json
   (override con GSC_CREDENTIALS); va aggiunto come utente nelle proprietà.

API usate:
- Sites:      GET  https://www.googleapis.com/webmasters/v3/sites
- Analytics:  POST https://www.googleapis.com/webmasters/v3/sites/{siteUrl}/searchAnalytics/query
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from google.auth.transport.requests import AuthorizedSession

from .google_creds import (  # noqa: F401 — ri-esportati per gli script
    OAUTH_CLIENT_PATH,
    SCOPES,
    TOKEN_PATH,
    GoogleAuthError,
    load_credentials,
    sa_path,
)

API_BASE = "https://www.googleapis.com/webmasters/v3"


class GSCError(Exception):
    """Errore della Search Console API o di configurazione."""


def credentials_path() -> Path:
    return sa_path()


class GSCClient:
    """Wrapper sincrono minimale (le chiamate sono rapide e poco frequenti)."""

    def __init__(self):
        try:
            credentials, self.auth_mode = load_credentials()
        except GoogleAuthError as exc:
            raise GSCError(str(exc)) from exc
        self.service_account_email = getattr(
            credentials, "service_account_email", "(account utente via OAuth)"
        )
        self._session = AuthorizedSession(credentials)

    def _request(self, method: str, url: str, json: dict | None = None) -> Any:
        response = self._session.request(method, url, json=json, timeout=30)
        if response.status_code >= 400:
            try:
                detail = response.json().get("error", {}).get("message", response.text[:200])
            except ValueError:
                detail = response.text[:200]
            raise GSCError(f"[HTTP {response.status_code}] {detail}")
        return response.json() if response.content else None

    def list_sites(self) -> list[dict[str, Any]]:
        """Proprietà a cui il service account ha accesso (siteUrl + permissionLevel)."""
        data = self._request("GET", f"{API_BASE}/sites")
        return data.get("siteEntry", []) if data else []

    def query(
        self,
        site_url: str,
        start_date: str,
        end_date: str,
        dimensions: list[str] | None = None,
        row_limit: int = 100,
        start_row: int = 0,
        search_type: str = "web",
        dimension_filters: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Search Analytics query.

        Args:
            site_url: proprietà GSC, es. "sc-domain:example.com" o "https://example.com/".
            start_date / end_date: "YYYY-MM-DD" (il dato GSC arriva con ~2 giorni di ritardo).
            dimensions: tra query, page, date, device, country, searchAppearance.
            dimension_filters: lista di {dimension, operator, expression},
                es. {"dimension": "query", "operator": "contains", "expression": "seo"}.
        """
        body: dict[str, Any] = {
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": dimensions or ["query"],
            "rowLimit": min(row_limit, 25000),
            "startRow": start_row,
            "type": search_type,
        }
        if dimension_filters:
            body["dimensionFilterGroups"] = [{"filters": dimension_filters}]

        from urllib.parse import quote

        url = f"{API_BASE}/sites/{quote(site_url, safe='')}/searchAnalytics/query"
        data = self._request("POST", url, json=body) or {}
        rows = [
            {
                "keys": row.get("keys", []),
                "clicks": row.get("clicks", 0),
                "impressions": row.get("impressions", 0),
                "ctr": round(row.get("ctr", 0) * 100, 2),       # in percento
                "position": round(row.get("position", 0), 1),
            }
            for row in data.get("rows", [])
        ]
        return {"rows": rows, "row_count": len(rows)}
