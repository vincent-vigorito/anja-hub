"""Client per la Google Ads API (REST + GAQL) — sola lettura.

Riusa il token OAuth Google condiviso (serve lo scope
https://www.googleapis.com/auth/adwords) + developer token dal vault:
- GOOGLE_ADS_DEVELOPER_TOKEN (hub-level, Settings → Integrations)
- GOOGLE_ADS_LOGIN_CUSTOMER_ID (hub-level, opzionale: manager/MCC)
- GOOGLE_ADS_CUSTOMER_ID (brand, Connettori → Google)

API: https://googleads.googleapis.com/v22
- customers:listAccessibleCustomers            (check connessione)
- customers/{id}/googleAds:searchStream (GAQL)  (report)
Scrittura (mutate) volutamente NON esposta: arriverà dietro permessi ASP.
"""

from __future__ import annotations

from typing import Any

from google.auth.transport.requests import AuthorizedSession

from . import vault
from .google_creds import GoogleAuthError, load_credentials

API_BASE = "https://googleads.googleapis.com/v22"


class AdsError(Exception):
    """Errore della Google Ads API o di configurazione."""


def _digits(s: str | None) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


class AdsClient:
    def __init__(self):
        dev = (vault.get("GOOGLE_ADS_DEVELOPER_TOKEN") or "").strip()
        if not dev:
            raise AdsError("GOOGLE_ADS_DEVELOPER_TOKEN mancante (Settings → Integrations → Google Ads API)")
        try:
            credentials, self.auth_mode = load_credentials()
        except GoogleAuthError as exc:
            raise AdsError(str(exc)) from exc
        self._session = AuthorizedSession(credentials)
        self._headers = {"developer-token": dev}
        login = _digits(vault.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID"))
        if login:
            self._headers["login-customer-id"] = login

    def customer_id(self) -> str:
        cid = _digits(vault.get("GOOGLE_ADS_CUSTOMER_ID"))
        if not cid:
            raise AdsError("GOOGLE_ADS_CUSTOMER_ID non è nel vault del brand (Connettori → Google)")
        return cid

    def _request(self, method: str, url: str, json_body: dict | None = None) -> Any:
        r = self._session.request(method, url, headers=self._headers, json=json_body, timeout=90)
        if r.status_code >= 400:
            msg = r.text[:400]
            try:
                err = r.json()
                if isinstance(err, list):
                    err = err[0]
                e = err.get("error") or {}
                msg = e.get("message") or msg
                for d in e.get("details") or []:
                    for x in d.get("errors") or []:
                        if x.get("message"):
                            msg = x["message"]
            except Exception:
                pass
            raise AdsError(f"HTTP {r.status_code}: {msg}")
        return r.json() if r.text else {}

    def list_accessible_customers(self) -> list[str]:
        data = self._request("GET", f"{API_BASE}/customers:listAccessibleCustomers")
        return [n.rsplit("/", 1)[-1] for n in data.get("resourceNames", [])]

    def search(self, customer_id: str, gaql: str) -> list[dict[str, Any]]:
        """searchStream GAQL. Se l'account non è raggiungibile via MCC
        (USER_PERMISSION_DENIED) ritenta senza login-customer-id."""
        url = f"{API_BASE}/customers/{_digits(customer_id)}/googleAds:searchStream"
        try:
            batches = self._request("POST", url, {"query": gaql})
        except AdsError as exc:
            if "USER_PERMISSION_DENIED" in str(exc) and "login-customer-id" in self._headers:
                saved = self._headers.pop("login-customer-id")
                try:
                    batches = self._request("POST", url, {"query": gaql})
                finally:
                    self._headers["login-customer-id"] = saved
            else:
                raise
        rows: list[dict[str, Any]] = []
        for b in batches or []:
            rows.extend(b.get("results") or [])
        return rows
