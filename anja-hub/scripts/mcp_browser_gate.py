#!/usr/bin/env python3
"""mcp_browser_gate.py — F-AgentBrowser: gate read/act davanti a @playwright/mcp.

Thin proxy stdio↔stdio: spawna il server Playwright MCP come child e filtra il
protocollo. Il gate è SERVER-SIDE così vale per ogni harness (Claude, Grok,
LiteLLM) — stessa filosofia dell'outbox mail:

  * policy `read` (default F1): i tool di AZIONE (click/type/fill/…) spariscono
    da tools/list e tools/call li rifiuta.
  * `browser_evaluate` / run-code / install: negati SEMPRE (possono esfiltrare
    cookie con una fetch) — qualunque policy.
  * il resto passa inalterato, linea per linea.

Usage (materializzato dal writer nel .mcp.json dello scope):
  python3 mcp_browser_gate.py --policy read -- npx -y @playwright/mcp@latest --headless ...

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading

# Azioni: negate con policy read. (Nomi @playwright/mcp attuali + varianti note;
# il match è anche per prefisso su browser_run_code*.)
ACT_TOOLS = {
    "browser_click", "browser_type", "browser_fill_form", "browser_select_option",
    "browser_press_key", "browser_drag", "browser_drop", "browser_file_upload",
    "browser_handle_dialog", "browser_upload_image",
}
# Negati sempre, qualunque policy: JS arbitrario = esfiltrazione cookie.
HARD_DENY = {"browser_evaluate", "browser_install", "browser_start_tracing"}
HARD_DENY_PREFIXES = ("browser_run_code",)


def denied(tool: str, policy: str) -> bool:
    if tool in HARD_DENY or any(tool.startswith(p) for p in HARD_DENY_PREFIXES):
        return True
    if policy == "read" and tool in ACT_TOOLS:
        return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", default="read", choices=["read", "act"])
    ap.add_argument("child", nargs=argparse.REMAINDER,
                    help="-- seguito dal comando del server Playwright MCP")
    args = ap.parse_args()
    child_cmd = args.child[1:] if args.child[:1] == ["--"] else args.child
    if not child_cmd:
        print("[browser-gate] no child command", file=sys.stderr)
        return 2
    policy = args.policy

    proc = subprocess.Popen(child_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=sys.stderr, text=True, bufsize=1)
    out_lock = threading.Lock()
    tools_list_ids: set = set()

    def _reply(obj: dict) -> None:
        with out_lock:
            sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    def parent_to_child() -> None:
        """stdin nostro → child. Intercetta tools/call negati (risposta diretta)
        e traccia gli id delle tools/list per filtrare le risposte."""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                proc.stdin.write(line + "\n")
                proc.stdin.flush()
                continue
            method = req.get("method")
            if method == "tools/list":
                tools_list_ids.add(req.get("id"))
            elif method == "tools/call":
                name = str((req.get("params") or {}).get("name") or "")
                if denied(name, policy):
                    reason = ("negato dal gate browser: JS/tracing non consentiti"
                              if name in HARD_DENY or name.startswith("browser_run_code")
                              else f"policy '{policy}': i tool di azione non sono consentiti "
                                   f"su questo workspace (solo lettura/snapshot)")
                    _reply({"jsonrpc": "2.0", "id": req.get("id"),
                            "error": {"code": -32601, "message": reason}})
                    continue
            try:
                proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
                proc.stdin.flush()
            except BrokenPipeError:
                break
        try:
            proc.stdin.close()
        except Exception:
            pass

    def child_to_parent() -> None:
        """child → stdout nostro. Filtra i tool negati dalle risposte tools/list."""
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                with out_lock:
                    sys.stdout.write(line + "\n")
                    sys.stdout.flush()
                continue
            if msg.get("id") in tools_list_ids and isinstance(
                    (msg.get("result") or {}).get("tools"), list):
                tools_list_ids.discard(msg.get("id"))
                msg["result"]["tools"] = [t for t in msg["result"]["tools"]
                                          if not denied(str(t.get("name") or ""), policy)]
            _reply(msg)

    t_in = threading.Thread(target=parent_to_child, daemon=True)
    t_in.start()
    child_to_parent()          # termina quando il child chiude stdout
    proc.wait()
    return proc.returncode or 0


if __name__ == "__main__":
    sys.exit(main())
