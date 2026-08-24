"""F-DelegateHardening (b)/(c): allowlist Bash, confinamento path, deny segreti.

    /opt/homebrew/opt/python@3.12/bin/python3.12 anja-hub/tests/test_agent_guard.py
"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "anja-hub" / "webapp"))

import agent_guard as g            # noqa: E402
import blueprint_scaffold as bs    # noqa: E402

PASS, FAIL = 0, 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label} {detail}")


def test_bash():
    print("bash_denied")
    al = ["swerpicommerce *", "python3 scripts/*", "git status*"]
    check("comando in allowlist → ok", g.bash_denied("swerpicommerce products list", al) is None)
    check("fuori allowlist → deny", g.bash_denied("curl http://evil", al) is not None)
    check("segmento concatenato ostile → deny (il fnmatch non attraversa i &&)",
          g.bash_denied("git status && curl http://evil", al) is not None)
    check("tutti i segmenti in allowlist → ok",
          g.bash_denied("git status && python3 scripts/build.py", al) is None)
    check("pipe fuori lista → deny", g.bash_denied("git status | sh", al) is not None)
    check("command substitution → deny", g.bash_denied("python3 scripts/$(whoami).py", al) is not None)
    check("backtick → deny", g.bash_denied("git status`id`", al) is not None)
    check("allowlist vuota → Bash negato", g.bash_denied("ls", []) is not None)
    check("allowlist None → nessun vincolo di forma", g.bash_denied("qualunque cosa", None) is None)
    check("segreti negati anche con None", g.bash_denied("cat ../ws/.secrets.env", None) is not None)
    check("menzione .anjawiki/mail → deny", g.bash_denied("grep x .anjawiki/mail/main/creds", al) is not None)
    check("case-insensitive sui segreti", g.bash_denied("cat BACKUP.KEY", None) is not None)


def test_paths():
    print("path_denied")
    tmp = Path(tempfile.mkdtemp())
    # il temp macOS (/var/folders) è nei TMP_ROOTS consentiti → per testare il
    # confinamento restringiamo i roots temporanei del guard
    g.TMP_ROOTS = (Path("/tmp"), Path("/private/tmp"))
    ws = tmp / "hub" / "workspaces" / "alpha"
    other = tmp / "hub" / "workspaces" / "beta"
    (ws / "files").mkdir(parents=True)
    other.mkdir(parents=True)
    roots = [ws]
    check("dentro il workspace → ok", g.path_denied(str(ws / "files" / "x.md"), roots) is None)
    check("relativo (risolto sul root) → ok", g.path_denied("files/x.md", roots) is None)
    check("altro workspace → deny", g.path_denied(str(other / "y.md"), roots) is not None)
    check("traversal ../ → deny", g.path_denied(str(ws / ".." / "beta" / "y.md"), roots) is not None)
    check("/tmp consentito", g.path_denied("/tmp/scratch.txt", roots) is None)
    check("secret glob dentro il ws → deny comunque",
          g.path_denied(str(ws / ".anjawiki" / "mail" / "m" / "creds.env"), roots) is not None)
    check("token json → deny", g.path_denied(str(ws / "google-token.json"), roots) is not None)
    check("roots None → solo check segreti", g.path_denied(str(other / "y.md"), None) is None
          and g.path_denied(str(other / ".env"), None) is not None)


def test_precheck():
    print("precheck_secrets (sessioni interattive)")
    check("Read su .secrets.env → deny", g.precheck_secrets("Read", {"file_path": "/x/.secrets.env"}))
    check("Read normale → ok", g.precheck_secrets("Read", {"file_path": "/x/doc.md"}) is None)
    check("Bash con token.json → deny", g.precheck_secrets("Bash", {"command": "cat google-token.json"}))
    check("Bash normale → ok", g.precheck_secrets("Bash", {"command": "ls -la"}) is None)
    check("Edit su oauth-client → deny", g.precheck_secrets("Edit", {"file_path": "a/google-oauth-client.json"}))
    check("tool MCP → passthrough", g.precheck_secrets("mcp__anja_mail__mail_search", {"query": ".env"}) is None)


def test_plan():
    print("delegate_permission_plan")
    natives = ["Read", "Write", "Edit", "Bash", "Grep", "Glob", "LS"]
    mcps = ["mcp__anja_marketing__*"]
    p = g.delegate_permission_plan({"bash_allowlist": ["giv *"]}, natives, mcps)
    check("allowlist presente → guard mode, nativi FUORI da allowed_tools",
          p["mode"] == "default" and p["guarded"]
          and not any(t in p["allowed_tools"] for t in natives)
          and "mcp__anja_marketing__*" in p["allowed_tools"], str(p))
    check("granted = i nativi dichiarati", p["granted"] == set(natives))
    p = g.delegate_permission_plan({"bash_allowlist": []}, ["Read", "Write"], mcps)
    check("allowlist [] → guard mode, Bash negato dal guard", p["guarded"] and p["bash_allowlist"] == [])
    p = g.delegate_permission_plan({"bypass_permissions": True}, natives, mcps)
    check("legacy bypass senza allowlist → invariato", p["mode"] == "bypassPermissions" and not p["guarded"])
    p = g.delegate_permission_plan({}, ["Read"], mcps)
    check("default → least-privilege storico", p["mode"] == "default" and not p["guarded"])


def test_blueprint():
    print("blueprint: configs + adapter swerpi")
    base = REPO / "anja-hub" / "blueprints" / "marketing-site" / "agents"
    social = json.loads((base / "social.json").read_text())
    dev = json.loads((base / "dev.json").read_text())
    copy = json.loads((base / "seo-copy.json").read_text())
    check("social: allowlist al posto del bypass", "bypass_permissions" not in social
          and social.get("bash_allowlist"), str(social.get("bash_allowlist")))
    check("dev (wp): niente Bash in delega + guard", "Bash" not in dev.get("delegate_tools", [])
          and dev.get("bash_allowlist") == [])
    check("seo-copy: guard, no bypass", "bypass_permissions" not in copy and copy.get("bash_allowlist") == [])
    adapted = bs._adapt_agent_for_backend(json.loads((base / "dev.json").read_text()), "swerpi")
    check("adapter swerpi: Bash torna in delega con allowlist CLI",
          "Bash" in adapted.get("delegate_tools", [])
          and "swerpicommerce *" in adapted.get("bash_allowlist", []), str(adapted.get("bash_allowlist")))
    adapted_wp = bs._adapt_agent_for_backend(json.loads((base / "dev.json").read_text()), "wp")
    check("adapter wp: no-op", "Bash" not in adapted_wp.get("delegate_tools", []))


def main():
    test_bash()
    test_paths()
    test_precheck()
    test_plan()
    test_blueprint()
    print("=" * 44)
    if FAIL:
        print(f"FAIL: {FAIL} (pass {PASS})")
        sys.exit(1)
    print(f"ALL PASS ({PASS})")


if __name__ == "__main__":
    main()
