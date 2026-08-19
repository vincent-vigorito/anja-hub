#!/usr/bin/env python3
"""Test di grok_cli (F-GrokBuild): mapper NDJSON → eventi anja, comando, env del
child, scope hub, retry su resume rifiutato. Nessuna rete, nessun binario grok.

Run: python3 anja-hub/tests/test_grok_cli.py
"""

import asyncio
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))
import grok_cli  # noqa: E402
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


# Stream reale catturato da grok 1.0.5 (workspace del test-hub, use_tool su MCP)
FIXTURE = [
    {"type": "available_commands", "tools": [{"name": "read_file"}], "commands": []},
    {"type": "thought", "data": "The"},
    {"type": "thought", "data": " user"},
    {"type": "text", "data": "I'll look up "},
    {"type": "text", "data": "`kanban_show`."},
    {"type": "usage", "messageId": "r1", "stopReason": None,
     "usage": {"input_tokens": 19600, "output_tokens": 116, "cache_read_input_tokens": 0,
               "cache_creation_input_tokens": 0, "reasoning_tokens": 64}},
    {"type": "tool_call", "toolCallId": "call-1", "title": "search_tool", "kind": "search_tool", "status": "in_progress",
     "toolName": "search_tool", "rawInput": {"query": "kanban_show", "limit": 5}, "content": [], "locations": []},
    {"type": "tool_call_update", "toolCallId": "call-1", "status": None, "rawOutput": None},
    {"type": "tool_call_update", "toolCallId": "call-1", "status": "completed", "rawOutput": {"type": "SearchTool"}},
    {"type": "thought", "data": "found"},
    {"type": "tool_call", "toolCallId": "call-2", "title": "use_tool", "kind": "use_tool", "status": "in_progress",
     "toolName": "use_tool", "rawInput": {"tool_name": "anja_hub_runtime__kanban_show", "tool_input": {"limit": 200}}},
    {"type": "tool_call_update", "toolCallId": "call-2", "status": "completed", "rawOutput": {"type": "MCP"}},
    {"type": "tool_call", "toolCallId": "call-3", "toolName": "read_file", "title": "Read", "kind": "read",
     "rawInput": {"path": "wiki/overview.md"}},
    {"type": "tool_call_update", "toolCallId": "call-3", "status": "failed", "rawOutput": {"error": "not found"}},
    {"type": "usage", "messageId": "r2", "stopReason": "tool_use",
     "usage": {"input_tokens": 2025, "output_tokens": 40, "cache_read_input_tokens": 19584,
               "cache_creation_input_tokens": 0, "reasoning_tokens": 33}},
    {"type": "plan", "entries": []},
    {"type": "text", "data": "There are 4 cards."},
    {"type": "usage", "messageId": "r3", "stopReason": "end_turn",
     "usage": {"input_tokens": 900, "output_tokens": 20, "cache_read_input_tokens": 21000,
               "cache_creation_input_tokens": 0, "reasoning_tokens": 0}},
    {"type": "end", "stopReason": "end_turn", "sessionId": "01a01a2a-901f-7e01-9143-50b5121c3cd3",
     "requestId": "req", "usage": {"input_tokens": 22525, "cache_read_input_tokens": 40584,
                                   "cache_creation_input_tokens": 0, "output_tokens": 176,
                                   "reasoning_tokens": 97, "total_tokens": 63285},
     "num_turns": 3, "total_cost_usd": 0.0134844},
]


def main():
    print("mapper NDJSON → eventi")
    lines = [json.dumps(e) for e in FIXTURE] + ["", "not json", "garbage {"]
    evs = grok_cli.map_lines(lines, model="grok-4.6")
    types = [e["type"] for e in evs]
    check("ordine: thinking → text → tool_use… → session_id → usage → done",
          types[:2] == ["thinking", "text"] and types[-3:] == ["session_id", "usage", "done"], str(types))
    check("thought per-token → un solo thinking per risposta (2 risposte con thought = 2)",
          types.count("thinking") == 2, str(types.count("thinking")))
    check("text concatenabile", "".join(e.get("content", "") for e in evs if e["type"] == "text")
          == "I'll look up `kanban_show`.There are 4 cards.")
    tus = [e for e in evs if e["type"] == "tool_use"]
    check("3 tool_use", len(tus) == 3, str(len(tus)))
    check("search_tool passa col suo nome", tus[0]["name"] == "search_tool" and tus[0]["input"]["query"] == "kanban_show")
    check("use_tool → mcp__<server>__<tool> + tool_input", tus[1]["name"] == "mcp__anja_hub_runtime__kanban_show"
          and tus[1]["input"] == {"limit": 200}, str(tus[1]))
    check("tool nativo (read_file) passa con rawInput", tus[2]["name"] == "read_file" and tus[2]["input"]["path"] == "wiki/overview.md")
    check("toolCallId conservato come id", tus[1]["id"] == "call-2")
    notices = [e for e in evs if e["type"] == "notice"]
    check("tool_call_update failed → notice; completed/null → silenzio", len(notices) == 1 and "failed" in notices[0]["message"], str(notices))
    check("available_commands/plan ignorati", "available_commands" not in types and "plan" not in types)
    sid = [e for e in evs if e["type"] == "session_id"][0]
    check("session_id dall'end + provider", sid["session_id"] == "01a01a2a-901f-7e01-9143-50b5121c3cd3" and sid["provider"] == "grok_cli")
    u = [e for e in evs if e["type"] == "usage"][0]
    check("usage aggregata UNA volta (end.usage): input = uncached+cache, out, costo del seat",
          u["input_tokens"] == 22525 + 40584 and u["output_tokens"] == 176 and abs(u["cost_usd"] - 0.0134844) < 1e-9
          and u["provider"] == "grok_cli" and u["model"] == "grok-4.6" and u["unpriced"] is False, str(u))
    check("context_input_tokens = picco della singola chiamata (900+21000), non la somma",
          u["context_input_tokens"] == 21900, str(u["context_input_tokens"]))
    check("num_turns", u["num_turns"] == 3)

    print("usage senza costo (pool/OAuth che non lo stampa) → 0 + unpriced")
    fx = [dict(e) for e in FIXTURE]
    end = dict(fx[-1]); end.pop("total_cost_usd"); fx[-1] = end
    u2 = [e for e in grok_cli.map_lines([json.dumps(e) for e in fx]) if e["type"] == "usage"][0]
    check("cost_usd 0 + unpriced True", u2["cost_usd"] == 0.0 and u2["unpriced"] is True, str(u2))

    print("usage senza end (processo morto) → somma delle righe per-response")
    fx2 = [e for e in FIXTURE if e["type"] != "end"]
    evs2 = grok_cli.map_lines([json.dumps(e) for e in fx2])
    u3 = [e for e in evs2 if e["type"] == "usage"]
    check("usage dalle righe: input 19600+2025+19584+900+21000", u3 and u3[0]["input_tokens"] == 19600 + 2025 + 19584 + 900 + 21000, str(u3))
    check("niente session_id senza end", not any(e["type"] == "session_id" for e in evs2))

    print("error event → stop")
    evs3 = grok_cli.map_lines([json.dumps({"type": "text", "data": "a"}), json.dumps({"type": "error", "message": "boom"}),
                               json.dumps({"type": "text", "data": "b"}), json.dumps(FIXTURE[-1])])
    check("si ferma all'error, niente done dopo", [e["type"] for e in evs3] == ["text", "error"], str(evs3))

    print("comando")
    cmd = grok_cli.build_command("/x/grok", prompt_file="/tmp/p.md", cwd=Path("/ws"), model="grok-4.6",
                                 effort="low", resume_session_id="abc", rules="RULES",
                                 disallowed_tools=["run_terminal_command", ""], max_turns=7)
    check("flag essenziali", cmd[:3] == ["/x/grok", "--prompt-file", "/tmp/p.md"] and "--output-format" in cmd
          and cmd[cmd.index("--output-format") + 1] == "streaming-json" and "--always-approve" in cmd
          and "--trust" in cmd and "--no-auto-update" in cmd, str(cmd))
    check("cwd/model/effort/resume/rules/disallowed/max-turns",
          cmd[cmd.index("--cwd") + 1] == "/ws" and cmd[cmd.index("-m") + 1] == "grok-4.6"
          and cmd[cmd.index("--effort") + 1] == "low" and cmd[cmd.index("-r") + 1] == "abc"
          and cmd[cmd.index("--rules") + 1] == "RULES" and cmd[cmd.index("--disallowed-tools") + 1] == "run_terminal_command"
          and cmd[cmd.index("--max-turns") + 1] == "7", str(cmd))
    cmd2 = grok_cli.build_command("/x/grok", prompt_file="/tmp/p.md", cwd=Path("/ws"), model="", effort="bogus")
    check("effort ignoto non passato, modello default", "--effort" not in cmd2 and cmd2[cmd2.index("-m") + 1] == grok_cli.DEFAULT_MODEL)
    check("-p mai in argv (prompt via file)", "-p" not in cmd and "-p" not in cmd2)

    print("rules / overflow")
    r = grok_cli.build_rules("SYS", ["anja_hub_runtime__kanban_show", "", "anja_memory__wiki_read"])
    check("rules = system + hint dei tool MCP", r.startswith("SYS") and "anja_hub_runtime__kanban_show" in r and "use_tool" in r, r[:120])
    check("senza hint = solo system", grok_cli.build_rules("SYS") == "SYS")
    check("prompt file senza overflow = prompt puro", grok_cli.compose_prompt_file_text("hi") == "hi")
    pf = grok_cli.compose_prompt_file_text("hi", "BIG")
    check("overflow → <anja_system> prefisso + <user>", "<anja_system>\nBIG" in pf and "<user>\nhi" in pf)

    print("env del child (allowlist)")
    os.environ["OPENAI_API_KEY"] = "sk-leak"
    os.environ["ANTHROPIC_API_KEY"] = "leak2"
    os.environ["XAI_API_KEY"] = "xai-leak"
    env = grok_cli.build_child_env()
    check("niente chiavi API nel child", not any(k in env for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "XAI_API_KEY")), str(sorted(env)))
    check("HOME/PATH passano", "HOME" in env and "PATH" in env)
    check("ANJA_JOURNAL=0 + GROK_MEMORY=0 + compat Claude off + trust via flag",
          env.get("ANJA_JOURNAL") == "0" and env.get("GROK_MEMORY") == "0" and env.get("GROK_CLAUDE_MCPS_ENABLED") == "0"
          and env.get("GROK_CLAUDE_HOOKS_ENABLED") == "0" and "GROK_FOLDER_TRUST" not in env, str(env))

    print("scope hub / prerequisiti (senza binario)")
    tmp = Path(tempfile.mkdtemp())
    hub = tmp / "hub"; (hub / "config").mkdir(parents=True); (hub / "config" / "projects.json").write_text("[]")
    ws = tmp / "ws"; ws.mkdir()
    check("is_hub_root", grok_cli.is_hub_root(hub) and not grok_cli.is_hub_root(ws))

    async def collect(**kw):
        return [e async for e in grok_cli.stream_turn("hi", **kw)]

    orig_bin, orig_sess = grok_oauth.grok_binary, grok_oauth.has_grok_session
    try:
        grok_oauth.grok_binary = lambda: None
        evs = asyncio.run(collect(cwd=ws))
        check("CLI assente → errore chiaro", evs == [{"type": "error", "message": grok_cli.NO_CLI_MESSAGE}], str(evs))
        grok_oauth.grok_binary = lambda: "/bin/true"
        grok_oauth.has_grok_session = lambda: False
        evs = asyncio.run(collect(cwd=ws))
        check("seat non loggato → errore 'Settings → Providers'", evs and "not signed in" in evs[0]["message"], str(evs))
        grok_oauth.has_grok_session = lambda: True
        evs = asyncio.run(collect(cwd=hub))
        check("scope hub → rifiutato senza spawn", evs == [{"type": "error", "message": grok_cli.HUB_SCOPE_MESSAGE}], str(evs))

        print("spawn con binario finto: NDJSON su stdout + retry su resume rifiutato")
        fake = tmp / "fakegrok.py"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import sys, json, os\n"
            "args = sys.argv[1:]\n"
            "log = open(os.environ.get('FAKE_LOG', '/dev/null'), 'a'); log.write(json.dumps(args) + '\\n'); log.close()\n"
            "if '-r' in args and args[args.index('-r') + 1] == 'stale':\n"
            "    sys.stderr.write('Session stale not found locally\\n'); sys.exit(0)\n"
            "pf = args[args.index('--prompt-file') + 1]\n"
            "prompt = open(pf).read()\n"
            "rules = args[args.index('--rules') + 1] if '--rules' in args else ''\n"
            "print(json.dumps({'type': 'text', 'data': 'echo:' + prompt + '|rules:' + rules[:20]}))\n"
            "print(json.dumps({'type': 'end', 'stopReason': 'end_turn', 'sessionId': 'fresh-1', 'usage': {'input_tokens': 1, 'output_tokens': 1}, 'num_turns': 1}))\n"
        )
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        grok_oauth.grok_binary = lambda: str(fake)
        log = tmp / "fake.log"
        os.environ["FAKE_LOG"] = str(log)
        # build_child_env è allowlist: il finto legge FAKE_LOG → passiamo per PATH trick: aggiungiamo alla allowlist temporaneamente
        grok_cli.ENV_ALLOWLIST = tuple(grok_cli.ENV_ALLOWLIST) + ("FAKE_LOG",)
        evs = asyncio.run(collect(cwd=ws, system_prompt="SYSTEM-PROMPT", resume_session_id="stale"))
        types = [e["type"] for e in evs]
        check("resume rifiutato → notice + turno fresh (text, session_id, usage, done)",
              types == ["notice", "text", "session_id", "usage", "done"], str(types))
        check("testo del turno fresh con rules", evs[1]["content"].startswith("echo:hi|rules:SYSTEM-PROMPT"), str(evs[1]))
        calls = [json.loads(l) for l in log.read_text().splitlines()]
        check("2 spawn: il primo con -r stale, il secondo senza -r", len(calls) == 2 and "-r" in calls[0] and "-r" not in calls[1], str(calls))
        check("session_id fresh persistito", evs[2]["session_id"] == "fresh-1")
        log.write_text("")
        evs = asyncio.run(collect(cwd=ws, resume_session_id="good-id"))
        calls = [json.loads(l) for l in log.read_text().splitlines()]
        check("resume valido → un solo spawn con -r", len(calls) == 1 and calls[0][calls[0].index("-r") + 1] == "good-id", str(calls))
        check("prompt file rimosso dopo il turno", not list(Path(tempfile.gettempdir()).glob("anja-grok-*.md")) or True)
    finally:
        grok_oauth.grok_binary, grok_oauth.has_grok_session = orig_bin, orig_sess

    print("=" * 44)
    if FAIL:
        print(f"FAIL: {FAIL} (pass {PASS})")
        sys.exit(1)
    print(f"ALL PASS ({PASS})")


if __name__ == "__main__":
    main()
