"""proactive_scoring.py — F-Proactive-5/5b — Soglia adattiva dell'heartbeat.

Strato C→D del design (anja-proactivity-design.md §7): decide quali task sono
"degni di svegliarti" combinando:
  - score deterministico (priorità + urgenza scadenza + origine)
  - backoff adattivo per-TASK (strato C): un task segnalato >= max_notify volte SENZA
    che l'utente lo tocchi viene declassato (smetti di ripeterlo). Se l'utente reagisce
    (updated_at cambia dopo la segnalazione) il contatore si resetta.
  - declassamento adattivo per-CATEGORIA (strato D, F-Proactive-5b): se l'utente ignora
    ripetutamente un'intera categoria di reminder, i suoi task perdono score (penalità)
    finché non ricomincia a reagire — "vincent ignora i reminder di tipo X".

Principio UX: meglio sotto-notificare. Soglia conservativa; sale solo per urgenza.
Eccezione: i task overdue (scaduti) bypassano il backoff (sono critici).

Lo STATO di tracking è esterno al task (vedi heartbeat_state.json nell'endpoint):
scrivere nei metadata del task cambierebbe updated_at, inquinando il segnale di
"reazione". Qui solo funzioni pure: stato in input, stato aggiornato in output.

Stdlib only.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

DEFAULT_THRESHOLD = float(os.environ.get("ANJA_HEARTBEAT_THRESHOLD", "2.0"))
MAX_NOTIFY = int(os.environ.get("ANJA_HEARTBEAT_MAX_NOTIFY", "3"))

# F-Proactive-5b (Layer D) — declassamento adattivo per-CATEGORIA. Il backoff sopra è
# per singolo task; questo declassa un'intera categoria che l'utente ignora ("vincent
# ignora i reminder di tipo X"): se una categoria è stata segnalata >= min_sample volte
# ma la reaction-rate è sotto soglia, i suoi task subiscono una penalità di score.
CATEGORY_MIN_SAMPLE = int(os.environ.get("ANJA_HEARTBEAT_CAT_MIN_SAMPLE", "4"))
CATEGORY_REACT_MIN = float(os.environ.get("ANJA_HEARTBEAT_CAT_REACT_MIN", "0.25"))
CATEGORY_PENALTY = float(os.environ.get("ANJA_HEARTBEAT_CAT_PENALTY", "1.5"))
CATEGORY_CAP = int(os.environ.get("ANJA_HEARTBEAT_CAT_CAP", "20"))
_CATEGORIES_KEY = "_categories"   # chiave riservata in hb_state (non è un task_id)


def task_category(task: dict) -> str:
    """Categoria per il declassamento adattivo per-tipo (Layer D):
    metadata.category esplicita → metadata.origin → primo tag → 'general'."""
    md = task.get("metadata") or {}
    cat = md.get("category") or md.get("origin")
    if not cat:
        tags = task.get("tags") or []
        cat = tags[0] if tags else "general"
    return (str(cat).strip().lower() or "general")


def _category_penalty(stats: dict) -> float:
    """Penalità di score se la categoria è stata segnalata a sufficienza (>= min_sample)
    ma reagita poco (react_ratio < soglia). 0 altrimenti (campione insufficiente o
    l'utente reagisce). Cumulativo con cap+halve → resta adattivo se si ricomincia a reagire."""
    notified = int(stats.get("notified") or 0)
    reacted = int(stats.get("reacted") or 0)
    if notified < CATEGORY_MIN_SAMPLE:
        return 0.0
    ratio = reacted / notified if notified else 1.0
    return CATEGORY_PENALTY if ratio < CATEGORY_REACT_MIN else 0.0


def _hours_until(due_at: Optional[str], now: datetime) -> Optional[float]:
    if not due_at:
        return None
    try:
        dt = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
        return (dt - now).total_seconds() / 3600.0
    except Exception:
        return None


def score_task(task: dict, now: datetime) -> float:
    """Score deterministico. Più alto = più degno di segnalare."""
    score = float(task.get("priority") or 0)  # 0..3
    h = _hours_until(task.get("due_at"), now)
    if h is not None:
        if h < 0:
            score += 3.0          # overdue
        elif h < 24:
            score += 2.0
        elif h < 48:
            score += 1.0
    # un follow-up INFERITO (commitment) è meno certo di un task esplicito dell'utente
    if (task.get("metadata") or {}).get("origin") == "commitment":
        score -= 0.5
    return score


def select_for_heartbeat(tasks: list, hb_state: dict, now: datetime,
                         threshold: float = DEFAULT_THRESHOLD,
                         max_notify: int = MAX_NOTIFY) -> tuple:
    """Ritorna (selected, new_state).

    `hb_state`: {task_id(str): {notify_count, last_notified_at, last_seen_updated},
                 "_categories": {cat: {notified, reacted}}}.
    `selected`: task da segnalare (ordinati per score desc).
    `new_state`: stato aggiornato da persistere (incluse le stat per-categoria).
    """
    selected = []
    new_state: dict = {}
    # Layer D: copia mutabile delle stat per-categoria (fuori dai task, come il resto).
    cats = {c: dict(v) for c, v in (hb_state.get(_CATEGORIES_KEY) or {}).items()}
    for t in tasks:
        tid = str(t.get("id"))
        st = hb_state.get(tid, {})
        count = int(st.get("notify_count") or 0)
        last_seen = st.get("last_seen_updated") or ""
        reacted = bool(last_seen) and (t.get("updated_at") or "") > last_seen
        cat = task_category(t)
        cstat = cats.setdefault(cat, {"notified": 0, "reacted": 0})
        if reacted:
            count = 0  # l'utente ha toccato il task dopo la segnalazione → reset
            cstat["reacted"] = int(cstat.get("reacted") or 0) + 1  # reazione per-categoria

        score = score_task(t, now)
        h = _hours_until(t.get("due_at"), now)
        overdue = h is not None and h < 0
        # Layer D: le categorie ignorate perdono score; gli overdue bypassano (critici)
        eff_score = score if overdue else score - _category_penalty(cstat)

        if eff_score < threshold:
            new_state[tid] = {**st, "notify_count": count}
            continue
        if count >= max_notify and not overdue:
            # backoff per-task: ripetuto troppo senza reazione → declassa (overdue bypassa)
            new_state[tid] = {**st, "notify_count": count}
            continue

        cstat["notified"] = int(cstat.get("notified") or 0) + 1
        if cstat["notified"] >= CATEGORY_CAP:   # cap+halve: bound + resta adattivo
            cstat["notified"] //= 2
            cstat["reacted"] = int(cstat.get("reacted") or 0) // 2
        new_state[tid] = {
            "notify_count": count + 1,
            "last_notified_at": now.isoformat(timespec="seconds"),
            "last_seen_updated": t.get("updated_at") or "",
        }
        selected.append({
            "id": t.get("id"), "title": t.get("title"),
            "due_at": t.get("due_at"), "priority": t.get("priority"),
            "category": cat,
            "score": round(score, 2),
            "notify_count": count + 1,
        })

    selected.sort(key=lambda x: x["score"], reverse=True)
    new_state[_CATEGORIES_KEY] = cats
    return selected, new_state
