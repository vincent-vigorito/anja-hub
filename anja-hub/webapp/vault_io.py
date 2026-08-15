"""vault_io.py — cassaforte credenziali cifrata (F2, Fernet).

Aggiunge cifratura a riposo allo store dei segreti dei connettori (F1a). Modello
(design §6, come anja-marketer): il **vault** cifrato è il canonico; il runtime
(server MCP) legge `.secrets.env` in chiaro → i segreti si **materializzano** su
`.secrets.env` solo su richiesta ("applica al runtime"), e si possono **rimuovere**
(restando nel vault cifrato). Così sono cifrati di default, plaintext solo nella
finestra in cui servono al runtime.

Chiave Fernet: env `ANJA_FERNET_KEY` (precede) oppure `<hub>/.anjawiki/.fernet.key`
(generata 0600 al primo uso). MAI nel repo (gitignored), separata dai dati.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


def _key_path(hub_path: Path) -> Path:
    return Path(hub_path) / ".anjawiki" / ".fernet.key"


def load_key(hub_path: Path) -> bytes:
    """Chiave Fernet: env ANJA_FERNET_KEY → file → genera+scrivi (0600)."""
    env = os.environ.get("ANJA_FERNET_KEY", "").strip()
    if env:
        return env.encode()
    kp = _key_path(hub_path)
    if kp.is_file():
        return kp.read_bytes().strip()
    key = Fernet.generate_key()
    kp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(kp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    try:
        os.chmod(kp, 0o600)
    except OSError:
        pass
    return key


def load(hub_path: Path, vault_path: Path) -> dict:
    """Decifra il vault → dict {KEY: value}. {} se assente/illeggibile."""
    vp = Path(vault_path)
    if not vp.is_file():
        return {}
    try:
        raw = Fernet(load_key(hub_path)).decrypt(vp.read_bytes())
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except (InvalidToken, ValueError, json.JSONDecodeError):
        return {}


def store(hub_path: Path, vault_path: Path, values: dict) -> None:
    """Cifra `values` e scrive il vault (0600)."""
    vp = Path(vault_path)
    vp.parent.mkdir(parents=True, exist_ok=True)
    token = Fernet(load_key(hub_path)).encrypt(json.dumps(values, ensure_ascii=False).encode("utf-8"))
    fd = os.open(str(vp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(token)
    try:
        os.chmod(vp, 0o600)
    except OSError:
        pass
