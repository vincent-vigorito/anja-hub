"""F-EventTriggers: webhook inbound → routine.

Copre i 4 pezzi: validator (trigger webhook senza schedule, hook_secret obbligatorio),
endpoint POST /hooks/<name> (secret/HMAC, rate-limit, dedup, fire-file), daemon
(_fire_events consuma i fire-file), runner ({{event}} DOPO i secrets — un payload
con {{VAR}} non esfiltra).

    /opt/homebrew/opt/python@3.12/bin/python3.12 anja-hub/tests/test_event_triggers.py
"""
import hashlib
import hmac as hmaclib
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WEBAPP = REPO / "anja-hub" / "webapp"
SCRIPTS = REPO / "anja-routines" / "scripts"
sys.path.insert(0, str(WEBAPP))
sys.path.insert(0, str(SCRIPTS))

import routine_validate as rv      # noqa: E402
import runner as rn                # noqa: E402
import daemon                      # noqa: E402

PASS, FAIL = 0, 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label} {detail}")


def test_validator():
    print("validator")
    base = {"name": "wh", "scope": "hub", "prompt": "p"}
    errs, _ = rv.validate_routine({**base, "trigger": "webhook", "hook_secret": "s"})
    check("webhook senza schedule → valido", not errs, str(errs))
    errs, _ = rv.validate_routine({**base, "trigger": "webhook"})
    check("webhook senza hook_secret → invalido", any("hook_secret" in e for e in errs), str(errs))
    errs, _ = rv.validate_routine(base)
    check("no trigger senza schedule → invalido", any("schedule" in e for e in errs), str(errs))
    errs, _ = rv.validate_routine({**base, "trigger": "slack", "hook_secret": "s"})
    check("trigger sconosciuto → invalido", any("trigger" in e for e in errs), str(errs))
    errs, _ = rv.validate_routine({**base, "trigger": "webhook", "hook_secret": "s",
                                   "hook_hmac": "github", "schedule": "0 8 * * *"})
    check("webhook + cron coesistono, hmac github ok", not errs, str(errs))


def _mk_hub(tmp: Path) -> Path:
    hub = tmp / "hub"
    (hub / "routines").mkdir(parents=True)
    (hub / "config").mkdir()
    (hub / "config" / "config.json").write_text("{}")
    (hub / "routines" / ".secrets.env").write_text("HOOK_SECRET_T=topsecret\nOPENAI_API_KEY=sk-leak-me\n")
    (hub / "routines" / "wh-test.yaml").write_text(
        "name: wh-test\nscope: hub\ntrigger: webhook\n"
        'hook_secret: "{{HOOK_SECRET_T}}"\nprompt: |\n  Evento: {{event}}\n')
    (hub / "routines" / "wh-gh.yaml").write_text(
        "name: wh-gh\nscope: hub\ntrigger: webhook\nhook_hmac: github\n"
        'hook_secret: "ghsecret"\nprompt: |\n  {{event}}\n')
    (hub / "routines" / "cron-only.yaml").write_text(
        'name: cron-only\nscope: hub\nschedule: "0 8 * * *"\nprompt: ciao\n')
    return hub


def test_endpoint():
    print("endpoint POST /hooks/<name>")
    from fastapi.testclient import TestClient
    import server
    tmp = Path(tempfile.mkdtemp())
    hub = _mk_hub(tmp)
    server.HUB_PATH = hub
    server._HOOK_HITS.clear()
    server._HOOK_LAST.clear()
    c = TestClient(server.app)

    r = c.post("/hooks/wh-test", json={"a": 1})
    check("senza secret → 403", r.status_code == 403, str(r.status_code))
    r = c.post("/hooks/wh-test", json={"a": 1}, headers={"X-Anja-Hook-Secret": "sbagliato"})
    check("secret errato → 403", r.status_code == 403, str(r.status_code))
    r = c.post("/hooks/cron-only", json={}, headers={"X-Anja-Hook-Secret": "x"})
    check("routine non-webhook → 404", r.status_code == 404, str(r.status_code))
    r = c.post("/hooks/ignota", json={})
    check("routine inesistente → 404", r.status_code == 404, str(r.status_code))

    r = c.post("/hooks/wh-test", json={"a": 1}, headers={"X-Anja-Hook-Secret": "topsecret"})
    fire = list((hub / "routines" / ".fire").glob("*.json"))
    check("secret giusto ({{VAR}} risolto) → 202 + fire-file", r.status_code == 202 and len(fire) == 1,
          f"{r.status_code} {fire}")
    data = json.loads(fire[0].read_text())
    check("fire-file: routine + event", data["routine"] == "wh-test" and data["event"] == {"a": 1}, str(data))

    r = c.post("/hooks/wh-test", json={"a": 1}, headers={"X-Anja-Hook-Secret": "topsecret"})
    check("payload identico entro 120s → dedup, no secondo file",
          r.status_code == 200 and r.json().get("deduped") and
          len(list((hub / "routines" / ".fire").glob("*.json"))) == 1, str(r.json()))

    codes = [c.post("/hooks/wh-test", json={"n": i}, headers={"X-Anja-Hook-Secret": "topsecret"}).status_code
             for i in range(2, 9)]
    check("rate limit 6/min → 429", 429 in codes, str(codes))

    body = json.dumps({"ref": "refs/heads/main"}).encode()
    sig = "sha256=" + hmaclib.new(b"ghsecret", body, hashlib.sha256).hexdigest()
    r = c.post("/hooks/wh-gh", content=body, headers={"X-Hub-Signature-256": sig,
                                                      "Content-Type": "application/json"})
    check("HMAC github valida → 202", r.status_code == 202, str(r.status_code))
    r = c.post("/hooks/wh-gh", content=body, headers={"X-Hub-Signature-256": "sha256=deadbeef"})
    check("HMAC github invalida → 403", r.status_code == 403, str(r.status_code))
    r = c.post("/hooks/wh-gh", content=b"x" * (server._HOOK_BODY_MAX + 1),
               headers={"X-Hub-Signature-256": sig})
    check("body oltre il cap → 413", r.status_code == 413, str(r.status_code))

    # routine disabilitata → 404 (uniforme, no-leak)
    (hub / "routines" / "routines.json").write_text(json.dumps({"wh-test": {"enabled": False}}))
    server._HOOK_LAST.clear()
    server._HOOK_HITS.clear()
    r = c.post("/hooks/wh-test", json={"b": 2}, headers={"X-Anja-Hook-Secret": "topsecret"})
    check("routine disabled → 404", r.status_code == 404, str(r.status_code))
    return hub


def test_daemon(hub: Path):
    print("daemon _fire_events")
    (hub / "routines" / "routines.json").write_text("{}")
    import routine_registry as rr
    routines = rr.list_routines(hub)
    spawned = []
    orig = daemon._spawn
    daemon._spawn = lambda name, h, extra_args=None: spawned.append((name, extra_args)) or True
    try:
        n = daemon._fire_events(hub, routines)
    finally:
        daemon._spawn = orig
    check("fire-file consumati → spawn con --event-file",
          n >= 1 and all("--event-file" in (e or []) for _, e in spawned), str(spawned))
    check("file rinominati .run (anti double-fire)",
          not list((hub / "routines" / ".fire").glob("*.json")) and
          list((hub / "routines" / ".fire").glob("*.json.run")), "")
    n2 = daemon._fire_events(hub, routines)
    check("secondo giro: niente double-fire", n2 == 0, str(n2))


def test_runner_event():
    print("runner: {{event}} dopo i secrets")
    prompt = rn.expand_secrets("Chiave: {{OPENAI_API_KEY}}\nEvento: {{event}}",
                               {"OPENAI_API_KEY": "sk-real"})
    out = rn._insert_event(prompt, {"msg": "payload con {{OPENAI_API_KEY}} dentro"})
    check("secret del PROMPT espanso", "sk-real" in out, "")
    check("{{VAR}} nel PAYLOAD non espanso (anti-esfiltrazione)",
          "{{OPENAI_API_KEY}}" in out, out[:200])
    check("payload delimitato come non fidato", "<untrusted_webhook_event>" in out, "")
    out2 = rn._insert_event("prompt senza placeholder", {"a": 1})
    check("senza {{event}} → blocco anteposto", out2.startswith("<untrusted_webhook_event>"), "")
    big = rn._insert_event("{{event}}", {"big": "x" * 50_000})
    check("payload troncato a EVENT_MAX_CHARS", "[truncated]" in big and len(big) < 20_000, str(len(big)))


def main():
    test_validator()
    hub = test_endpoint()
    test_daemon(hub)
    test_runner_event()
    print("=" * 44)
    if FAIL:
        print(f"FAIL: {FAIL} (pass {PASS})")
        sys.exit(1)
    print(f"ALL PASS ({PASS})")


if __name__ == "__main__":
    main()
