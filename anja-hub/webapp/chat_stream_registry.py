"""chat_stream_registry.py — F-Notify-5 + F-AgentSessions Fase 0.

In-memory registry process-level di stream chat attivi. Disaccoppiato dalle
WS connection: lo stream gira come asyncio.Task indipendente, gli eventi sono
bufferati e i WS reader li pollano. Permette:

 - Multi-tab chat in parallelo (1 stream per conv_id, N tab WS readers).
 - Idle persistence opzione A: stream continua anche se TUTTE le WS muoiono.
 - Resume su reconnect via `events_since(last_seq)`.
 - Cost guard: hard timeout + cap tool iterations.
 - Cancel esplicito via task.cancel().

Fase 0 ASP (design anja-agent-sessions-design.md §3-4): il registry è promosso
a event-log del protocollo — envelope {seq, ts, type, ...}, eventi lifecycle
`turn.started`/`turn.completed` auto-emessi, hook di persistenza su disco
(set_persist → asp_log). I client ignorano i type sconosciuti: gli eventi
attuali restano invariati, il vocabolario cresce senza breaking change.

Stdlib only.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional


# Cap massimo eventi mantenuti in buffer (per stream): oltre, scrolling sliding
BUFFER_MAX_EVENTS = 1000
# Idle prune: stream completed più vecchi di N secondi vengono rimossi
PRUNE_MAX_AGE_SEC = 600
# Cost guards default (override via env in server.py)
DEFAULT_MAX_DURATION_SEC = 600
DEFAULT_MAX_TOOL_ITERATIONS = 30


@dataclass
class StreamState:
    conv_id: str
    scope: str
    model: str
    provider: str
    user_msg: str
    title: str = ""
    started_ts: float = field(default_factory=time.time)
    last_seq: int = 0
    buffer: list = field(default_factory=list)  # [{seq, ...event}]
    full_response: str = ""
    last_usage: Optional[dict] = None
    tool_iter_count: int = 0
    completed: bool = False
    error: Optional[str] = None
    task: Optional[asyncio.Task] = None
    turn_completed_emitted: bool = False   # Fase 0 ASP: guard su turn.completed
    last_session_id: Optional[str] = None  # dedup: l'SDK riemette session_id a ogni messaggio

    def append(self, event: dict) -> int:
        """Append event al buffer, restituisce seq assegnato. Aggiorna anche
        accumulators (`full_response`, `last_usage`, `tool_iter_count`).

        Fase 0 ASP: prima del `done` terminale viene auto-emesso
        `turn.completed` (riepilogo strutturato del turno) — PRIMA, perché i
        client esistenti smettono di leggere al done."""
        if event.get("type") == "session_id":
            sid = event.get("session_id")
            if sid and sid == self.last_session_id:
                return self.last_seq
            self.last_session_id = sid
        if (event.get("type") == "done" and not self.turn_completed_emitted):
            self.turn_completed_emitted = True
            self._append_raw({
                "type": "turn.completed",
                "duration_ms": int((time.time() - self.started_ts) * 1000),
                "tool_uses": self.tool_iter_count,
                "response_chars": len(self.full_response),
                "error": self.error,
                "usage": self.last_usage,
            })
        return self._append_raw(event)

    def _append_raw(self, event: dict) -> int:
        self.last_seq += 1
        seq = self.last_seq
        ev = {"seq": seq, "ts": time.time(), **event}
        self.buffer.append(ev)
        if len(self.buffer) > BUFFER_MAX_EVENTS:
            self.buffer = self.buffer[-BUFFER_MAX_EVENTS:]
        et = event.get("type")
        if et == "text":
            self.full_response += event.get("content", "")
        elif et == "usage":
            self.last_usage = {
                "input_tokens": int(event.get("input_tokens", 0) or 0),
                "context_input_tokens": int(event.get("context_input_tokens", 0) or 0),
                "output_tokens": int(event.get("output_tokens", 0) or 0),
                "context_window": int(event.get("context_window", 0) or 0),
                "ts": time.time(),
            }
        elif et == "tool_use":
            self.tool_iter_count += 1
        if _persist_fn is not None:
            try:
                _persist_fn(self.conv_id, ev)
            except Exception as e:
                print(f"[chat_streams] persist error: {e}")
        return seq

    def events_since(self, since_seq: int) -> list:
        if since_seq <= 0:
            return list(self.buffer)
        return [e for e in self.buffer if e["seq"] > since_seq]

    def to_snapshot(self) -> dict:
        return {
            "conv_id": self.conv_id,
            "scope": self.scope,
            "model": self.model,
            "provider": self.provider,
            "user_msg": self.user_msg[:200],
            "title": self.title,
            "started_ts": self.started_ts,
            "last_seq": self.last_seq,
            "tool_iter_count": self.tool_iter_count,
            "completed": self.completed,
            "error": self.error,
        }


# Process-level registry
_streams: "dict[str, StreamState]" = {}

# Fase 0 ASP — hook di persistenza (asp_log.append), impostato da server.py
# allo startup quando il flag è attivo. None = solo buffer in-memory.
_persist_fn = None


def set_persist(fn) -> None:
    global _persist_fn
    _persist_fn = fn


def register(conv_id: str, scope: str, model: str, provider: str,
             user_msg: str, title: str = "") -> StreamState:
    """Crea o riusa StreamState per conv_id. Se esiste stream attivo (not completed)
    per stesso conv_id, ritorna esistente (caller dovrebbe averlo gestito)."""
    existing = _streams.get(conv_id)
    if existing and not existing.completed:
        return existing
    state = StreamState(conv_id=conv_id, scope=scope, model=model,
                        provider=provider, user_msg=user_msg, title=title)
    _streams[conv_id] = state
    # Fase 0 ASP: apertura turno nel log (i client ignorano i type sconosciuti)
    state._append_raw({
        "type": "turn.started",
        "scope": scope, "model": model, "provider": provider,
        "title": title, "user_msg": (user_msg or "")[:500],
    })
    return state


def get(conv_id: str) -> Optional[StreamState]:
    return _streams.get(conv_id)


def list_active() -> list:
    """Snapshot di stream non completati."""
    return [s.to_snapshot() for s in _streams.values() if not s.completed]


def list_all() -> list:
    """Snapshot di tutti gli stream (anche completati ancora in buffer)."""
    return [s.to_snapshot() for s in _streams.values()]


def cancel(conv_id: str) -> bool:
    s = _streams.get(conv_id)
    if not s:
        return False
    if s.task and not s.task.done():
        s.task.cancel()
        return True
    return False


async def prune_done(max_age_sec: int = PRUNE_MAX_AGE_SEC) -> int:
    """Rimuove stream completati più vecchi di `max_age_sec`. Ritorna count rimossi."""
    now = time.time()
    removed = 0
    for cid in list(_streams.keys()):
        s = _streams[cid]
        if s.completed and (now - s.started_ts) > max_age_sec:
            del _streams[cid]
            removed += 1
    return removed


async def prune_loop(interval_sec: int = 60):
    """Background loop di cleanup. Da lanciare in startup hook."""
    while True:
        try:
            await asyncio.sleep(interval_sec)
            await prune_done()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[chat_streams] prune error: {e}")


def stats() -> dict:
    active = sum(1 for s in _streams.values() if not s.completed)
    return {
        "active": active,
        "total_tracked": len(_streams),
        "buffer_total_events": sum(len(s.buffer) for s in _streams.values()),
    }
