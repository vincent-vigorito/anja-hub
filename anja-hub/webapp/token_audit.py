#!/usr/bin/env python3
"""token_audit — misura DOVE vanno i token di contesto di un turno chat (scope hub).

Metodo: misura differenziale. Stesso prompt minimale ("ping"), stesso modello,
si varia UN componente alla volta e si legge `context_input_tokens` (il picco
finestra reale, non il cumulativo billing — vedi claude_chat.py:1099). Il delta
fra due misure isola il peso del componente aggiunto.

Componenti scomposti:
  M0  FLOOR        system minimo,  MCP nessuno   -> costo incomprimibile SDK (system nativo + tool nativi)
  M1  +SYSTEM      system completo, MCP nessuno  -> (M1-M0) = nostro system prompt (template + memoria)
  M2  +MCP scoped  system completo, MCP hub      -> (M2-M1) = schema tool MCP attivi di default (hub_api+anja_memory)
  M3  +MCP all     system completo, MCP tutti    -> (M3-M1) = schema tool MCP di TUTTI i server; (M3-M2) = risparmio scoper

Più una stima statica del blocco-memoria iniettato dal context_composer.

Uso:
  python3.12 token_audit.py --hub /path/to/your-hub
Fa 4 chiamate reali al modello (prompt "ping", sonnet) — costo trascurabile.
"""
import argparse
import asyncio
import sys
from pathlib import Path

import claude_chat as cc

# Lista nativa COSTANTE in tutte le misure (così il delta isola system/MCP, non i nativi).
NATIVE = ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebFetch", "WebSearch", "TodoWrite"]
PROMPT = "ping"
MODEL = "sonnet"
CHAR_PER_TOK = 4.0  # stima grezza char->token per testo markdown misto it/en


async def measure(label: str, system_prompt: str, cwd: Path, scoped_servers, allowed_tools) -> int:
    """Lancia un turno e ritorna context_input_tokens (picco finestra)."""
    ctx_in = 0
    async for ev in cc.stream_response(
        user_prompt=PROMPT,
        system_prompt=system_prompt,
        cwd=cwd,
        model=MODEL,
        allowed_tools=allowed_tools,
        provider="claude",
        scoped_servers=scoped_servers,
    ):
        t = ev.get("type")
        if t == "usage":
            ctx_in = int(ev.get("context_input_tokens") or 0)
        elif t == "error":
            print(f"  [!] {label}: errore -> {ev.get('message')}", file=sys.stderr)
    return ctx_in


async def main(hub: Path) -> None:
    all_servers = []
    mcp_file = hub / ".mcp.json"
    if mcp_file.is_file():
        import json
        all_servers = list((json.loads(mcp_file.read_text())["mcpServers"]).keys())

    scoped_hub = [s for s in ("hub_api", "anja_memory") if s in all_servers]

    sys_full = cc.build_system_prompt(hub, projects=[], user_prompt=PROMPT)
    mem_block = cc._compose_or_legacy(hub, "hub", None, hub.name, PROMPT, "user", "")
    sys_min = "You are a helpful assistant."

    tools_none = cc.augment_with_mcp(NATIVE, hub, "claude", [])
    tools_scoped = cc.augment_with_mcp(NATIVE, hub, "claude", scoped_hub)
    tools_all = cc.augment_with_mcp(NATIVE, hub, "claude", all_servers)

    print(f"\nHub: {hub}")
    print(f"Server MCP nel .mcp.json ({len(all_servers)}): {', '.join(all_servers)}")
    print(f"Scoped di default (hub tier1): {', '.join(scoped_hub)}")
    print(f"system_prompt completo: {len(sys_full):,} char  (~{int(len(sys_full)/CHAR_PER_TOK):,} tok stimati)")
    print(f"  di cui blocco memoria (context_composer): {len(mem_block):,} char  (~{int(len(mem_block)/CHAR_PER_TOK):,} tok stimati)")
    print("\nMisuro (4 chiamate 'ping')...\n")

    m0 = await measure("M0 FLOOR", sys_min, hub, [], NATIVE)
    print(f"  M0 FLOOR        (sys minimo, no MCP) : {m0:>8,} tok")
    m1 = await measure("M1 +SYSTEM", sys_full, hub, [], NATIVE)
    print(f"  M1 +SYSTEM      (sys full,   no MCP) : {m1:>8,} tok")
    m2 = await measure("M2 +MCP scoped", sys_full, hub, scoped_hub, tools_scoped)
    print(f"  M2 +MCP scoped  (sys full, hub MCP)  : {m2:>8,} tok")
    m3 = await measure("M3 +MCP all", sys_full, hub, all_servers, tools_all)
    print(f"  M3 +MCP all     (sys full, TUTTI MCP): {m3:>8,} tok")

    print("\n" + "=" * 56)
    print("BREAKDOWN (picco finestra, per-chiamata)")
    print("=" * 56)
    print(f"  SDK floor (system nativo + tool nativi) : {m0:>8,} tok")
    print(f"  Nostro system prompt (template+memoria) : {m1 - m0:>8,} tok")
    print(f"     └─ di cui memoria (stima)            : {int(len(mem_block)/CHAR_PER_TOK):>8,} tok")
    print(f"  Schema tool MCP — scoped (default)      : {m2 - m1:>8,} tok")
    print(f"  Schema tool MCP — se attivassi TUTTI    : {m3 - m1:>8,} tok")
    print("-" * 56)
    print(f"  TOTALE reale di default (= M2)          : {m2:>8,} tok")
    print(f"  Risparmio dello scoper (M3 - M2)        : {m3 - m2:>8,} tok")
    print("=" * 56 + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub", required=True, type=Path)
    args = ap.parse_args()
    asyncio.run(main(args.hub.resolve()))
