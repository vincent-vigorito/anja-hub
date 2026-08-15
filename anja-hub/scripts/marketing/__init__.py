"""marketing — client + resolver per il verticale gestione sito/ecommerce.

Portato da anja-marketer (assorbito in AnjaHub, vedi anja-marketing-workspace-design.md).
Client puri (httpx / google.auth) usati dal server MCP `anja_marketing` e, in futuro,
da routine/dashboard in-process. Le credenziali NON stanno qui: arrivano dal vault
a 2 livelli (vault.py), scopizzato per-workspace.
"""
