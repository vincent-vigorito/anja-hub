"""F-TelegramAsyncNotify — delivery async su Telegram.

Congela la logica nuova: label del thread, lock per-conv_id, comando /async (lancio
in background + ack). Niente rete/LLM. Il dispatch completo (stream LLM) è coperto
per lettura: reply_prefix è un prepend, conv_id_override è un `or`.

    /opt/homebrew/opt/python@3.12/bin/python3.12 anja-hub/tests/test_telegram_async.py
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


class FakeChat:
    def load_conversation(self, webapp_dir, conv_id):
        p = Path(webapp_dir) / "conversations" / f"{conv_id}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def _write_conv(tmp, conv_id, **fields):
    p = tmp / "conversations" / f"{conv_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"id": conv_id, **fields}, ensure_ascii=False), encoding="utf-8")


_SENT = []


async def _fake_send(token, chat_id, text, reply_markup=None, **kw):
    _SENT.append({"chat_id": chat_id, "text": text})


def _cmd(cmd, args="", chat_id=123):
    """Esegue un comando lasciando girare anche un eventuale task in background."""
    _SENT.clear()
    conv_id = server._tg_active_conv(chat_id)

    async def go():
        r = await server._telegram_handle_command(FakeChat(), conv_id, chat_id, cmd, args, "tok")
        await asyncio.sleep(0)        # lascia girare il task bg schedulato
        return r

    return asyncio.run(go())


def main():
    tmp = Path(tempfile.mkdtemp())
    server.WEBAPP_DIR = tmp
    telegram_daemon.send_message = _fake_send

    # 1. label del thread ---------------------------------------------------
    assert server._tg_thread_label({"title": "Lavoro lungo"}, "telegram-123", 123) == "Lavoro lungo"
    assert server._tg_thread_label({"scope_agent": "trader"}, "telegram-123-t2", 123) == "t2·trader"
    assert server._tg_thread_label({}, "telegram-123", 123) == "main"
    print("✓ label: titolo · suffix·agent · main")

    # 2. lock per-conv_id ---------------------------------------------------
    la = server._telegram_conv_lock("telegram-123")
    lb = server._telegram_conv_lock("telegram-123-t2")
    assert la is server._telegram_conv_lock("telegram-123")     # stesso conv → stesso lock
    assert la is not lb                                          # thread diversi → lock distinti
    print("✓ lock per-conv: stesso conv stesso lock, thread diversi distinti")

    # 3. /async senza prompt → messaggio d'uso ------------------------------
    _write_conv(tmp, "telegram-123", title="Main chat")
    assert _cmd("/async") is True
    assert "Usage:" in _SENT[-1]["text"]
    print("✓ /async senza prompt: messaggio d'uso")

    # 4. /async <prompt> → lancia bg sul thread attivo + ack ----------------
    captured = {}

    def fake_bg(chat_id, conv_id, text, label):
        captured.update(chat_id=chat_id, conv_id=conv_id, text=text, label=label)
        async def _noop():
            return
        return _noop()

    server._telegram_async_bg = fake_bg
    assert _cmd("/async", "riassumimi il trimestre") is True
    assert captured == {"chat_id": 123, "conv_id": "telegram-123",
                        "text": "riassumimi il trimestre", "label": "Main chat"}, captured
    assert "background" in _SENT[-1]["text"].lower() and "Main chat" in _SENT[-1]["text"]
    print("✓ /async <prompt>: bg lanciato su thread attivo (label=Main chat) + ack")

    # 5. alias /bg ----------------------------------------------------------
    captured.clear()
    assert _cmd("/bg", "fai una cosa") is True
    assert captured.get("text") == "fai una cosa"
    print("✓ /bg: alias funzionante")

    print("\nOK 5/5")


if __name__ == "__main__":
    main()
