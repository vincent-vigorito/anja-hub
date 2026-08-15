"""asp_log.py — F-AgentSessions Fase 0: event-log persistente ASP.

Persistenza append-only degli eventi di conversazione (Anja Session Protocol)
in JSONL per-conversazione sotto <hub>/sessions-log/. Il log è la fonte di
verità per replay, audit e (in futuro) resume post-restart; il buffer in-memory
di chat_stream_registry resta la vista calda per i client WS.

Decisioni (design §12, confermate 2026-08-07):
- JSONL per conversazione (bash-native, coerente col resto dell'hub), no DB.
- Envelope evento: {seq, ts, type, ...payload}. seq assegnato dal registry.
- I client ignorano i type sconosciuti → il vocabolario può crescere.

Vocabolario eventi (design §4). I tipi legacy sono il sottoinsieme già emesso
oggi; i tipi asp-* arrivano con le fasi successive. Documentato qui come
riferimento unico, non enforcement rigido (il log accetta qualsiasi type: un
evento nuovo non deve mai andare perso perché il validatore è vecchio).

Stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 1

# Vocabolario di riferimento (design §4.1) — informativo, non bloccante.
EVENT_TYPES = {
    # legacy (già emessi oggi, invariati per compat)
    "text", "tool_use", "usage", "session_id", "done", "error", "notice",
    # Fase 0 — lifecycle turno emesso dal registry
    "turn.started", "turn.completed",
    # riservati per le fasi successive
    "session.started", "session.idle", "session.closed",
    "thinking", "todo.updated", "plan.proposed",
    "permission.requested", "permission.resolved",
    "question.asked", "diff.ready", "subagent.started", "subagent.completed",
}

_MAX_FILE_BYTES = 10 * 1024 * 1024   # rotazione singola generazione (.old)
_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


def _safe_name(conv_id: str) -> str:
    """conv_id è input del client: mai usarlo come path senza sanitizzare."""
    name = _SAFE_RE.sub("_", (conv_id or "").strip())[:80].strip("._")
    return name or "_anon"


class AspLog:
    def __init__(self, hub_path: Path):
        self.base = Path(hub_path) / "sessions-log"
        self.base.mkdir(parents=True, exist_ok=True)

    def _path(self, conv_id: str) -> Path:
        return self.base / f"{_safe_name(conv_id)}.jsonl"

    def append(self, conv_id: str, event: dict) -> None:
        """Append best-effort di un evento (già con seq/ts dal registry).
        Non deve MAI rompere lo stream: gli errori si loggano e basta."""
        try:
            path = self._path(conv_id)
            try:
                if path.stat().st_size > _MAX_FILE_BYTES:
                    path.replace(path.with_suffix(".jsonl.old"))
            except FileNotFoundError:
                pass
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False,
                                   default=str) + "\n")
        except Exception as e:
            print(f"[asp-log] WARN append {conv_id}: {e}")

    def read(self, conv_id: str, since_seq: int = 0,
             limit: int = 2000) -> list[dict]:
        path = self._path(conv_id)
        if not path.is_file():
            return []
        out: list[dict] = []
        try:
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if int(ev.get("seq", 0) or 0) > since_seq:
                        out.append(ev)
                        if len(out) >= limit:
                            break
        except Exception as e:
            print(f"[asp-log] WARN read {conv_id}: {e}")
        return out

    def list_logs(self) -> list[dict]:
        out = []
        for p in sorted(self.base.glob("*.jsonl"),
                        key=lambda x: x.stat().st_mtime, reverse=True):
            out.append({"file": p.name, "bytes": p.stat().st_size,
                        "modified": p.stat().st_mtime})
        return out


_log: Optional[AspLog] = None


def get_log(hub_path) -> AspLog:
    global _log
    if _log is None:
        _log = AspLog(Path(hub_path))
    return _log


def enabled() -> bool:
    return os.environ.get("ANJA_ASP_ENABLED") == "1"


def now_ts() -> float:
    return time.time()
