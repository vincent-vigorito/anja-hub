#!/usr/bin/env python3
"""Traccia cosa l'agente lean del workspace passa a kanban.show (dentro run_workspace_query)."""
import os
import asyncio
import json
from pathlib import Path

import claude_chat as chat
import mcp_scoper

HUB = Path(os.environ.get("ANJA_HUB", ""))
WS = "demo-brand"


async def main() -> None:
    ws_root = HUB / "workspaces" / WS
    scoped, _ = mcp_scoper.scope_mcps(hub_path=HUB, scope_kind="project", target_name=WS,
                                      cwd=ws_root, user_prompt="cosa in programma")
    allowed = chat.augment_with_mcp(list(chat.PROJECT_TOOLS_FULL), ws_root,
                                    provider="claude", scoped_servers=scoped)
    sysp = ("Sei l'assistente operativo del workspace demo-brand. Usa i tuoi tool "
            "(kanban, goals, roadmap, marketing) per rispondere in modo FATTUALE.")
    print("scoped servers:", scoped)
    text = ""
    async for ev in chat.stream_response(
        user_prompt="cosa abbiamo in programma oggi? mostrami i task kanban attivi del workspace",
        system_prompt=sysp, cwd=ws_root, model="sonnet",
        allowed_tools=allowed, provider="claude", scoped_servers=scoped,
    ):
        t = ev.get("type")
        if t == "tool_use":
            print(f"TOOL → {ev.get('name')}  input: {json.dumps(ev.get('input'), ensure_ascii=False)[:300]}")
        elif t == "text":
            text += ev.get("content", "")
        elif t == "error":
            print("ERR:", ev.get("message"))
    print("\n--- risposta agente lean ---\n", text.strip()[:500])


if __name__ == "__main__":
    asyncio.run(main())
