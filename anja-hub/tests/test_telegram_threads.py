"""F-TelegramMultiSession — multi-thread per chat_id su Telegram.

Congela il comportamento: stato thread (funzioni _tg_*) + il command flow reale
(/threads, /thread <suffix>, /newchat) con send_message mockato. Niente rete/LLM.

    /opt/homebrew/opt/python@3.12/bin/python3.12 anja-hub/tests/test_telegram_threads.py
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
    """Solo load_conversation: i comandi thread non invocano l'LLM."""
    def load_conversation(self, webapp_dir, conv_id):
        p = Path(webapp_dir) / "conversations" / f"{conv_id}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def _write_conv(tmp, conv_id, **fields):
    p = tmp / "conversations" / f"{conv_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"id": conv_id, **fields}, ensure_ascii=False), encoding="utf-8")


_SENT = []


async def _fake_send(token, chat_id, text, reply_markup=None, **kw):
    _SENT.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})


def _callbacks(markup):
    return [b["callback_data"] for row in (markup or {}).get("inline_keyboard", []) for b in row]


def _cmd(cmd, args="", chat_id=123):
    """Esegue un comando attraverso il path attivo, come fa il daemon."""
    _SENT.clear()
    conv_id = server._tg_active_conv(chat_id)
    return asyncio.run(server._telegram_handle_command(FakeChat(), conv_id, chat_id, cmd, args, "tok"))


def main():
    tmp = Path(tempfile.mkdtemp())
    server.WEBAPP_DIR = tmp                       # isola le conversations su tmp
    telegram_daemon.send_message = _fake_send     # cattura gli invii

    _write_conv(tmp, "telegram-123", title="Main chat", messages=[{}, {}])
    _write_conv(tmp, "telegram-123-t2", title="Secondo", scope_agent="trader",
                messages=[{}, {}, {}, {}])

    # 1. stato puro -------------------------------------------------------
    assert server._tg_active_conv(123) == "telegram-123"          # default = main
    threads = server._tg_list_threads(123)
    suffixes = {t["suffix"] for t in threads}
    assert suffixes == {"main", "t2"}, suffixes
    assert server._tg_new_thread_conv(123) == "telegram-123-t3"   # primo libero ≥2
    assert server._tg_active_conv(999) == "telegram-999"          # isolamento chat_id
    print("✓ stato: default main · list main+t2 · new=t3 · isolamento per chat_id")

    # 2. /threads → lista con bottoni inline ------------------------------
    assert _cmd("/threads") is True
    cbs = _callbacks(_SENT[-1]["reply_markup"])
    assert "thread:main" in cbs and "thread:t2" in cbs and "thread:new" in cbs, cbs
    print("✓ /threads: bottoni thread:main · thread:t2 · ➕thread:new")

    # 3. /thread t2 → switch attivo (persistito) --------------------------
    assert _cmd("/thread", "t2") is True
    assert server._tg_active_conv(123) == "telegram-123-t2"
    assert "Secondo" in _SENT[-1]["text"] and "trader" in _SENT[-1]["text"]
    # il messaggio normale successivo ora risolve t2, non main
    assert server._tg_active_conv(123) == "telegram-123-t2"
    print("✓ /thread t2: attivo persiste su t2 · conferma con titolo+agent")

    # 4. /newchat → nuovo thread attivo, i precedenti restano -------------
    assert _cmd("/newchat") is True
    assert server._tg_active_conv(123) == "telegram-123-t3"        # nuovo
    assert {t["suffix"] for t in server._tg_list_threads(123)} == {"main", "t2"}  # vecchi intatti
    print("✓ /newchat: attivo=t3 nuovo · main+t2 NON cancellati")

    # 5. /thread main → ritorno al thread legacy --------------------------
    assert _cmd("/thread", "main") is True
    assert server._tg_active_conv(123) == "telegram-123"
    print("✓ /thread main: ritorno al thread legacy")

    # 6. /thread inesistente → rifiutato, attivo invariato ----------------
    assert _cmd("/thread", "t9") is True
    assert server._tg_active_conv(123) == "telegram-123"           # invariato
    assert "non trovato" in _SENT[-1]["text"].lower()
    print("✓ /thread t9: inesistente rifiutato, attivo invariato")

    print("\nOK 6/6")


if __name__ == "__main__":
    main()
