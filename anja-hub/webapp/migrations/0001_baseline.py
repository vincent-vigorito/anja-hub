"""0001_baseline — punto di partenza del versioning dell'hub.

Assicura le directory attese dalle feature recenti (F-BackupDR: `backups/`, `config/`).
Idempotente. Per hub creati prima del versioning stabilisce la baseline senza toccare i dati.
"""

from pathlib import Path


def up(hub_path: Path) -> None:
    hub = Path(hub_path)
    for d in ("backups", "config"):
        (hub / d).mkdir(parents=True, exist_ok=True)
