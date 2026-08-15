#!/usr/bin/env python3
"""test_security_gates.py — regressione dei gate di sicurezza (audit 2026-07).

Congela le protezioni introdotte nel giro di hardening 2026-07 così non si
riaprono in un refactor futuro. Usa FastAPI TestClient contro `server.app` con un
hub temporaneo; il TestClient NON viene usato come context manager → gli startup
event (daemon telegram/kanban/…) non partono.

Copre: security headers, CSRF same-origin guard, rate-limit login, path-traversal
slug, SSRF check+pin, sandbox→skip-permissions, e l'enforcement authz in concierge
(401 unauth, 403 member su azioni admin/ws non-membro, escalation admin→owner).

Run: /opt/homebrew/opt/python@3.12/bin/python3.12 anja-hub/tests/test_security_gates.py
Exit 0 = OK, 1 = regressione.
"""

import json
import sys
import tempfile
from pathlib import Path

WEBAPP = Path(__file__).resolve().parents[1] / "webapp"
sys.path.insert(0, str(WEBAPP))

import server              # noqa: E402
import auth_io             # noqa: E402
import coding_engines      # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

RESULTS: list[bool] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    RESULTS.append(bool(cond))
    print(f"  {'✓' if cond else '❌'} {name}" + (f" — {detail}" if detail and not cond else ""))
    return cond


def _setup_hub() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="anja-sectest-"))
    (tmp / "config").mkdir(parents=True, exist_ok=True)
    (tmp / "config" / "projects.json").write_text(json.dumps(
        {"projects": [{"name": "wsA", "id": "wsA", "type": "office"},
                      {"name": "wsB", "id": "wsB", "type": "office"}]}))
    (tmp / "config.json").write_text("{}")
    (tmp / "workspaces" / "wsA").mkdir(parents=True, exist_ok=True)
    (tmp / "workspaces" / "wsB").mkdir(parents=True, exist_ok=True)
    (tmp / "workspaces" / "wsA.meta.yaml").write_text("kind: office\nmembers:\n  - mem\n")
    (tmp / "workspaces" / "wsB.meta.yaml").write_text("kind: office\nmembers:\n  - other\n")
    auth_io.create_user(tmp, "boss", "Boss", "ownerpass1", "owner")
    auth_io.create_user(tmp, "adm", "Adm", "adminpass1", "admin")
    auth_io.create_user(tmp, "mem", "Mem", "memberpass1", "member")
    return tmp


# --- unit-level (no HTTP) ---------------------------------------------------

def test_ssrf_check_and_pin():
    print("SSRF check + pin:")
    ok, err = server._ssrf_check("http://127.0.0.1/x")
    check("loopback bloccato", ok is None and err is not None)
    ok, err = server._ssrf_check("http://169.254.169.254/latest/meta-data/")
    check("metadata link-local bloccato", ok is None and err is not None)
    ok, err = server._ssrf_check("https://8.8.8.8/x")
    check("IP pubblico passa e ritorna l'IP pinnato", err is None and ok and ok[1] == "8.8.8.8", str((ok, err)))
    check("_pin_dns esiste (anti-rebinding)", hasattr(server, "_pin_dns"))


def test_session_token_roundtrip(hub: Path):
    print("Session token round-trip (L1: firma splittata per lunghezza, non su '.'):")
    N, ok = 300, 0
    for i in range(N):  # exp variabile → firme diverse → ~12% conterrebbe il byte 0x2e
        now = 1_700_000_000 + i
        tok = auth_io.make_session(hub, "mem", now=now)
        if auth_io.read_session(hub, tok, now=now) == "mem":
            ok += 1
    check(f"{N}/{N} token validi round-trip", ok == N, f"solo {ok}/{N} (regressione rsplit firma)")


def test_sandbox_drives_skip_permissions():
    print("coding_engines sandbox→skip-permissions:")
    with_sb = coding_engines._build_claude_cmd("p", {}, {"sandbox": True, "tools": ["Bash"]})
    without = coding_engines._build_claude_cmd("p", {}, {"tools": ["Bash"]})
    check("sandbox=true → --dangerously-skip-permissions presente", "--dangerously-skip-permissions" in with_sb)
    check("senza sandbox → skip-permissions ASSENTE", "--dangerously-skip-permissions" not in without)


# --- personal mode (auth NO-OP): headers, CSRF, throttle, traversal ---------

def test_security_headers(c: TestClient):
    print("Security headers:")
    r = c.get("/api/_status")
    h = r.headers
    check("Content-Security-Policy presente", "content-security-policy" in h)
    check("CSP connect-src 'self' (anti-esfiltrazione)", "connect-src 'self'" in h.get("content-security-policy", ""))
    check("X-Frame-Options: DENY", h.get("x-frame-options") == "DENY")
    check("X-Content-Type-Options: nosniff", h.get("x-content-type-options") == "nosniff")


def test_csrf_guard(c: TestClient):
    print("CSRF same-origin guard:")
    cross = c.post("/api/routines", json={}, headers={"origin": "http://evil.example"})
    check("POST cross-origin → 403", cross.status_code == 403, f"got {cross.status_code}")
    same = c.post("/api/routines", json={})   # niente Origin = client non-browser → passa il guard
    check("POST senza Origin NON è 403 (passa il guard)", same.status_code != 403, f"got {same.status_code}")


def test_login_throttle(c: TestClient):
    print("Rate-limit login:")
    codes = [c.post("/api/auth/login", json={"slug": "ghost-x", "password": "wrong"}).status_code for _ in range(6)]
    check("primi 5 tentativi → 401", codes[:5] == [401] * 5, str(codes))
    check("6° tentativo → 429 (lockout)", codes[5] == 429, str(codes))


def test_path_traversal_memory_user(c: TestClient):
    print("Path-traversal slug /api/memory/user:")
    r = c.get("/api/memory/user", params={"slug": "../../../../etc/passwd"})
    check("slug con traversal → 400", r.status_code == 400, f"got {r.status_code}")


# --- concierge mode: enforcement authz --------------------------------------

def test_concierge_authz(hub: Path, c: TestClient):
    print("Concierge authz:")
    auth_io.set_mode(hub, "concierge")

    # Sessione settata DIRETTAMENTE nel jar (via make_session) invece del flow di login:
    # deterministico, niente race sui Set-Cookie in rapida successione nel TestClient.
    def be(slug: str | None):
        c.cookies.clear()
        if slug:
            c.cookies.set(auth_io.SESSION_COOKIE, auth_io.make_session(hub, slug))

    # resolve→None: coding/run per un ws-membro arriva a 404 (dopo il gate) senza schedulare task
    import coding_worker
    coding_worker.resolve_workspace_dir = lambda h, w: None

    be(None)
    check("unauth POST /api/routines → 401", c.post("/api/routines", json={}).status_code == 401)

    be("mem")
    check("member POST /api/routines → 403", c.post("/api/routines", json={"name": "x", "scope": "hub", "schedule": "* * * * *", "prompt": "p"}).status_code == 403)
    check("member POST /api/telegram/config → 403", c.post("/api/telegram/config", json={}).status_code == 403)
    check("member POST /api/checkpoints/restore → 403", c.post("/api/checkpoints/restore", json={"ref": "HEAD"}).status_code == 403)
    check("member coding/run su ws NON-membro (wsB) → 403", c.post("/api/coding/run", json={"workspace": "wsB", "task": {"title": "t"}}).status_code == 403)
    r_wsa = c.post("/api/coding/run", json={"workspace": "wsA", "task": {"title": "t"}})
    check("member coding/run sul proprio ws (wsA) → gate passa (≠403)", r_wsa.status_code != 403, f"got {r_wsa.status_code}")

    be("boss")
    r_owner = c.post("/api/routines", json={})
    check("owner POST /api/routines → gate passa (≠401/403)", r_owner.status_code not in (401, 403), f"got {r_owner.status_code}")

    be("adm")
    r_esc = c.post("/api/auth/users", json={"slug": "evil", "password": "evilpass12", "role": "owner"})
    check("admin che crea un OWNER → 400 (escalation bloccata)", r_esc.status_code == 400, f"got {r_esc.status_code}")
    r_ok = c.post("/api/auth/users", json={"slug": "m2", "password": "m2pass1234", "role": "member"})
    check("admin che crea un member → 200 (consentito)", r_ok.status_code == 200, f"got {r_ok.status_code}")


def main() -> int:
    hub = _setup_hub()
    server.HUB_PATH = hub
    c = TestClient(server.app)

    test_ssrf_check_and_pin()
    test_session_token_roundtrip(hub)
    test_sandbox_drives_skip_permissions()
    test_security_headers(c)
    test_csrf_guard(c)
    test_login_throttle(c)
    test_path_traversal_memory_user(c)
    test_concierge_authz(hub, c)

    passed = sum(RESULTS)
    total = len(RESULTS)
    print(f"\n{'✅' if passed == total else '❌'} {passed}/{total} check superati")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
