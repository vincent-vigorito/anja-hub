"""memory_undo.py — F-BackupDR Fase 2 — undo mirato delle mutazioni autonome del cervello.

"Time machine" della memoria: annulla una promozione a USER.md sbagliata (il judge del
consolidamento) o le card spazzatura create dallo steward, SENZA rollback dell'intero hub.

Due store, due meccanismi:
  - **Memoria markdown** (`users/*.md`: HOT/detail/dialectic): versionata dal git-shadow
    (`checkpoint.py`, che include i .md). Undo = restore CHIRURGICO di `users/` a un
    checkpoint pre-mutazione — reversibile (il restore crea un checkpoint pre-undo).
  - **Card steward** (`kanban.db`, fuori dallo shadow): le card autonome hanno
    `metadata.origin` noto (commitment/steward/…). Undo = archiviazione (status='archived',
    reversibile, mai delete) delle card autonome recenti.

Il punto di ritorno per la memoria lo crea `_run_dreaming` (server) con un checkpoint
`pre-dreaming` PRIMA di consolidare. Stdlib + checkpoint + kanban_io.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import checkpoint
import kanban_io as kio

# Path (relativi all'hub) dove vive la memoria markdown.
MEMORY_PATHS = ["users"]

# origin delle card generate da azioni AUTONOME (le uniche candidabili all'undo steward).
AUTONOMOUS_ORIGINS = {"commitment", "steward", "proactive", "heartbeat", "dreaming"}

# Label dei checkpoint che sono punti di ritorno della memoria.
_MEM_LABEL_PREFIXES = ("pre-dreaming", "pre-memory", "pre-promotion", "pre-undo")


# ----------------------------------------------------------------------
# Memoria markdown (users/*.md) — via git-shadow
# ----------------------------------------------------------------------

def list_memory_snapshots(hub: Path, n: int = 30) -> list[dict]:
    """I checkpoint che sono punti di ritorno della memoria (creati pre-mutazione)."""
    cps = checkpoint.list_checkpoints(hub, n=200)
    snaps = [c for c in cps if any(c["label"].startswith(p) for p in _MEM_LABEL_PREFIXES)]
    return snaps[:n]


def preview_memory_undo(hub: Path, ref: str) -> dict:
    """Cosa cambierebbe ripristinando la memoria a `ref` (dry-run, solo diff su users/)."""
    diff = checkpoint.diff_paths(hub, ref, MEMORY_PATHS)
    return {"ref": ref, "changed": bool(diff.strip()), "diff": diff}


def undo_memory(hub: Path, ref: str) -> dict:
    """Ripristina i file memoria (`users/*.md`) allo stato `ref`. Reversibile: il restore
    crea un checkpoint pre-undo. Non tocca il resto dell'hub."""
    return checkpoint.restore_paths(hub, ref, MEMORY_PATHS)


# ----------------------------------------------------------------------
# Card steward (kanban.db) — archiviazione reversibile
# ----------------------------------------------------------------------

def list_steward_cards(hub: Path, since_iso: Optional[str] = None) -> list[dict]:
    """Card ATTIVE create da azioni autonome (origin noto), opz. dopo `since_iso`."""
    out = []
    for t in kio.list_tasks(hub, include_archived=False):
        origin = (t.get("metadata") or {}).get("origin")
        if origin not in AUTONOMOUS_ORIGINS:
            continue
        if since_iso and (t.get("created_at") or "") < since_iso:
            continue
        out.append(t)
    return out


def undo_steward_cards(hub: Path, since_iso: Optional[str] = None, dry_run: bool = True) -> dict:
    """Archivia (status='archived', reversibile) le card autonome recenti. Dry-run default:
    ritorna cosa verrebbe archiviato senza toccare nulla."""
    cards = list_steward_cards(hub, since_iso)
    ids = [c["id"] for c in cards]
    if not dry_run:
        for cid in ids:
            kio.update_status(hub, cid, "archived")
    return {
        "dry_run": dry_run,
        "count": len(ids),
        "card_ids": ids,
        "cards": [{"id": c["id"], "title": c.get("title"),
                   "origin": (c.get("metadata") or {}).get("origin"),
                   "created_at": c.get("created_at")} for c in cards],
    }
