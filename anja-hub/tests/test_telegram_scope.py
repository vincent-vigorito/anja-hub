"""Telegram: lo scope di un thread (/project, /agent) deve SOPRAVVIVERE su disco.

Bug trovato col primo turno Grok Build da Telegram (2026-08-19): `/project swebby`
salvava solo `scope: "project:swebby"`, il dispatch leggeva `scope_project` (mai
persistito) → ogni turno ricadeva su hub e ri-salvava `scope: "hub"`. Ora lo scope
si deriva da `scope` (`_tg_scope_parts`) e il persist post-turno non forza "hub".
Bonus: `/provider grok_cli` porta il modello a uno del seat (non "opus").

    /opt/homebrew/opt/python@3.12/bin/python3.12 anja-hub/tests/test_telegram_scope.py
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

WEBAPP = Path(__file__).resolve().parents[1] / "webapp"
sys.path.insert(0, str(WEBAPP))

import server                      # noqa: E402
import telegram_daemon            # noqa: E402
import claude_chat                # noqa: E402
import grok_oauth                 # noqa: E402

PASS, FAIL = 0, 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label} {detail}")


class FakeChat:
    load_conversation = staticmethod(claude_chat.load_conversation)
    save_conversation = staticmethod(claude_chat.save_conversation)


_SENT = []


async def _fake_send(token, chat_id, text, reply_markup=None, **kw):
    _SENT.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})


def _cmd(cmd, args="", chat_id=321):
    _SENT.clear()
    conv_id = server._tg_active_conv(chat_id)
    return asyncio.run(server._telegram_handle_command(FakeChat(), conv_id, chat_id, cmd, args, "tok"))


def _conv(tmp, chat_id=321):
    return json.loads((tmp / "conversations" / f"telegram-{chat_id}.json").read_text(encoding="utf-8"))


def main():
    tmp = Path(tempfile.mkdtemp())
    hub = tmp / "hub"
    (hub / "config").mkdir(parents=True)
    ws = hub / "workspaces" / "swebby"
    ws.mkdir(parents=True)
    (hub / "config" / "projects.json").write_text(json.dumps({"projects": [
        {"name": "swebby", "location": {"kind": "local", "path": str(ws)}}]}))
    (hub / "config" / "config.json").write_text("{}")
    server.WEBAPP_DIR = tmp
    server.HUB_PATH = hub
    telegram_daemon.send_message = _fake_send
    (tmp / "conversations").mkdir()
    (tmp / "conversations" / "telegram-321.json").write_text(json.dumps(
        {"id": "telegram-321", "title": "Main", "scope": "hub", "provider": "claude", "model": "opus",
         "messages": [], "sdk_session_id": "claude-sid"}))

    print("_tg_scope_parts")
    check("chiavi esplicite vincono", server._tg_scope_parts({"scope_project": "a", "scope": "hub"}) == ("a", ""))
    check("da scope project:", server._tg_scope_parts({"scope": "project:swebby"}) == ("swebby", ""))
    check("da scope agent:", server._tg_scope_parts({"scope": "agent:trader"}) == ("", "trader"))
    check("hub / vuoto", server._tg_scope_parts({"scope": "hub"}) == ("", "") and server._tg_scope_parts({}) == ("", ""))

    print("/project swebby → scope su disco → il dispatch lo vede")
    projects_ctx = server._build_projects_context()
    check("progetto registrato visto dal contesto", any(p.get("name") == "swebby" for p in projects_ctx), str(projects_ctx)[:200])
    check("/project ok", _cmd("/project", "swebby") is True and "project:swebby" in _SENT[-1]["text"], str(_SENT[-1:]))
    c = _conv(tmp)
    check("scope persistito", c.get("scope") == "project:swebby" and c.get("sdk_session_id", "") == "", str(c))
    sp, sa = server._tg_scope_parts(c)
    cwd, kt = claude_chat.resolve_chat_cwd(hub, f"project:{sp}", projects_ctx)
    check("cwd risolta = workspace (non hub)", Path(cwd) == ws and kt == ("project", "swebby"), str(cwd))

    print("/status mostra il project")
    _cmd("/status")
    check("status: project: swebby", "project: `swebby`" in _SENT[-1]["text"], _SENT[-1]["text"][:200])

    print("/provider grok_cli → modello del seat, scope intatto")
    orig = grok_oauth.grok_model_ids
    grok_oauth.grok_model_ids = lambda: ["grok-4.6", "grok-4.5"]
    try:
        _cmd("/provider", "grok_cli")
    finally:
        grok_oauth.grok_model_ids = orig
    c = _conv(tmp)
    check("provider grok_cli + model grok-4.6 (era opus) + scope ancora project",
          c.get("provider") == "grok_cli" and c.get("model") == "grok-4.6" and c.get("scope") == "project:swebby", str(c))
    check("ack con il modello", "grok-4.6" in _SENT[-1]["text"], _SENT[-1]["text"])

    print("/project reset → hub")
    _cmd("/project", "reset")
    c = _conv(tmp)
    check("scope hub", c.get("scope") == "hub", str(c))

    print("=" * 44)
    if FAIL:
        print(f"FAIL: {FAIL} (pass {PASS})")
        sys.exit(1)
    print(f"ALL PASS ({PASS})")


if __name__ == "__main__":
    main()
