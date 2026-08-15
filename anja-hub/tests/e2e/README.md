# ASP e2e — F-AgentSessions (design: anja-agent-sessions-design.md)

Test end-to-end della webapp VERA via WS `/api/chat` + endpoint REST di
sessione. Fanno turni haiku reali (costano token) e richiedono il server
del test-hub attivo coi flag:

```bash
ANJA_ASP_ENABLED=1 ANJA_ASP_PERMISSIONS=1 ANJA_ASP_GIT=1 \
  python3.12 anja-hub/webapp/server.py --hub <test-hub> --port 8765
```

| Script | Copre | Attese |
|---|---|---|
| `asp_core_e2e.py` | Fasi 0/1: steering inline, contesto, interrupt, riuso, session.set, event-log JSONL + replay, zero frame WS avanzati | 12/12 |
| `asp_perm_e2e.py` | Fase 2: ask→allow/deny, always-allow appreso, auto-allow, audit decision-trail (richiede `asp-perm-e2e/` pulita e config permessi assente) | 9/9 |
| `asp_f3_e2e.py` | Fase 3: todo.updated live, plan mode (proponi→approva→esegui con permission) | 7/7 |
| `asp_git_e2e.py` | Fase 4: worktree isolato, diff.ready pre-done, review patch, merge esplicito (richiede progetto git `asplab` registrato) | 7/7 |

| `legacy_smoke.py` | path one-shot con flag ASP SPENTI: un done per turno (fix double-done), contesto, niente frame ASP, endpoint 404, niente JSONL — da girare prima di ogni merge in main | 7/7 |

NB: gli URL puntano a 127.0.0.1:8765 — adegua se il server è altrove.
`legacy_smoke.py` vuole il server SENZA flag; gli altri con tutti i flag.
Gli unit test senza LLM (asp_log/asp_map/asp_perm/asp_git) stanno in `../`.
