"""Bottoni inline Telegram per permessi (🔐) e piani (📋) — F-AgentSessions.

Congela: il push 🔐/📋 porta inline keyboard con il request_id; il click
risolve ESATTAMENTE quella pending (anche di una conv web UI) e edita il
messaggio togliendo i bottoni; secondo click → "already resolved".
Niente rete: send/edit/answer mockati.

    /opt/homebrew/opt/python@3.12/bin/python3.12 anja-hub/tests/test_telegram_asp_buttons.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

WEBAPP = Path(__file__).resolve().parents[1] / "webapp"
sys.path.insert(0, str(WEBAPP))
os.environ["ANJA_ASP_PERMISSIONS"] = "1"

import server              # noqa: E402
import telegram_daemon     # noqa: E402
import asp_permissions     # noqa: E402

PASS = FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label} {detail}")


SENT, EDITED, ANSWERED = [], [], []


async def _fake_send(token, chat_id, text, reply_markup=None, **kw):
    SENT.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
    return {"ok": True}


async def _fake_edit(token, chat_id, message_id, text, parse_mode="Markdown", reply_markup=None):
    EDITED.append({"chat_id": chat_id, "message_id": message_id, "text": text,
                   "parse_mode": parse_mode, "reply_markup": reply_markup})
    return {"ok": True}


async def _fake_answer(token, cb_id, text=""):
    ANSWERED.append(text)
    return {"ok": True}


class FakeDaemon:
    token = "tok"
    callback_handlers = {}


class FakeChat:
    """Solo load_conversation (server._get_chat_module cerca claude_chat.py in WEBAPP_DIR)."""
    def load_conversation(self, webapp_dir, conv_id):
        import json
        p = Path(webapp_dir) / "conversations" / f"{conv_id}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def _cbs(markup):
    return [b["callback_data"] for row in (markup or {}).get("inline_keyboard", []) for b in row]


def _click(data, chat_id=123, from_id=123, message_id=77, text="orig text"):
    cbq = {"id": "cb1", "data": data,
           "from": {"id": from_id, "username": "vincent"},
           "message": {"message_id": message_id, "chat": {"id": chat_id}, "text": text}}
    return asyncio.run(server._tg_asp_callback(cbq))


async def _pending_and_notify(conv_id, tool, target, input_data=None):
    rid, fut = asp_permissions.pending.create(conv_id, "", tool, target, input_data or {})
    await server._asp_notify_permission_ask(conv_id, tool, target, rid)
    return rid, fut


def main():
    tmp = Path(tempfile.mkdtemp())
    server.WEBAPP_DIR = tmp
    server.HUB_PATH = tmp
    server.TELEGRAM_DAEMON = FakeDaemon()
    server._chat_module = FakeChat()
    telegram_daemon.send_message = _fake_send
    telegram_daemon.edit_message_text = _fake_edit
    telegram_daemon.answer_callback_query = _fake_answer

    print("permission push su thread Telegram: bottoni con rid")
    rid, fut = asyncio.run(_pending_and_notify("telegram-123-t2", "Bash", "rm -rf build/"))
    check("un messaggio inviato alla chat 123", len(SENT) == 1 and SENT[0]["chat_id"] == 123)
    check("testo 🔐 con tool", "🔐" in SENT[0]["text"] and "Bash" in SENT[0]["text"])
    cbs = _cbs(SENT[0]["reply_markup"])
    check("3 bottoni allow/always/deny col rid",
          cbs == [f"perm:allow:{rid}", f"perm:always:{rid}", f"perm:deny:{rid}"], str(cbs))
    check("niente riga source (è Telegram)", "source" not in SENT[0]["text"])

    print("click ✅ Always → risolve, edita, toglie bottoni")
    handled = _click(f"perm:always:{rid}", text="🔐 Permission requested — Bash")
    check("handled=True", handled is True)
    check("future risolta always_allow", fut.done() and fut.result()["decision"] == "always_allow")
    check("by = telegram:<from_id>", fut.result()["by"] == "telegram:123", str(fut.result()))
    check("toast al click", ANSWERED[-1] == "✅ allowed (always)", str(ANSWERED))
    check("messaggio editato in plain con esito e autore",
          EDITED and EDITED[-1]["parse_mode"] is None
          and "allowed (always) by vincent" in EDITED[-1]["text"]
          and EDITED[-1]["text"].startswith("🔐 Permission requested — Bash"), str(EDITED))
    check("inline_keyboard svuotata", EDITED[-1]["reply_markup"] == {"inline_keyboard": []})

    print("secondo click sullo stesso rid → already resolved, bottoni comunque via")
    n_ed = len(EDITED)
    _click(f"perm:allow:{rid}")
    check("toast already resolved", ANSWERED[-1] == "Already resolved or expired", str(ANSWERED[-1]))
    check("edit anche se già risolta", len(EDITED) == n_ed + 1
          and "already resolved" in EDITED[-1]["text"])

    print("permission di una conv web UI → notify chat + source line, risolvibile dal bottone")
    SENT.clear()
    (tmp / "config.json").write_text('{"notify_telegram": true, "notify_telegram_chat_id": 999}')
    (tmp / "conversations").mkdir(exist_ok=True)
    (tmp / "conversations" / "abc.json").write_text('{"id": "abc", "title": "Deploy *fix* [x]"}')
    rid2, fut2 = asyncio.run(_pending_and_notify("abc", "Write", "/srv/app/x.py"))
    check("inviato alla chat di notifica 999", SENT and SENT[0]["chat_id"] == 999, str(SENT))
    check("source: web UI + titolo sanificato",
          "source: web UI · Deploy fix x" in SENT[0]["text"], SENT[0]["text"])
    _click(f"perm:deny:{rid2}", chat_id=999, from_id=123)
    check("deny risolta dalla chat di notifica", fut2.done() and fut2.result()["decision"] == "deny")

    print("plan proposed → 📋 con estratto piano e bottoni approve/replan")
    SENT.clear()
    rid3, fut3 = asyncio.run(_pending_and_notify(
        "telegram-123", "ExitPlanMode", "piano proposto — /approve · /replan <note>",
        {"plan": "1. `edit` foo\n2. run tests"}))
    check("testo 📋 con estratto senza backtick", "📋" in SENT[0]["text"]
          and "1. 'edit' foo" in SENT[0]["text"], SENT[0]["text"])
    cbs = _cbs(SENT[0]["reply_markup"])
    check("bottoni plan", cbs == [f"plan:approve:{rid3}", f"plan:replan:{rid3}"], str(cbs))
    _click(f"plan:replan:{rid3}")
    check("replan → decision deny", fut3.done() and fut3.result()["decision"] == "deny")
    check("toast replan", ANSWERED[-1] == "🔄 replan requested", ANSWERED[-1])

    print("callback data sconosciuto → non gestito (False), nessun edit")
    n_ed = len(EDITED)
    check("perm:foo:x → False", _click("perm:foo:xyz") is False)
    check("model:opus → False (va al dispatch comandi)", _click("model:opus") is False)
    check("nessun edit", len(EDITED) == n_ed)

    print("daemon: registry callback_handlers ha precedenza sul dispatch generico")
    d = telegram_daemon.TelegramDaemon.__new__(telegram_daemon.TelegramDaemon)
    d.token = "tok"
    d.config = {"allowed_chat_ids": [123]}
    d.callback_handlers = {}
    seen = []

    async def _h(cbq):
        seen.append(cbq["data"])
        return True

    async def _on_msg(payload):
        seen.append("dispatch:" + payload["text"])

    d.on_message = _on_msg
    d.callback_handlers["perm"] = _h
    cbq = {"id": "c", "data": "perm:allow:abc", "from": {"id": 123},
           "message": {"message_id": 1, "chat": {"id": 123}}}
    asyncio.run(d._handle_callback_query(cbq))
    check("handler registrato chiamato, niente dispatch", seen == ["perm:allow:abc"], str(seen))
    seen.clear()
    cbq["data"] = "model:opus"
    asyncio.run(d._handle_callback_query(cbq))
    check("prefisso non registrato → dispatch /model opus", seen == ["dispatch:/model opus"], str(seen))
    seen.clear()
    cbq["from"] = {"id": 555}
    asyncio.run(d._handle_callback_query(cbq))
    check("from.id fuori allow-list → nulla", seen == [] and ANSWERED[-1] == "❌ Not authorized")

    print("=" * 44)
    if FAIL:
        print(f"FAIL: {FAIL} (pass {PASS})")
        sys.exit(1)
    print(f"ALL PASS ({PASS})")


if __name__ == "__main__":
    main()
