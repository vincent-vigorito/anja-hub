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

    @staticmethod
    def _error_message(r) -> str:
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
        return f"HTTP {r.status_code}: {msg}"

    def _request(self, method: str, url: str, json_body: dict | None = None) -> Any:
        r = self._session.request(method, url, headers=self._headers, json=json_body, timeout=90)
        if r.status_code >= 400:
            raise AdsError(self._error_message(r))
        return r.json() if r.text else {}

    def list_accessible_customers(self) -> list[str]:
        data = self._request("GET", f"{API_BASE}/customers:listAccessibleCustomers")
        return [n.rsplit("/", 1)[-1] for n in data.get("resourceNames", [])]

    def search(self, customer_id: str, gaql: str) -> list[dict[str, Any]]:
        """searchStream GAQL. L'header login-customer-id (MCC) serve SOLO per
        account raggiunti tramite manager: se la chiamata con MCC dà 403
        (permission) si ritenta senza — l'utente può avere accesso diretto —
        e viceversa. Il primo tentativo che funziona viene ricordato."""
        url = f"{API_BASE}/customers/{_digits(customer_id)}/googleAds:searchStream"
        mcc = self._headers.get("login-customer-id")
        attempts = [dict(self._headers)]
        if mcc:
            attempts.append({k: v for k, v in self._headers.items() if k != "login-customer-id"})
        last: AdsError | None = None
        for hdrs in attempts:
            try:
                r = self._session.post(url, headers=hdrs, json={"query": gaql}, timeout=90)
                if r.status_code == 403 and len(attempts) > 1 and hdrs is attempts[0]:
                    last = AdsError(f"HTTP 403: {r.text[:200]}")
                    continue
                if r.status_code >= 400:
                    raise AdsError(self._error_message(r))
                self._headers = dict(hdrs)     # ricorda la variante che funziona
                batches = r.json() if r.text else []
                break
            except AdsError as exc:
                last = exc
                if hdrs is attempts[-1]:
                    raise
        else:
            raise last or AdsError("searchStream failed")
        rows: list[dict[str, Any]] = []
        for b in batches or []:
            rows.extend(b.get("results") or [])
        return rows
