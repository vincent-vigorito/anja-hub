#!/usr/bin/env python3
"""Spike cross-session messaging: una sessione SDK (cli_path → CLI 2.1.226)
è visibile/raggiungibile come peer da altre sessioni Claude Code?"""
import os
import anyio
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock


async def show(client, label):
    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            for b in msg.content:
                if isinstance(b, TextBlock):
                    print(f"[{label}] {b.text}", flush=True)
        elif isinstance(msg, ResultMessage):
            print(f"[{label}] -- fine turno --", flush=True)


async def main():
    opts = ClaudeAgentOptions(
        cli_path=os.environ.get("CLAUDE_BIN", "claude"),
        permission_mode="bypassPermissions",
        model="haiku",
        cwd="/tmp/anja-spike",
        setting_sources=[],
        extra_args={"settings": '{"crossSessionInbound":"accept"}'},
    )
    async with ClaudeSDKClient(options=opts) as client:
        await client.query(
            "Esegui `echo SOCKET=$CLAUDE_CODE_MESSAGING_SOCKET` con Bash e riporta "
            "l'output esatto. Poi dimmi se hai a disposizione un tool chiamato "
            "ListAgents: se sì chiamalo e riporta la lista dei peer che vedi "
            "(nomi e directory). Rispondi conciso."
        )
        await show(client, "turno1")
        print("[spike] sessione idle 90s, in attesa di messaggi peer...", flush=True)
        await anyio.sleep(90)
        await client.query(
            "Hai ricevuto messaggi da altre sessioni mentre eri in attesa? "
            "Se sì citali testualmente e di' da chi arrivavano. Poi, se il mittente "
            "è raggiungibile, rispondigli con SendMessage: 'ricevuto, sono la "
            "sessione SDK di AnjaHub'. Riporta l'esito."
        )
        await show(client, "turno2")


anyio.run(main)
