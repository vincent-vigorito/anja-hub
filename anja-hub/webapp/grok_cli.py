"""grok_cli.py — Grok Build seat as a chat backend (provider `grok_cli`).

Plan A of anja-grok-build-subscription-design: the official `grok` CLI *is* the
agent. One turn = one `grok -p … --output-format streaming-json` process, spawned
with `cwd` = hub|workspace so Grok sees the files and the `.mcp.json` of that
folder (trusted via `--trust`). AnjaHub is the client: it passes the composed
system prompt as `--rules`, maps the NDJSON stream to anja events and persists the
Grok `sessionId` for `-r` resume. No second tool loop on our side.

Not the xAI API key (`provider=xai` via LiteLLM is untouched).

Stdlib + asyncio.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import tempfile
import time
from pathlib import Path
from typing import AsyncIterator, Iterable, Optional

import grok_oauth

DEFAULT_MODEL = "grok-4.6"
DEFAULT_TIMEOUT_SEC = int(os.environ.get("ANJA_GROK_CLI_TIMEOUT", "900"))
DEFAULT_MAX_TURNS = int(os.environ.get("ANJA_GROK_CLI_MAX_TURNS", "60"))
# `--rules` rides argv: Linux caps a single argument at 128 KiB. Above this the
# composer block goes into the prompt file as a prefix instead.
RULES_ARGV_MAX = 100_000
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

HUB_SCOPE_MESSAGE = ("Grok Build is for workspaces (agent harness). "
                     "Use Claude for hub chat, or open a workspace chat.")
NOT_SIGNED_IN_MESSAGE = "Grok Build not signed in — Settings → Providers → Grok Build."
NO_CLI_MESSAGE = ("Grok Build CLI not installed on the host: "
                  "curl -fsSL https://x.ai/cli/install.sh | bash")

# Least-privilege child env (pattern coding_engines.ENV_ALLOWLIST). HOME is the
# user that owns ~/.grok/auth.json. Never os.environ.copy(): hub secrets stay here.
ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "USER", "SHELL", "TMPDIR", "TZ", "GROK_HOME")
CHILD_ENV_FIXED = {
    "TERM": "dumb",
    # no machine sessions in the anja journal, no side effects from hooks
    "ANJA_JOURNAL": "0",
    "ANJA_AUTO_SUMMARY": "0",
    "ANJA_WIKI_EMBED": "0",
    # Grok: no auto-update noise on stderr, no cross-session memory bleed
    "GROK_DISABLE_AUTOUPDATER": "1",
    "GROK_MEMORY": "0",
    # Grok scans the host user's Claude Code/Cursor config too (MCPs, hooks, skills,
    # 48 skills + 14 plugins on a dev Mac): keep the child to the cwd's .mcp.json.
    "GROK_CLAUDE_MCPS_ENABLED": "0",
    "GROK_CLAUDE_HOOKS_ENABLED": "0",
    "GROK_CLAUDE_SKILLS_ENABLED": "0",
    "GROK_CLAUDE_RULES_ENABLED": "0",
    "GROK_CLAUDE_AGENTS_ENABLED": "0",
    "GROK_CURSOR_MCPS_ENABLED": "0",
    "GROK_CURSOR_SKILLS_ENABLED": "0",
    # kanban_show / wiki pages exceed the 20 kB default easily
    "GROK_MAX_MCP_OUTPUT_BYTES": "60000",
}


def build_child_env() -> dict:
    env = {k: os.environ[k] for k in ENV_ALLOWLIST if k in os.environ}
    env.update(CHILD_ENV_FIXED)
    return env


def is_hub_root(cwd: Path) -> bool:
    """The hub root has config/projects.json; workspaces/agents don't."""
    try:
        return (Path(cwd) / "config" / "projects.json").is_file()
    except Exception:
        return False


def build_rules(system_prompt: str, tool_hints: Optional[Iterable[str]] = None) -> str:
    """System-prompt block for `--rules`. Grok discovers MCP tools lazily
    (`search_tool`/`use_tool`), so we name the ones the scoper knows are mounted."""
    parts = []
    if system_prompt:
        parts.append(system_prompt.strip())
    hints = [h for h in (tool_hints or []) if h]
    if hints:
        parts.append(
            "MCP tools mounted in this folder (call them with use_tool, "
            "tool_name = <server>__<tool>, e.g. anja_hub_runtime__kanban_show; "
            "search_tool only if you need one not listed): " + ", ".join(hints)
        )
    return "\n\n".join(parts)


def build_command(binary: str, *, prompt_file: str, cwd: Path, model: str,
                  effort: Optional[str] = None, resume_session_id: Optional[str] = None,
                  rules: str = "", disallowed_tools: Optional[Iterable[str]] = None,
                  max_turns: Optional[int] = None) -> list[str]:
    cmd = [binary, "--prompt-file", prompt_file,
           "--output-format", "streaming-json",
           "--cwd", str(cwd),
           "--always-approve",   # == --permission-mode bypassPermissions; v1 headless contract
           "--trust",            # folder trust → .mcp.json/hooks of cwd (hidden alias of --trust-folder)
           "--no-auto-update",
           "-m", model or DEFAULT_MODEL]
    if effort and effort in EFFORTS:
        cmd += ["--effort", effort]
    if resume_session_id:
        cmd += ["-r", resume_session_id]
    if rules:
        cmd += ["--rules", rules]
    dis = [d for d in (disallowed_tools or []) if d]
    if dis:
        cmd += ["--disallowed-tools", ",".join(dis)]
    if max_turns:
        cmd += ["--max-turns", str(int(max_turns))]
    return cmd


def compose_prompt_file_text(user_prompt: str, rules_overflow: str = "") -> str:
    if not rules_overflow:
        return user_prompt
    return f"<anja_system>\n{rules_overflow}\n</anja_system>\n\n<user>\n{user_prompt}\n</user>\n"


# ============================================================
# NDJSON → anja events
# ============================================================

def _mcp_name(raw_input: dict) -> str:
    tn = str((raw_input or {}).get("tool_name") or "")
    if "__" in tn:
        srv, tool = tn.split("__", 1)
        return f"mcp__{srv}__{tool}"
    return f"mcp__{tn}" if tn else "use_tool"


class StreamState:
    """Per-turn accumulator for the mapper (pure; used by tests with fixtures)."""

    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model
        self.session_id = ""
        self.thinking_emitted = False
        self.usage_rows: list[dict] = []
        self.end_usage: Optional[dict] = None
        self.cost_usd: Optional[float] = None
        self.stop_reason = ""
        self.saw_end = False
        self.saw_error = False
        self.num_turns = 0
        self.returncode: Optional[int] = None


def map_event(ev: dict, st: StreamState) -> list[dict]:
    """Map one Grok streaming-json object to 0..n anja events."""
    t = ev.get("type")
    if t == "text":
        data = ev.get("data") or ""
        st.thinking_emitted = False
        return [{"type": "text", "content": data}] if data else []
    if t == "thought":
        if st.thinking_emitted:
            return []
        st.thinking_emitted = True
        return [{"type": "thinking"}]
    if t == "tool_call":
        raw = ev.get("rawInput") or {}
        name = ev.get("toolName") or ev.get("title") or "tool"
        if name == "use_tool":
            out_name, out_input = _mcp_name(raw), (raw.get("tool_input") or {})
        else:
            out_name, out_input = name, raw
        st.thinking_emitted = False
        return [{"type": "tool_use", "name": out_name, "input": out_input,
                 "id": ev.get("toolCallId") or ""}]
    if t == "tool_call_update":
        # v1: results stay silent (parity with the Claude path); failures surface as text-less notice
        if ev.get("status") == "failed":
            msg = json.dumps(ev.get("rawOutput") or ev.get("content") or "", ensure_ascii=False)[:300]
            return [{"type": "notice", "message": f"tool failed: {msg}"}]
        return []
    if t == "usage":
        u = ev.get("usage") or {}
        if u:
            st.usage_rows.append(u)
        st.thinking_emitted = False
        return []
    if t == "end":
        st.saw_end = True
        st.session_id = ev.get("sessionId") or st.session_id
        st.end_usage = ev.get("usage") or None
        st.cost_usd = ev.get("total_cost_usd")
        st.stop_reason = ev.get("stopReason") or ""
        st.num_turns = int(ev.get("num_turns") or 0)
        return []
    if t == "error":
        st.saw_error = True
        return [{"type": "error", "message": str(ev.get("message") or "grok error")}]
    if t == "max_turns_reached":
        return [{"type": "notice", "message": "Grok reached the turn limit for this message."}]
    # plan / available_commands / auto_compact_* / unknown → ignore
    return []


def usage_event(st: StreamState) -> Optional[dict]:
    """One aggregated usage event per turn (cost_store records per event — never
    emit per-response rows or the bill doubles)."""
    if st.end_usage:
        u = st.end_usage
        inp = int(u.get("input_tokens") or 0)
        cache = int(u.get("cache_read_input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0)
        out = int(u.get("output_tokens") or 0)
    elif st.usage_rows:
        inp = sum(int(r.get("input_tokens") or 0) for r in st.usage_rows)
        cache = sum(int(r.get("cache_read_input_tokens") or 0) + int(r.get("cache_creation_input_tokens") or 0)
                    for r in st.usage_rows)
        out = sum(int(r.get("output_tokens") or 0) for r in st.usage_rows)
    else:
        return None
    # context fill = biggest single prompt (uncached + cached), not the sum over tool rounds
    peak = 0
    for r in st.usage_rows:
        peak = max(peak, int(r.get("input_tokens") or 0) + int(r.get("cache_read_input_tokens") or 0)
                   + int(r.get("cache_creation_input_tokens") or 0))
    ctx_window = 0
    for m in grok_oauth.grok_models():
        if m.get("id") == st.model:
            ctx_window = int(m.get("context_window") or 0)
    ev = {
        "type": "usage",
        "provider": "grok_cli",
        "model": st.model,
        "input_tokens": inp + cache,
        "context_input_tokens": peak or (inp + cache),
        "output_tokens": out,
        "total_tokens": inp + cache + out,
        "cache_read_tokens": cache,
        "context_window": ctx_window,
        # the seat usually reports a complete cost; when it doesn't, 0 + unpriced (never invent a bill)
        "cost_usd": float(st.cost_usd) if st.cost_usd is not None else 0.0,
        "unpriced": st.cost_usd is None,
        "num_turns": st.num_turns,
    }
    return ev


def map_lines(lines: Iterable[str], model: str = DEFAULT_MODEL) -> list[dict]:
    """Fixture-friendly: whole NDJSON → anja events incl. the trailing session_id/usage/done."""
    st = StreamState(model)
    out: list[dict] = []
    for line in lines:
        line = (line or "").strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        out.extend(map_event(ev, st))
        if st.saw_error:
            return out
    out.extend(_tail_events(st))
    return out


def _tail_events(st: StreamState) -> list[dict]:
    out = []
    if st.session_id:
        out.append({"type": "session_id", "session_id": st.session_id, "provider": "grok_cli"})
    u = usage_event(st)
    if u:
        out.append(u)
    out.append({"type": "done"})
    return out



# ============================================================
# MCP tool hints (Grok discovers MCP lazily; naming the mounted tools saves a
# search round ≈ 20k tokens/turn). Cached per (cwd, server, command) for 10 min.
# ============================================================

_HINT_CACHE: dict = {}
HINT_TTL_SEC = 600
HINT_MAX = 80


async def tool_hints_for(cwd: Path, scoped_servers: Optional[Iterable[str]] = None) -> list[str]:
    """`<server>__<tool>` names of the servers in cwd/.mcp.json (restricted to
    `scoped_servers` when given — the scoper already decided what is relevant)."""
    try:
        from llm_router import discover_mcp_servers, _mcp_list_tools, build_subprocess_env
    except Exception:
        return []
    servers = discover_mcp_servers(Path(cwd))
    if scoped_servers is not None:
        allowed = set(scoped_servers)
        servers = {k: v for k, v in servers.items() if k in allowed}
    if not servers:
        return []
    env = None
    hints: list[str] = []
    now = time.monotonic()
    for srv, cfg in servers.items():
        if not isinstance(cfg, dict) or not cfg.get("command"):
            continue
        key = (str(cwd), srv, json.dumps([cfg.get("command"), cfg.get("args"), cfg.get("env")], sort_keys=True))
        hit = _HINT_CACHE.get(key)
        if hit and now - hit[0] < HINT_TTL_SEC:
            names = hit[1]
        else:
            if env is None:
                env = build_subprocess_env(Path(cwd))
            try:
                tools = await asyncio.wait_for(_mcp_list_tools(srv, cfg, env), timeout=20)
            except Exception:
                tools = []
            names = [str(t.get("name") or "").replace(".", "_") for t in tools if t.get("name")]
            _HINT_CACHE[key] = (now, names)
        hints.extend(f"{srv}__{n}" for n in names)
    return hints[:HINT_MAX]

# ============================================================
# spawn + stream
# ============================================================

async def _kill_group(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGINT)
    except Exception:
        try:
            proc.send_signal(signal.SIGINT)
        except Exception:
            pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
        return
    except Exception:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


async def _run_process(cmd: list[str], *, cwd: Path, st: StreamState, timeout_sec: int,
                       stderr_sink: list[bytes]) -> AsyncIterator[dict]:
    """Spawn one `grok -p` and yield mapped events. Sets st.saw_end / st.saw_error /
    st.returncode; the caller decides on tail events and retries."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(cwd), env=build_child_env(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except Exception as e:
        st.saw_error = True
        yield {"type": "error", "message": f"grok spawn failed: {type(e).__name__}: {e}"}
        return

    deadline = time.monotonic() + timeout_sec

    async def _drain_stderr():
        try:
            while True:
                chunk = await proc.stderr.read(4096)
                if not chunk:
                    break
                if sum(len(c) for c in stderr_sink) < 64_000:
                    stderr_sink.append(chunk)
        except Exception:
            pass

    err_task = asyncio.create_task(_drain_stderr())
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                await _kill_group(proc)
                st.saw_error = True
                yield {"type": "error", "message": f"Grok Build timed out after {timeout_sec}s"}
                return
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
            except asyncio.TimeoutError:
                await _kill_group(proc)
                st.saw_error = True
                yield {"type": "error", "message": f"Grok Build timed out after {timeout_sec}s"}
                return
            if not line:
                break
            s = line.decode("utf-8", "replace").strip()
            if not s or not s.startswith("{"):
                continue
            try:
                ev = json.loads(s)
            except Exception:
                continue
            for out in map_event(ev, st):
                yield out
            if st.saw_error:
                await _kill_group(proc)
                return
        await proc.wait()
        try:
            await asyncio.wait_for(err_task, timeout=2)
        except Exception:
            pass
        st.returncode = proc.returncode
    except (asyncio.CancelledError, GeneratorExit):
        await _kill_group(proc)
        raise
    except Exception as e:
        await _kill_group(proc)
        st.saw_error = True
        yield {"type": "error", "message": f"grok stream failed: {type(e).__name__}: {e}"}
    finally:
        err_task.cancel()


async def stream_turn(
    user_prompt: str,
    *,
    cwd: Path,
    system_prompt: str = "",
    model: str = DEFAULT_MODEL,
    effort: Optional[str] = None,
    resume_session_id: Optional[str] = None,
    tool_hints: Optional[Iterable[str]] = None,
    disallowed_tools: Optional[Iterable[str]] = None,
    timeout_sec: Optional[int] = None,
    max_turns: Optional[int] = None,
    allow_hub_scope: bool = False,
) -> AsyncIterator[dict]:
    """Async generator of anja events for one Grok Build turn."""
    binary = grok_oauth.grok_binary()
    if not binary:
        yield {"type": "error", "message": NO_CLI_MESSAGE}
        return
    if not grok_oauth.has_grok_session():
        yield {"type": "error", "message": NOT_SIGNED_IN_MESSAGE}
        return
    cwd = Path(cwd)
    if is_hub_root(cwd) and not allow_hub_scope:
        # v1 decision (design §8): no shell/write agent loose on the hub root
        yield {"type": "error", "message": HUB_SCOPE_MESSAGE}
        return

    rules = build_rules(system_prompt, tool_hints)
    overflow = ""
    if len(rules) > RULES_ARGV_MAX:
        rules, overflow = "", rules
    prompt_text = compose_prompt_file_text(user_prompt, overflow)
    timeout_sec = timeout_sec or DEFAULT_TIMEOUT_SEC
    max_turns = max_turns if max_turns is not None else DEFAULT_MAX_TURNS

    fd, prompt_path = tempfile.mkstemp(prefix="anja-grok-", suffix=".md")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(prompt_text)
    try:
        attempts = [resume_session_id] if resume_session_id else [None]
        if resume_session_id:
            attempts.append(None)   # a stale/foreign id (e.g. a Claude sdk_session_id) → fresh session
        for idx, rid in enumerate(attempts):
            cmd = build_command(binary, prompt_file=prompt_path, cwd=cwd, model=model, effort=effort,
                                resume_session_id=rid, rules=rules,
                                disallowed_tools=disallowed_tools, max_turns=max_turns)
            st = StreamState(model or DEFAULT_MODEL)
            st.returncode = None
            stderr_sink: list[bytes] = []
            produced = False
            async for ev in _run_process(cmd, cwd=cwd, st=st, timeout_sec=timeout_sec, stderr_sink=stderr_sink):
                if ev.get("type") in ("text", "tool_use"):
                    produced = True
                yield ev
            if st.saw_error:
                return
            if st.saw_end:
                for out in _tail_events(st):
                    yield out
                return
            tail = b"".join(stderr_sink).decode("utf-8", "replace")[-500:].strip()
            # resume refused (rc 0, no events, "Session … not found" on stderr): retry fresh once
            if rid and not produced and idx + 1 < len(attempts):
                print(f"[grok_cli] resume {rid[:8]}… refused, starting a fresh session ({tail[:120]!r})")
                yield {"type": "notice", "message": "Grok session expired — started a fresh one."}
                continue
            yield {"type": "error", "message": f"grok exited {st.returncode}: {tail or 'no output'}"}
            return
    finally:
        try:
            os.unlink(prompt_path)
        except Exception:
            pass


def call_blocking(user_prompt: str, *, cwd: Path, system_prompt: str = "", model: str = DEFAULT_MODEL,
                  effort: Optional[str] = None, timeout_sec: Optional[int] = None,
                  tool_hints: Optional[Iterable[str]] = None,
                  disallowed_tools: Optional[Iterable[str]] = None) -> dict:
    """Sync wrapper for routines (runner.py): {text, duration_sec, error, session_id, usage}."""
    async def _run():
        text, err, sid, usage = [], "", "", None
        async for ev in stream_turn(user_prompt, cwd=cwd, system_prompt=system_prompt, model=model,
                                    effort=effort, timeout_sec=timeout_sec, tool_hints=tool_hints,
                                    disallowed_tools=disallowed_tools):
            t = ev.get("type")
            if t == "text":
                text.append(ev.get("content", ""))
            elif t == "error":
                err = ev.get("message", "error")
            elif t == "session_id":
                sid = ev.get("session_id", "")
            elif t == "usage":
                usage = ev
        return "".join(text), err, sid, usage
    t0 = time.time()
    text, err, sid, usage = asyncio.run(_run())
    return {"text": text, "duration_sec": round(time.time() - t0, 1), "error": err or None,
            "session_id": sid, "usage": usage}
