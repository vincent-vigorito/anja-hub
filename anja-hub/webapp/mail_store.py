"""mail_store.py — F-Mail: registro caselle, binding per scope, outbox two-phase.

Una casella è una connessione di livello hub (config/mailboxes.json, NESSUN
segreto dentro: token OAuth e creds IMAP vivono in .anjawiki/mail/<id>/, 0600).
Hub e workspace si AGGANCIANO alle caselle registrate — nessuna risalita
implicita ws→hub. L'invio è two-phase: mail_send scrive un pending nell'outbox
(data/mail_outbox.json), l'approvazione (Telegram mact:/UI) fa inviare il server.

Stdlib only. Design: anja-mail-design.md (repo ops).
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path

REGISTRY_NAME = "mailboxes.json"
OUTBOX_NAME = "mail_outbox.json"
SECRETS_DIRNAME = "mail"           # <hub>/.anjawiki/mail/<id>/
OUTBOX_TTL_SEC = 24 * 3600         # pending più vecchi → expired
VALID_KINDS = ("gmail", "imap")
VALID_POLICIES = ("ask", "auto", "deny")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


# ---------------------------------------------------------------- registry

def registry_path(hub: Path) -> Path:
    return Path(hub) / "config" / REGISTRY_NAME


def secrets_dir(hub: Path, mailbox_id: str) -> Path:
    return Path(hub) / ".anjawiki" / SECRETS_DIRNAME / mailbox_id


def list_mailboxes(hub: Path) -> list[dict]:
    p = registry_path(hub)
    if not p.is_file():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("mailboxes", [])
    except Exception:
        return []


def get_mailbox(hub: Path, mailbox_id: str) -> dict | None:
    return next((m for m in list_mailboxes(hub) if m.get("id") == mailbox_id), None)


def _save_registry(hub: Path, boxes: list[dict]) -> None:
    p = registry_path(hub)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"mailboxes": boxes}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, p)


def upsert_mailbox(hub: Path, box: dict) -> dict:
    """Crea/aggiorna un record casella. Valida id/kind; RIFIUTA chiavi segreto
    (il registro non contiene mai credenziali)."""
    mid = str(box.get("id") or "").strip()
    if not _ID_RE.match(mid):
        raise ValueError(f"mailbox id '{mid}' must be kebab-case (max 32 chars)")
    kind = box.get("kind")
    if kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {VALID_KINDS}")
    for k in ("password", "pass", "token", "secret", "client_secret", "refresh_token"):
        if k in box or any(k in (box.get(s) or {}) for s in ("imap", "smtp")):
            raise ValueError(f"secrets do not belong in the registry (found '{k}')")
    caps = box.get("capabilities") or (["read", "draft", "send", "modify"] if kind == "gmail"
                                       else ["read", "draft", "send"])
    rec = {"id": mid, "label": str(box.get("label") or mid), "kind": kind,
           "address": str(box.get("address") or ""), "capabilities": caps,
           "created": box.get("created") or time.strftime("%Y-%m-%d")}
    if kind == "imap":
        rec["imap"] = box.get("imap") or {}
        rec["smtp"] = box.get("smtp") or {}
    boxes = [m for m in list_mailboxes(hub) if m.get("id") != mid]
    boxes.append(rec)
    boxes.sort(key=lambda m: m["id"])
    _save_registry(hub, boxes)
    return rec


def remove_mailbox(hub: Path, mailbox_id: str) -> bool:
    """Toglie la casella dal registro + cancella la dir segreti. Ritorna True se esisteva."""
    boxes = list_mailboxes(hub)
    kept = [m for m in boxes if m.get("id") != mailbox_id]
    if len(kept) == len(boxes):
        return False
    _save_registry(hub, kept)
    sd = secrets_dir(hub, mailbox_id)
    if sd.is_dir():
        import shutil
        shutil.rmtree(sd, ignore_errors=True)
    return True


def mailbox_connected(hub: Path, box: dict) -> bool:
    """La casella ha le credenziali sul disco? (gmail: token OAuth; imap: creds.env)"""
    sd = secrets_dir(hub, box.get("id", ""))
    if box.get("kind") == "gmail":
        return (sd / "google-token.json").is_file()
    return (sd / "creds.env").is_file()


def load_imap_creds(hub: Path, mailbox_id: str) -> dict:
    """Parse di .anjawiki/mail/<id>/creds.env (MAIL_USER, MAIL_PASS, SMTP_USER, SMTP_PASS)."""
    p = secrets_dir(hub, mailbox_id) / "creds.env"
    out: dict = {}
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip("'\"")
    return out


def save_imap_creds(hub: Path, mailbox_id: str, creds: dict) -> None:
    sd = secrets_dir(hub, mailbox_id)
    sd.mkdir(parents=True, exist_ok=True)
    p = sd / "creds.env"
    body = "".join(f"{k}={v}\n" for k, v in creds.items() if v)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(body)


# ---------------------------------------------------------------- binding

def scope_binding(hub: Path, scope: str) -> dict:
    """{mailboxes: [...], send_policy} per lo scope ('hub' | 'project:<ws>').
    Solo caselle ESISTENTI nel registro (un binding stantio non monta niente)."""
    hub = Path(hub)
    raw: dict = {}
    if scope == "hub":
        cfg = hub / "config" / "config.json"
        try:
            raw = (json.loads(cfg.read_text(encoding="utf-8")) or {}).get("mail", {})
        except Exception:
            raw = {}
    elif scope.startswith("project:"):
        ws = scope.split(":", 1)[1]
        p = hub / "workspaces" / ws / ".anjawiki" / "mail.json"
        try:
            raw = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        except Exception:
            raw = {}
    known = {m["id"] for m in list_mailboxes(hub)}
    ids = [i for i in (raw.get("mailboxes") or []) if i in known]
    policy = raw.get("send_policy") if raw.get("send_policy") in VALID_POLICIES else "ask"
    return {"mailboxes": ids, "send_policy": policy}


def set_scope_binding(hub: Path, scope: str, mailboxes: list[str],
                      send_policy: str = "ask") -> dict:
    hub = Path(hub)
    if send_policy not in VALID_POLICIES:
        raise ValueError(f"send_policy must be one of {VALID_POLICIES}")
    known = {m["id"] for m in list_mailboxes(hub)}
    unknown = [i for i in mailboxes if i not in known]
    if unknown:
        raise ValueError(f"unknown mailboxes: {unknown}")
    data = {"mailboxes": list(mailboxes), "send_policy": send_policy}
    if scope == "hub":
        cfg = hub / "config" / "config.json"
        obj = {}
        try:
            obj = json.loads(cfg.read_text(encoding="utf-8")) or {}
        except Exception:
            obj = {}
        obj["mail"] = data
        tmp = cfg.with_suffix(".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, cfg)
    elif scope.startswith("project:"):
        ws = scope.split(":", 1)[1]
        p = hub / "workspaces" / ws / ".anjawiki" / "mail.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    else:
        raise ValueError(f"invalid scope '{scope}'")
    return data


# ---------------------------------------------------------------- outbox

def _outbox_path(hub: Path) -> Path:
    return Path(hub) / "data" / OUTBOX_NAME


def _load_outbox(hub: Path) -> list[dict]:
    p = _outbox_path(hub)
    if not p.is_file():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("items", [])
    except Exception:
        return []


def _save_outbox(hub: Path, items: list[dict]) -> None:
    p = _outbox_path(hub)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, p)


def _expire(items: list[dict]) -> list[dict]:
    now = time.time()
    for it in items:
        if it.get("status") == "pending" and now - it.get("created_ts", now) > OUTBOX_TTL_SEC:
            it["status"] = "expired"
    return items


def outbox_add(hub: Path, *, scope: str, mailbox: str, to: list[str], subject: str,
               body: str, cc: list[str] | None = None, reply_to_id: str = "",
               draft_id: str = "", status: str = "pending") -> dict:
    """Accoda un invio. Il BODY completo resta nell'outbox (serve per inviare
    all'approve) ma le liste per UI/Telegram espongono solo la preview."""
    item = {
        "id": uuid.uuid4().hex[:12], "scope": scope, "mailbox": mailbox,
        "to": list(to), "cc": list(cc or []), "subject": subject, "body": body,
        "reply_to_id": reply_to_id, "draft_id": draft_id,
        "status": status, "created_ts": time.time(),
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "resolved_by": "", "provider_message_id": "",
    }
    items = _expire(_load_outbox(hub))
    items.append(item)
    _save_outbox(hub, items)
    return item


def outbox_list(hub: Path, *, scope: str = "", status: str = "") -> list[dict]:
    """Vista SENZA body: solo preview (i corpi non girano per UI/log/Telegram)."""
    items = _expire(_load_outbox(hub))
    _save_outbox(hub, items)
    out = []
    for it in items:
        if scope and it.get("scope") != scope:
            continue
        if status and it.get("status") != status:
            continue
        pub = {k: v for k, v in it.items() if k != "body"}
        pub["body_preview"] = (it.get("body") or "")[:160]
        out.append(pub)
    out.sort(key=lambda x: x.get("created_ts", 0), reverse=True)
    return out


def outbox_get(hub: Path, outbox_id: str) -> dict | None:
    return next((it for it in _load_outbox(hub) if it.get("id") == outbox_id), None)


def outbox_resolve(hub: Path, outbox_id: str, action: str, by: str = "") -> dict | None:
    """approve|reject su un pending. Ritorna l'item COMPLETO (col body: serve al
    chiamante per inviare) o None se non pending/inesistente. Non invia: l'invio
    è del chiamante (server), che poi chiama outbox_mark."""
    if action not in ("approve", "reject"):
        raise ValueError("action must be approve|reject")
    items = _expire(_load_outbox(hub))
    for it in items:
        if it.get("id") == outbox_id and it.get("status") == "pending":
            it["status"] = "approved" if action == "approve" else "rejected"
            it["resolved_by"] = by
            it["resolved"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            _save_outbox(hub, items)
            return dict(it)
    _save_outbox(hub, items)
    return None


def outbox_unnotified(hub: Path) -> list[dict]:
    """Pending mai notificati (per il watcher del webapp: Telegram + bell UI).
    Item COMPLETI (col body: la notifica mostra solo la preview, ma il chiamante
    decide)."""
    items = _expire(_load_outbox(hub))
    _save_outbox(hub, items)
    return [dict(it) for it in items
            if it.get("status") == "pending" and not it.get("notified")]


def outbox_mark_notified(hub: Path, outbox_id: str, message_ref: str = "") -> None:
    items = _load_outbox(hub)
    for it in items:
        if it.get("id") == outbox_id:
            it["notified"] = True
            if message_ref:
                it["notify_ref"] = message_ref
    _save_outbox(hub, items)


def outbox_mark(hub: Path, outbox_id: str, status: str,
                provider_message_id: str = "", error: str = "") -> None:
    """Esito dell'invio post-approve: sent | error."""
    items = _load_outbox(hub)
    for it in items:
        if it.get("id") == outbox_id:
            it["status"] = status
            if provider_message_id:
                it["provider_message_id"] = provider_message_id
            if error:
                it["error"] = error[:300]
    _save_outbox(hub, items)
