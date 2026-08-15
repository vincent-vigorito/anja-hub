"""telegram_action_notifier.py — Phase B notification + callback flow.

Quando il pipeline executor emette un pending_action su un goal con autonomy_level=2,
questo modulo:
1. Manda un messaggio Telegram al chat_id allow-listed con inline buttons Approve/Reject/Hold
2. Salva message_id nel record così possiamo edit-after-resolve
3. Risolve pending action quando arriva il callback inline button click

Callback data format: `act:<approve|reject|hold>:<goal_id>:<scope_kind>:<scope_target>:<action_id>`
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


def _format_action_message(action: dict, goal_title: str = "") -> str:
    """Format pending action come messaggio Telegram Markdown."""
    a_type = action.get("type", "?")
    payload = action.get("payload") or {}
    rationale = action.get("rationale", "")
    agent = action.get("agent", "?")
    expires_min = action.get("expires_in_min", 30)

    lines = [
        f"🎯 *Pending action* — `{goal_title or action.get('goal_id','?')}`",
        f"by `{agent}` · type: `{a_type}`",
        "",
    ]
    # Domain-agnostic formatting: mostra mcp_server.mcp_tool + args se presenti
    # (schema canonico definito in goal_executor_l3.execute_pending_action).
    mcp_server = payload.get("mcp_server", "")
    mcp_tool = payload.get("mcp_tool", "")
    args = payload.get("args")
    if mcp_server and mcp_tool:
        lines.append(f"tool: `{mcp_server}.{mcp_tool}`")
        if args:
            try:
                lines.append(f"args: `{json.dumps(args, ensure_ascii=False)[:300]}`")
            except Exception:
                pass
    else:
        # Fallback: dump del payload intero per pending action di forma libera
        lines.append(f"payload: `{json.dumps(payload, ensure_ascii=False)[:300]}`")

    if rationale:
        lines.append("")
        lines.append(f"_{rationale[:300]}_")

    lines.append("")
    lines.append(f"⏱ scade in `{expires_min}min`")
    return "\n".join(lines)


def _build_inline_keyboard(action_id: str, goal_id: str, scope: str) -> dict:
    """Costruisci inline_keyboard con 3 bottoni: Approve / Reject / Hold."""
    # scope='hub' → kind=hub, target=hub
    # scope='workspace:X' → kind=workspace, target=X
    if scope.startswith("workspace:"):
        kind = "workspace"
        target = scope.split(":", 1)[1]
    else:
        kind = "hub"
        target = "hub"
    # cb data limit: 64 bytes. Be terse.
    base = f"act:{{verdict}}:{kind}:{target}:{action_id}"
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Approve", "callback_data": base.replace("{verdict}", "approve")},
                {"text": "❌ Reject",  "callback_data": base.replace("{verdict}", "reject")},
                {"text": "⏸ Hold",    "callback_data": base.replace("{verdict}", "hold")},
            ]
        ]
    }


async def notify_pending_action(hub_path: Path, scope: str, goal_id: str, action: dict) -> Optional[int]:
    """Manda messaggio Telegram per pending action. Restituisce message_id se ok."""
    try:
        from telegram_daemon import load_token, load_config, send_message
        import goal_io
    except ImportError as e:
        print(f"[action_notify] missing deps: {e}", flush=True)
        return None

    token = load_token(hub_path)
    cfg = load_config(hub_path)
    if not token or not cfg.get("allowed_chat_ids"):
        print(f"[action_notify] telegram not configured, skip")
        return None
    chat_id = int(cfg["allowed_chat_ids"][0])  # primo allow-listed

    # Goal title per il context
    goal_title = ""
    try:
        g = goal_io.read_goal(hub_path, scope, goal_id)
        if g:
            goal_title = g.get("meta", {}).get("title", "")
    except Exception:
        pass

    text = _format_action_message(action, goal_title)
    keyboard = _build_inline_keyboard(action.get("id", ""), goal_id, scope)
    try:
        resp = await send_message(token, chat_id, text, reply_markup=keyboard)
        if not resp or not resp.get("ok"):
            print(f"[action_notify] send failed: {resp}", flush=True)
            return None
        msg_id = (resp.get("result") or {}).get("message_id")
        if msg_id:
            try:
                goal_io.set_pending_telegram_message(hub_path, scope, goal_id, action["id"], int(msg_id), chat_id)
            except Exception:
                pass
            print(f"[action_notify] sent message_id={msg_id} for action {action.get('id')}", flush=True)
        return msg_id
    except Exception as e:
        print(f"[action_notify] error: {e}", flush=True)
        return None


async def handle_action_callback(hub_path: Path, cbq: dict) -> bool:
    """Gestisce inline button click per pending action.

    Restituisce True se il callback è stato gestito (caller deve skippare il flow normale).
    callback_data: `act:<verdict>:<kind>:<target>:<action_id>`
    """
    data = cbq.get("data", "")
    if not data.startswith("act:"):
        return False
    try:
        parts = data.split(":", 4)
        if len(parts) != 5:
            return False
        _, verdict, kind, target, action_id = parts
    except Exception:
        return False

    if verdict not in ("approve", "reject", "hold"):
        return False

    scope = "hub" if kind == "hub" else f"workspace:{target}"

    try:
        from telegram_daemon import load_token, answer_callback_query, edit_message_text
        import goal_io
    except ImportError:
        return False

    cb_id = cbq.get("id", "")
    chat_meta = (cbq.get("message") or {}).get("chat") or {}
    chat_id = int(chat_meta.get("id", 0))
    message_id = (cbq.get("message") or {}).get("message_id", 0)
    user_name = (cbq.get("from") or {}).get("username", "user")
    token = load_token(hub_path)

    # Find the action — search across goals (scope is known)
    # Use action_id to find which goal it belongs to via filesystem scan
    # For simplicity: action_id ha pattern act_<ts>_<hex>, the goal is encoded in callback
    # but goal_id isn't in callback (no space). Workaround: scan recent goals.
    found_goal_id = None
    try:
        goals = goal_io.list_goals(hub_path, scope=scope, status=None)
        for g in goals:
            rec = goal_io.get_pending_action(hub_path, scope, g["id"], action_id)
            if rec:
                found_goal_id = g["id"]
                break
    except Exception as e:
        if token and cb_id:
            await answer_callback_query(token, cb_id, f"❌ errore: {e}")
        return True

    if not found_goal_id:
        if token and cb_id:
            await answer_callback_query(token, cb_id, "❌ action non trovata")
        return True

    # Hold = nothing, just ack
    if verdict == "hold":
        if token and cb_id:
            await answer_callback_query(token, cb_id, "⏸ in attesa (riprova con Approve/Reject)")
        return True

    # Resolve
    try:
        resolved = goal_io.resolve_pending_action(
            hub_path, scope, found_goal_id, action_id,
            resolution="approved" if verdict == "approve" else "rejected",
            note=f"by @{user_name} via Telegram",
            by=f"telegram:{user_name}",
        )
    except Exception as e:
        if token and cb_id:
            await answer_callback_query(token, cb_id, f"❌ errore resolve: {e}")
        return True

    # Log come activity event
    try:
        goal_io.append_activity(hub_path, scope, found_goal_id, {
            "agent": f"telegram:{user_name}",
            "level": "success" if verdict == "approve" else "warn",
            "event_type": f"action_{verdict}d",
            "msg": f"pending action {action_id} {verdict}d via Telegram",
            "payload": {"action_id": action_id},
        })
    except Exception:
        pass

    # ACK callback
    if token and cb_id:
        emoji = "✅" if verdict == "approve" else "❌"
        await answer_callback_query(token, cb_id, f"{emoji} action {verdict}d")

    # Edit original message to reflect resolution
    if token and message_id and chat_id:
        try:
            original_text = (cbq.get("message") or {}).get("text", "")
            new_text = original_text + f"\n\n*{verdict.upper()}* by @{user_name}"
            await edit_message_text(token, chat_id, int(message_id), new_text, reply_markup={"inline_keyboard": []})
        except Exception:
            pass

    return True


# ============================================================
# Coding worker gate (F-GoalCodingWorker) — notify + callback Telegram.
# Separato dal flow goal (act:): un coding run NON è un goal, lo stato vive nel
# record JSON <hub>/coding_runs/<run_id>.json. callback_data: cact:<verdict>:<run_id>
# (run_id non contiene ':' → split sicuro; ~37 byte, sotto il limite 64).
# ============================================================

def _format_coding_message(data: dict) -> str:
    st = data.get("status", "?")
    icon = {"verified": "✅", "verify-failed": "⚠️", "engine-error": "💥", "timeout": "⏱"}.get(st, "•")
    eng = data.get("engine") or {}
    diff = (data.get("diff_stat") or "").strip().splitlines()
    diff_last = diff[-1] if diff else "(nessuna modifica)"
    lines = [
        f"{icon} *Coding run* — `{data.get('workspace','?')}`",
        f"status: `{st}` · engine: `{eng.get('engine','?')}` (turns={eng.get('turns',0)})",
        "",
        f"diff: `{diff_last[:120]}`",
    ]
    if data.get("summary"):
        lines += ["", f"_{data['summary'][:300]}_"]
    if data.get("error"):
        lines += ["", f"⚠️ `{str(data['error'])[:200]}`"]
    return "\n".join(lines)


def _coding_keyboard(run_id: str) -> dict:
    base = f"cact:{{v}}:{run_id}"
    return {"inline_keyboard": [[
        {"text": "✅ Approve", "callback_data": base.replace("{v}", "approve")},
        {"text": "❌ Reject",  "callback_data": base.replace("{v}", "reject")},
    ]]}


async def notify_coding_run(hub_path: Path, data: dict) -> Optional[int]:
    """Gate Telegram per un coding run completato. Bottoni solo se c'è un
    checkpoint da cui fare rollback (cioè qualcosa da approvare/rifiutare)."""
    try:
        from telegram_daemon import load_token, load_config, send_message
    except ImportError as e:
        print(f"[coding_notify] missing deps: {e}", flush=True)
        return None
    token = load_token(hub_path)
    cfg = load_config(hub_path)
    if not token or not cfg.get("allowed_chat_ids"):
        return None
    chat_id = int(cfg["allowed_chat_ids"][0])
    text = _format_coding_message(data)
    kb = _coding_keyboard(data["run_id"]) if data.get("checkpoint_before") else None
    try:
        resp = await send_message(token, chat_id, text, reply_markup=kb)
        if not resp or not resp.get("ok"):
            print(f"[coding_notify] send failed: {resp}", flush=True)
            return None
        return (resp.get("result") or {}).get("message_id")
    except Exception as e:
        print(f"[coding_notify] error: {e}", flush=True)
        return None


async def handle_coding_callback(hub_path: Path, cbq: dict) -> bool:
    """Gestisce i bottoni del coding gate. callback_data: cact:<verdict>:<run_id>.
    Restituisce True se gestito (il caller skippa il flow normale)."""
    data = cbq.get("data", "")
    if not data.startswith("cact:"):
        return False
    parts = data.split(":", 2)
    if len(parts) != 3:
        return False
    _, verdict, run_id = parts
    if verdict not in ("approve", "reject"):
        return False

    try:
        from telegram_daemon import load_token, answer_callback_query, edit_message_text
        import coding_worker
    except ImportError:
        return False

    cb_id = cbq.get("id", "")
    chat_meta = (cbq.get("message") or {}).get("chat") or {}
    chat_id = int(chat_meta.get("id", 0))
    message_id = (cbq.get("message") or {}).get("message_id", 0)
    user_name = (cbq.get("from") or {}).get("username", "user")
    token = load_token(hub_path)

    result = coding_worker.resolve(hub_path, run_id, verdict)

    if token and cb_id:
        if result.get("ok"):
            await answer_callback_query(token, cb_id, f"{'✅' if verdict == 'approve' else '❌'} {result.get('status')}")
        else:
            await answer_callback_query(token, cb_id, f"❌ {result.get('error', 'errore')}")

    if result.get("ok") and token and message_id and chat_id:
        try:
            original_text = (cbq.get("message") or {}).get("text", "")
            new_text = original_text + f"\n\n*{result['status'].upper()}* by @{user_name}"
            await edit_message_text(token, chat_id, int(message_id), new_text, reply_markup={"inline_keyboard": []})
        except Exception:
            pass

    return True
