"""
llm_router.py — wrapper multi-provider per chat/routine anja.

Pattern dual-engine (Fase 8a):
  - Claude (anthropic) → claude-agent-sdk in-process Python (MCP/skill nativi)
  - Tutto il resto      → LiteLLM in-process con tool calling MCP integrato
                          (xAI, OpenAI, Gemini, OpenRouter, Mistral, Grok, Ollama, ...)

Fallback legacy (deprecato): opencode subprocess. Conservato come ultimate fallback
se LiteLLM fallisce o non disponibile.

Output uniforme: async generator yield-a {type, ...} eventi.
"""

import asyncio
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import AsyncIterator, Optional


CLAUDE_PROVIDERS = ("claude", "anthropic")

# Mapping provider → env var letta da opencode
PROVIDER_ENV_KEY = {
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "xai": "XAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "groq": "GROQ_API_KEY",
}


def _find_hub_root(start: Path) -> Optional[Path]:
    """Risale da `start` cercando un dir con config/projects.json (= hub root)."""
    env_hub = os.environ.get("ANJA_HUB")
    if env_hub and (Path(env_hub) / "config" / "projects.json").is_file():
        return Path(env_hub)
    p = Path(start).resolve()
    for cand in [p] + list(p.parents):
        if (cand / "config" / "projects.json").is_file():
            return cand
    return None


def load_secrets_env(hub_root: Optional[Path]) -> dict:
    """Carica <hub>/.secrets.env (formato KEY=VALUE) → dict.

    Righe blank/# ignorate. Quote singole/doppie strippate.
    """
    out: dict = {}
    if not hub_root:
        return out
    secrets_file = Path(hub_root) / ".secrets.env"
    if not secrets_file.is_file():
        return out
    try:
        for raw in secrets_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            if key:
                out[key] = val
    except Exception:
        pass
    return out


def build_subprocess_env(cwd: Path) -> dict:
    """Costruisce env per subprocess opencode: os.environ copiato + secrets.env merged."""
    env = os.environ.copy()
    hub = _find_hub_root(cwd)
    secrets = load_secrets_env(hub)
    env.update(secrets)
    return env


# ============================================================
# MCP bridge: .mcp.json (Claude) → opencode.json (opencode)
# ============================================================

OPENCODE_CONFIG_HEADER = "# auto-generato da anja (Fase 7f). Non editare a mano."


def _claude_mcp_to_opencode(mcp_servers: dict) -> dict:
    """Convert Claude `.mcp.json` mcpServers entries → opencode `mcp` config entries.

    Claude format:    {name: {command: str, args: [...], env: {...}}}
    Opencode format:  {name: {type: 'local', command: [str, ...], environment: {...}, enabled: true}}
    """
    out = {}
    for name, srv in (mcp_servers or {}).items():
        if not isinstance(srv, dict):
            continue
        # Stdio (local) — Claude format usa command + args
        if "command" in srv:
            cmd = srv["command"]
            args = srv.get("args", []) or []
            cmd_array = [cmd] + list(args) if isinstance(cmd, str) else list(cmd)
            out[name] = {
                "type": "local",
                "command": cmd_array,
                "environment": srv.get("env", {}) or {},
                "enabled": True,
            }
        # Remote (http/sse) — Claude usa url
        elif "url" in srv:
            out[name] = {
                "type": "remote",
                "url": srv["url"],
                "enabled": True,
            }
            if srv.get("headers"):
                out[name]["headers"] = srv["headers"]
    return out


# Server MCP da disabilitare quando si spawna opencode (legacy fallback only).
# Con Fase 8a (LiteLLM) opencode è deprecato — questa lista resta come safety
# se opencode dovesse essere usato in futuro.
OPENCODE_DISABLED_MCP: set = set()


def ensure_opencode_config(cwd: Path) -> Optional[Path]:
    """Genera/aggiorna `<cwd>/opencode.json` partendo da `<cwd>/.mcp.json`.

    Idempotente: se .mcp.json non esiste, no-op (ritorna None).
    Se opencode.json esiste già, ne preserva tutti i campi non-`mcp` (provider, ecc.).
    Disabilita MCP server "grossi" (vedi OPENCODE_DISABLED_MCP) per stare sotto
    il limite di 200 tool di opencode.
    Ritorna il path generato per debug, o None.
    """
    mcp_file = Path(cwd) / ".mcp.json"
    if not mcp_file.is_file():
        return None
    try:
        mcp_payload = json.loads(mcp_file.read_text(encoding="utf-8"))
    except Exception:
        return None
    servers = mcp_payload.get("mcpServers", {}) or {}
    if not servers:
        return None

    bridge_mcp = _claude_mcp_to_opencode(servers)
    # Tampone Fase 8a: disabilita MCP grossi sopra limite opencode 200 tool
    for name in list(bridge_mcp.keys()):
        if name in OPENCODE_DISABLED_MCP:
            bridge_mcp[name]["enabled"] = False

    target = Path(cwd) / "opencode.json"
    existing = {}
    if target.is_file():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    existing.setdefault("$schema", "https://opencode.ai/config.json")
    existing["mcp"] = bridge_mcp
    try:
        target.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[llm_router] opencode bridge write failed: {e}")
        return None
    return target


def is_claude_provider(provider: str) -> bool:
    return (provider or "claude").lower() in CLAUDE_PROVIDERS


def opencode_model_id(provider: str, model: str) -> str:
    if not provider:
        return model
    if provider.lower() in CLAUDE_PROVIDERS:
        return f"anthropic/{model}"
    return f"{provider}/{model}"


def find_opencode_binary() -> Optional[str]:
    found = shutil.which("opencode")
    if found:
        return found
    home = Path.home()
    for cand in (home / ".opencode" / "bin" / "opencode", Path("/usr/local/bin/opencode")):
        if cand.is_file():
            return str(cand)
    return None


def _build_full_prompt(user_prompt: str, system_prompt: str) -> str:
    if not system_prompt:
        return user_prompt
    return f"<context>\n{system_prompt}\n</context>\n\n{user_prompt}"


# ============================================================
# Fase 8a — LiteLLM wrapper (replaces opencode for non-Claude providers)
# ============================================================


def litellm_model_id(provider: str, model: str) -> str:
    """LiteLLM accetta 'provider/model' style: xai/grok-4.3, openai/gpt-5.5, ecc.

    Ollama: usa prefix `ollama_chat/<model>` (chat-completion endpoint, raccomandato
    dal vendor per tool-calling robusto vs `ollama/<model>` legacy).
    """
    model = (model or "").removeprefix("models/")   # id Gemini già prefissati
    if not provider or provider.lower() in CLAUDE_PROVIDERS:
        return f"anthropic/{model}"
    p = provider.lower()
    if p == "ollama":
        return f"ollama_chat/{model}"
    return f"{p}/{model}"


def ollama_api_base(cwd: Path) -> Optional[str]:
    """Read <hub>/config/ollama.json → base_url. Returns None if not configured."""
    hub = _find_hub_root(cwd)
    if not hub:
        return None
    f = hub / "config" / "ollama.json"
    if not f.is_file():
        return None
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        if not d.get("enabled"):
            return None
        url = (d.get("base_url") or "").strip().rstrip("/")
        return url or None
    except Exception:
        return None


def discover_mcp_servers(cwd: Path) -> dict:
    """Carica .mcp.json e ritorna dict {server_name: {command, args, env, url, ...}}.
    Ritorna solo server abilitati (default: enabled=true)."""
    mcp_file = Path(cwd) / ".mcp.json"
    if not mcp_file.is_file():
        return {}
    try:
        cfg = json.loads(mcp_file.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return cfg.get("mcpServers") or {}


async def _mcp_list_tools(server_name: str, server_cfg: dict, env: dict) -> list:
    """Spawn MCP server, fa tools/list via JSON-RPC, ritorna lista tool dict.
    Ogni tool: {name, description, inputSchema}."""
    cmd = server_cfg.get("command")
    args = server_cfg.get("args", []) or []
    if not cmd:
        return []
    # Merge env del server con env passato (es. secrets)
    proc_env = dict(env)
    proc_env.update(server_cfg.get("env") or {})
    try:
        proc = await asyncio.create_subprocess_exec(
            cmd, *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=proc_env,
        )
    except Exception:
        return []

    async def request(method: str, params: dict = None, rid: int = 1):
        msg = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}) + "\n"
        proc.stdin.write(msg.encode("utf-8"))
        await proc.stdin.drain()
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=10)
            return json.loads(line.decode("utf-8"))
        except Exception:
            return {}

    try:
        await request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}}, rid=0)
        proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}\n')
        await proc.stdin.drain()
        resp = await request("tools/list", {}, rid=1)
        tools = (resp.get("result") or {}).get("tools") or []
        return tools
    except Exception:
        return []
    finally:
        try:
            proc.stdin.close()
            proc.kill()
            await proc.wait()
        except Exception:
            pass


async def _mcp_call_tool(server_name: str, server_cfg: dict, tool_name: str, args: dict, env: dict) -> str:
    """Spawn MCP, chiama tools/call e ritorna content text."""
    cmd = server_cfg.get("command")
    cmd_args = server_cfg.get("args", []) or []
    proc_env = dict(env)
    proc_env.update(server_cfg.get("env") or {})
    try:
        proc = await asyncio.create_subprocess_exec(
            cmd, *cmd_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=proc_env,
        )
    except Exception as e:
        return json.dumps({"error": f"spawn failed: {e}"})

    async def request(method: str, params: dict = None, rid: int = 1):
        msg = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}) + "\n"
        proc.stdin.write(msg.encode("utf-8"))
        await proc.stdin.drain()
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=120)
            return json.loads(line.decode("utf-8"))
        except Exception as e:
            return {"error": str(e)}

    try:
        await request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}}, rid=0)
        proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}\n')
        await proc.stdin.drain()
        resp = await request("tools/call", {"name": tool_name, "arguments": args}, rid=1)
        result = resp.get("result") or resp.get("error") or {}
        content = result.get("content") or []
        # Concat tutti i text blocks
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        return "\n".join(texts) if texts else json.dumps(result)
    finally:
        try:
            proc.stdin.close()
            proc.kill()
            await proc.wait()
        except Exception:
            pass


async def _build_litellm_tools(cwd: Path, env: dict, allowed_patterns: Optional[list] = None) -> tuple:
    """Discover tutti i MCP server e flat-list i tool in formato OpenAI tool calling.

    Ritorna (tools_list, dispatch_map):
      - tools_list = [{"type":"function","function":{"name":"server__tool", "description":..., "parameters":...}}]
      - dispatch_map = {"server__tool": (server_name, server_cfg, original_tool_name)}

    `allowed_patterns` filtra: se contiene 'mcp__<server>__*' allora include tutti i tool
    di quel server; se contiene 'mcp__<server>__<tool>' include solo quello.
    Se None → include tutti.
    """
    servers = discover_mcp_servers(cwd)
    tools_list = []
    dispatch_map = {}

    def is_allowed(server: str, tool: str) -> bool:
        if not allowed_patterns:
            return True
        full = f"mcp__{server}__{tool}"
        for pat in allowed_patterns:
            if pat == full:
                return True
            if pat.endswith("__*") and pat[:-1] == f"mcp__{server}__":
                return True
        return False

    for srv_name, srv_cfg in servers.items():
        if srv_cfg.get("command") is None:
            continue
        try:
            tools = await _mcp_list_tools(srv_name, srv_cfg, env)
        except Exception:
            tools = []
        for t in tools:
            tool_name = t.get("name")
            if not tool_name:
                continue
            if not is_allowed(srv_name, tool_name):
                continue
            # Nome flat per OpenAI tool calling (non può avere "/" né dot in alcuni provider)
            flat_name = f"{srv_name}__{tool_name}".replace(".", "_").replace("/", "_")[:64]
            tools_list.append({
                "type": "function",
                "function": {
                    "name": flat_name,
                    "description": (t.get("description") or "")[:1024],
                    "parameters": t.get("inputSchema") or {"type": "object", "properties": {}},
                },
            })
            dispatch_map[flat_name] = (srv_name, srv_cfg, tool_name)
    return tools_list, dispatch_map


async def stream_via_litellm(
    user_prompt: str,
    system_prompt: str,
    cwd: Path,
    provider: str,
    model: str,
    timeout_sec: int = 300,
    allowed_tools: Optional[list] = None,
    image_attachments: Optional[list] = None,
) -> AsyncIterator[dict]:
    """Async generator: chat completion via LiteLLM con MCP tool calling integrato.

    Loop:
      - litellm.acompletion(stream=True) → emit text chunks + collect tool_calls
      - se tool_calls presenti → eseguili via MCP, append role=tool messages, ricomincia
      - quando finish_reason='stop' → done
    """
    try:
        import litellm
        litellm.suppress_debug_info = True
        litellm.drop_params = True  # ignora param non supportati dal provider
    except ImportError:
        yield {"type": "error", "message": "litellm not installed (pip install litellm)"}
        return

    model_id = litellm_model_id(provider, model)
    env = build_subprocess_env(cwd)

    # Context window size for this model (Fase 7t)
    try:
        info = litellm.get_model_info(model_id) or {}
        ctx_window = int(info.get("max_input_tokens") or info.get("max_tokens") or 0) or None
    except Exception:
        ctx_window = None

    # Discover MCP tools (filtra per allowed_tools patterns)
    mcp_patterns = [t for t in (allowed_tools or []) if t.startswith("mcp__")]
    try:
        tools, dispatch_map = await _build_litellm_tools(cwd, env, mcp_patterns or None)
    except Exception as e:
        yield {"type": "error", "message": f"MCP discovery failed: {e}"}
        return

    # Fase 7u M-Cx 6: prompt caching cross-provider tramite cache_control ephemeral.
    # Anthropic native; OpenAI/Gemini via LiteLLM ignorano gracefully se non supportato.
    # Dal 2° turno il blob ProjectContext è cache-hit (~10% costo).
    sys_text = system_prompt or ""
    if sys_text and len(sys_text) > 1000:
        system_msg = {
            "role": "system",
            "content": [{"type": "text", "text": sys_text, "cache_control": {"type": "ephemeral"}}],
        }
    else:
        system_msg = {"role": "system", "content": sys_text}
    # Fase 24 — Image attachments: se presenti, user content diventa array di parts
    if image_attachments:
        user_content_parts = [{"type": "text", "text": user_prompt}]
        for img in image_attachments:
            b64 = img.get("image_b64")
            if not b64:
                # Re-read da disk se disponibile
                _p = img.get("path")
                if _p:
                    try:
                        import base64 as _b64
                        b64 = _b64.b64encode(Path(_p).read_bytes()).decode("ascii")
                    except Exception:
                        continue
            if not b64:
                continue
            mime = img.get("mime") or "image/png"
            user_content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })
        messages = [system_msg, {"role": "user", "content": user_content_parts}]
    else:
        messages = [system_msg, {"role": "user", "content": user_prompt}]

    # Ollama (Fase 7t): pass api_base + drop unsupported params
    ollama_base = None
    if provider and provider.lower() == "ollama":
        ollama_base = ollama_api_base(cwd)
        if not ollama_base:
            yield {"type": "error", "message": "Ollama is not enabled. Configure it in Settings → Providers → Local Models."}
            return

    # Multi-turn tool loop (max 10 turni per safety)
    for turn in range(10):
        try:
            kwargs = {
                "model": model_id,
                "messages": messages,
                "stream": True,
                "timeout": timeout_sec,
                "stream_options": {"include_usage": True},  # Fase 7t — emits usage in last chunk
            }
            if tools:
                kwargs["tools"] = tools
            if ollama_base:
                kwargs["api_base"] = ollama_base
            response = await litellm.acompletion(**kwargs)
        except Exception as e:
            yield {"type": "error", "message": f"litellm error: {type(e).__name__}: {e}"}
            return

        assistant_content = ""
        tool_calls_buffer = {}  # idx → {id, name, arguments_str}
        finished_stop = False

        try:
            async for chunk in response:
                # Fase 7t — usage può arrivare anche in chunk senza choices (last chunk)
                usage = getattr(chunk, "usage", None)
                if usage:
                    in_t = getattr(usage, "prompt_tokens", 0) or 0
                    out_t = getattr(usage, "completion_tokens", 0) or 0
                    yield {
                        "type": "usage",
                        "input_tokens": int(in_t),
                        "output_tokens": int(out_t),
                        "total_tokens": int(in_t + out_t),
                        "context_window": ctx_window,
                        "model": model_id,
                    }
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta is None:
                    continue
                # text content
                content = getattr(delta, "content", None)
                if content:
                    assistant_content += content
                    yield {"type": "text", "content": content}
                # tool calls (streaming chunked)
                # Provider standard (OpenAI/Anthropic) emette FRAGMENTI incrementali con index stabile.
                # Ollama (small models): ri-emette nome+args completi ad ogni chunk → bisogna dedup
                # altrimenti accumulo crea "kanban_showkanban_searchkanban_search..." e JSON malformato.
                tcs = getattr(delta, "tool_calls", None)
                if tcs:
                    for tc in tcs:
                        idx = getattr(tc, "index", 0)
                        slot = tool_calls_buffer.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        if getattr(tc, "id", None):
                            slot["id"] = tc.id
                        fn = getattr(tc, "function", None)
                        if fn:
                            new_name = getattr(fn, "name", None) or ""
                            if new_name:
                                cur = slot["name"]
                                if not cur:
                                    slot["name"] = new_name
                                elif new_name == cur or cur.endswith(new_name) or new_name in cur:
                                    pass  # duplicate (Ollama-style), skip
                                else:
                                    slot["name"] += new_name  # standard fragmented
                            new_args = getattr(fn, "arguments", None) or ""
                            if new_args:
                                cur_a = slot["arguments"]
                                if not cur_a:
                                    slot["arguments"] = new_args
                                elif new_args == cur_a:
                                    pass  # exact duplicate
                                elif cur_a.endswith(new_args):
                                    pass  # already at tail
                                else:
                                    # Detect duplicated complete JSON: try parsing — if cur_a is valid JSON
                                    # and new_args is also valid JSON, prefer the longer one (more complete)
                                    try:
                                        _ = json.loads(cur_a)
                                        try:
                                            _ = json.loads(new_args)
                                            if len(new_args) > len(cur_a):
                                                slot["arguments"] = new_args
                                            # else: keep current
                                        except Exception:
                                            slot["arguments"] += new_args
                                    except Exception:
                                        slot["arguments"] += new_args
                # finish_reason
                fr = chunk.choices[0].finish_reason
                if fr == "stop":
                    finished_stop = True  # NOT return — usage chunk arriva dopo
                    continue
                if fr == "tool_calls":
                    break  # esci dal loop chunks, processa tool calls
        except Exception as e:
            yield {"type": "error", "message": f"stream error: {type(e).__name__}: {e}"}
            return

        # Stream finito (chunk loop terminato). Decidi: stop oppure tool_calls?
        if finished_stop and not tool_calls_buffer:
            yield {"type": "done"}
            return
        if not tool_calls_buffer:
            # nè stop nè tool_calls — anomalia, esci
            yield {"type": "done"}
            return

        # Append assistant message con tool_calls
        tc_list = []
        for idx in sorted(tool_calls_buffer.keys()):
            tc = tool_calls_buffer[idx]
            tc_list.append({
                "id": tc["id"] or f"call_{turn}_{idx}",
                "type": "function",
                "function": {"name": tc["name"], "arguments": tc["arguments"] or "{}"},
            })
        messages.append({
            "role": "assistant",
            "content": assistant_content or None,
            "tool_calls": tc_list,
        })

        # Esegui ogni tool, append role=tool messages
        for tc in tc_list:
            flat = tc["function"]["name"]
            args_str = tc["function"]["arguments"] or "{}"
            try:
                args_obj = json.loads(args_str) if args_str else {}
            except Exception:
                args_obj = {}
            yield {"type": "tool_use", "name": f"mcp__{flat.replace('__', '__', 1)}", "input": args_obj}
            if flat in dispatch_map:
                srv_name, srv_cfg, original = dispatch_map[flat]
                try:
                    result_text = await _mcp_call_tool(srv_name, srv_cfg, original, args_obj, env)
                except Exception as e:
                    result_text = json.dumps({"error": str(e)})
            else:
                result_text = json.dumps({"error": f"unknown tool: {flat}"})
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result_text[:50000],
            })
        # loop continua per il prossimo turn LLM con i tool results

    yield {"type": "error", "message": "max tool turns reached (10)"}


async def stream_via_opencode(
    user_prompt: str,
    system_prompt: str,
    cwd: Path,
    provider: str,
    model: str,
    timeout_sec: int = 300,
) -> AsyncIterator[dict]:
    """Async generator: spawn opencode run --format json e parsa stream JSON line-by-line."""
    binary = find_opencode_binary()
    if not binary:
        yield {"type": "error", "message": "opencode binary not found. Install: https://opencode.ai"}
        return

    model_id = opencode_model_id(provider, model)
    full_prompt = _build_full_prompt(user_prompt, system_prompt)
    cmd = [binary, "run", "--format", "json", "-m", model_id, full_prompt]

    # Fase 7f — bridge .mcp.json → opencode.json del cwd (hub o project)
    ensure_opencode_config(cwd)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
            env=build_subprocess_env(cwd),
        )
    except Exception as e:
        yield {"type": "error", "message": f"opencode spawn failed: {type(e).__name__}: {e}"}
        return

    deadline = asyncio.get_event_loop().time() + timeout_sec

    try:
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                proc.kill()
                yield {"type": "error", "message": f"opencode timeout after {timeout_sec}s"}
                return
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
            except asyncio.TimeoutError:
                proc.kill()
                yield {"type": "error", "message": f"opencode timeout after {timeout_sec}s"}
                return
            if not line:
                break
            line_str = line.decode("utf-8", errors="replace").strip()
            if not line_str:
                continue
            try:
                ev = json.loads(line_str)
            except Exception:
                continue
            etype = ev.get("type", "")
            if etype == "text":
                text = ev.get("part", {}).get("text", "")
                if text:
                    yield {"type": "text", "content": text}
            elif etype == "step_finish":
                tokens = ev.get("part", {}).get("tokens", {})
                cost = ev.get("part", {}).get("cost", 0)
                if tokens or cost:
                    yield {
                        "type": "tool_use",
                        "name": "_opencode_stats",
                        "input": {
                            "model": model_id,
                            "tokens_total": tokens.get("total", 0),
                            "tokens_input": tokens.get("input", 0),
                            "tokens_output": tokens.get("output", 0),
                            "cost_usd": cost,
                        },
                    }
            elif etype == "error":
                err = ev.get("error", {})
                msg = err.get("data", {}).get("message", "") or err.get("name", "OpenCodeError")
                yield {"type": "error", "message": f"opencode error: {msg}"}
                return

        await proc.wait()
        if proc.returncode != 0:
            stderr_data = await proc.stderr.read()
            yield {"type": "error", "message": f"opencode exit {proc.returncode}: {stderr_data.decode('utf-8', errors='replace')[:500]}"}
            return
        yield {"type": "done"}

    except Exception as e:
        try:
            proc.kill()
        except Exception:
            pass
        yield {"type": "error", "message": f"opencode stream failed: {type(e).__name__}: {e}"}


def call_opencode_blocking(
    user_prompt: str,
    system_prompt: str,
    cwd: Path,
    provider: str,
    model: str,
    timeout_sec: int = 300,
) -> dict:
    """Blocking version per runner.py (routine). Ritorna {text, duration_sec, error, cost, tokens}."""
    binary = find_opencode_binary()
    if not binary:
        return {"text": "", "duration_sec": 0.0, "error": "opencode binary not found"}

    model_id = opencode_model_id(provider, model)
    full_prompt = _build_full_prompt(user_prompt, system_prompt)
    cmd = [binary, "run", "--format", "json", "-m", model_id, full_prompt]

    # Fase 7f — bridge .mcp.json → opencode.json del cwd
    ensure_opencode_config(cwd)

    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True, text=True, timeout=timeout_sec,
            env=build_subprocess_env(cwd),
        )
    except subprocess.TimeoutExpired:
        return {"text": "", "duration_sec": time.time() - started, "error": f"timeout after {timeout_sec}s"}
    except Exception as e:
        return {"text": "", "duration_sec": time.time() - started, "error": f"{type(e).__name__}: {e}"}

    duration = time.time() - started
    chunks = []
    cost = 0
    tokens = {}
    last_error = None

    for raw in proc.stdout.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except Exception:
            continue
        if ev.get("type") == "text":
            chunks.append(ev.get("part", {}).get("text", ""))
        elif ev.get("type") == "step_finish":
            cost = ev.get("part", {}).get("cost", 0)
            tokens = ev.get("part", {}).get("tokens", {})
        elif ev.get("type") == "error":
            last_error = ev.get("error", {}).get("data", {}).get("message", "") or ev.get("error", {}).get("name", "OpenCodeError")

    if proc.returncode != 0 and not chunks:
        return {"text": "", "duration_sec": duration, "error": last_error or f"opencode exit {proc.returncode}: {proc.stderr[:300]}"}

    return {
        "text": "".join(chunks),
        "duration_sec": duration,
        "error": last_error if not chunks else None,
        "cost_usd": cost,
        "tokens": tokens,
        "model": model_id,
    }
