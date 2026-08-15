"""kanban_dispatcher.py — Fase 15.2 — Background dispatcher per kanban.

Funzioni:
  - auto-promote todo → ready quando deps satisfied
  - detect running tasks "stalled" (no progress >1h) → auto-block
  - detect task "overdue" (due_at passato, non done/archived) → notifica una volta
  - broadcast eventi via WebSocket

Stdlib only (asyncio, sqlite via kanban_io).
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Callable, Awaitable, Optional


POLL_INTERVAL_SEC = 30
STALLED_AFTER_SEC = 3600  # 1h


def _parse_due(raw: str) -> Optional[datetime]:
    """due_at lo scrive un agente in linguaggio naturale-ish: spesso senza fuso
    ('2026-07-28T09:00:00') e a volte come sola data. Un naive assume UTC —
    confrontarlo con un aware solleva TypeError e faceva abortire l'intero tick.
    """
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except Exception:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class KanbanDispatcher:
    def __init__(self, hub_path: Path,
                 on_event: Optional[Callable[[dict], Awaitable[None]]] = None):
        self.hub_path = hub_path
        self.on_event = on_event
        self.running = False
        self.task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self.last_poll_at: Optional[float] = None
        self.last_promotions = 0
        self.last_stalls = 0
        self.last_overdue = 0
        self.last_commitments = 0

    def status(self) -> dict:
        return {
            "running": self.running,
            "last_poll_at": self.last_poll_at,
            "last_promotions": self.last_promotions,
            "last_stalls": self.last_stalls,
            "last_overdue": self.last_overdue,
            "last_commitments": self.last_commitments,
        }

    async def start(self):
        if self.running:
            return
        self._stop_event.clear()
        self.running = True
        self.task = asyncio.create_task(self._loop(), name="kanban-dispatcher")
        print("[kanban_dispatcher] daemon started")

    async def stop(self):
        if not self.running:
            return
        self._stop_event.set()
        if self.task:
            try:
                await asyncio.wait_for(self.task, timeout=5)
            except asyncio.TimeoutError:
                self.task.cancel()
        self.running = False
        print("[kanban_dispatcher] daemon stopped")

    async def _loop(self):
        while not self._stop_event.is_set():
            try:
                await self._tick()
            except Exception as e:
                print(f"[kanban_dispatcher] tick error: {type(e).__name__}: {e}")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=POLL_INTERVAL_SEC)
            except asyncio.TimeoutError:
                pass

    async def _tick(self):
        self.last_poll_at = time.time()
        try:
            import kanban_io as kio
        except ImportError:
            return

        # 1. Auto-promote
        promoted = kio.auto_promote_ready(self.hub_path)
        if promoted:
            self.last_promotions = len(promoted)
            await self._emit({"event": "auto_promoted", "task_ids": promoted})
            print(f"[kanban_dispatcher] auto-promoted {len(promoted)} tasks: {promoted}")
        else:
            self.last_promotions = 0

        # 2. Detect stalled (running >1h senza updates)
        now_iso = datetime.now(timezone.utc)
        stalled_count = 0
        running_tasks = kio.list_tasks(self.hub_path, status="running")
        for t in running_tasks:
            try:
                updated = datetime.fromisoformat(t["updated_at"].replace("Z", "+00:00"))
                if (now_iso - updated).total_seconds() > STALLED_AFTER_SEC:
                    kio.update_status(
                        self.hub_path, t["id"], "blocked",
                        block_reason=f"stalled (no progress in {STALLED_AFTER_SEC // 60}min)"
                    )
                    stalled_count += 1
                    await self._emit({"event": "auto_blocked_stalled", "task_id": t["id"]})
            except Exception:
                continue
        self.last_stalls = stalled_count
        if stalled_count:
            print(f"[kanban_dispatcher] auto-blocked {stalled_count} stalled tasks")

        # 3. Detect overdue (due_at passato, non done/archived, non già notificati)
        overdue_count = 0
        for t in kio.list_tasks(self.hub_path, status="active"):
            due = t.get("due_at")
            if not due:
                continue
            due_dt = _parse_due(due)
            if due_dt is None or due_dt > now_iso:
                continue
            meta = t.get("metadata") or {}
            if meta.get("overdue_notified_at"):
                continue  # già notificato una volta (reset su riprogrammazione: TODO F-Proactive 4)
            meta["overdue_notified_at"] = now_iso.isoformat(timespec="seconds")
            kio.update_task(self.hub_path, t["id"], metadata=meta)
            overdue_count += 1
            await self._emit({"event": "task_overdue", "task_id": t["id"],
                              "title": t["title"], "due_at": due})
        self.last_overdue = overdue_count
        if overdue_count:
            print(f"[kanban_dispatcher] {overdue_count} task(s) overdue")

        # 5. Commitment Steward: consuma la inbox dei follow-up → dedup → crea task
        created = 0
        try:
            import commitment_inbox as cinbox
            import difflib
            pending = cinbox.list_pending(self.hub_path)
            if pending:
                active_titles = [t["title"].lower()
                                 for t in kio.list_tasks(self.hub_path, status="active")]
                for sig in pending:
                    title = (sig.get("text") or "").strip()
                    if not title:
                        cinbox.mark_done(self.hub_path, sig["id"], status="skipped")
                        continue
                    dup = any(difflib.SequenceMatcher(None, title.lower(), at).ratio() > 0.82
                              for at in active_titles)
                    if dup:
                        cinbox.mark_done(self.hub_path, sig["id"], status="skipped")
                        continue
                    t = kio.create_task(
                        self.hub_path, title=title, due_at=sig.get("due_at"),
                        scope=sig.get("scope") or "hub", priority=1,
                        metadata={"origin": "commitment", "source_conv": sig.get("source_conv") or ""},
                    )
                    active_titles.append(title.lower())
                    cinbox.mark_done(self.hub_path, sig["id"], status="done")
                    created += 1
                    try:
                        import decision_trail
                        decision_trail.record(self.hub_path, actor="steward",
                                              trigger=f"signal inbox: {title[:120]}",
                                              decision="task creato",
                                              rationale="nessun task attivo simile (difflib <0.82)",
                                              alternative="scartato come duplicato", confidence=0.7,
                                              scope=sig.get("scope") or "hub", ref=str(t.get("id", "")))
                    except Exception:
                        pass
                    await self._emit({"event": "commitment_task_created", "task_id": t["id"],
                                      "title": title, "due_at": sig.get("due_at")})
        except Exception as e:
            print(f"[kanban_dispatcher] commitment steward error: {type(e).__name__}: {e}")
        self.last_commitments = created
        if created:
            print(f"[kanban_dispatcher] {created} commitment task(s) created")

    async def _emit(self, event: dict):
        if self.on_event:
            try:
                await self.on_event(event)
            except Exception as e:
                print(f"[kanban_dispatcher] emit error: {e}")
        # F-Notify: persist eventi rilevanti per UI Bell.
        try:
            import notification_bus as _nb
            et = event.get("event")
            if et == "auto_promoted":
                ids = event.get("task_ids") or []
                if ids:
                    _nb.publish(
                        self.hub_path, source="kanban", category="info",
                        title=f"{len(ids)} task ready",
                        body=f"Auto-promoted: #{', #'.join(str(i) for i in ids[:5])}"
                             + (f" +{len(ids) - 5} more" if len(ids) > 5 else ""),
                        action={"label": "Open board", "url": "/#kanban", "type": "navigate"},
                        payload={"task_ids": ids},
                    )
            elif et == "auto_blocked_stalled":
                tid = event.get("task_id")
                _nb.publish(
                    self.hub_path, source="kanban", category="warn",
                    title=f"Task #{tid} stalled",
                    body=f"No progress in {STALLED_AFTER_SEC // 60}min, marked blocked",
                    action={"label": "View task", "url": f"/#kanban/task/{tid}", "type": "navigate"},
                    payload={"task_id": tid},
                )
            elif et == "task_overdue":
                tid = event.get("task_id")
                _nb.publish(
                    self.hub_path, source="kanban", category="warn",
                    title=f"Task #{tid} scaduto",
                    body=f"\"{event.get('title', '')}\" era atteso per {event.get('due_at', '')}",
                    action={"label": "View task", "url": f"/#kanban/task/{tid}", "type": "navigate"},
                    payload={"task_id": tid, "due_at": event.get("due_at")},
                )
            elif et == "commitment_task_created":
                tid = event.get("task_id")
                _nb.publish(
                    self.hub_path, source="kanban", category="info",
                    title="Nuovo promemoria da conversazione",
                    body=f"\"{event.get('title', '')}\" — scadenza {event.get('due_at', '')}",
                    action={"label": "View task", "url": f"/#kanban/task/{tid}", "type": "navigate"},
                    payload={"task_id": tid, "origin": "commitment"},
                )
        except Exception:
            pass
