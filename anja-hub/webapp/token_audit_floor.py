#!/usr/bin/env python3
"""token_audit_floor — scompone il FLOOR del Claude Code SDK (i ~119k di token-audit.py).

Parla DIRETTO all'SDK (no claude_chat) per controllare il parametro `tools` e
isolare: quanto del floor è il system nativo SDK vs gli schema dei tool nativi.

  A  tools=[]            -> system nativo SDK puro (incomprimibile senza cambiare motore)
  B  tools=["Read"]      -> + 1 tool nativo
  C  tools=NATIVE(9)     -> + i 9 tool nativi che la webapp passa di solito
  D  tools=None (omesso) -> default SDK = TUTTI i tool nativi (cosa carica se non filtri)

Tutto con system minimo, mcp_servers={}, setting_sources=[], skills=[].
4 chiamate reali 'ping' (sonnet) — costo trascurabile.
"""
import os
import argparse
import asyncio
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, query

NATIVE = ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebFetch", "WebSearch", "TodoWrite"]


async def floor(label: str, tools, cwd: Path, model: str = "sonnet", allowed=None) -> int:
    kwargs = dict(
        system_prompt="You are a helpful assistant.",
        model=model,
        cwd=str(cwd),
        permission_mode="bypassPermissions",
        skills=[],
        setting_sources=[],
        mcp_servers={},
    )
    if tools is not None:
        kwargs["tools"] = tools
    if allowed is not None:
        kwargs["allowed_tools"] = allowed
    opts = ClaudeAgentOptions(**kwargs)
    peak = 0
    async for m in query(prompt="ping", options=opts):
        if type(m).__name__ == "AssistantMessage":
            u = getattr(m, "usage", None)
            if isinstance(u, dict):
                ci = sum(int(u.get(k, 0) or 0) for k in
                         ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
                peak = max(peak, ci)
    print(f"  {label:<36}: {peak:>8,} tok")
    return peak


async def main(cwd: Path) -> None:
    print(f"\nFloor decomposition (cwd={cwd}, system minimo, no MCP)\n")
    a = await floor("A tools=[] (lista vuota)", [], cwd)
    c = await floor("C tools=NATIVE (i 9, come ora)", NATIVE, cwd)
    d = await floor("D tools OMESSO (default SDK)", None, cwd)
    e = await floor("E tools OMESSO + allowed_tools=NATIVE", None, cwd, allowed=NATIVE)

    print("\n" + "=" * 56)
    print("SCOMPOSIZIONE FLOOR")
    print("=" * 56)
    print(f"  Floor minimo SDK (tools omesso, D)       : {d:>8,} tok")
    print(f"  Sovraccarico per AVER PASSATO tools (A-D): {a - d:>8,} tok")
    print(f"  Config attuale webapp (tools=NATIVE, C)  : {c:>8,} tok")
    print(f"  Fix candidato: solo allowed_tools (E)    : {e:>8,} tok")
    print(f"  RISPARMIO del fix (C - E)                : {c - e:>8,} tok")
    print("=" * 56 + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd", type=Path, default=Path(os.environ.get("ANJA_HUB", "")))
    args = ap.parse_args()
    asyncio.run(main(args.cwd.resolve()))
