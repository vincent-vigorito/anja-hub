"""F-Mail: registro caselle, binding per scope, server MCP anja_mail, outbox two-phase.

Senza rete: backend finti. Copre store (validazioni, niente segreti nel registro,
outbox), server MCP (gruppi, flat names, mailbox non montata, send→pending,
policy auto/deny), endpoint webapp (CRUD, binding→.mcp.json, resolve→invio),
runner routine (mailbox output via outbox).

    /opt/homebrew/opt/python@3.12/bin/python3.12 anja-hub/tests/test_mail.py
"""
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WEBAPP = REPO / "anja-hub" / "webapp"
SCRIPTS = REPO / "anja-hub" / "scripts"
RSCRIPTS = REPO / "anja-routines" / "scripts"
for p in (str(WEBAPP), str(SCRIPTS), str(RSCRIPTS)):
    sys.path.insert(0, p)

import mail_store as ms            # noqa: E402

PASS, FAIL = 0, 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label} {detail}")


class FakeBackend:
    """Backend finto: registra le chiamate, risponde col modello unificato."""
    sent = []

    def search(self, query, max_results=20):
        return [{"id": "m1", "thread_id": "t1", "from": "a@x.it", "to": "me@x.it",
                 "cc": "", "date": "today", "subject": f"match {query}", "snippet": "hi",
                 "labels": ["INBOX"], "unread": True, "has_attachments": False}]

    def get(self, msg_id, format="summary"):
        m = self.search("")[0]
        m["id"] = msg_id
        if format in ("text", "full"):
            m["body"] = "corpo del messaggio"
        return m

    def thread(self, tid):
        return self.search("")

    def labels(self):
        return ["INBOX", "Sent"]

    def create_draft(self, to, subject, body, cc=None, reply_to_id=""):
        return {"draft_id": "d1"}

    def send(self, to, subject, body, cc=None, reply_to_id="", draft_id=""):
        FakeBackend.sent.append({"to": to, "subject": subject})
        return "prov-123"

    def profile(self):
        return {"emailAddress": "fake@gmail.com", "messagesTotal": 9}

    def test(self):
        return {"ok": True}


def mk_hub(tmp: Path) -> Path:
    hub = tmp / "hub"
    (hub / "config").mkdir(parents=True)
    (hub / "config" / "config.json").write_text("{}")
    (hub / "workspaces" / "swebby" / ".anjawiki").mkdir(parents=True)
    (hub / "config" / "projects.json").write_text(json.dumps({"projects": [
        {"name": "swebby", "location": {"kind": "local", "path": str(hub / "workspaces" / "swebby")}}]}))
    return hub


def test_store():
    print("mail_store")
    tmp = Path(tempfile.mkdtemp())
    hub = mk_hub(tmp)
    ms.upsert_mailbox(hub, {"id": "main", "kind": "gmail", "label": "Main"})
    ms.upsert_mailbox(hub, {"id": "swebby", "kind": "imap", "imap": {"host": "h"}, "smtp": {"host": "s"}})
    check("registro: 2 caselle", len(ms.list_mailboxes(hub)) == 2)
    try:
        ms.upsert_mailbox(hub, {"id": "BAD ID", "kind": "gmail"})
        check("id invalido rifiutato", False)
    except ValueError:
        check("id invalido rifiutato", True)
    try:
        ms.upsert_mailbox(hub, {"id": "x", "kind": "imap", "imap": {"host": "h", "password": "nope"}})
        check("segreti nel registro rifiutati", False)
    except ValueError:
        check("segreti nel registro rifiutati", True)
    raw = ms.registry_path(hub).read_text()
    check("nessun segreto nel file registro", "password" not in raw and "nope" not in raw)

    ms.save_imap_creds(hub, "swebby", {"MAIL_USER": "u@x.it", "MAIL_PASS": "pw"})
    mode = oct(ms.secrets_dir(hub, "swebby").joinpath("creds.env").stat().st_mode)[-3:]
    check("creds.env 0600", mode == "600", mode)
    check("connected: imap sì, gmail no (niente token)",
          ms.mailbox_connected(hub, ms.get_mailbox(hub, "swebby"))
          and not ms.mailbox_connected(hub, ms.get_mailbox(hub, "main")))

    # binding
    try:
        ms.set_scope_binding(hub, "hub", ["ghost"])
        check("binding con casella ignota rifiutato", False)
    except ValueError:
        check("binding con casella ignota rifiutato", True)
    ms.set_scope_binding(hub, "hub", ["main"], "ask")
    ms.set_scope_binding(hub, "project:swebby", ["swebby"], "auto")
    check("hub vede main, non swebby", ms.scope_binding(hub, "hub")["mailboxes"] == ["main"])
    b = ms.scope_binding(hub, "project:swebby")
    check("ws vede solo la sua (niente risalita)", b["mailboxes"] == ["swebby"] and b["send_policy"] == "auto")

    # outbox
    it = ms.outbox_add(hub, scope="hub", mailbox="main", to=["x@y.it"], subject="Ciao",
                       body="corpo segreto lungo")
    lst = ms.outbox_list(hub)
    check("outbox pending, lista SENZA body", lst[0]["status"] == "pending"
          and "body" not in lst[0] and lst[0]["body_preview"].startswith("corpo"))
    check("unnotified lo vede", len(ms.outbox_unnotified(hub)) == 1)
    ms.outbox_mark_notified(hub, it["id"])
    check("dopo mark_notified sparisce", ms.outbox_unnotified(hub) == [])
    full = ms.outbox_resolve(hub, it["id"], "approve", by="test")
    check("resolve approve → item col body", full["status"] == "approved" and full["body"] == "corpo segreto lungo")
    check("secondo resolve → None", ms.outbox_resolve(hub, it["id"], "approve") is None)
    ms.outbox_mark(hub, it["id"], "sent", provider_message_id="p1")
    check("mark sent", ms.outbox_get(hub, it["id"])["status"] == "sent")
    it2 = ms.outbox_add(hub, scope="hub", mailbox="main", to=["x@y.it"], subject="old", body="b")
    items = ms._load_outbox(hub)
    for x in items:
        if x["id"] == it2["id"]:
            x["created_ts"] -= 90000
    ms._save_outbox(hub, items)
    check("pending >24h → expired", any(x["status"] == "expired" and x["id"] == it2["id"]
                                        for x in ms.outbox_list(hub)))
    return hub


def _load_mail_server(hub, mailboxes="main", groups="mail_read,mail_write", policy="ask"):
    os.environ.update({"ANJA_HUB": str(hub), "ANJA_MAILBOXES": mailboxes,
                       "ANJA_TOOL_GROUPS": groups, "ANJA_SCOPE_NAME": "hub",
                       "ANJA_MAIL_SEND_POLICY": policy})
    import mcp_mail_server
    srv = importlib.reload(mcp_mail_server)
    srv._BACKENDS.clear()
    import mail_backends
    mail_backends.backend_for = lambda h, b: FakeBackend()
    srv.mail_backends = mail_backends
    return srv


def test_mcp_server(hub):
    print("mcp_mail_server (backend finti)")
    srv = _load_mail_server(hub)
    names = [t["name"] for t in srv.handle_request(
        {"id": 1, "method": "tools/list"})["result"]["tools"]]
    check("tools/list flat (mail_search, no punti)", "mail_search" in names
          and all("." not in n for n in names), str(names))
    check("write tools presenti (policy ask)", "mail_send" in names and "mail_draft" in names)

    def call(name, args=None):
        r = srv.handle_request({"id": 2, "method": "tools/call",
                                "params": {"name": name, "arguments": args or {}}})
        if "error" in r:
            return {"error": r["error"]["message"]}
        return json.loads(r["result"]["content"][0]["text"])

    res = call("mail_search", {"query": "is:unread"})
    check("search via wire flat → modello unificato", res["count"] == 1
          and res["messages"][0]["subject"].startswith("match"), str(res)[:120])
    res = call("mail.get", {"id": "m9", "format": "text"})
    check("get accetta nome canonico", res["id"] == "m9" and "body" in res)
    res = call("mail_search", {"query": "x", "mailbox": "swebby"})
    check("mailbox non montata → errore", "not bound" in str(res.get("error", res)), str(res)[:120])

    FakeBackend.sent.clear()
    res = call("mail_send", {"to": ["dest@x.it"], "subject": "Prova", "body": "b"})
    check("send policy ask → pending, NON inviata", res["status"] == "pending_approval"
          and not FakeBackend.sent, str(res)[:120])
    pend = ms.outbox_list(hub, status="pending")
    check("pending in outbox", any(p["subject"] == "Prova" for p in pend))

    srv2 = _load_mail_server(hub, policy="auto")
    res = json.loads(srv2.handle_request({"id": 3, "method": "tools/call", "params": {
        "name": "mail_send", "arguments": {"to": ["a@b.it"], "subject": "Auto", "body": "x"}}}
    )["result"]["content"][0]["text"])
    check("policy auto → inviata + audit sent", res["status"] == "sent" and FakeBackend.sent
          and any(i["subject"] == "Auto" and i["status"] == "sent" for i in ms.outbox_list(hub)))

    srv3 = _load_mail_server(hub, policy="deny")
    names3 = [t["name"] for t in srv3.handle_request({"id": 4, "method": "tools/list"})["result"]["tools"]]
    check("policy deny → niente mail_send, draft resta", "mail_send" not in names3 and "mail_draft" in names3)

    srv4 = _load_mail_server(hub, groups="mail_read")
    names4 = [t["name"] for t in srv4.handle_request({"id": 5, "method": "tools/list"})["result"]["tools"]]
    check("gruppo solo read → nessun write tool", not any(n in names4 for n in ("mail_send", "mail_draft", "mail_modify")))


def test_endpoints():
    print("endpoint webapp")
    from fastapi.testclient import TestClient
    import server
    tmp = Path(tempfile.mkdtemp())
    hub = mk_hub(tmp)
    (hub / ".anjawiki").mkdir()
    server.HUB_PATH = hub
    c = TestClient(server.app)

    r = c.post("/api/mail/mailboxes", json={"id": "main", "kind": "gmail", "label": "Main"})
    check("crea gmail record", r.status_code == 200, r.text[:120])
    r = c.post("/api/mail/mailboxes", json={"id": "x", "kind": "ftp"})
    check("kind invalido → 400", r.status_code == 400)

    r = c.post("/api/mail/mailboxes", json={"id": "wsbox", "kind": "imap",
               "imap": {"host": "imap.x.it"}, "smtp": {"host": "smtp.x.it"}})
    check("crea imap record", r.status_code == 200)
    import mail_backends
    orig_test = mail_backends.ImapBackend.test
    mail_backends.ImapBackend.test = lambda self: {"ok": True}
    try:
        r = c.post("/api/mail/mailboxes/wsbox/imap", json={"user": "u@x.it", "password": "pw"})
        check("creds imap salvate + test ok", r.status_code == 200 and r.json()["ok"] is True, r.text[:150])
    finally:
        mail_backends.ImapBackend.test = orig_test
    lst = c.get("/api/mail/mailboxes").json()["mailboxes"]
    check("lista: wsbox connected", any(b["id"] == "wsbox" and b["connected"] for b in lst))

    r = c.put("/api/mail/binding", json={"scope": "hub", "mailboxes": ["main"], "send_policy": "ask"})
    check("binding hub ok", r.status_code == 200, r.text[:120])
    mcp = json.loads((hub / ".mcp.json").read_text())["mcpServers"]["anja_mail"]
    check(".mcp.json hub: env corretto", mcp["env"]["ANJA_MAILBOXES"] == "main"
          and mcp["env"]["ANJA_TOOL_GROUPS"] == "mail_read,mail_write"
          and mcp["env"]["ANJA_SCOPE_NAME"] == "hub", str(mcp)[:200])
    r = c.put("/api/mail/binding", json={"scope": "project:swebby", "mailboxes": ["wsbox"],
                                         "send_policy": "deny"})
    ws_mcp = json.loads((hub / "workspaces" / "swebby" / ".mcp.json").read_text())["mcpServers"]["anja_mail"]
    check(".mcp.json ws: deny → solo mail_read", ws_mcp["env"]["ANJA_TOOL_GROUPS"] == "mail_read")
    r = c.put("/api/mail/binding", json={"scope": "hub", "mailboxes": ["ghost"]})
    check("binding casella ignota → 400", r.status_code == 400)

    # outbox resolve → invio (monkeypatch del send)
    item = ms.outbox_add(hub, scope="hub", mailbox="main", to=["a@b.it"], subject="S", body="B")
    orig = server._mail_do_send
    server._mail_do_send = lambda it: (True, "prov-9")
    try:
        r = c.post(f"/api/mail/outbox/{item['id']}/resolve", json={"action": "approve"})
        check("resolve approve → sent", r.status_code == 200 and r.json()["status"] == "sent", r.text[:150])
    finally:
        server._mail_do_send = orig
    check("outbox marcata sent + provider id",
          ms.outbox_get(hub, item["id"])["provider_message_id"] == "prov-9")
    item2 = ms.outbox_add(hub, scope="hub", mailbox="main", to=["a@b.it"], subject="S2", body="B")
    r = c.post(f"/api/mail/outbox/{item2['id']}/resolve", json={"action": "reject"})
    check("reject → rejected, non inviata", r.json()["status"] == "rejected"
          and ms.outbox_get(hub, item2["id"])["status"] == "rejected")


def test_runner_mailbox():
    print("runner: output email via mailbox")
    import runner as rn
    tmp = Path(tempfile.mkdtemp())
    hub = mk_hub(tmp)
    ms.upsert_mailbox(hub, {"id": "main", "kind": "gmail"})
    ms.set_scope_binding(hub, "hub", ["main"], "ask")
    res = rn._send_via_mailbox({"mailbox": "main", "to": "x@y.it", "subject": "Report"}, "corpo", hub, "hub")
    check("policy ask → queued pending", "pending" in res["details"], str(res))
    check("outbox ha il pending", ms.outbox_list(hub, status="pending"))
    import mail_backends
    orig = mail_backends.backend_for
    mail_backends.backend_for = lambda h, b: FakeBackend()
    try:
        ms.set_scope_binding(hub, "hub", ["main"], "auto")
        res = rn._send_via_mailbox({"mailbox": "main", "to": "x@y.it", "subject": "R2"}, "corpo", hub, "hub")
        check("policy auto → sent", res["status"] == "success" and "sent" in res["details"], str(res))
    finally:
        mail_backends.backend_for = orig
    res = rn._send_via_mailbox({"mailbox": "main", "to": "x@y.it"}, "b", hub, "project:swebby")
    check("scope senza binding → errore", res["status"] == "error" and "not bound" in res["details"], str(res))
    ms.set_scope_binding(hub, "hub", ["main"], "deny")
    res = rn._send_via_mailbox({"mailbox": "main", "to": "x@y.it"}, "b", hub, "hub")
    check("policy deny → errore", res["status"] == "error" and "deny" in res["details"])


def main():
    hub = test_store()
    test_mcp_server(hub)
    test_endpoints()
    test_runner_mailbox()
    print("=" * 44)
    if FAIL:
        print(f"FAIL: {FAIL} (pass {PASS})")
        sys.exit(1)
    print(f"ALL PASS ({PASS})")


if __name__ == "__main__":
    main()
