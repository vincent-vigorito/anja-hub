"""Link di una chat Telegram DALLA UI (codice monouso → /start <code> o /link <code>).

Congela: create_link_code (TTL, deep link), consumo monouso, /start+/link da chat
sconosciuta → allow-list in config.json + config live; codice errato/scaduto;
/start da chat già linkata; /start nudo → onboarding standard. Niente rete.

    /opt/homebrew/opt/python@3.12/bin/python3.12 anja-hub/tests/test_telegram_link.py
"""
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

WEBAPP = Path(__file__).resolve().parents[1] / "webapp"
sys.path.insert(0, str(WEBAPP))

import telegram_daemon as td   # noqa: E402

PASS = FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label} {detail}")


SENT, DISPATCHED = [], []


async def _fake_send(token, chat_id, text, **kw):
    SENT.append({"chat_id": chat_id, "text": text})
    return {"ok": True}


def _msg(chat_id, text, username="vincent"):
    return {"message": {"chat": {"id": chat_id, "username": username},
                        "from": {"id": chat_id, "first_name": "V"},
                        "text": text, "message_id": 1}}


def main():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "config.json").write_text(json.dumps({"telegram": {"enabled": True, "allowed_chat_ids": [111]}}))
    td.send_message = _fake_send

    async def _on_msg(payload):
        DISPATCHED.append(payload["text"])

    d = td.TelegramDaemon(tmp, on_message=_on_msg)
    d.token = "tok"
    d.bot_username = "anja_test_bot"

    print("create_link_code")
    lc = d.create_link_code(by="vincent")
    check("codice 12 char", len(lc["code"]) == 12, lc["code"])
    check("deep link t.me/<bot>?start=<code>", lc["deep_link"] == f"https://t.me/anja_test_bot?start={lc['code']}", str(lc))
    check("ttl 600", lc["ttl_sec"] == 600 and lc["expires_at"] > time.time() + 500)

    print("/start <code> da chat sconosciuta → linkata")
    SENT.clear()
    asyncio.run(d._handle_update(_msg(222, f"/start {lc['code']}")))
    check("222 in allow-list live", 222 in d.config["allowed_chat_ids"], str(d.config))
    cfg = json.loads((tmp / "config.json").read_text())
    check("222 persistito in config.json (111 conservato)",
          cfg["telegram"]["allowed_chat_ids"] == [111, 222], str(cfg))
    check("risposta Linked", SENT and "Linked" in SENT[-1]["text"], str(SENT))
    check("codice bruciato", lc["code"] not in d.link_codes)
    check("niente dispatch ad Anja", DISPATCHED == [])

    print("stesso codice riusato → invalid")
    SENT.clear()
    asyncio.run(d._handle_update(_msg(333, f"/link {lc['code']}")))
    check("333 NON linkata", 333 not in d.config["allowed_chat_ids"])
    check("risposta invalid/expired", "Invalid or expired" in SENT[-1]["text"], str(SENT))

    print("codice scaduto → invalid")
    lc2 = d.create_link_code()
    d.link_codes[lc2["code"]]["expires"] = time.time() - 1
    SENT.clear()
    asyncio.run(d._handle_update(_msg(333, f"/link {lc2['code']}")))
    check("scaduto rifiutato", 333 not in d.config["allowed_chat_ids"] and "Invalid or expired" in SENT[-1]["text"])
    check("purge: codice scaduto rimosso", lc2["code"] not in d.link_codes)

    print("/link <code> (e /start@bot nei gruppi) → linkata")
    lc3 = d.create_link_code()
    SENT.clear()
    asyncio.run(d._handle_update(_msg(-444, f"/start@anja_test_bot {lc3['code']}", username="")))
    check("gruppo -444 linkato", -444 in d.config["allowed_chat_ids"], str(d.config))

    print("/start da chat già linkata → 'already linked', niente dispatch")
    SENT.clear(); DISPATCHED.clear()
    asyncio.run(d._handle_update(_msg(111, "/start")))
    check("already linked", "already linked" in SENT[-1]["text"], str(SENT))
    check("niente dispatch", DISPATCHED == [])

    print("/start nudo da sconosciuta → onboarding standard (con hint /link)")
    SENT.clear()
    asyncio.run(d._handle_update(_msg(555, "/start")))
    check("555 non linkata", 555 not in d.config["allowed_chat_ids"])
    check("onboarding con /link e chat_id", SENT and "/link" in SENT[-1]["text"] and "555" in SENT[-1]["text"], str(SENT))
    check("555 tra gli unknown", 555 in d.unknown_chat_ids)

    print("messaggio normale da chat linkata → dispatch")
    DISPATCHED.clear()
    asyncio.run(d._handle_update(_msg(222, "ciao")))
    check("dispatch ad Anja", DISPATCHED == ["ciao"], str(DISPATCHED))

    print("=" * 44)
    if FAIL:
        print(f"FAIL: {FAIL} (pass {PASS})")
        sys.exit(1)
    print(f"ALL PASS ({PASS})")


if __name__ == "__main__":
    main()
