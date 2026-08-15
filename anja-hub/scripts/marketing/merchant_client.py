"""Client per Google Merchant API (products_v1 + reports_v1).

Riusa le credenziali Google condivise (stesso token OAuth di Search Console);
serve lo scope https://www.googleapis.com/auth/content nel token.

API usate (la Content API for Shopping è sunset dal 18/08/2026):
- Account:  GET  https://merchantapi.googleapis.com/accounts/v1/accounts
- Prodotti: GET  https://merchantapi.googleapis.com/products/v1/accounts/{a}/products
- Report:   POST https://merchantapi.googleapis.com/reports/v1/accounts/{a}/reports:search
"""

from __future__ import annotations

from typing import Any

from google.auth.transport.requests import AuthorizedSession

from .google_creds import GoogleAuthError, load_credentials

BASE = "https://merchantapi.googleapis.com"


class MerchantError(Exception):
    """Errore della Merchant API o di configurazione."""


class MerchantClient:
    """Wrapper sincrono minimale per la Merchant API."""

    def __init__(self):
        try:
            credentials, self.auth_mode = load_credentials()
        except GoogleAuthError as exc:
            raise MerchantError(str(exc)) from exc
        self._session = AuthorizedSession(credentials)

    def _request(self, method: str, url: str, json: dict | None = None,
                 params: dict | None = None) -> Any:
        response = self._session.request(method, url, json=json, params=params, timeout=60)
        if response.status_code >= 400:
            try:
                detail = response.json().get("error", {}).get("message", response.text[:300])
            except ValueError:
                detail = response.text[:300]
            raise MerchantError(f"[HTTP {response.status_code}] {detail}")
        return response.json() if response.content else None

    def list_accounts(self) -> list[dict[str, Any]]:
        """Account Merchant Center accessibili col token (diagnostica/picker)."""
        out: list[dict[str, Any]] = []
        page_token = ""
        while True:
            params: dict[str, Any] = {"pageSize": 200}
            if page_token:
                params["pageToken"] = page_token
            data = self._request("GET", f"{BASE}/accounts/v1/accounts", params=params) or {}
            for a in data.get("accounts", []):
                out.append({"account_id": (a.get("name") or "").split("/")[-1],
                            "name": a.get("accountName") or ""})
            page_token = data.get("nextPageToken", "")
            if not page_token:
                break
        return out

    def list_products(self, account_id: str, max_products: int = 1000) -> list[dict[str, Any]]:
        """Prodotti processati con status e issues, normalizzati per l'agente."""
        out: list[dict[str, Any]] = []
        page_token = ""
        while len(out) < max_products:
            params: dict[str, Any] = {"pageSize": min(250, max_products - len(out))}
            if page_token:
                params["pageToken"] = page_token
            data = self._request(
                "GET", f"{BASE}/products/v1/accounts/{account_id}/products",
                params=params) or {}
            for p in data.get("products", []):
                attrs = p.get("productAttributes") or {}
                status = p.get("productStatus") or {}
                issues = [i for i in (status.get("itemLevelIssues") or [])
                          if isinstance(i, dict)]
                severities = {i.get("severity", "") for i in issues}
                out.append({
                    "offer_id": p.get("offerId", ""),
                    "title": attrs.get("title", ""),
                    "link": attrs.get("link", ""),
                    "status": ("disapproved" if "DISAPPROVED" in severities
                               else "demoted" if "DEMOTED" in severities else "ok"),
                    "issues": [{
                        "code": i.get("code", ""),
                        "severity": i.get("severity", ""),
                        "description": i.get("description", ""),
                        "detail": i.get("detail", ""),
                        "attribute": i.get("attribute", ""),
                        "documentation": i.get("documentation", ""),
                    } for i in issues],
                })
            page_token = data.get("nextPageToken", "")
            if not page_token:
                break
        return out

    def search_report(self, account_id: str, query: str,
                      max_rows: int = 1000) -> list[dict[str, Any]]:
        """reports:search MQL grezza — la condizione su `date` nel WHERE è obbligatoria."""
        out: list[dict[str, Any]] = []
        page_token = ""
        while len(out) < max_rows:
            body: dict[str, Any] = {"query": query,
                                    "pageSize": min(1000, max_rows - len(out))}
            if page_token:
                body["pageToken"] = page_token
            data = self._request(
                "POST", f"{BASE}/reports/v1/accounts/{account_id}/reports:search",
                json=body) or {}
            out.extend(data.get("results", []))
            page_token = data.get("nextPageToken", "")
            if not page_token:
                break
        return out
