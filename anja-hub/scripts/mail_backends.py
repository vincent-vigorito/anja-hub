"""mail_backends.py — F-Mail: backend gmail_api (REST, stdlib) e imap (imaplib/smtplib).

Modello messaggio unificato (l'agente non sa quale backend c'è sotto):
  {id, thread_id, mailbox, from, to, cc, date, subject, snippet,
   labels, unread, has_attachments}

Usato da scripts/mcp_mail_server.py (tool read/draft) e da webapp/server.py
(invio post-approvazione dall'outbox). Stdlib only: il token OAuth json ha
client_id/client_secret/refresh_token → refresh via oauth2.googleapis.com.
"""

from __future__ import annotations

import base64
import email
import email.header
import email.utils
import imaplib
import json
import os
import smtplib
import time
import urllib.parse
import urllib.request
from email.mime.text import MIMEText
from pathlib import Path

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"
FULL_BODY_CAP = 20_000


# ================================================================ gmail_api

class GmailBackend:
    def __init__(self, token_path: Path):
        self.token_path = Path(token_path)
        self._access = ""
        self._exp = 0.0

    # ---- auth -----------------------------------------------------------
    def _token(self) -> str:
        if self._access and time.time() < self._exp - 60:
            return self._access
        tok = json.loads(self.token_path.read_text(encoding="utf-8"))
        body = urllib.parse.urlencode({
            "client_id": tok["client_id"], "client_secret": tok["client_secret"],
            "refresh_token": tok["refresh_token"], "grant_type": "refresh_token",
        }).encode()
        req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode())
        self._access = data["access_token"]
        self._exp = time.time() + int(data.get("expires_in", 3600))
        return self._access

    def _call(self, path: str, *, method: str = "GET", payload: dict | None = None) -> dict:
        url = f"{GMAIL_API}/{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self._token()}",
            **({"Content-Type": "application/json"} if data else {}),
        })
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
        return json.loads(raw) if raw else {}

    # ---- read -----------------------------------------------------------
    def profile(self) -> dict:
        return self._call("profile")

    def search(self, query: str, max_results: int = 20) -> list[dict]:
        q = urllib.parse.quote(query or "")
        res = self._call(f"messages?maxResults={max_results}&q={q}")
        out = []
        for ref in res.get("messages", []):
            out.append(self.get(ref["id"], format="summary"))
        return out

    def get(self, msg_id: str, format: str = "summary") -> dict:
        fmt = "metadata" if format == "summary" else "full"
        extra = "&metadataHeaders=From&metadataHeaders=To&metadataHeaders=Cc&metadataHeaders=Subject&metadataHeaders=Date" if fmt == "metadata" else ""
        m = self._call(f"messages/{msg_id}?format={fmt}{extra}")
        item = self._to_model(m)
        if format in ("text", "full"):
            item["body"] = _gmail_body_text(m.get("payload") or {})[:FULL_BODY_CAP]
        return item

    def thread(self, thread_id: str) -> list[dict]:
        t = self._call(f"threads/{thread_id}?format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date")
        return [self._to_model(m) for m in t.get("messages", [])]

    def labels(self) -> list[str]:
        return sorted(l.get("name", "") for l in self._call("labels").get("labels", []))

    def _to_model(self, m: dict) -> dict:
        headers = {h["name"].lower(): h["value"]
                   for h in ((m.get("payload") or {}).get("headers") or [])}
        labels = m.get("labelIds") or []
        return {
            "id": m.get("id", ""), "thread_id": m.get("threadId", ""),
            "from": headers.get("from", ""), "to": headers.get("to", ""),
            "cc": headers.get("cc", ""), "date": headers.get("date", ""),
            "subject": headers.get("subject", ""),
            "snippet": (m.get("snippet") or "")[:200],
            "labels": [l for l in labels if not l.startswith("CATEGORY_")],
            "unread": "UNREAD" in labels,
            "has_attachments": _gmail_has_attachments(m.get("payload") or {}),
        }

    # ---- write ----------------------------------------------------------
    def create_draft(self, to: list[str], subject: str, body: str,
                     cc: list[str] | None = None, reply_to_id: str = "") -> dict:
        raw, thread_id = self._mime(to, subject, body, cc, reply_to_id)
        payload: dict = {"message": {"raw": raw}}
        if thread_id:
            payload["message"]["threadId"] = thread_id
        d = self._call("drafts", method="POST", payload=payload)
        return {"draft_id": d.get("id", ""), "message_id": (d.get("message") or {}).get("id", "")}

    def send(self, to: list[str], subject: str, body: str,
             cc: list[str] | None = None, reply_to_id: str = "",
             draft_id: str = "") -> str:
        if draft_id:
            r = self._call(f"drafts/{draft_id}/send", method="POST", payload={})
            return r.get("id", "")
        raw, thread_id = self._mime(to, subject, body, cc, reply_to_id)
        payload = {"raw": raw}
        if thread_id:
            payload["threadId"] = thread_id
        r = self._call("messages/send", method="POST", payload=payload)
        return r.get("id", "")

    def modify(self, msg_id: str, add_labels: list[str] | None = None,
               remove_labels: list[str] | None = None) -> dict:
        r = self._call(f"messages/{msg_id}/modify", method="POST", payload={
            "addLabelIds": add_labels or [], "removeLabelIds": remove_labels or []})
        return {"id": r.get("id", ""), "labels": r.get("labelIds", [])}

    def _mime(self, to, subject, body, cc, reply_to_id) -> tuple[str, str]:
        msg = MIMEText(body, "plain", "utf-8")
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["Subject"] = subject
        thread_id = ""
        if reply_to_id:
            try:
                orig = self._call(f"messages/{reply_to_id}?format=metadata&metadataHeaders=Message-ID")
                hdrs = {h["name"].lower(): h["value"]
                        for h in ((orig.get("payload") or {}).get("headers") or [])}
                mid = hdrs.get("message-id", "")
                if mid:
                    msg["In-Reply-To"] = mid
                    msg["References"] = mid
                thread_id = orig.get("threadId", "")
            except Exception:
                pass
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        return raw, thread_id


def _gmail_body_text(payload: dict) -> str:
    """Estrae il text/plain (fallback: html spogliato grezzo) dal payload Gmail."""
    def _walk(p) -> str:
        mime = p.get("mimeType", "")
        if mime == "text/plain" and (p.get("body") or {}).get("data"):
            return base64.urlsafe_b64decode(p["body"]["data"] + "==").decode("utf-8", "replace")
        for part in p.get("parts") or []:
            t = _walk(part)
            if t:
                return t
        if mime == "text/html" and (p.get("body") or {}).get("data"):
            import re as _re
            html = base64.urlsafe_b64decode(p["body"]["data"] + "==").decode("utf-8", "replace")
            return _re.sub(r"<[^>]+>", " ", html)
        return ""
    return _walk(payload).strip()


def _gmail_has_attachments(payload: dict) -> bool:
    for part in payload.get("parts") or []:
        if part.get("filename"):
            return True
        if _gmail_has_attachments(part):
            return True
    return False


# ================================================================ imap

class ImapBackend:
    """IMAP (lettura, draft via APPEND) + SMTP (invio). Config dal registro,
    creds da creds.env. Connessioni per-chiamata: niente stato tra tool call."""

    def __init__(self, imap_cfg: dict, smtp_cfg: dict, creds: dict):
        self.imap_cfg = imap_cfg or {}
        self.smtp_cfg = smtp_cfg or {}
        self.creds = creds or {}

    def _imap(self) -> imaplib.IMAP4:
        host = self.imap_cfg.get("host", "")
        port = int(self.imap_cfg.get("port") or 993)
        conn = (imaplib.IMAP4_SSL(host, port) if self.imap_cfg.get("ssl", True)
                else imaplib.IMAP4(host, port))
        conn.login(self.creds.get("MAIL_USER", ""), self.creds.get("MAIL_PASS", ""))
        return conn

    def test(self) -> dict:
        """Login IMAP + connessione SMTP (per la card 'collegata')."""
        conn = self._imap()
        try:
            conn.select("INBOX", readonly=True)
        finally:
            try:
                conn.logout()
            except Exception:
                pass
        if self.smtp_cfg.get("host"):
            with self._smtp() as s:
                s.noop()
        return {"ok": True}

    def _smtp(self) -> smtplib.SMTP:
        host = self.smtp_cfg.get("host", "")
        port = int(self.smtp_cfg.get("port") or 587)
        if self.smtp_cfg.get("ssl"):
            s = smtplib.SMTP_SSL(host, port, timeout=20)
        else:
            s = smtplib.SMTP(host, port, timeout=20)
            if self.smtp_cfg.get("tls", True):
                s.starttls()
        user = self.creds.get("SMTP_USER") or self.creds.get("MAIL_USER", "")
        pwd = self.creds.get("SMTP_PASS") or self.creds.get("MAIL_PASS", "")
        if user and pwd:
            s.login(user, pwd)
        return s

    # ---- read -----------------------------------------------------------
    def search(self, query: str, max_results: int = 20, folder: str = "INBOX") -> list[dict]:
        """Traduzione best-effort della sintassi Gmail → IMAP SEARCH."""
        crit = _imap_criteria(query)
        conn = self._imap()
        try:
            conn.select(folder, readonly=True)
            _, data = conn.search(None, *crit)
            ids = (data[0] or b"").split()
            out = []
            for mid in reversed(ids[-max_results:]):
                out.append(self._fetch_summary(conn, mid, folder))
            return out
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def get(self, msg_id: str, format: str = "summary", folder: str = "INBOX") -> dict:
        conn = self._imap()
        try:
            conn.select(folder, readonly=True)
            item = self._fetch_summary(conn, msg_id.encode(), folder)
            if format in ("text", "full"):
                _, data = conn.fetch(msg_id.encode(), "(BODY.PEEK[])")
                raw = data[0][1] if data and data[0] else b""
                msg = email.message_from_bytes(raw)
                item["body"] = _imap_body_text(msg)[:FULL_BODY_CAP]
            return item
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def _fetch_summary(self, conn, mid: bytes, folder: str) -> dict:
        _, data = conn.fetch(mid, "(FLAGS BODYSTRUCTURE BODY.PEEK[HEADER.FIELDS (FROM TO CC DATE SUBJECT MESSAGE-ID)])")
        raw = b""
        flags = b""
        for part in data or []:
            if isinstance(part, tuple):
                flags = part[0] or b""
                raw = part[1] or b""
        msg = email.message_from_bytes(raw)
        return {
            "id": mid.decode(), "thread_id": "", "folder": folder,
            "from": _dec(msg.get("From", "")), "to": _dec(msg.get("To", "")),
            "cc": _dec(msg.get("Cc", "")), "date": msg.get("Date", ""),
            "subject": _dec(msg.get("Subject", "")), "snippet": "",
            "labels": [folder], "unread": b"\\Seen" not in flags,
            "has_attachments": b"attachment" in flags.lower() if flags else False,
        }

    def labels(self) -> list[str]:
        conn = self._imap()
        try:
            _, data = conn.list()
            out = []
            for line in data or []:
                if isinstance(line, bytes):
                    name = line.decode(errors="replace").rsplit(' "', 1)[-1].strip('" ')
                    out.append(name)
            return sorted(out)
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    # ---- write ----------------------------------------------------------
    def create_draft(self, to: list[str], subject: str, body: str,
                     cc: list[str] | None = None, reply_to_id: str = "") -> dict:
        msg = self._mime(to, subject, body, cc)
        conn = self._imap()
        try:
            for folder in ("Drafts", "INBOX.Drafts", "Bozze"):
                try:
                    typ, _ = conn.append(folder, r"(\Draft)",
                                         imaplib.Time2Internaldate(time.time()), msg.as_bytes())
                    if typ == "OK":
                        return {"draft_id": "", "folder": folder}
                except Exception:
                    continue
            return {"error": "no Drafts folder found"}
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def send(self, to: list[str], subject: str, body: str,
             cc: list[str] | None = None, reply_to_id: str = "",
             draft_id: str = "") -> str:
        msg = self._mime(to, subject, body, cc)
        with self._smtp() as s:
            s.send_message(msg)
        return msg["Message-ID"] or ""

    def modify(self, msg_id: str, add_labels=None, remove_labels=None,
               folder: str = "INBOX") -> dict:
        """mark_read/unread via flag \\Seen (i 'labels' IMAP sono folder: no move in v1)."""
        conn = self._imap()
        try:
            conn.select(folder)
            if add_labels and "READ" in [l.upper() for l in add_labels]:
                conn.store(msg_id.encode(), "+FLAGS", r"(\Seen)")
            if remove_labels and "READ" in [l.upper() for l in remove_labels]:
                conn.store(msg_id.encode(), "-FLAGS", r"(\Seen)")
            return {"id": msg_id}
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def _mime(self, to, subject, body, cc):
        msg = MIMEText(body, "plain", "utf-8")
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["Subject"] = subject
        msg["From"] = self.creds.get("MAIL_FROM") or self.creds.get("MAIL_USER", "")
        msg["Message-ID"] = email.utils.make_msgid()
        msg["Date"] = email.utils.formatdate(localtime=True)
        return msg


def _dec(value: str) -> str:
    """Decodifica header RFC2047 (=?utf-8?...?=)."""
    try:
        parts = email.header.decode_header(value)
        return "".join(p.decode(c or "utf-8", "replace") if isinstance(p, bytes) else p
                       for p, c in parts)
    except Exception:
        return value


def _imap_body_text(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                return part.get_payload(decode=True).decode(
                    part.get_content_charset() or "utf-8", "replace")
        for part in msg.walk():
            if part.get_content_type() == "text/html" and not part.get_filename():
                import re as _re
                html = part.get_payload(decode=True).decode(
                    part.get_content_charset() or "utf-8", "replace")
                return _re.sub(r"<[^>]+>", " ", html)
        return ""
    payload = msg.get_payload(decode=True)
    return payload.decode(msg.get_content_charset() or "utf-8", "replace") if payload else ""


def _imap_criteria(query: str) -> list[str]:
    """Sintassi Gmail → criteri IMAP SEARCH (best-effort):
    is:unread → UNSEEN · from:x → FROM x · subject:x → SUBJECT x ·
    newer_than:Nd → SINCE <data> · resto → TEXT."""
    crit: list[str] = []
    free: list[str] = []
    for tok in (query or "").split():
        low = tok.lower()
        if low == "is:unread":
            crit.append("UNSEEN")
        elif low == "is:read":
            crit.append("SEEN")
        elif low.startswith("from:"):
            crit += ["FROM", tok[5:]]
        elif low.startswith("to:"):
            crit += ["TO", tok[3:]]
        elif low.startswith("subject:"):
            crit += ["SUBJECT", tok[8:]]
        elif low.startswith("newer_than:") and low.endswith("d"):
            try:
                days = int(low[11:-1])
                since = time.strftime("%d-%b-%Y", time.gmtime(time.time() - days * 86400))
                crit += ["SINCE", since]
            except ValueError:
                free.append(tok)
        else:
            free.append(tok)
    if free:
        crit += ["TEXT", " ".join(free)]
    return crit or ["ALL"]


# ================================================================ factory

def backend_for(hub: Path, box: dict):
    """Istanzia il backend giusto per un record casella del registro."""
    import sys
    webapp = Path(__file__).resolve().parent.parent / "webapp"
    if str(webapp) not in sys.path:
        sys.path.insert(0, str(webapp))
    import mail_store
    hub = Path(hub)
    if box.get("kind") == "gmail":
        return GmailBackend(mail_store.secrets_dir(hub, box["id"]) / "google-token.json")
    creds = mail_store.load_imap_creds(hub, box["id"])
    return ImapBackend(box.get("imap") or {}, box.get("smtp") or {}, creds)
