"""Client Meta Graph API per pubblicazione organica su Facebook Pages e Instagram.

Riadattato da anja-marketer: le credenziali non vengono più da `siti/<dominio>/.env`
ma dal vault del brand (`from_vault()`).

Note operative:
- Token consigliato: utente di sistema Business Manager (non scade).
- I post su pagina usano il PAGE access token, ricavato dal token utente/sistema.
- Instagram Content Publishing richiede immagini su URL PUBBLICI.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

GRAPH_VERSION = os.environ.get("META_GRAPH_VERSION", "v25.0")
GRAPH_ROOT = f"https://graph.facebook.com/{GRAPH_VERSION}"


class MetaError(RuntimeError):
    def __init__(self, status: int, payload: Any):
        self.status = status
        self.payload = payload
        msg = payload
        if isinstance(payload, dict):
            msg = payload.get("error", {}).get("message", payload)
        super().__init__(f"Meta API {status}: {msg}")


class MetaClient:
    def __init__(self, access_token: str, page_id: str | None = None,
                 ig_user_id: str | None = None):
        self.access_token = access_token
        self.page_id = page_id
        self.ig_user_id = ig_user_id
        self._page_token: str | None = None
        self._client = httpx.Client(timeout=60)

    @classmethod
    def from_vault(cls) -> "MetaClient":
        from . import vault

        token = vault.get("META_ACCESS_TOKEN")
        if not token:
            raise RuntimeError("META_ACCESS_TOKEN assente nel vault del brand")
        return cls(token, vault.get("META_PAGE_ID"), vault.get("META_IG_USER_ID"))

    # ------------------------------------------------------------------
    def _request(self, method: str, path: str, *, token: str | None = None,
                 **params: Any) -> dict[str, Any]:
        params["access_token"] = token or self.access_token
        resp = self._client.request(method, f"{GRAPH_ROOT}{path}",
                                    params=params if method == "GET" else None,
                                    data=None if method == "GET" else params)
        data = resp.json()
        if resp.status_code >= 400 or "error" in data:
            raise MetaError(resp.status_code, data)
        return data

    # ------------------------------------------------------------------
    def discover(self) -> dict[str, Any]:
        """Pagine accessibili dal token + IG business collegati."""
        me = self._request("GET", "/me", fields="id,name")
        pages = self._request("GET", "/me/accounts",
                              fields="id,name,instagram_business_account{id,username}")
        return {"token_identity": me, "pages": pages.get("data", [])}

    def _get_page_token(self) -> str:
        if not self.page_id:
            raise RuntimeError("META_PAGE_ID non impostato (usa discover())")
        if not self._page_token:
            data = self._request("GET", f"/{self.page_id}", fields="access_token")
            self._page_token = data["access_token"]
        return self._page_token

    # ------------------------------------------------------------------
    # Facebook Page
    # ------------------------------------------------------------------
    def fb_post(self, message: str, link: str | None = None,
                scheduled_unix: int | None = None,
                published: bool = True) -> dict[str, Any]:
        """Post testo/link sulla pagina. ``scheduled_unix`` → programmato."""
        params: dict[str, Any] = {"message": message}
        if link:
            params["link"] = link
        if scheduled_unix:
            params["published"] = "false"
            params["scheduled_publish_time"] = scheduled_unix
        elif not published:
            params["published"] = "false"
        return self._request("POST", f"/{self.page_id}/feed",
                             token=self._get_page_token(), **params)

    def fb_photo(self, image_url: str, caption: str = "",
                 scheduled_unix: int | None = None,
                 published: bool = True) -> dict[str, Any]:
        """Post con foto (immagine da URL pubblico)."""
        params: dict[str, Any] = {"url": image_url, "caption": caption}
        if scheduled_unix:
            params["published"] = "false"
            params["scheduled_publish_time"] = scheduled_unix
        elif not published:
            params["published"] = "false"
        return self._request("POST", f"/{self.page_id}/photos",
                             token=self._get_page_token(), **params)

    # ------------------------------------------------------------------
    # Instagram (Content Publishing)
    # ------------------------------------------------------------------
    def _wait_container(self, container_id: str, timeout_s: int = 90) -> None:
        """Attende che un container IG sia pronto (status FINISHED)."""
        import time

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            status = self._request("GET", f"/{container_id}",
                                   fields="status_code").get("status_code")
            if status == "FINISHED":
                return
            if status == "ERROR":
                raise MetaError(400, {"error": {"message":
                    f"container {container_id} in stato ERROR"}})
            time.sleep(3)
        raise MetaError(408, {"error": {"message":
            f"container {container_id} non pronto entro {timeout_s}s"}})

    def ig_post(self, image_url: str, caption: str = "") -> dict[str, Any]:
        """Post singolo su Instagram Business (immagine da URL pubblico)."""
        if not self.ig_user_id:
            raise RuntimeError("META_IG_USER_ID non impostato (usa discover())")
        container = self._request("POST", f"/{self.ig_user_id}/media",
                                  image_url=image_url, caption=caption)
        self._wait_container(container["id"])
        return self._request("POST", f"/{self.ig_user_id}/media_publish",
                             creation_id=container["id"])

    def ig_carousel(self, image_urls: list[str], caption: str = "") -> dict[str, Any]:
        """Carosello Instagram (2-10 immagini da URL pubblici)."""
        if not self.ig_user_id:
            raise RuntimeError("META_IG_USER_ID non impostato (usa discover())")
        if not 2 <= len(image_urls) <= 10:
            raise ValueError("Il carosello richiede da 2 a 10 immagini")
        children = []
        for url in image_urls:
            c = self._request("POST", f"/{self.ig_user_id}/media",
                              image_url=url, is_carousel_item="true")
            children.append(c["id"])
        for child in children:
            self._wait_container(child)
        container = self._request("POST", f"/{self.ig_user_id}/media",
                                  media_type="CAROUSEL",
                                  children=",".join(children), caption=caption)
        self._wait_container(container["id"])
        return self._request("POST", f"/{self.ig_user_id}/media_publish",
                             creation_id=container["id"])

    def close(self) -> None:
        self._client.close()
