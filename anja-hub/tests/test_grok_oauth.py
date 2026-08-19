#!/usr/bin/env python3
"""Test di grok_oauth (F-GrokBuild): detection del seat senza leak del token,
catalogo modelli, device-login con un finto `grok` (nessuna rete).

Run: python3 anja-hub/tests/test_grok_oauth.py
"""

import json
import os
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))
import grok_oauth  # noqa: E402

PASS, FAIL = 0, 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label} {detail}")


SECRET = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.SECRETPAYLOAD.SIG"
REFRESH = "rt_supersecret_refresh"


def main():
    tmp = Path(tempfile.mkdtemp())
    home = tmp / ".grok"; home.mkdir()
    grok_oauth.GROK_HOME = home
    grok_oauth.AUTH_PATH = home / "auth.json"
    grok_oauth.MODELS_CACHE = home / "models_cache.json"

    print("senza auth.json / senza CLI")
    orig_which = grok_oauth.shutil.which
    grok_oauth.shutil.which = lambda name: None
    check("has_grok_cli False (no PATH, no ~/.grok/bin)", not grok_oauth.has_grok_cli())
    check("has_grok_session False", not grok_oauth.has_grok_session())
    s = grok_oauth.grok_auth_summary()
    check("summary: cli_installed False, session_active False, models []",
          s["cli_installed"] is False and s["session_active"] is False and s["models"] == [], str(s))
    check("fallback model ids", grok_oauth.grok_model_ids() == ["grok-4.6", "grok-4.5"])

    print("auth.json reale (forma {scope: {...}}) → summary senza token")
    (home / "auth.json").write_text(json.dumps({
        "https://auth.x.ai::client-1": {
            "key": SECRET, "auth_mode": "oidc", "user_id": "3bc898f0-fd6d-4870-9fe0-05d46baa44c4",
            "email": "owner@example.com", "refresh_token": REFRESH,
            "expires_at": "2020-01-01T00:00:00Z",  # scaduto: ma c'è il refresh_token → la CLI rinnova
            "oidc_issuer": "https://accounts.x.ai",
        }
    }))
    (home / "models_cache.json").write_text(json.dumps({"auth_method": "session", "models": {
        "grok-4.6": {"info": {"id": "grok-4.6", "name": "Grok 4.6", "context_window": 500000, "reasoning_effort": "high",
                              "reasoning_efforts": [{"id": "xhigh"}, {"id": "high"}, {"id": "medium"}, {"id": "low"}]}},
        "grok-4.5": {"info": {"id": "grok-4.5", "name": "Grok 4.5", "context_window": 500000,
                              "reasoning_efforts": [{"id": "high"}, {"id": "medium"}, {"id": "low"}]}},
        "secret-model": {"info": {"id": "secret-model", "hidden": True}},
    }}))
    check("has_grok_session True (refresh_token presente anche se key scaduta)", grok_oauth.has_grok_session())
    s = grok_oauth.grok_auth_summary()
    dump = json.dumps(s)
    check("NESSUN token/refresh nel summary", SECRET not in dump and REFRESH not in dump and "rt_" not in dump, dump[:200])
    check("email + user_id prefisso + expires_at + auth_mode", s["email"] == "owner@example.com" and s["user_id_prefix"] == "3bc898f0…"
          and s["expires_at"].startswith("2020") and s["auth_mode"] == "oidc", str(s))
    check("modelli dal cache (hidden escluso, efforts dal menu)", [m["id"] for m in s["models"]] == ["grok-4.6", "grok-4.5"]
          and s["models"][0]["efforts"] == ["xhigh", "high", "medium", "low"] and s["models"][0]["default_effort"] == "high", str(s["models"]))
    check("grok_model_ids dal cache", grok_oauth.grok_model_ids() == ["grok-4.6", "grok-4.5"])

    print("key scaduta SENZA refresh_token → non attivo")
    (home / "auth.json").write_text(json.dumps({"https://auth.x.ai::c": {"key": SECRET, "expires_at": "2020-01-01T00:00:00Z", "email": "x@y"}}))
    check("has_grok_session False", not grok_oauth.has_grok_session())
    s = grok_oauth.grok_auth_summary()
    check("email non esposta quando non attivo", s["email"] == "" and s["session_active"] is False)
    (home / "auth.json").write_text(json.dumps({"https://auth.x.ai::c": {"key": SECRET, "expires_at": "2999-01-01T00:00:00Z", "email": "x@y"}}))
    check("key valida senza refresh → attivo", grok_oauth.has_grok_session())
    (home / "auth.json").write_text("{garbage")
    check("auth.json corrotto → False, nessuna eccezione", not grok_oauth.has_grok_session())

    print("binario: ~/.grok/bin/grok come fallback a PATH")
    (home / "bin").mkdir()
    fake = home / "bin" / "grok"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, json, time, os\n"
        "a = sys.argv[1:]\n"
        "if a[:1] == ['--version']:\n"
        "    print('grok 9.9.9 (fake) [stable]'); sys.exit(0)\n"
        "if a[:1] == ['logout']:\n"
        "    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(sys.argv[0]))), 'auth.json')\n"
        "    os.path.exists(p) and os.unlink(p); sys.exit(0)\n"
        "if a[:2] == ['login', '--device-auth']:\n"
        "    print('\\nTo sign in, open this URL in your browser:\\n\\n  https://accounts.x.ai/oauth2/device?user_code=WWEV-3EC5\\n\\nConfirm this code in your browser:\\n\\n  WWEV-3EC5\\n\\nWaiting for authorization...', flush=True)\n"
        "    time.sleep(3.0)\n"
        "    home = os.path.dirname(os.path.dirname(os.path.abspath(sys.argv[0])))\n"
        "    open(os.path.join(home, 'auth.json'), 'w').write(json.dumps({'https://auth.x.ai::c': {'key': 'k', 'refresh_token': 'r', 'email': 'dev@example.com', 'expires_at': '2999-01-01T00:00:00Z'}}))\n"
        "    print('Signed in as dev@example.com'); sys.exit(0)\n"
        "sys.exit(2)\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    check("grok_binary → ~/.grok/bin/grok", grok_oauth.grok_binary() == str(fake))
    check("versione letta", grok_oauth.grok_cli_version().startswith("grok 9.9.9"))

    print("device login con finto CLI")
    (home / "auth.json").unlink(missing_ok=True)
    grok_oauth.login_cancel()
    r = grok_oauth.login_start()   # il finto CLI stampa URL+code e "polla" 3 s prima di scrivere auth.json
    check("start ok: URL + user_code estratti", r.get("ok") and r["auth_url"].endswith("user_code=WWEV-3EC5") and r["user_code"] == "WWEV-3EC5", str(r))
    p = grok_oauth.login_pending()
    check("pending True con url/code", p["pending"] and p["user_code"] == "WWEV-3EC5" and not p["logged_in"], str(p))
    w = grok_oauth.login_wait(0.2)
    check("wait breve → done False (CLI ancora in attesa)", w.get("ok") and w.get("done") is False, str(w))
    w = grok_oauth.login_wait(5)
    check("wait → done True, logged_in (auth.json scritto dal CLI)", w.get("ok") and w.get("done") and w.get("logged_in"), str(w))
    check("pending False dopo", not grok_oauth.login_pending()["pending"])
    check("session attiva con email", grok_oauth.grok_auth_summary()["email"] == "dev@example.com")

    print("logout")
    r = grok_oauth.logout()
    check("logout ok + session_active False", r.get("ok") and r.get("session_active") is False and not (home / "auth.json").exists(), str(r))

    print("login_wait senza login in corso")
    w = grok_oauth.login_wait(0.1)
    check("errore 'no login in progress'", not w.get("ok") and "no login" in w.get("error", ""), str(w))

    grok_oauth.shutil.which = orig_which
    print("=" * 44)
    if FAIL:
        print(f"FAIL: {FAIL} (pass {PASS})")
        sys.exit(1)
    print(f"ALL PASS ({PASS})")


if __name__ == "__main__":
    main()
