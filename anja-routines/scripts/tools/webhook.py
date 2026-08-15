"""
webhook.py — POST generico HTTP/JSON a un URL arbitrario.

Config:
    type: webhook
    url: "https://..."           (obbligatorio)
    method: POST                 (default POST; supporta GET, PUT)
    payload_field: text          (chiave del JSON dove mettere il body, default 'text')
    payload_template: |          (alternativa: template JSON con {body})
        {"text": "{body}", "channel": "..."}
    headers:                     (opzionale)
        Authorization: "Bearer {{TOKEN}}"
    title: optional              (prepend al body)

Compatibile sia con webhook generici che con incoming webhook di servizi
tipo Google Chat, Slack, Discord (gli endpoint che accettano `{"text": ...}`).
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.error
import json
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


WEBHOOK_BODY_LIMIT = 8000  # safe limit per la maggior parte dei servizi


import contextlib
import threading

_DNS_PIN_LOCK = threading.Lock()


def _ssrf_check(url: str):
    """SSRF guard con pin: ((host, ip_validato), None) se sicuro, altrimenti (None, msg).
    Un webhook con secret interpolati ({{VAR}}) verso un host interno esfiltrerebbe le
    chiavi dell'hub → si sceglie il primo IP pubblico e ci si connette a QUELLO."""
    host = urlsplit(url).hostname
    if not host:
        return None, "URL senza host"
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception as e:
        return None, f"host non risolvibile: {e}"
    chosen = None
    for info in infos:
        ip = info[4][0]
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return None, f"IP non valido: {ip}"
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            return None, f"destinazione interna non consentita ({addr})"
        if chosen is None:
            chosen = ip
    return (host, chosen), None


def _ssrf_blocked(url: str):
    """Compat: messaggio d'errore se l'URL non è sicuro, altrimenti None."""
    _, err = _ssrf_check(url)
    return err


@contextlib.contextmanager
def _pin_dns(hostname: str, ip: str):
    """Forza hostname→ip per la durata del blocco (anti DNS-rebinding tra check e connect)."""
    real = socket.getaddrinfo

    def pinned(host, *a, **k):
        return real(ip, *a, **k) if host == hostname else real(host, *a, **k)

    with _DNS_PIN_LOCK:
        socket.getaddrinfo = pinned
        try:
            yield
        finally:
            socket.getaddrinfo = real


class _SSRFRedirectGuard(urllib.request.HTTPRedirectHandler):
    """Ri-applica il guard a ogni redirect (un URL pubblico può rimbalzare su un IP interno)."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        blocked = _ssrf_blocked(newurl)
        if blocked:
            raise urllib.error.HTTPError(newurl, code, blocked, headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def send_webhook(cfg: dict, body: str, hub: Path) -> dict:
    url = cfg.get("url")
    if not url or url.startswith("{{"):
        return {"status": "failed", "details": "missing url (or unexpanded secret)"}

    if not (str(url).startswith("http://") or str(url).startswith("https://")):
        return {"status": "failed", "details": "url must be http(s)"}
    ok, err = _ssrf_check(url)
    if err:
        return {"status": "failed", "details": f"SSRF guard: {err}"}
    pin_host, pin_ip = ok

    method = (cfg.get("method") or "POST").upper()
    headers = cfg.get("headers") or {}
    if not isinstance(headers, dict):
        return {"status": "failed", "details": "headers must be an object"}

    title = cfg.get("title")
    text = f"*{title}*\n{body}" if title else body
    if len(text) > WEBHOOK_BODY_LIMIT:
        text = text[: WEBHOOK_BODY_LIMIT - 80] + "\n\n…(truncated)"

    payload_template = cfg.get("payload_template")
    payload_field = cfg.get("payload_field", "text")

    if payload_template:
        try:
            payload_str = payload_template.replace("{body}", json.dumps(text)[1:-1])
            payload_obj = json.loads(payload_str)
        except Exception as e:
            return {"status": "failed", "details": f"invalid payload_template: {e}"}
    else:
        payload_obj = {payload_field: text}

    data = json.dumps(payload_obj).encode("utf-8")
    final_headers = {"Content-Type": "application/json; charset=UTF-8"}
    final_headers.update(headers)

    req = urllib.request.Request(url, data=data if method != "GET" else None, headers=final_headers, method=method)
    opener = urllib.request.build_opener(_SSRFRedirectGuard())
    try:
        with _pin_dns(pin_host, pin_ip), opener.open(req, timeout=15) as resp:
            code = resp.getcode()
            resp_body = resp.read(512).decode("utf-8", errors="replace")
            if 200 <= code < 300:
                return {"status": "success", "details": f"http {code} · {len(text)} chars"}
            return {"status": "failed", "details": f"http {code}: {resp_body[:200]}"}
    except urllib.error.HTTPError as e:
        return {"status": "failed", "details": f"http {e.code}: {e.reason}"}
    except Exception as e:
        return {"status": "failed", "details": f"{type(e).__name__}: {e}"}
