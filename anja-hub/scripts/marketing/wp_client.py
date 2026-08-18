"""Client asincrono per la REST API di WordPress.

Usa l'autenticazione Basic con le Application Password native di WordPress
(disponibili dalla 5.6, richiede HTTPS). Copre articoli, pagine,
categorie e tag tramite gli endpoint /wp-json/wp/v2/*.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

# Campi restituiti negli elenchi: evita di scaricare il contenuto completo di ogni post.
LIST_FIELDS = "id,date,modified,slug,status,type,link,title,excerpt"

# Tipi di contenuto e tassonomie supportati (nome -> endpoint REST).
CONTENT_ENDPOINTS = {"posts": "posts", "pages": "pages"}
TAXONOMY_ENDPOINTS = {"categories": "categories", "tags": "tags"}


class WordPressError(Exception):
    """Errore restituito dalla REST API di WordPress."""

    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(f"[HTTP {status_code}] {code}: {message}")


class WordPressClient:
    """Wrapper minimale e tipizzato sugli endpoint /wp-json/wp/v2."""

    def __init__(
        self,
        base_url: str,
        username: str,
        app_password: str,
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_root = f"{self.base_url}/wp-json"
        self._client = httpx.AsyncClient(
            auth=(username, app_password),
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "anja-marketer-agent/0.1"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Esegue una richiesta e normalizza gli errori della REST API."""
        url = f"{self.api_root}{path}"
        if method.upper() == "GET":
            # Cache-buster: alcuni siti (es. LiteSpeed Cache) cachano le risposte
            # REST come pubbliche; senza questo le riletture post-scrittura
            # restituirebbero dati stantii.
            params = {**(params or {}), "_nocache": str(time.time_ns())}
        try:
            response = await self._client.request(method, url, params=params, json=json)
        except httpx.HTTPError as exc:
            raise WordPressError(0, "connection_error", f"{type(exc).__name__}: {exc}") from exc

        if response.status_code >= 400:
            try:
                body = response.json()
                raise WordPressError(
                    response.status_code,
                    body.get("code", "unknown"),
                    body.get("message", response.text[:300]),
                )
            except ValueError:
                raise WordPressError(
                    response.status_code, "http_error", response.text[:300]
                ) from None

        if not response.content:
            return None
        return response.json()

    # ------------------------------------------------------------------
    # WooCommerce (wc/v3) — stesse credenziali WP (Application Password
    # con permessi shop). Sola lettura.
    # ------------------------------------------------------------------

    async def wc_orders(self, after: str, before: str, status: str = "completed",
                        per_page: int = 100, page: int = 1) -> list[dict[str, Any]]:
        """Ordini nel periodo (date ISO YYYY-MM-DD). `status`: completed|processing|any."""
        params = {"per_page": min(max(per_page, 1), 100), "page": page, "orderby": "date",
                  "order": "desc", "after": f"{after}T00:00:00", "before": f"{before}T23:59:59"}
        if status and status != "any":
            params["status"] = status
        return await self._request("GET", "/wc/v3/orders", params=params) or []

    async def wc_sales_report(self, date_min: str, date_max: str) -> list[dict[str, Any]]:
        """Report vendite aggregato Woo (totali + serie giornaliera) nel periodo."""
        return await self._request("GET", "/wc/v3/reports/sales",
                                   params={"date_min": date_min, "date_max": date_max}) or []

    async def wc_top_sellers(self, date_min: str, date_max: str) -> list[dict[str, Any]]:
        return await self._request("GET", "/wc/v3/reports/top_sellers",
                                   params={"date_min": date_min, "date_max": date_max}) or []

    # ------------------------------------------------------------------
    # Diagnostica / informazioni sito
    # ------------------------------------------------------------------

    async def site_info(self) -> dict[str, Any]:
        """Informazioni di base del sito (nome, descrizione, URL)."""
        return await self._request(
            "GET", "", params={"_fields": "name,description,url,home,namespaces"}
        )

    async def current_user(self) -> dict[str, Any]:
        """Utente autenticato: verifica che le credenziali funzionino."""
        return await self._request(
            "GET",
            "/wp/v2/users/me",
            params={"context": "edit", "_fields": "id,name,slug,roles,capabilities"},
        )

    # ------------------------------------------------------------------
    # Contenuti (articoli e pagine)
    # ------------------------------------------------------------------

    @staticmethod
    def _content_path(post_type: str) -> str:
        endpoint = CONTENT_ENDPOINTS.get(post_type)
        if not endpoint:
            raise WordPressError(
                0, "invalid_post_type",
                f"post_type non valido: {post_type!r} (validi: {', '.join(CONTENT_ENDPOINTS)})",
            )
        return f"/wp/v2/{endpoint}"

    async def list_content(
        self,
        post_type: str = "posts",
        search: str | None = None,
        status: str | None = None,
        page: int = 1,
        per_page: int = 10,
        orderby: str | None = None,
        order: str | None = None,
    ) -> list[dict[str, Any]]:
        """Elenca articoli o pagine (campi ridotti, senza contenuto completo)."""
        params: dict[str, Any] = {
            "page": page,
            "per_page": per_page,
            "_fields": LIST_FIELDS,
        }
        if search:
            params["search"] = search
        if status:
            params["status"] = status  # es. "publish", "draft", "publish,draft"
        if orderby:
            params["orderby"] = orderby
        if order:
            params["order"] = order
        return await self._request("GET", self._content_path(post_type), params=params)

    async def get_content(self, post_type: str, content_id: int) -> dict[str, Any]:
        """Recupera un singolo contenuto con il sorgente completo (context=edit)."""
        return await self._request(
            "GET",
            f"{self._content_path(post_type)}/{content_id}",
            params={"context": "edit"},
        )

    async def create_content(self, post_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", self._content_path(post_type), json=payload)

    async def update_content(
        self, post_type: str, content_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request(
            "POST", f"{self._content_path(post_type)}/{content_id}", json=payload
        )

    async def delete_content(
        self, post_type: str, content_id: int, force: bool = False
    ) -> dict[str, Any]:
        """Sposta nel cestino; con force=True elimina definitivamente.

        Alcuni siti (es. dietro Cloudflare) bloccano il verbo HTTP DELETE:
        in quel caso ripiega su POST con X-HTTP-Method-Override.
        """
        path = f"{self._content_path(post_type)}/{content_id}"
        params = {"force": "true" if force else "false"}
        try:
            return await self._request("DELETE", path, params=params)
        except WordPressError as exc:
            if exc.status_code not in (403, 405, 520):
                raise
            response = await self._client.post(
                f"{self.api_root}{path}",
                params={**params, "_nocache": str(time.time_ns())},
                headers={"X-HTTP-Method-Override": "DELETE"},
            )
            if response.status_code >= 400:
                raise
            return response.json() if response.content else None

    # ------------------------------------------------------------------
    # Media
    # ------------------------------------------------------------------

    async def upload_media(
        self,
        file_path: str,
        alt_text: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Carica un file nella libreria media e (opzionale) imposta alt/title."""
        from pathlib import Path

        path = Path(file_path)
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "webp": "image/webp", "gif": "image/gif"}.get(
            path.suffix.lstrip(".").lower(), "application/octet-stream")
        response = await self._client.post(
            f"{self.api_root}/wp/v2/media",
            content=path.read_bytes(),
            headers={
                "Content-Disposition": f'attachment; filename="{path.name}"',
                "Content-Type": mime,
            },
        )
        if response.status_code >= 400:
            raise WordPressError(response.status_code, "upload_error", response.text[:300])
        media = response.json()
        if alt_text or title:
            payload: dict[str, Any] = {}
            if alt_text:
                payload["alt_text"] = alt_text
            if title:
                payload["title"] = title
            media = await self._request("POST", f"/wp/v2/media/{media['id']}", json=payload)
        return media

    # ------------------------------------------------------------------
    # SEO (SEOPress PRO — namespace seopress/v1)
    # ------------------------------------------------------------------

    @staticmethod
    def _settings_to_dict(settings: Any) -> dict[str, Any]:
        """Converte la lista di campi SEOPress [{key, value, ...}] in {chiave: valore}."""
        if not isinstance(settings, list):
            return settings if isinstance(settings, dict) else {}
        return {
            field["key"]: field.get("value", "")
            for field in settings
            if isinstance(field, dict) and "key" in field
        }

    async def seo_effective(self, post_id: int) -> dict[str, Any]:
        """SEO 'effettiva' calcolata da SEOPress (template e fallback applicati)."""
        data = await self._request("GET", f"/seopress/v1/posts/{post_id}")
        return data.get("data", data) if isinstance(data, dict) else data

    async def seo_get(self, post_id: int) -> dict[str, Any]:
        """Meta SEO grezzi salvati sul contenuto (vuoti se mai impostati)."""
        titles = await self._request(
            "GET", f"/seopress/v1/posts/{post_id}/title-description-metas"
        )
        keywords = await self._request(
            "GET", f"/seopress/v1/posts/{post_id}/target-keywords"
        )
        robots = await self._request(
            "GET", f"/seopress/v1/posts/{post_id}/meta-robot-settings"
        )
        social = await self._request(
            "GET", f"/seopress/v1/posts/{post_id}/social-settings"
        )
        return {
            "titles": titles,
            "target_keywords": keywords.get("value", []) if isinstance(keywords, dict) else keywords,
            "robots": self._settings_to_dict(robots),
            "social": self._settings_to_dict(social),
        }

    async def seo_set_titles(
        self,
        post_id: int,
        title: str | None = None,
        description: str | None = None,
    ) -> Any:
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if description is not None:
            payload["description"] = description
        return await self._request(
            "PUT", f"/seopress/v1/posts/{post_id}/title-description-metas", json=payload
        )

    async def seo_set_target_keywords(self, post_id: int, keywords: list[str]) -> Any:
        # Il controller SEOPress si aspetta la chiave meta come parametro,
        # con le keyword in stringa separata da virgole.
        return await self._request(
            "PUT", f"/seopress/v1/posts/{post_id}/target-keywords",
            json={"_seopress_analysis_target_kw": ", ".join(keywords)},
        )

    async def seo_set_robots(self, post_id: int, values: dict[str, Any]) -> Any:
        """Imposta robots/canonical: chiavi _seopress_robots_* -> valore."""
        return await self._request(
            "PUT", f"/seopress/v1/posts/{post_id}/meta-robot-settings", json=values
        )

    async def seo_set_social(self, post_id: int, values: dict[str, Any]) -> Any:
        """Imposta i meta social: chiavi _seopress_social_* -> valore."""
        return await self._request(
            "PUT", f"/seopress/v1/posts/{post_id}/social-settings", json=values
        )

    async def seo_apply(
        self,
        post_id: int,
        *,
        title: str | None = None,
        description: str | None = None,
        target_keywords: list[str] | None = None,
        canonical: str | None = None,
        noindex: bool | None = None,
        nofollow: bool | None = None,
        fb_title: str | None = None,
        fb_description: str | None = None,
        x_title: str | None = None,
        x_description: str | None = None,
    ) -> dict[str, Any]:
        """Applica i meta SEO passati (solo quelli non-None) e rilegge lo stato salvato."""
        if title is not None or description is not None:
            await self.seo_set_titles(post_id, title=title, description=description)
        if target_keywords is not None:
            await self.seo_set_target_keywords(post_id, target_keywords)

        robots: dict[str, Any] = {}
        if canonical is not None:
            robots["_seopress_robots_canonical"] = canonical
        if noindex is not None:
            robots["_seopress_robots_index"] = "yes" if noindex else ""
        if nofollow is not None:
            robots["_seopress_robots_follow"] = "yes" if nofollow else ""
        if robots:
            await self.seo_set_robots(post_id, robots)

        social: dict[str, Any] = {}
        if fb_title is not None:
            social["_seopress_social_fb_title"] = fb_title
        if fb_description is not None:
            social["_seopress_social_fb_desc"] = fb_description
        if x_title is not None:
            social["_seopress_social_twitter_title"] = x_title
        if x_description is not None:
            social["_seopress_social_twitter_desc"] = x_description
        if social:
            await self.seo_set_social(post_id, social)

        # Rilettura di verifica: lo stato realmente salvato.
        return await self.seo_get(post_id)

    # ------------------------------------------------------------------
    # Tassonomie (categorie e tag)
    # ------------------------------------------------------------------

    @staticmethod
    def _taxonomy_path(taxonomy: str) -> str:
        endpoint = TAXONOMY_ENDPOINTS.get(taxonomy)
        if not endpoint:
            raise WordPressError(
                0, "invalid_taxonomy",
                f"taxonomy non valida: {taxonomy!r} (valide: {', '.join(TAXONOMY_ENDPOINTS)})",
            )
        return f"/wp/v2/{endpoint}"

    async def list_terms(
        self,
        taxonomy: str = "categories",
        search: str | None = None,
        per_page: int = 50,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "per_page": per_page,
            "_fields": "id,name,slug,description,parent,count",
        }
        if search:
            params["search"] = search
        return await self._request("GET", self._taxonomy_path(taxonomy), params=params)

    async def create_term(
        self,
        taxonomy: str,
        name: str,
        description: str | None = None,
        parent: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": name}
        if description:
            payload["description"] = description
        if parent:
            payload["parent"] = parent
        return await self._request("POST", self._taxonomy_path(taxonomy), json=payload)
