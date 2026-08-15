#!/usr/bin/env python3
"""mcp_hub_api_server.py — MCP server `hub_api`.

Thin HTTP wrapper sulla REST API della webapp AnjaHub (FastAPI su localhost).
Singolo tool `hub.api(method, path, body, query)` che fa la chiamata HTTP e
ritorna il JSON response.

Usato dai provider LLM che NON hanno Bash/WebFetch nativi nel toolset
(openai_oauth via Codex, LiteLLM standard). Per Claude SDK usare anja-cli o
WebFetch nativi è più efficiente, ma questo tool resta valido come fallback.

Sicurezza:
- Whitelist host: solo 127.0.0.1/localhost
- Whitelist path prefix: solo /api/* (no static assets, no /docs)
- Timeout 30s default
- Body+response cap 200KB per evitare context flood

Stdlib only.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


PROTO_VERSION = "2024-11-05"
SERVER_NAME = "hub_api"
SERVER_VERSION = "0.1.0"

# Config via env
API_BASE = os.environ.get("ANJA_API_BASE", "http://127.0.0.1:8765")
DEFAULT_TIMEOUT = int(os.environ.get("ANJA_API_TIMEOUT", "30"))
MAX_RESPONSE_KB = int(os.environ.get("ANJA_API_MAX_RESPONSE_KB", "200"))

ALLOWED_HOSTS = ("127.0.0.1", "localhost")
ALLOWED_PATH_PREFIX = "/api/"
ALLOWED_EXACT_PATHS = ("/openapi.json",)  # FastAPI auto-doc, utile per discovery


def _validate_url(path: str) -> tuple[str, str]:
    """Resolve API_BASE + path. Return (url, error_or_empty)."""
    if not path.startswith("/"):
        path = "/" + path
    # Strip query for prefix check (es. /openapi.json?ts=xx)
    base_path = path.split("?", 1)[0]
    if not (base_path.startswith(ALLOWED_PATH_PREFIX) or base_path in ALLOWED_EXACT_PATHS):
        return "", f"path must start with {ALLOWED_PATH_PREFIX!r} or be one of {ALLOWED_EXACT_PATHS}, got {path!r}"
    url = API_BASE.rstrip("/") + path
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname not in ALLOWED_HOSTS:
        return "", f"host {parsed.hostname!r} not in whitelist {ALLOWED_HOSTS}"
    return url, ""


def tool_hub_api(args: dict) -> dict:
    method = (args.get("method") or "GET").upper()
    if method not in ("GET", "POST", "PATCH", "PUT", "DELETE"):
        return {"error": f"unsupported method {method!r}"}

    path = args.get("path") or ""
    url, err = _validate_url(path)
    if err:
        return {"error": err}

    query = args.get("query")
    if query and isinstance(query, dict):
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(query, doseq=True)

    body = args.get("body")
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        try:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        except (TypeError, ValueError) as e:
            return {"error": f"body not JSON-serializable: {e}"}
        if len(data) > MAX_RESPONSE_KB * 1024:
            return {"error": f"body exceeds {MAX_RESPONSE_KB}KB cap"}

    timeout = int(args.get("timeout") or DEFAULT_TIMEOUT)

    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.getcode()
            raw_bytes = resp.read(MAX_RESPONSE_KB * 1024 + 1)
            truncated = len(raw_bytes) > MAX_RESPONSE_KB * 1024
            if truncated:
                raw_bytes = raw_bytes[:MAX_RESPONSE_KB * 1024]
            raw = raw_bytes.decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                payload = raw
            return {
                "status": status,
                "ok": 200 <= status < 300,
                "body": payload,
                "truncated": truncated,
            }
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            payload = raw
        return {"status": e.code, "ok": False, "body": payload, "error": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        return {"error": f"connection failed: {e.reason}. Is webapp running on {API_BASE}?"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


TOOLS = [
    {
        "name": "api",
        "description": (
            "Call the AnjaHub local REST API. Used to manage routine, agent, workspace, goal, "
            "skill, and all hub entities. The base URL is the local webapp (default "
            "http://127.0.0.1:8765). Only paths starting with /api/ are allowed.\n\n"
            "USE WHEN: you need to create/read/update/delete hub entities and you DON'T have "
            "Bash (which would let you use `anja-cli`) or WebFetch nativi in your toolset. "
            "Equivalent to calling `anja-cli` or `WebFetch http://127.0.0.1:8765/api/...`.\n\n"
            "FOR DISCOVERY: call `api(method='GET', path='/api/openapi.json')` to get the "
            "full OpenAPI schema (FastAPI-generated, always up-to-date).\n\n"
            "FOR DETAILED OPERATIONAL GUIDANCE (endpoint catalog + examples + provider/model "
            "validation): call `skill.load(name='hub-admin')` from anja_memory MCP."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PATCH", "PUT", "DELETE"],
                    "description": "HTTP method",
                },
                "path": {
                    "type": "string",
                    "description": "URL path, must start with /api/. Es: /api/routines, /api/routines/market-briefing-18, /api/agents",
                },
                "body": {
                    "description": "JSON body for POST/PATCH/PUT. Any JSON-serializable value (object, array, string).",
                },
                "query": {
                    "type": "object",
                    "description": "Query string params as dict. Es: {\"limit\": 10, \"status\": \"active\"}",
                },
                "timeout": {
                    "type": "integer",
                    "description": f"Timeout in secondi (default {DEFAULT_TIMEOUT}, max 120)",
                },
            },
            "required": ["method", "path"],
        },
    },
]

TOOL_HANDLERS = {"api": tool_hub_api}


def handle_request(req: dict):
    method = req.get("method")
    params = req.get("params") or {}
    req_id = req.get("id")

    if method == "initialize":
        return _ok(req_id, {
            "protocolVersion": PROTO_VERSION,
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "capabilities": {"tools": {"listChanged": False}},
        })
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return _ok(req_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        handler = TOOL_HANDLERS.get(name)
        if not handler:
            return _err(req_id, -32601, f"unknown tool: {name}")
        try:
            result = handler(args)
            content = [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]
            return _ok(req_id, {"content": content, "isError": "error" in result})
        except Exception as e:
            return _err(req_id, -32603, f"tool failed: {type(e).__name__}: {e}")
    if method == "ping":
        return _ok(req_id, {})
    return _err(req_id, -32601, f"method not found: {method}")


def _ok(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def main():
    print(f"[hub_api] starting (api_base={API_BASE})", file=sys.stderr, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            err = _err(None, -32700, f"parse error: {e}")
            sys.stdout.write(json.dumps(err) + "\n")
            sys.stdout.flush()
            continue
        resp = handle_request(req)
        if resp is None:
            continue
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
