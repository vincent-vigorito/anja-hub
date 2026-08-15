"""Client per Google Analytics 4 (Data API + Admin API).

Riusa le credenziali Google condivise (stesso token OAuth di Search Console).

API usate:
- Elenco proprietà: GET  https://analyticsadmin.googleapis.com/v1beta/accountSummaries
- Report:           POST https://analyticsdata.googleapis.com/v1beta/properties/{id}:runReport
"""

from __future__ import annotations

from typing import Any

from google.auth.transport.requests import AuthorizedSession

from .google_creds import GoogleAuthError, load_credentials

ADMIN_API = "https://analyticsadmin.googleapis.com/v1beta"
DATA_API = "https://analyticsdata.googleapis.com/v1beta"


class GAError(Exception):
    """Errore della Google Analytics API o di configurazione."""


class GAClient:
    """Wrapper sincrono minimale per GA4."""

    def __init__(self):
        try:
            credentials, self.auth_mode = load_credentials()
        except GoogleAuthError as exc:
            raise GAError(str(exc)) from exc
        self._session = AuthorizedSession(credentials)

    def _request(self, method: str, url: str, json: dict | None = None, params: dict | None = None) -> Any:
        response = self._session.request(method, url, json=json, params=params, timeout=30)
        if response.status_code >= 400:
            try:
                detail = response.json().get("error", {}).get("message", response.text[:300])
            except ValueError:
                detail = response.text[:300]
            raise GAError(f"[HTTP {response.status_code}] {detail}")
        return response.json() if response.content else None

    def list_properties(self) -> list[dict[str, Any]]:
        """Proprietà GA4 accessibili: account, property_id numerico e nome."""
        out: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params = {"pageSize": 200}
            if page_token:
                params["pageToken"] = page_token
            data = self._request("GET", f"{ADMIN_API}/accountSummaries", params=params) or {}
            for account in data.get("accountSummaries", []):
                for prop in account.get("propertySummaries", []):
                    out.append(
                        {
                            "account": account.get("displayName"),
                            "property_id": prop.get("property", "").removeprefix("properties/"),
                            "name": prop.get("displayName"),
                            "type": prop.get("propertyType", ""),
                        }
                    )
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return out

    def run_report(
        self,
        property_id: str,
        start_date: str,
        end_date: str,
        dimensions: list[str] | None = None,
        metrics: list[str] | None = None,
        row_limit: int = 100,
    ) -> dict[str, Any]:
        """Esegue un report GA4 e normalizza le righe.

        Args:
            property_id: ID numerico GA4 (es. "123456789", anche "properties/123456789").
            start_date / end_date: "YYYY-MM-DD" oppure relativi ("28daysAgo", "yesterday", "today").
            dimensions: es. sessionDefaultChannelGroup, sessionSourceMedium, landingPage,
                pagePath, date, deviceCategory, country, city.
            metrics: es. sessions, activeUsers, newUsers, screenPageViews,
                averageSessionDuration, bounceRate, keyEvents, totalRevenue.
        """
        pid = property_id.removeprefix("properties/")
        body: dict[str, Any] = {
            "dateRanges": [{"startDate": start_date, "endDate": end_date}],
            "dimensions": [{"name": d} for d in (dimensions or ["sessionDefaultChannelGroup"])],
            "metrics": [{"name": m} for m in (metrics or ["sessions", "activeUsers", "keyEvents"])],
            "limit": min(row_limit, 100000),
        }
        data = self._request("POST", f"{DATA_API}/properties/{pid}:runReport", json=body) or {}

        metric_names = [m.get("name") for m in data.get("metricHeaders", [])]
        rows = []
        for row in data.get("rows", []):
            values = {}
            for name, value in zip(metric_names, row.get("metricValues", [])):
                raw = value.get("value", "0")
                values[name] = float(raw) if "." in raw else int(raw)
            rows.append(
                {"keys": [d.get("value") for d in row.get("dimensionValues", [])], **values}
            )
        return {"rows": rows, "row_count": len(rows)}
