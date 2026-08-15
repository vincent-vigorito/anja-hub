"""claude_session.py — F-AgentSessions SPIKE (Fase 1, design: anja-agent-sessions-design.md).

Session pool: un ClaudeSDKClient PERSISTENTE per conversazione, al posto del
query() one-shot di claude_chat.stream_response. Obiettivo dello spike:
validare steering (messaggio iniettato a metà turno), interrupt pulito,
continuità di contesto tra turni e comportamento del subprocess (RAM, idle).

Attivo SOLO dietro flag: env ANJA_ASP_ENABLED=1, e solo per provider claude.
Il path esistente resta il default e non viene toccato.

NB spike: la costruzione delle opzioni duplica deliberatamente la logica di
claude_chat.stream_response (model map, no param `tools` per il fix token-economy,
skills/setting_sources vuoti, mcp_servers filtrati). Alla Fase 1 vera si estrae
un build_claude_options() condiviso — qui non tocchiamo il path in produzione.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, AsyncIterator, Optional

# Alias CLI non affidabili (vedi claude_chat.py): passiamo l'ID pieno.
_SDK_MODEL_IDS = {
    "opus":   "claude-opus-5",
    "sonnet": "claude-sonnet-5",
    "fable":  "claude-fable-5",
    "haiku":  "claude-haiku-4-5",
}

# Finestre di contesto per l'evento usage (stessi default di claude_chat:
# serve all'auto-compact, che senza context_window degrada al fallback 50 msg).
_MODEL_CTX_WINDOW = {
    "haiku":  200000,
    "sonnet": 1000000,
    "opus":   1000000,
    "fable":  1000000,
    "fast":   200000,
}

MAX_SESSIONS = int(os.environ.get("ANJA_ASP_MAX_SESSIONS", "3"))
IDLE_TIMEOUT_SEC = int(os.environ.get("ANJA_ASP_IDLE_SEC", "900"))


_PMODE_ALIASES = {"auto": "bypassPermissions"}


def _build_options_kwargs(
    system_prompt: str,
    cwd: Path,
    model: str,
    allowed_tools: Optional[list],
    effort: Optional[str],
    scoped_servers: Optional[list],
    resume_session_id: Optional[str],
    permission_mode: Optional[str] = None,
) -> dict:
    kwargs: dict[str, Any] = {
        "system_prompt": system_prompt,
        "model": _SDK_MODEL_IDS.get(model, model),
        "cwd": str(cwd),
    }
    # Fase 2 — control-plane permessi: con ANJA_ASP_PERMISSIONS=1 i tool
    # mutanti NON sono pre-approvati (escono da allowed_tools) e la sessione
    # gira in permission_mode "default": tutto ciò che non è whitelistato passa
    # dal callback can_use_tool → policy 3 livelli (asp_permissions).
    # `permission_mode` (preferenza dal client, es. "auto") override alla
    # CREAZIONE — così il mode vale dal primo turno, senza sessione già viva.
    # Senza flag: comportamento Fase 1 (parità col path one-shot in produzione).
    import asp_permissions as _ap
    if _ap.enabled():
        pref = _PMODE_ALIASES.get(permission_mode or "", permission_mode)
        kwargs["permission_mode"] = pref or "default"
        if allowed_tools:
            kwargs["allowed_tools"] = [t for t in allowed_tools
                                       if t not in _ap.SENSITIVE_TOOLS]
    else:
        kwargs["permission_mode"] = "bypassPermissions"
        if allowed_tools:
            kwargs["allowed_tools"] = allowed_tools
    # Token-economy: MAI passare `tools` (anche vuoto = +97k eager). Vedi claude_chat.py.
    kwargs["skills"] = []
    kwargs["setting_sources"] = []
    if scoped_servers is not None:
        try:
            mcp_path = Path(cwd) / ".mcp.json"
            if mcp_path.is_file():
                raw = json.loads(mcp_path.read_text(encoding="utf-8"))
                all_servers = raw.get("mcpServers") or {}
                scoped_dict = {}
                for name in scoped_servers:
                    if name not in all_servers:
                        continue
                    cfg = all_servers[name]
                    cfg_type = (cfg.get("type") or "stdio").lower()
                    entry: dict[str, Any] = {"type": cfg_type}
                    if cfg_type in ("sse", "http"):
                        entry["url"] = cfg.get("url", "")
                        if cfg.get("headers"):
                            entry["headers"] = cfg["headers"]
                    else:
                        entry["type"] = "stdio"
                        entry["command"] = cfg.get("command", "")
                        entry["args"] = cfg.get("args") or []
                        if cfg.get("env"):
                            entry["env"] = cfg["env"]
                    scoped_dict[name] = entry
                kwargs["mcp_servers"] = scoped_dict
                # senza strict il CLI aggiunge i connettori account claude.ai
                # (higgsfield, Canva, Gmail…) che scavalcano il catalogo hub
                kwargs["strict_mcp_config"] = True
        except Exception as e:
            print(f"[asp] WARN mcp_servers filter: {e}")
    if effort and effort in ("low", "medium", "high"):
        kwargs["effort"] = effort
    if resume_session_id:
        kwargs["resume"] = resume_session_id
    return kwargs


# Hook opzionale (set dal server): push di notifica quando una richiesta di
# permesso resta in attesa — es. messaggio Telegram "decidi con /allow o /deny".
notify_ask_fn = None   # async fn(conv_id, tool, target, request_id)


def _emit(conv_id: str, event: dict) -> None:
    """Evento di control-plane direttamente sullo stream della conv (WS + log).
    Il callback permessi gira DENTRO il turno SDK: non può yieldare nel
    generator, scrive sul registry come 'manager' (design §4.1)."""
    try:
        import chat_stream_registry as chat_streams
        state = chat_streams.get(conv_id)
        if state is not None:
            state.append(event)
    except Exception as e:
        print(f"[asp] WARN emit {event.get('type')}: {e}")


def _make_can_use_tool(conv_id: str):
    """Factory del callback permessi per una sessione (Fase 2, design §6)."""
    import asp_permissions as ap
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

    async def can_use_tool(tool_name: str, input_data: Any, context: Any):
        import chat_stream_registry as chat_streams
        target = ap.canonical_target(tool_name,
                                     input_data if isinstance(input_data, dict) else {})
        state = chat_streams.get(conv_id)
        scope = state.scope if state else "hub"

        # AskUserQuestion non ha rendering sui canali headless (in TG arrivava
        # come 🔐 con JSON grezzo): auto-deny con guida — il modello riformula
        # la domanda in testo semplice, che in chat funziona benissimo.
        if tool_name == "AskUserQuestion":
            return PermissionResultDeny(
                message="AskUserQuestion non è disponibile su questo canale: "
                        "fai la domanda direttamente nel testo della risposta, "
                        "elencando le opzioni, e aspetta il messaggio dell'utente.")

        # Fase 3 — plan mode: ExitPlanMode = proposta di piano da approvare,
        # non una permission di policy. Stesso meccanismo pending, eventi
        # plan.proposed/plan.resolved, endpoint /api/session/plan + TG /approve.
        if tool_name == "ExitPlanMode":
            plan_text = str((input_data or {}).get("plan", ""))[:8000] \
                if isinstance(input_data, dict) else ""
            request_id, fut = ap.pending.create(conv_id, scope, "ExitPlanMode",
                                                "plan", input_data or {})
            _emit(conv_id, {"type": "plan.proposed", "request_id": request_id,
                            "plan": plan_text,
                            "timeout_sec": ap.PERMISSION_TIMEOUT_SEC})
            if notify_ask_fn is not None:
                try:
                    await notify_ask_fn(conv_id, "ExitPlanMode",
                                        "piano proposto — /approve · /replan <note>",
                                        request_id)
                except Exception as e:
                    print(f"[asp] WARN notify plan: {e}")
            try:
                res = await asyncio.wait_for(fut, timeout=ap.PERMISSION_TIMEOUT_SEC)
            except asyncio.TimeoutError:
                ap.pending.drop(request_id)
                _emit(conv_id, {"type": "plan.resolved", "request_id": request_id,
                                "decision": "timeout", "by": "timeout"})
                return PermissionResultDeny(
                    message="nessuna risposta al piano: resta in plan mode e attendi",
                    interrupt=False)
            decision, by = res["decision"], res.get("by", "user")
            _emit(conv_id, {"type": "plan.resolved", "request_id": request_id,
                            "decision": decision, "by": by})
            ap.record_decision(tool="ExitPlanMode", target=plan_text[:200],
                               decision=decision, by=by, scope=scope,
                               conv_id=conv_id)
            if decision == "approve":
                return PermissionResultAllow(updated_input=input_data)
            return PermissionResultDeny(
                message=res.get("message") or "piano non approvato: rivedilo",
                interrupt=False)

        try:
            store = ap.get_store()
        except Exception as e:
            # Fail-closed: senza policy store non si concede nulla.
            return PermissionResultDeny(message=f"policy store: {e}", interrupt=False)

        verdict = store.evaluate(scope, tool_name, target)
        if verdict is not None:
            decision = "auto-allow" if verdict == "allow" else "auto-deny"
            _emit(conv_id, {"type": "permission.resolved", "tool": tool_name,
                            "target": target[:200], "decision": decision,
                            "by": "policy"})
            ap.record_decision(tool=tool_name, target=target, decision=decision,
                               by="policy", scope=scope, conv_id=conv_id)
            if verdict == "allow":
                return PermissionResultAllow(updated_input=input_data)
            return PermissionResultDeny(
                message=f"negato da regola di policy (scope {scope})", interrupt=False)

        # ask: evento sul log + eventuale push, poi attesa della risposta
        request_id, fut = ap.pending.create(conv_id, scope, tool_name, target,
                                            input_data if isinstance(input_data, dict) else {})
        _emit(conv_id, {"type": "permission.requested", "request_id": request_id,
                        "tool": tool_name, "target": target[:200],
                        "timeout_sec": ap.PERMISSION_TIMEOUT_SEC})
        if notify_ask_fn is not None:
            try:
                await notify_ask_fn(conv_id, tool_name, target, request_id)
            except Exception as e:
                print(f"[asp] WARN notify_ask: {e}")
        try:
            res = await asyncio.wait_for(fut, timeout=ap.PERMISSION_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            ap.pending.drop(request_id)
            _emit(conv_id, {"type": "permission.resolved", "request_id": request_id,
                            "tool": tool_name, "target": target[:200],
                            "decision": "timeout-deny", "by": "timeout"})
            ap.record_decision(tool=tool_name, target=target,
                               decision="timeout-deny", by="timeout",
                               scope=scope, conv_id=conv_id)
            return PermissionResultDeny(
                message=(f"nessuna risposta dall'utente entro "
                         f"{ap.PERMISSION_TIMEOUT_SEC}s: procedi senza questo tool"),
                interrupt=False)

        decision, by = res["decision"], res.get("by", "user")
        _emit(conv_id, {"type": "permission.resolved", "request_id": request_id,
                        "tool": tool_name, "target": target[:200],
                        "decision": decision, "by": by})
        ap.record_decision(tool=tool_name, target=target, decision=decision,
                           by=by, scope=scope, conv_id=conv_id)
        if decision in ("allow", "always_allow"):
            if decision == "always_allow":
                store.learn_allow(scope, tool_name, target, by=by)
            return PermissionResultAllow(updated_input=input_data)
        return PermissionResultDeny(
            message=res.get("message") or "negato dall'utente", interrupt=False)

    return can_use_tool


class SessionHandle:
    def __init__(self, conv_id: str, client: Any, signature: tuple):
        self.conv_id = conv_id
        self.client = client
        self.signature = signature          # (model, cwd) — se cambia, si ricrea
        self.pmode: Optional[str] = None    # permission_mode corrente della sessione
        self.created_ts = time.time()
        self.last_used_ts = time.time()
        self.turn_active = False
        self.turn_count = 0
        self.sdk_session_id: Optional[str] = None
        self.lock = asyncio.Lock()          # un turno alla volta per sessione
        self.steers_in_flight = 0           # steer inviati nel turno corrente
        # Reader continuo: UN task per sessione consuma receive_messages() e
        # spinge in coda; stream_turn legge dalla coda. Mai wait_for/cancel
        # direttamente sul generator SDK: la cancel di __anext__ lo rompe
        # definitivamente (secondo bug trovato dall'e2e).
        self.queue: asyncio.Queue = asyncio.Queue()
        self.reader_task: Optional[asyncio.Task] = None
        # Fase 3 — tracking subagent: tool_use id del tool Task → label,
        # per emettere subagent.completed quando arriva il suo tool_result.
        self.pending_subagents: dict[str, str] = {}
        # CLI ≥2.1.2xx: TodoWrite sostituito da TaskCreate/TaskUpdate (item
        # singoli, id sequenziali per sessione) — stato accumulato qui per
        # riemettere la lista intera come todo.updated.
        self.tasks: dict[str, dict] = {}


class _ReaderError:
    def __init__(self, msg: str):
        self.msg = msg



async def _reader_loop(handle: "SessionHandle") -> None:
    try:
        async for message in handle.client.receive_messages():
            handle.queue.put_nowait(message)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        handle.queue.put_nowait(_ReaderError(f"{type(e).__name__}: {e}"))


class SessionPool:
    def __init__(self):
        self._sessions: dict[str, SessionHandle] = {}
        self._pool_lock = asyncio.Lock()

    async def get_or_create(self, conv_id: str, options_kwargs: dict) -> SessionHandle:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        signature = (options_kwargs.get("model"), options_kwargs.get("cwd"))
        async with self._pool_lock:
            await self._prune_idle_locked()
            handle = self._sessions.get(conv_id)
            if handle is not None:
                if (handle.signature == signature
                        and options_kwargs.get("permission_mode") == handle.pmode):
                    handle.last_used_ts = time.time()
                    return handle
                # model/cwd/permission_mode cambiati → ricrea (il resume via
                # sdk_session_id mantiene la continuità di contesto; il pmode
                # DEVE passare dal respawn quando coinvolge bypassPermissions,
                # che l'SDK non accetta a runtime)
                if handle.sdk_session_id and "resume" not in options_kwargs:
                    options_kwargs["resume"] = handle.sdk_session_id
                await self._close_locked(conv_id)

            if len(self._sessions) >= MAX_SESSIONS:
                await self._evict_lru_locked()

            import asp_permissions as _ap
            if _ap.enabled():
                options_kwargs["can_use_tool"] = _make_can_use_tool(conv_id)
            # il default 1MB uccide il reader quando un tool result contiene
            # un'immagine base64 (es. Read di un PNG generato) → turno troncato
            options_kwargs.setdefault("max_buffer_size", 32 * 1024 * 1024)
            client = ClaudeSDKClient(options=ClaudeAgentOptions(**options_kwargs))
            await client.connect()
            handle = SessionHandle(conv_id, client, signature)
            handle.pmode = options_kwargs.get("permission_mode")
            handle.reader_task = asyncio.create_task(_reader_loop(handle))
            self._sessions[conv_id] = handle
            print(f"[asp] session created conv={conv_id} model={signature[0]} "
                  f"pool={len(self._sessions)}/{MAX_SESSIONS}")
            return handle

    async def steer(self, conv_id: str, text: str) -> bool:
        """Inietta un messaggio nel turno IN CORSO (steering vero, mid-turn).

        L'SDK può recepirlo inline nel ciclo attivo oppure accodarlo come
        ciclo di risposta successivo: stream_turn gestisce entrambi i casi con
        il contatore steers_in_flight + probe post-result. NB: il presunto
        "orfano a latenza di inferenza" dei primi e2e era in realtà il frame
        double-done del drainer (bug preesistente in server.py, ora fixato) —
        la semantica query() mid-turn è risultata affidabile.
        """
        handle = self._sessions.get(conv_id)
        if handle is None or not handle.turn_active:
            return False
        handle.steers_in_flight += 1
        await handle.client.query(text)
        print(f"[asp] steer conv={conv_id}: {text[:60]!r}")
        return True

    async def set(self, conv_id: str, model: Optional[str] = None,
                  permission_mode: Optional[str] = None) -> dict:
        """session.set runtime sulla sessione VIVA (senza respawn).

        NB: il payload del turno resta la fonte di verità alla ricreazione —
        se la UI manda un model diverso al turno dopo, la sessione si ricicla
        su quello (signature). permission_mode è per uso interno/Fase 2: senza
        control-plane can_use_tool un mode interattivo appenderebbe il turno.
        """
        handle = self._sessions.get(conv_id)
        if handle is None:
            return {"ok": False, "reason": "no session for conv_id"}
        applied: dict[str, str] = {}
        if model:
            sdk_model = _SDK_MODEL_IDS.get(model, model)
            await handle.client.set_model(sdk_model)
            handle.signature = (sdk_model, handle.signature[1])
            applied["model"] = sdk_model
        if permission_mode:
            await handle.client.set_permission_mode(permission_mode)
            handle.pmode = permission_mode
            applied["permission_mode"] = permission_mode
        print(f"[asp] session.set conv={conv_id}: {applied}")
        return {"ok": True, "applied": applied}

    async def interrupt(self, conv_id: str) -> bool:
        """Ferma il turno in corso al prossimo checkpoint. La sessione resta viva."""
        handle = self._sessions.get(conv_id)
        if handle is None or not handle.turn_active:
            return False
        await handle.client.interrupt()
        print(f"[asp] interrupt conv={conv_id}")
        return True

    async def close(self, conv_id: str) -> bool:
        async with self._pool_lock:
            return await self._close_locked(conv_id)

    async def close_all(self) -> None:
        async with self._pool_lock:
            for cid in list(self._sessions):
                await self._close_locked(cid)

    def stats(self) -> dict:
        now = time.time()
        return {
            "max_sessions": MAX_SESSIONS,
            "idle_timeout_sec": IDLE_TIMEOUT_SEC,
            "sessions": [
                {
                    "conv_id": h.conv_id,
                    "model": h.signature[0],
                    "cwd": h.signature[1],
                    "turn_active": h.turn_active,
                    "turn_count": h.turn_count,
                    "age_sec": int(now - h.created_ts),
                    "idle_sec": int(now - h.last_used_ts),
                    "sdk_session_id": h.sdk_session_id,
                }
                for h in self._sessions.values()
            ],
        }

    async def _close_locked(self, conv_id: str) -> bool:
        handle = self._sessions.pop(conv_id, None)
        if handle is None:
            return False
        if handle.reader_task is not None:
            handle.reader_task.cancel()
        try:
            await handle.client.disconnect()
        except Exception as e:
            print(f"[asp] WARN disconnect conv={conv_id}: {e}")
        print(f"[asp] session closed conv={conv_id} turns={handle.turn_count}")
        return True

    async def _prune_idle_locked(self) -> None:
        now = time.time()
        for cid, h in list(self._sessions.items()):
            if not h.turn_active and (now - h.last_used_ts) > IDLE_TIMEOUT_SEC:
                await self._close_locked(cid)

    async def _evict_lru_locked(self) -> None:
        idle = [h for h in self._sessions.values() if not h.turn_active]
        if not idle:
            raise RuntimeError(f"ASP pool full ({MAX_SESSIONS}) e tutte le sessioni attive")
        lru = min(idle, key=lambda h: h.last_used_ts)
        await self._close_locked(lru.conv_id)


pool = SessionPool()


def _extract_text(message: Any) -> str:
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


async def stream_turn(
    conv_id: str,
    user_prompt: str,
    system_prompt: str,
    cwd: Path,
    model: str = "sonnet",
    allowed_tools: Optional[list] = None,
    effort: Optional[str] = None,
    resume_session_id: Optional[str] = None,
    scoped_servers: Optional[list] = None,
    image_attachments: Optional[list] = None,
    permission_mode: Optional[str] = None,
) -> AsyncIterator[dict]:
    """Un turno sulla sessione persistente. Stessi eventi di stream_response:
    session_id / usage / text / tool_use / done / error.
    `permission_mode`: preferenza del client (sticky) — applicata alla
    creazione o riallineata a runtime se la sessione viva ha un mode diverso."""
    options_kwargs = _build_options_kwargs(
        system_prompt, cwd, model, allowed_tools, effort,
        scoped_servers, resume_session_id, permission_mode=permission_mode,
    )
    try:
        handle = await pool.get_or_create(conv_id, options_kwargs)
        _wanted = _PMODE_ALIASES.get(permission_mode or "", permission_mode)
        if _wanted and handle.pmode != _wanted:
            await handle.client.set_permission_mode(_wanted)
            handle.pmode = _wanted
            print(f"[asp] pmode riallineato conv={conv_id} → {_wanted}")
    except Exception as e:
        yield {"type": "error", "message": f"asp session create: {type(e).__name__}: {e}"}
        return

    if image_attachments:
        blocks: list[dict] = []
        if user_prompt:
            blocks.append({"type": "text", "text": user_prompt})
        for img in image_attachments:
            b64 = img.get("image_b64")
            if not b64:
                continue
            blocks.append({
                "type": "image",
                "source": {"type": "base64",
                           "media_type": img.get("mime") or "image/png",
                           "data": b64},
            })

        async def _prompt_iter(_blocks=blocks):
            yield {"type": "user", "message": {"role": "user", "content": _blocks}}

        prompt_arg: Any = _prompt_iter()
    else:
        prompt_arg = user_prompt

    async with handle.lock:
        handle.turn_active = True
        handle.turn_count += 1
        handle.steers_in_flight = 0
        peak_ctx_holder = [0]
        try:
            # RESYNC: drena dalla coda eventuali messaggi orfani di cicli
            # precedenti (es. steer arrivato dopo l'ultimo ResultMessage del
            # turno prima). Senza questo i turni si sfaserebbero di uno.
            stale = 0
            while True:
                try:
                    handle.queue.get_nowait()
                    stale += 1
                except asyncio.QueueEmpty:
                    break
            if stale:
                print(f"[asp] resync conv={conv_id}: drenati {stale} messaggi orfani")

            await handle.client.query(prompt_arg)

            # Turno normale = un ciclo, chiuso dal suo ResultMessage (esatto,
            # zero latenza extra). Con steer nel turno: l'SDK può recepirlo
            # inline (stesso ciclo) o accodarlo come ciclo extra — dopo il
            # result si fa un probe breve: se arriva il ciclo extra lo si
            # consuma nel turno, altrimenti era inline.
            PROBE_SEC = 2.0
            probing = False
            while True:
                try:
                    if probing:
                        message = await asyncio.wait_for(handle.queue.get(),
                                                         timeout=PROBE_SEC)
                        probing = False
                    else:
                        message = await handle.queue.get()
                except asyncio.TimeoutError:
                    break  # steer recepito inline: nessun ciclo extra

                if isinstance(message, _ReaderError):
                    yield {"type": "error", "message": f"asp reader: {message.msg}"}
                    return

                for ev in _map_message(message, handle, model, peak_ctx_holder):
                    yield ev

                if type(message).__name__ == "ResultMessage":
                    if handle.steers_in_flight > 0:
                        handle.steers_in_flight -= 1
                        probing = True  # possibile ciclo extra dello steer
                        continue
                    break

            yield {"type": "done"}
        except Exception as e:
            yield {"type": "error", "message": f"asp turn: {type(e).__name__}: {e}"}
        finally:
            handle.turn_active = False
            handle.last_used_ts = time.time()


def _map_message(message: Any, handle: SessionHandle, model: str,
                 peak_ctx_holder: list) -> list:
    """Mappa un messaggio SDK negli eventi dict del protocollo attuale."""
    out: list[dict] = []
    sid = getattr(message, "session_id", None)
    if sid:
        handle.sdk_session_id = sid
        out.append({"type": "session_id", "session_id": sid})
    data = getattr(message, "data", None)
    if isinstance(data, dict) and data.get("session_id"):
        handle.sdk_session_id = data["session_id"]
        out.append({"type": "session_id", "session_id": data["session_id"]})

    mtype = type(message).__name__
    if mtype == "AssistantMessage":
        _mu = getattr(message, "usage", None)
        if isinstance(_mu, dict):
            _ci = (int(_mu.get("input_tokens", 0) or 0)
                   + int(_mu.get("cache_creation_input_tokens", 0) or 0)
                   + int(_mu.get("cache_read_input_tokens", 0) or 0))
            peak_ctx_holder[0] = max(peak_ctx_holder[0], _ci)
    if mtype == "ResultMessage":
        usage_dict = getattr(message, "usage", None)
        if isinstance(usage_dict, dict):
            in_t = int(usage_dict.get("input_tokens", 0) or 0)
            out_t = int(usage_dict.get("output_tokens", 0) or 0)
            cache_in = int(usage_dict.get("cache_creation_input_tokens", 0) or 0)
            cache_read = int(usage_dict.get("cache_read_input_tokens", 0) or 0)
            total_in = in_t + cache_in + cache_read
            out.append({
                "type": "usage",
                "input_tokens": total_in,
                "context_input_tokens": peak_ctx_holder[0] or total_in,
                "output_tokens": out_t,
                "total_tokens": total_in + out_t,
                "context_window": _MODEL_CTX_WINDOW.get(model, 200000),
                "cache_read_tokens": cache_read,
                "model": model,
            })
        _tr = getattr(message, "terminal_reason", None)
        if _tr and str(_tr).startswith("aborted"):
            out.append({"type": "notice", "message": "turno interrotto"})

    text = _extract_text(message)
    if text and mtype == "AssistantMessage":
        out.append({"type": "text", "content": text})

    content = getattr(message, "content", None)
    if isinstance(content, list):
        for block in content:
            block_type = type(block).__name__
            # Fase 3 — thinking: segnale senza contenuto (design §4.1)
            if "Thinking" in block_type:
                out.append({"type": "thinking"})
                continue
            if "Tool" in block_type and "Use" in block_type:
                tool_name = getattr(block, "name", "?")
                tool_input = getattr(block, "input", {})
                if not isinstance(tool_input, dict):
                    tool_input = {}
                # Fase 3 — TodoWrite → todo.updated (pannello progresso),
                # niente chip generico: il todo È il rendering.
                if tool_name == "TodoWrite":
                    todos = tool_input.get("todos") or []
                    out.append({"type": "todo.updated", "todos": [
                        {"content": str(t.get("content", ""))[:200],
                         "status": t.get("status", "pending")}
                        for t in todos if isinstance(t, dict)
                    ]})
                    continue
                # CLI ≥2.1.2xx — TaskCreate/TaskUpdate al posto di TodoWrite:
                # id sequenziali per sessione (come li assegna il CLI).
                if tool_name in ("TaskCreate", "TaskUpdate"):
                    if tool_name == "TaskCreate":
                        tid = str(len(handle.tasks) + 1)
                        handle.tasks[tid] = {
                            "content": str(tool_input.get("subject", ""))[:200],
                            "status": "pending"}
                    else:
                        tid = str(tool_input.get("taskId", ""))
                        task = handle.tasks.get(tid)
                        status = str(tool_input.get("status", ""))
                        if task and status == "deleted":
                            handle.tasks.pop(tid)
                        elif task:
                            if status:
                                task["status"] = status
                            if tool_input.get("subject"):
                                task["content"] = str(tool_input["subject"])[:200]
                    out.append({"type": "todo.updated",
                                "todos": list(handle.tasks.values())})
                    continue
                # Fase 3 — Task → subagent.started (tracked per il completed)
                if tool_name == "Task":
                    label = str(tool_input.get("description")
                                or tool_input.get("prompt", ""))[:120]
                    tuid = getattr(block, "id", "") or ""
                    if tuid:
                        handle.pending_subagents[tuid] = label
                    out.append({"type": "subagent.started",
                                "tool_use_id": tuid, "label": label})
                    continue
                out.append({
                    "type": "tool_use",
                    "name": tool_name,
                    "input": tool_input,
                })
            # Fase 3 — tool_result: esito visibile (errori) + chiusura subagent
            elif "ToolResult" in block_type:
                tuid = getattr(block, "tool_use_id", "") or ""
                is_err = bool(getattr(block, "is_error", False))
                if tuid in handle.pending_subagents:
                    out.append({"type": "subagent.completed",
                                "tool_use_id": tuid,
                                "label": handle.pending_subagents.pop(tuid),
                                "is_error": is_err})
                elif is_err:
                    raw = getattr(block, "content", "")
                    out.append({"type": "tool.result", "tool_use_id": tuid,
                                "is_error": True, "output": str(raw)[:300]})
    return out
