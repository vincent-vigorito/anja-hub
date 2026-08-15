#!/usr/bin/env python3
"""token_smoke — verifica funzionale post-fix: tool NATIVI e MCP restano usabili senza `tools` param.

Col fix gli schema MCP sono passati eager->lazy (deferred). Questo test verifica che il
modello riesca COMUNQUE a invocarli (1) tool nativo Read, (2) tool MCP anja_memory.
"""
import os
import asyncio
from pathlib import Path

import claude_chat as cc

HUB = Path(os.environ.get("ANJA_HUB", ""))


async def run(prompt: str, allowed: list, scoped: list):
    tools_used, texts, err, ctx = [], [], None, 0
    async for ev in cc.stream_response(
        user_prompt=prompt,
        system_prompt="Sei un assistente. Usa i tool disponibili quando servono.",
        cwd=HUB, model="sonnet", allowed_tools=allowed, provider="claude", scoped_servers=scoped,
    ):
        t = ev.get("type")
        if t == "tool_use":
            tools_used.append(ev.get("name"))
        elif t == "text":
            texts.append(ev.get("content") or "")
        elif t == "error":
            err = ev.get("message")
        elif t == "usage":
            ctx = ev.get("context_input_tokens")
    return tools_used, " ".join(texts).strip(), err, ctx


async def main() -> None:
    print("\n[1] Tool NATIVO (Read)")
    tu, txt, err, ctx = await run(
        f"Usa il tool Read per leggere {HUB / '.mcp.json'} e dimmi SOLO quante righe ha.",
        allowed=["Read"], scoped=[])
    nat_ok = "Read" in tu and not err
    print(f"    tool_use: {tu}  ctx={ctx:,}  err={err or '-'}  -> {'OK' if nat_ok else 'FAIL'}  «{txt[:80]}»")

    print("\n[2] Tool MCP (anja_memory, lazy/deferred dopo il fix)")
    tu, txt, err, ctx = await run(
        "Cerca 'demo-brand' nel wiki usando il tool di ricerca wiki disponibile (mcp anja_memory). "
        "Dimmi SOLO quanti risultati trovi.",
        allowed=["mcp__anja_memory__*", "Read"], scoped=["anja_memory"])
    mcp_used = [t for t in tu if str(t).startswith("mcp__")]
    mcp_ok = bool(mcp_used) and not err
    print(f"    tool_use: {tu}  ctx={ctx:,}  err={err or '-'}  -> {'OK' if mcp_ok else 'FAIL'}  «{txt[:80]}»")

    print("\n" + "=" * 56)
    print(f"  Nativi: {'✓' if nat_ok else '✗'}   MCP (lazy): {'✓' if mcp_ok else '✗ — il fix ha reso gli MCP non invocabili!'}")
    print("=" * 56 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
