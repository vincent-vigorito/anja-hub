"""asp_permissions.py — F-AgentSessions Fase 2: control-plane permessi.

Il cuore della fase: quando la sessione ASP incontra un tool NON pre-approvato,
la decisione passa da qui (design §6). Tre livelli, primo match vince:

  1. regole statiche per scope   (deny prima di allow, file di policy)
  2. always-allow appresi        (persistiti dalle risposte "sempre", stessi file)
  3. ask                         → permission.requested sul log eventi, attesa
                                   della risposta da QUALUNQUE canale (UI/TG),
                                   timeout → deny-and-continue

Policy store: <hub>/config/asp_permissions.json — ispezionabile e editabile a
mano. Ogni regola: {scope, tool, pattern, action, source, added, by}.
Matching: tool esatto + fnmatch del pattern sul "target canonico" dell'input
(Bash→command, Write/Edit→file_path, altrimenti dump compatto).

Attivo solo con ANJA_ASP_PERMISSIONS=1 (in aggiunta ad ANJA_ASP_ENABLED):
la Fase 1 (bypassPermissions) resta il default finché il control-plane non è
validato. Stdlib only.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional

PERMISSION_TIMEOUT_SEC = int(os.environ.get("ANJA_ASP_PERMISSION_TIMEOUT_SEC", "300"))

# Tool nativi mutanti: con control-plane attivo NON vengono pre-approvati in
# allowed_tools — passano da can_use_tool (e quindi da questa policy).
SENSITIVE_TOOLS = {"Bash", "Write", "Edit", "MultiEdit", "NotebookEdit"}


def enabled() -> bool:
    return (os.environ.get("ANJA_ASP_ENABLED") == "1"
            and os.environ.get("ANJA_ASP_PERMISSIONS") == "1")


def canonical_target(tool_name: str, input_data: dict) -> str:
    """La stringa su cui matchano i pattern delle regole."""
    if not isinstance(input_data, dict):
        return str(input_data)[:500]
    if tool_name == "Bash":
        return str(input_data.get("command", ""))[:500]
    for key in ("file_path", "path", "url", "pattern"):
        if input_data.get(key):
            return str(input_data[key])[:500]
    return json.dumps(input_data, ensure_ascii=False, sort_keys=True)[:500]


class PolicyStore:
    def __init__(self, hub_path: Path):
        self.path = Path(hub_path) / "config" / "asp_permissions.json"

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"version": 1, "rules": []}

    def rules(self) -> list[dict]:
        return self._load().get("rules", [])

    def evaluate(self, scope: str, tool: str, target: str) -> Optional[str]:
        """'allow' | 'deny' | None (=ask). Deny vince su allow a parità."""
        matched_allow = False
        for r in self.rules():
            if r.get("tool") != tool:
                continue
            r_scope = r.get("scope", "*")
            if r_scope not in ("*", scope):
                continue
            if not fnmatch.fnmatch(target, r.get("pattern", "*")):
                continue
            if r.get("action") == "deny":
                return "deny"
            matched_allow = True
        return "allow" if matched_allow else None

    def learn_allow(self, scope: str, tool: str, target: str, by: str = "") -> dict:
        """Persiste un always-allow appreso. Pattern = target esatto per Bash
        (comando specifico), prefisso-dir per i path (file vicini inclusi)."""
        if tool == "Bash":
            pattern = target
        elif "/" in target:
            pattern = target.rsplit("/", 1)[0] + "/*"
        else:
            pattern = target or "*"
        rule = {"scope": scope, "tool": tool, "pattern": pattern,
                "action": "allow", "source": "learned",
                "added": time.strftime("%Y-%m-%d %H:%M:%S"), "by": by or "user"}
        data = self._load()
        data.setdefault("rules", []).append(rule)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        return rule


class PendingRequests:
    """Richieste di permesso in attesa di risposta, risolvibili da ogni canale."""

    def __init__(self):
        self._pending: dict[str, dict] = {}   # request_id → {future, meta}

    def create(self, conv_id: str, scope: str, tool: str, target: str,
               input_data: dict) -> tuple[str, asyncio.Future]:
        request_id = uuid.uuid4().hex[:12]
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = {
            "future": fut, "conv_id": conv_id, "scope": scope, "tool": tool,
            "target": target, "input": input_data, "ts": time.time(),
        }
        return request_id, fut

    def resolve(self, request_id: str, decision: str, message: str = "",
                by: str = "") -> Optional[dict]:
        """decision: allow | always_allow | deny. Ritorna i meta o None."""
        meta = self._pending.pop(request_id, None)
        if meta is None or meta["future"].done():
            return None
        meta["future"].set_result({"decision": decision, "message": message,
                                   "by": by})
        return meta

    def latest_for_conv(self, conv_id: str) -> Optional[str]:
        """request_id più recente pendente per una conv (per /allow /deny TG)."""
        best, best_ts = None, 0.0
        for rid, m in self._pending.items():
            if m["conv_id"] == conv_id and not m["future"].done() and m["ts"] > best_ts:
                best, best_ts = rid, m["ts"]
        return best

    def drop(self, request_id: str) -> None:
        self._pending.pop(request_id, None)

    def snapshot(self) -> list[dict]:
        return [{k: v for k, v in m.items() if k != "future"} | {"request_id": rid}
                for rid, m in self._pending.items()]


pending = PendingRequests()

_store: Optional[PolicyStore] = None
_hub_path: Optional[Path] = None


def configure(hub_path) -> None:
    """Chiamata dal server allo startup (evita import circolari altrove)."""
    global _store, _hub_path
    _hub_path = Path(hub_path)
    _store = PolicyStore(_hub_path)


def get_store() -> PolicyStore:
    if _store is None:
        raise RuntimeError("asp_permissions non configurato (configure(hub_path))")
    return _store


def record_decision(*, tool: str, target: str, decision: str, by: str,
                    scope: str, conv_id: str) -> None:
    """Audit nel decision-trail (/decisions). Best-effort, mai bloccante."""
    if _hub_path is None:
        return
    try:
        import decision_trail
        decision_trail.record(
            _hub_path, actor="asp-permission",
            trigger=f"{tool}: {target[:200]}",
            decision=decision, rationale=f"by={by}",
            scope=scope, ref=conv_id,
        )
    except Exception:
        pass
