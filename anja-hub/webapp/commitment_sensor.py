"""commitment_sensor.py — F-Proactive-3 — Sensore follow-up impliciti.

Pass LLM leggero (haiku) fire-and-forget DOPO ogni risposta chat. Legge l'ultimo
scambio (user + assistant) ed estrae impegni futuri DATATI impliciti dell'utente
("domani devo chiamare X" → {text, due}). Deposita i candidati in
[[commitment_inbox]]; sarà il Kanban Steward (kanban_dispatcher) a dedup + creare i task.

Disaccoppiato (approccio B): sensore cheap/frequente qui, curatore batched altrove.
Cap giornaliero per non spammare. Riusa `_haiku_call` di dialectic_pass.

Stdlib + claude-agent-sdk (via dialectic_pass).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import commitment_inbox

MAX_PER_DAY = int(os.environ.get("ANJA_COMMITMENTS_MAX_PER_DAY", "3"))

SYSTEM_PROMPT = (
    "Sei un estrattore di impegni. Leggi uno scambio chat e individua SOLO gli impegni "
    "futuri DATATI che l'UTENTE ha (non l'assistente): cose che l'utente dovrà fare entro "
    "una data/ora ricavabile ('domani', 'lunedì', 'tra 3 giorni', 'il 5 giugno'). "
    "NON inventare. NON includere intenzioni vaghe senza data. NON includere ciò che fa l'assistente. "
    "Rispondi SOLO con un array JSON: [{\"text\": \"<azione concisa, max 8 parole>\", \"due\": \"<YYYY-MM-DD oppure YYYY-MM-DDTHH:MM>\"}]. "
    "Se non c'è alcun impegno datato chiaro, rispondi esattamente: []"
)


def _normalize_due(due: Optional[str]) -> Optional[str]:
    if not due or not isinstance(due, str):
        return None
    due = due.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
        return due + "T09:00:00+00:00"
    try:
        dt = datetime.fromisoformat(due.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat(timespec="seconds")
    except Exception:
        return None


def _build_prompt(user_msg: str, assistant_reply: str) -> str:
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d (%A)")
    return (
        f"Oggi è {today} (UTC). Estrai gli impegni futuri datati dell'utente da questo scambio.\n\n"
        f"--- UTENTE ---\n{user_msg[:2000]}\n\n"
        f"--- ASSISTENTE ---\n{assistant_reply[:2000]}\n"
    )


async def run_commitment_sensor(user_msg: str, assistant_reply: str,
                                scope: str, hub_path: Path,
                                source_conv: str = "") -> int:
    """Estrae follow-up e li accoda. Ritorna n. signal accodati. Non solleva."""
    try:
        if not user_msg or not assistant_reply:
            return 0
        remaining = MAX_PER_DAY - commitment_inbox.count_today(hub_path)
        if remaining <= 0:
            return 0
        from dialectic_pass import _haiku_call, _strip_json_wrappers
        raw = await _haiku_call(_build_prompt(user_msg, assistant_reply), system_prompt=SYSTEM_PROMPT,
                                hub_path=hub_path, feature="commitment")
        if not raw:
            return 0
        try:
            items = json.loads(_strip_json_wrappers(raw))
        except Exception:
            return 0
        if not isinstance(items, list):
            return 0
        enq = 0
        for it in items:
            if enq >= remaining:
                break
            if not isinstance(it, dict):
                continue
            text = (it.get("text") or "").strip()
            due_at = _normalize_due(it.get("due"))
            if not text or not due_at:
                continue  # MVP: scartiamo i follow-up senza scadenza parsabile
            commitment_inbox.enqueue(hub_path, text=text, due_at=due_at,
                                     scope=scope or "hub", source_conv=source_conv)
            enq += 1
        if enq:
            print(f"[commitment] {enq} follow-up accodati (scope={scope})")
        return enq
    except Exception as e:
        print(f"[commitment] sensor error: {type(e).__name__}: {e}")
        return 0


def schedule_commitment_sensor(user_msg: str, assistant_reply: str,
                               scope: str, hub_path: Path, source_conv: str = ""):
    """Fire-and-forget. Stesso pattern di schedule_dialectic_pass."""
    try:
        loop = asyncio.get_event_loop()
        return loop.create_task(
            run_commitment_sensor(user_msg, assistant_reply, scope, hub_path, source_conv))
    except RuntimeError:
        return asyncio.run(
            run_commitment_sensor(user_msg, assistant_reply, scope, hub_path, source_conv))
