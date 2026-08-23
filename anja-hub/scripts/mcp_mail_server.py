#!/usr/bin/env python3
"""mcp_mail_server.py — F-Mail — MCP server `anja_mail`.

Caselle di posta per hub e workspace, provider-agnostico (gmail_api | imap).
Monta SOLO le caselle di ANJA_MAILBOXES (binding dello scope): un workspace non
può nominare caselle altrui. `mail.send` NON invia: scrive un pending nell'outbox
(two-phase, approvazione Telegram/UI) — a meno che ANJA_MAIL_SEND_POLICY=auto.

Env: ANJA_HUB, ANJA_MAILBOXES (csv id), ANJA_TOOL_GROUPS (mail_read[,mail_write]),
     ANJA_SCOPE_NAME (default hub), ANJA_MAIL_SEND_POLICY (ask|auto|deny).

Nomi flat sul wire (mail_search) come gli altri server; tools/call accetta entrambi.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
WEBAPP = SCRIPTS.parent / "webapp"
for p in (str(SCRIPTS), str(WEBAPP)):
    if p not in sys.path:
        sys.path.insert(0, p)

import mail_backends  # noqa: E402
import mail_store     # noqa: E402

PROTO_VERSION = "2024-11-05"
SERVER_NAME = "anja_mail"
SERVER_VERSION = "0.1.0"

HUB = Path(os.environ.get("ANJA_HUB", os.getcwd())).resolve()
MAILBOX_IDS = [s.strip() for s in os.environ.get("ANJA_MAILBOXES", "").split(",") if s.strip()]
TOOL_GROUPS = {s.strip() for s in os.environ.get("ANJA_TOOL_GROUPS", "mail_read").split(",") if s.strip()}
SCOPE_NAME = os.environ.get("ANJA_SCOPE_NAME", "hub")
SEND_POLICY = os.environ.get("ANJA_MAIL_SEND_POLICY", "ask")

_BACKENDS: dict = {}


def _boxes() -> list[dict]:
    return [m for m in mail_store.list_mailboxes(HUB) if m["id"] in MAILBOX_IDS]


def _box(mailbox: str = "") -> dict:
    boxes = _boxes()
    if not boxes:
        raise ValueError("no mailboxes bound to this scope")
    if not mailbox:
        return boxes[0]
    b = next((m for m in boxes if m["id"] == mailbox), None)
    if not b:
        raise ValueError(f"mailbox '{mailbox}' not bound to this scope "
                         f"(available: {[m['id'] for m in boxes]})")
    return b


def _backend(box: dict):
    if box["id"] not in _BACKENDS:
        _BACKENDS[box["id"]] = mail_backends.backend_for(HUB, box)
    return _BACKENDS[box["id"]]


# ---------------------------------------------------------------- tools

def tool_mailboxes(args: dict) -> dict:
    out = []
    for b in _boxes():
        info = {"id": b["id"], "label": b.get("label", ""), "kind": b["kind"],
                "address": b.get("address", ""), "capabilities": b.get("capabilities", [])}
        try:
            unread = _backend(b).search("is:unread newer_than:7d", max_results=1)
            info["reachable"] = True
        except Exception as e:
            info["reachable"] = False
            info["error"] = str(e)[:120]
        out.append(info)
    return {"mailboxes": out, "send_policy": SEND_POLICY}


def tool_search(args: dict) -> dict:
    box = _box(args.get("mailbox", ""))
    msgs = _backend(box).search(args.get("query", ""), int(args.get("max", 20)))
    for m in msgs:
        m["mailbox"] = box["id"]
    return {"mailbox": box["id"], "count": len(msgs), "messages": msgs}


def tool_get(args: dict) -> dict:
    box = _box(args.get("mailbox", ""))
    fmt = args.get("format", "text")
    if fmt not in ("summary", "text", "full"):
        fmt = "text"
    m = _backend(box).get(str(args.get("id", "")), format=fmt)
    m["mailbox"] = box["id"]
    return m


def tool_thread(args: dict) -> dict:
    box = _box(args.get("mailbox", ""))
    be = _backend(box)
    if not hasattr(be, "thread"):
        return {"error": "threads not supported on this backend (imap)"}
    msgs = be.thread(str(args.get("id", "")))
    return {"mailbox": box["id"], "count": len(msgs), "messages": msgs}


def tool_labels(args: dict) -> dict:
    box = _box(args.get("mailbox", ""))
    return {"mailbox": box["id"], "labels": _backend(box).labels()}


def tool_draft(args: dict) -> dict:
    box = _box(args.get("mailbox", ""))
    if "draft" not in box.get("capabilities", []):
        return {"error": f"mailbox '{box['id']}' has no draft capability"}
    to = args.get("to") or []
    if isinstance(to, str):
        to = [to]
    res = _backend(box).create_draft(to, args.get("subject", ""), args.get("body", ""),
                                     cc=args.get("cc") or [],
                                     reply_to_id=args.get("reply_to_id", ""))
    return {"mailbox": box["id"], **res}


def tool_send(args: dict) -> dict:
    """Two-phase: accoda in outbox (pending) e ritorna. Con policy auto invia
    subito (comunque loggato in outbox). Policy deny → il tool non esiste."""
    box = _box(args.get("mailbox", ""))
    if "send" not in box.get("capabilities", []):
        return {"error": f"mailbox '{box['id']}' has no send capability"}
    to = args.get("to") or []
    if isinstance(to, str):
        to = [to]
    if not to:
        return {"error": "recipient list 'to' is empty"}
    cc = args.get("cc") or []
    if isinstance(cc, str):
        cc = [cc]
    subject = args.get("subject", "")
    body = args.get("body", "")
    if SEND_POLICY == "auto":
        mid = _backend(box).send(to, subject, body, cc=cc,
                                 reply_to_id=args.get("reply_to_id", ""),
                                 draft_id=args.get("draft_id", ""))
        item = mail_store.outbox_add(HUB, scope=SCOPE_NAME, mailbox=box["id"], to=to,
                                     subject=subject, body=body, cc=cc, status="sent")
        mail_store.outbox_mark(HUB, item["id"], "sent", provider_message_id=mid)
        return {"status": "sent", "outbox_id": item["id"], "provider_message_id": mid}
    item = mail_store.outbox_add(HUB, scope=SCOPE_NAME, mailbox=box["id"], to=to,
                                 subject=subject, body=body, cc=cc,
                                 reply_to_id=args.get("reply_to_id", ""),
                                 draft_id=args.get("draft_id", ""))
    # La notifica Telegram/UI la fa il watcher dell'outbox nel webapp (unico path).
    return {"status": "pending_approval", "outbox_id": item["id"],
            "note": "the message is queued; a human approves it from Telegram or the UI"}


def tool_modify(args: dict) -> dict:
    box = _box(args.get("mailbox", ""))
    add = list(args.get("add_labels") or [])
    rem = list(args.get("remove_labels") or [])
    if args.get("mark_read"):
        rem.append("UNREAD") if box["kind"] == "gmail" else add.append("READ")
    if args.get("archive") and box["kind"] == "gmail":
        rem.append("INBOX")
    return _backend(box).modify(str(args.get("id", "")), add_labels=add, remove_labels=rem)


def tool_outbox(args: dict) -> dict:
    items = mail_store.outbox_list(HUB, scope=SCOPE_NAME, status=args.get("status", ""))
    return {"scope": SCOPE_NAME, "count": len(items), "items": items[:30]}


# ---------------------------------------------------------------- registry

def _s(name, desc, props=None, req=None):
    return {"name": name, "description": desc,
            "inputSchema": {"type": "object", "properties": props or {},
                            **({"required": req} if req else {})}}


_MB = {"mailbox": {"type": "string", "description": "mailbox id (default: la prima dello scope)"}}

READ_TOOLS = [
    _s("mail.mailboxes", "Caselle di posta di questo scope: id, indirizzo, capabilities, raggiungibilità."),
    _s("mail.search", "Cerca messaggi. Gmail: sintassi nativa (is:unread from:x newer_than:2d); IMAP: traduzione best-effort.",
       {**_MB, "query": {"type": "string"}, "max": {"type": "integer", "default": 20}}, ["query"]),
    _s("mail.get", "Leggi un messaggio. format: summary|text|full (testo cap 20kB; allegati solo elencati).",
       {**_MB, "id": {"type": "string"}, "format": {"type": "string", "enum": ["summary", "text", "full"]}}, ["id"]),
    _s("mail.thread", "Messaggi del thread (solo Gmail).", {**_MB, "id": {"type": "string"}}, ["id"]),
    _s("mail.labels", "Etichette (Gmail) o cartelle (IMAP) della casella.", _MB),
    _s("mail.outbox", "Invii dello scope: pending/sent/rejected/expired (audit two-phase).",
       {"status": {"type": "string", "enum": ["pending", "sent", "rejected", "expired", "approved", "error"]}}),
]

WRITE_TOOLS = [
    _s("mail.draft", "Crea una BOZZA (non invia).",
       {**_MB, "to": {"type": "array", "items": {"type": "string"}}, "subject": {"type": "string"},
        "body": {"type": "string"}, "cc": {"type": "array", "items": {"type": "string"}},
        "reply_to_id": {"type": "string", "description": "id del messaggio a cui rispondere (thread)"}},
       ["to", "subject", "body"]),
    _s("mail.send", "Chiedi l'INVIO: la mail va in outbox e un umano la approva (Telegram/UI). "
       "Ritorna pending_approval + outbox_id — di' all'utente che è in attesa di approvazione.",
       {**_MB, "to": {"type": "array", "items": {"type": "string"}}, "subject": {"type": "string"},
        "body": {"type": "string"}, "cc": {"type": "array", "items": {"type": "string"}},
        "reply_to_id": {"type": "string"}, "draft_id": {"type": "string"}},
       ["to", "subject", "body"]),
    _s("mail.modify", "Etichette/stato: add_labels, remove_labels, mark_read, archive (Gmail).",
       {**_MB, "id": {"type": "string"}, "add_labels": {"type": "array", "items": {"type": "string"}},
        "remove_labels": {"type": "array", "items": {"type": "string"}},
        "mark_read": {"type": "boolean"}, "archive": {"type": "boolean"}}, ["id"]),
]

HANDLERS = {
    "mail.mailboxes": tool_mailboxes, "mail.search": tool_search, "mail.get": tool_get,
    "mail.thread": tool_thread, "mail.labels": tool_labels, "mail.outbox": tool_outbox,
    "mail.draft": tool_draft, "mail.send": tool_send, "mail.modify": tool_modify,
}

TOOLS = list(READ_TOOLS)
if "mail_write" in TOOL_GROUPS and SEND_POLICY != "deny":
    TOOLS += WRITE_TOOLS
elif "mail_write" in TOOL_GROUPS:                       # deny: draft sì, send no
    TOOLS += [t for t in WRITE_TOOLS if t["name"] != "mail.send"]

_ALLOWED = {t["name"] for t in TOOLS}


def _wire_name(name: str) -> str:
    return name.replace(".", "_")


_CANONICAL_BY_WIRE = {_wire_name(n): n for n in HANDLERS}


def _canonical_name(name: str) -> str:
    return _CANONICAL_BY_WIRE.get(name, name)


def handle_request(req: dict):
    method = req.get("method")
    params = req.get("params") or {}
    req_id = req.get("id")
    if method == "initialize":
        return _ok(req_id, {"protocolVersion": PROTO_VERSION,
                            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                            "capabilities": {"tools": {"listChanged": False}}})
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return _ok(req_id, {"tools": [{**t, "name": _wire_name(t["name"])} for t in TOOLS]})
    if method == "tools/call":
        name = _canonical_name(params.get("name") or "")
        if name not in _ALLOWED:
            return _err(req_id, -32601, f"unknown tool: {name}")
        try:
            result = HANDLERS[name](params.get("arguments") or {})
            content = [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]
            return _ok(req_id, {"content": content, "isError": isinstance(result, dict) and "error" in result})
        except Exception as e:
            return _err(req_id, -32603, f"tool '{name}' failed: {type(e).__name__}: {e}")
    if method == "ping":
        return _ok(req_id, {})
    return _err(req_id, -32601, f"method not found: {method}")


def _ok(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def main():
    print(f"[anja_mail] starting (scope={SCOPE_NAME} mailboxes={MAILBOX_IDS} "
          f"groups={sorted(TOOL_GROUPS)} policy={SEND_POLICY})", file=sys.stderr, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            sys.stdout.write(json.dumps(_err(None, -32700, f"parse error: {e}")) + "\n")
            sys.stdout.flush()
            continue
        resp = handle_request(req)
        if resp is None:
            continue
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
