"""openai_oauth_client.py — async SSE client per OpenAI Responses API via ChatGPT subscription (Fase 7v).

Bypassa LiteLLM perché:
- Endpoint diverso (chatgpt.com/backend-api/codex/responses)
- Schema body diverso (Responses API: input/instructions, no messages)
- Schema tool diverso (top-level type=function vs chat completions nested)
- Stream SSE event format diverso (response.output_text.delta vs chat.completion.chunk)

Output uniforme: async generator yield-a stesso shape di llm_router.stream_via_litellm:
  {type: 'text', content: str}
  {type: 'tool_use', name: str, input: dict}
  {type: 'usage', input_tokens, output_tokens, total_tokens, ...}
  {type: 'error', message: str}
  {type: 'done'}

Riusa MCP dispatch (_mcp_call_tool) da llm_router.py.

Stdlib + httpx (già presente in deps webapp via fastapi).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import AsyncIterator, Optional

import httpx

from openai_oauth import (
    CODEX_RESPONSES_URL,
    SUPPORTED_MODELS,
    get_chatgpt_token,
)


# ============================================================
# Tool schema adapter: MCP → OpenAI Responses API
# ============================================================

def mcp_tools_to_responses_format(litellm_tools: list) -> list:
    """LiteLLM/ChatCompletion tool format:
        [{"type":"function","function":{"name":..., "description":..., "parameters":...}}]

    Responses API tool format (flat, no nested 'function' key):
        [{"type":"function","name":..., "description":..., "parameters":...}]

    Ritorna lista nel secondo schema.
    """
    out = []
    for t in litellm_tools or []:
        if t.get("type") != "function":
            continue
        fn = t.get("function") or {}
        out.append({
            "type": "function",
            "name": fn.get("name", ""),
            "description": fn.get("description", "")[:1024],
            "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return out


# ============================================================
# SSE line parser
# ============================================================

async def _iter_sse_events(response: httpx.Response) -> AsyncIterator[dict]:
    """Itera SSE events da una httpx streaming response.

    Format:
      event: <type>\n
      data: <json>\n
      \n  (blank line separator)
    """
    event_type = None
    data_lines: list[str] = []
    async for raw_line in response.aiter_lines():
        line = raw_line.rstrip("\r")
        if not line:
            # End of event
            if event_type and data_lines:
                data_str = "\n".join(data_lines)
                try:
                    payload = json.loads(data_str)
                except Exception:
                    payload = {"_raw": data_str}
                yield {"event": event_type, "data": payload}
            event_type = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue  # SSE comment
        if line.startswith("event:"):
            event_type = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())


# ============================================================
# Main streaming entry
# ============================================================

async def stream_via_openai_oauth(
    user_prompt: str,
    system_prompt: str,
    cwd: Path,
    model: str = "gpt-5.5",
    timeout_sec: int = 300,
    allowed_tools: Optional[list] = None,
    effort: Optional[str] = None,
    image_attachments: Optional[list] = None,
) -> AsyncIterator[dict]:
    """Async generator: stream chat via OpenAI Responses API con ChatGPT subscription auth.

    Multi-turn tool loop (max 10) supportato.
    """
    # Validate model
    if model not in SUPPORTED_MODELS:
        yield {
            "type": "error",
            "message": f"Modello '{model}' non supportato con ChatGPT subscription. Disponibili: {', '.join(SUPPORTED_MODELS)}",
        }
        return

    token, account_id, err = get_chatgpt_token()
    if err or not token:
        yield {"type": "error", "message": f"ChatGPT auth not available: {err or 'no token'}"}
        return

    # Lazy import per evitare circular: llm_router importa già da openai_oauth
    from llm_router import _build_litellm_tools, _mcp_call_tool, build_subprocess_env, discover_mcp_servers

    env = build_subprocess_env(cwd)

    # Discover MCP tools (riusa logica esistente per coerenza)
    mcp_patterns = [t for t in (allowed_tools or []) if t.startswith("mcp__")]
    try:
        litellm_tools, dispatch_map = await _build_litellm_tools(cwd, env, mcp_patterns or None)
    except Exception as e:
        yield {"type": "error", "message": f"MCP discovery failed: {e}"}
        return

    responses_tools = mcp_tools_to_responses_format(litellm_tools)

    # Build initial input items. Responses API usa "input" array di content parts.
    instructions = system_prompt or ""
    user_content: list[dict] = [{"type": "input_text", "text": user_prompt}]
    # Fase 24 — Image attachments via input_image
    if image_attachments:
        import base64 as _b64
        for img in image_attachments:
            b64 = img.get("image_b64")
            if not b64:
                _p = img.get("path")
                if _p:
                    try:
                        b64 = _b64.b64encode(Path(_p).read_bytes()).decode("ascii")
                    except Exception:
                        continue
            if not b64:
                continue
            mime = img.get("mime") or "image/png"
            user_content.append({"type": "input_image", "image_url": f"data:{mime};base64,{b64}"})
    input_items: list[dict] = [{"role": "user", "content": user_content}]

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": "codex_cli_rs/0.124.0",
        "OpenAI-Beta": "responses=experimental",
        "chatgpt-account-id": account_id or "",
        "originator": "codex_cli_rs",
    }

    # Multi-turn tool loop
    for turn in range(10):
        body = {
            "model": model,
            "instructions": instructions,
            "input": input_items,
            "stream": True,
            "store": False,
        }
        if responses_tools:
            body["tools"] = responses_tools
        # Fase 7v — reasoning effort per gpt-5.5
        if effort in ("low", "medium", "high"):
            body["reasoning"] = {"effort": effort}

        # Track function_call outputs for this turn
        pending_function_calls: dict[str, dict] = {}  # call_id → {name, arguments, output_id}
        final_text_parts: list[str] = []
        usage_emitted = False

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_sec, connect=15)) as client:
                async with client.stream("POST", CODEX_RESPONSES_URL, headers=headers, json=body) as resp:
                    if resp.status_code != 200:
                        err_body = (await resp.aread()).decode(errors="replace")[:500]
                        yield {"type": "error", "message": f"HTTP {resp.status_code}: {err_body}"}
                        return

                    async for ev in _iter_sse_events(resp):
                        et = ev.get("event") or ""
                        data = ev.get("data") or {}

                        # Text deltas streamed to UI
                        if et == "response.output_text.delta":
                            delta = data.get("delta") or ""
                            if delta:
                                final_text_parts.append(delta)
                                yield {"type": "text", "content": delta}

                        # Function call (tool use) — full args arrive when output_item completes
                        elif et == "response.output_item.added":
                            item = data.get("item") or {}
                            if item.get("type") == "function_call":
                                call_id = item.get("call_id") or item.get("id") or f"call_{turn}_{len(pending_function_calls)}"
                                pending_function_calls[call_id] = {
                                    "name": item.get("name") or "",
                                    "arguments": "",
                                    "output_id": item.get("id"),
                                }

                        elif et == "response.function_call_arguments.delta":
                            item_id = data.get("item_id")
                            delta = data.get("delta") or ""
                            for call_id, info in pending_function_calls.items():
                                if info.get("output_id") == item_id:
                                    info["arguments"] += delta
                                    break

                        elif et == "response.function_call_arguments.done":
                            item_id = data.get("item_id")
                            full_args = data.get("arguments") or ""
                            for call_id, info in pending_function_calls.items():
                                if info.get("output_id") == item_id:
                                    info["arguments"] = full_args or info["arguments"]
                                    break

                        elif et == "response.output_item.done":
                            item = data.get("item") or {}
                            if item.get("type") == "function_call":
                                call_id = item.get("call_id") or item.get("id")
                                if call_id in pending_function_calls:
                                    pending_function_calls[call_id]["name"] = item.get("name") or pending_function_calls[call_id]["name"]
                                    pending_function_calls[call_id]["arguments"] = item.get("arguments") or pending_function_calls[call_id]["arguments"]

                        elif et == "response.completed":
                            usage = (data.get("response") or {}).get("usage") or {}
                            in_t = int(usage.get("input_tokens", 0) or 0)
                            out_t = int(usage.get("output_tokens", 0) or 0)
                            if in_t or out_t:
                                yield {
                                    "type": "usage",
                                    "input_tokens": in_t,
                                    "output_tokens": out_t,
                                    "total_tokens": in_t + out_t,
                                    "context_window": 400000,  # gpt-5.5 ctx
                                    "model": f"openai_oauth/{model}",
                                }
                                usage_emitted = True

                        elif et == "error" or et == "response.error":
                            err_msg = data.get("error") or data.get("message") or str(data)
                            yield {"type": "error", "message": f"OpenAI Responses API error: {err_msg}"}
                            return

        except httpx.HTTPError as e:
            yield {"type": "error", "message": f"HTTP error: {type(e).__name__}: {e}"}
            return
        except Exception as e:
            yield {"type": "error", "message": f"Stream error: {type(e).__name__}: {e}"}
            return

        # No tool calls → done
        if not pending_function_calls:
            yield {"type": "done"}
            return

        # Append assistant turn (function_call items) + execute tools
        for call_id, info in pending_function_calls.items():
            yield {
                "type": "tool_use",
                "name": f"mcp__{info['name']}" if info["name"] in dispatch_map else info["name"],
                "input": _try_parse_json(info["arguments"]),
            }
            # Add the function_call item back into input for next turn
            input_items.append({
                "type": "function_call",
                "call_id": call_id,
                "name": info["name"],
                "arguments": info["arguments"] or "{}",
            })

        # Dispatch each tool, append function_call_output items
        for call_id, info in pending_function_calls.items():
            name = info["name"]
            args_obj = _try_parse_json(info["arguments"])
            if name in dispatch_map:
                srv_name, srv_cfg, original_tool = dispatch_map[name]
                try:
                    result_text = await _mcp_call_tool(srv_name, srv_cfg, original_tool, args_obj, env)
                except Exception as e:
                    result_text = json.dumps({"error": f"{type(e).__name__}: {e}"})
            else:
                result_text = json.dumps({"error": f"unknown tool: {name}"})

            input_items.append({
                "type": "function_call_output",
                "call_id": call_id,
                "output": result_text[:50000],
            })

        # Loop continua per il prossimo turn

    yield {"type": "error", "message": "max tool turns (10) reached"}


def _try_parse_json(s: str) -> dict:
    if not s:
        return {}
    try:
        return json.loads(s)
    except Exception:
        return {}


def call_openai_oauth_blocking(
    user_prompt: str,
    system_prompt: str,
    cwd: Path,
    model: str = "gpt-5.5",
    timeout_sec: int = 300,
    allowed_tools: Optional[list] = None,
) -> dict:
    """Sync wrapper su stream_via_openai_oauth per routine runner.

    Stessa shape di llm_router.call_opencode_blocking:
    {text, duration_sec, error, cost, tokens}
    """
    import time as _time

    async def _run() -> dict:
        chunks: list[str] = []
        tokens: dict = {}
        last_error: Optional[str] = None
        try:
            async for ev in stream_via_openai_oauth(
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                cwd=cwd,
                model=model,
                timeout_sec=timeout_sec,
                allowed_tools=allowed_tools,
            ):
                t = ev.get("type")
                if t == "text":
                    chunks.append(ev.get("content", ""))
                elif t == "usage":
                    tokens = {
                        "input": ev.get("input_tokens", 0),
                        "output": ev.get("output_tokens", 0),
                        "total": ev.get("total_tokens", 0),
                    }
                elif t == "error":
                    last_error = ev.get("message", "openai_oauth error")
                elif t == "done":
                    break
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
        return {"chunks": chunks, "tokens": tokens, "error": last_error}

    started = _time.time()
    try:
        result = asyncio.run(asyncio.wait_for(_run(), timeout=timeout_sec))
    except asyncio.TimeoutError:
        return {"text": "", "duration_sec": _time.time() - started, "error": f"timeout after {timeout_sec}s"}
    except Exception as e:
        return {"text": "", "duration_sec": _time.time() - started, "error": f"{type(e).__name__}: {e}"}

    duration = _time.time() - started
    text = "".join(result["chunks"])
    if not text and result["error"]:
        return {"text": "", "duration_sec": duration, "error": result["error"]}
    return {
        "text": text,
        "duration_sec": duration,
        "error": result["error"],
        "cost": 0,
        "tokens": result["tokens"],
    }
