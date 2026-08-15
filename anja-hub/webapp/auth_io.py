"""auth_io.py — identità & sessioni (F4 Concierge).

Master-switch `config.mode` (design §3): `personal` (default, single-user, NESSUNA
auth — zero attrito) | `concierge` (multi-utente con login + ruoli). Tutto il
machinery auth è SPENTO in personal mode.

Stdlib-first (passlib non è una dep del progetto):
  - hashing password = `hashlib.scrypt` (memory-hard) + salt per-utente;
  - sessioni = cookie FIRMATO HMAC stateless (`<slug>.<exp>.<sig>`), sopravvive ai
    restart, niente session store; segreto in `<hub>/.anjawiki/.session.key`.

Store (design §5, single-org = l'hub): `<hub>/.anjawiki/auth.json` (0600, gitignored)
con `users:[{slug,name,role,salt,hash}]`. Ruoli Owner/Admin/Member; primo utente
creato = Owner. Invariante: per passare a concierge serve ≥1 owner.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from pathlib import Path

_SCRYPT = dict(n=2 ** 14, r=8, p=1, dklen=32)
_ROLES = ("owner", "admin", "member")
_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_-]{1,63}")
SESSION_COOKIE = "anja_session"


# --- mode (master-switch) ---------------------------------------------------

def get_mode(hub_path: Path) -> str:
    try:
        cfg = json.loads((Path(hub_path) / "config.json").read_text(encoding="utf-8"))
        m = (cfg.get("mode") or "personal").strip().lower()
        return m if m in ("personal", "concierge") else "personal"
    except Exception:
        return "personal"


def set_mode(hub_path: Path, mode: str) -> str:
    mode = (mode or "").strip().lower()
    if mode not in ("personal", "concierge"):
        raise ValueError("mode non valido")
    if mode == "concierge" and not has_owner(hub_path):
        raise ValueError("crea prima un utente owner")
    p = Path(hub_path) / "config.json"
    cfg = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    cfg["mode"] = mode
    p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    return mode


# --- user store -------------------------------------------------------------

def _auth_path(hub_path: Path) -> Path:
    return Path(hub_path) / ".anjawiki" / "auth.json"


def _load_auth(hub_path: Path) -> dict:
    p = _auth_path(hub_path)
    if not p.is_file():
        return {"users": []}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and isinstance(d.get("users"), list) else {"users": []}
    except Exception:
        return {"users": []}


def _save_auth(hub_path: Path, data: dict) -> None:
    p = _auth_path(hub_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=2, ensure_ascii=False))
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def _pub(u: dict) -> dict:
    return {"slug": u["slug"], "name": u.get("name", u["slug"]), "role": u.get("role", "member")}


def list_users(hub_path: Path) -> list[dict]:
    return [_pub(u) for u in _load_auth(hub_path)["users"]]


def get_user(hub_path: Path, slug: str) -> dict | None:
    for u in _load_auth(hub_path)["users"]:
        if u["slug"] == slug:
            return _pub(u)
    return None


def has_owner(hub_path: Path) -> bool:
    return any(u.get("role") == "owner" for u in _load_auth(hub_path)["users"])


def _hash_pw(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)
    return salt.hex(), h.hex()


def create_user(hub_path: Path, slug: str, name: str, password: str, role: str | None = None,
                 *, actor_role: str | None = None) -> dict:
    slug = (slug or "").strip().lower()
    if not _SLUG_RE.fullmatch(slug):
        raise ValueError("slug non valido (a-z 0-9 _ -, 2-64 char)")
    if len(password or "") < 8:
        raise ValueError("password troppo corta (min 8 caratteri)")
    data = _load_auth(hub_path)
    if any(u["slug"] == slug for u in data["users"]):
        raise ValueError("utente già esistente")
    role = role or ("owner" if not data["users"] else "member")   # primo utente = owner
    if role not in _ROLES:
        raise ValueError("ruolo non valido")
    # concierge: un admin NON può creare un account admin/owner (escalation). None =
    # bootstrap (primo utente) o personal mode = nessun vincolo gerarchico.
    if actor_role is not None and role in ("admin", "owner") and actor_role != "owner":
        raise ValueError("solo un owner può creare utenti admin/owner")
    salt_hex, hash_hex = _hash_pw(password)
    data["users"].append({"slug": slug, "name": (name or slug).strip(), "role": role,
                          "salt": salt_hex, "hash": hash_hex})
    _save_auth(hub_path, data)
    return _pub(data["users"][-1])


def verify(hub_path: Path, slug: str, password: str) -> dict | None:
    for u in _load_auth(hub_path)["users"]:
        if u["slug"] == (slug or "").strip().lower():
            _, calc = _hash_pw(password or "", bytes.fromhex(u["salt"]))
            return _pub(u) if hmac.compare_digest(calc, u["hash"]) else None
    return None


def count_owners(hub_path: Path) -> int:
    return sum(1 for u in _load_auth(hub_path)["users"] if u.get("role") == "owner")


def can_manage(actor_role: str, target_role: str) -> bool:
    """Gerarchia ruoli (concierge): un owner gestisce tutti; un admin gestisce solo
    i member; un member nessuno. Usata per role-change / pw-reset / delete su un
    ALTRO utente — impedisce l'escalation orizzontale/verticale (admin che tocca
    admin o owner). In personal mode il gate è bypassato (actor_role=None)."""
    if actor_role == "owner":
        return True
    if actor_role == "admin" and target_role == "member":
        return True
    return False


def set_password(hub_path: Path, slug: str, new_password: str, *, actor_role: str | None = None) -> dict:
    if len(new_password or "") < 8:
        raise ValueError("password troppo corta (min 8 caratteri)")
    data = _load_auth(hub_path)
    target = next((u for u in data["users"] if u["slug"] == slug), None)
    if not target:
        raise ValueError("utente inesistente")
    if actor_role is not None and not can_manage(actor_role, target.get("role", "member")):
        raise ValueError("permesso negato: ruolo insufficiente per reimpostare questa password")
    target["salt"], target["hash"] = _hash_pw(new_password)
    _save_auth(hub_path, data)
    return _pub(target)


def set_role(hub_path: Path, slug: str, role: str, *, actor_role: str | None = None) -> dict:
    role = (role or "").strip().lower()
    if role not in _ROLES:
        raise ValueError("ruolo non valido")
    data = _load_auth(hub_path)
    target = next((u for u in data["users"] if u["slug"] == slug), None)
    if not target:
        raise ValueError("utente inesistente")
    if actor_role is not None:   # concierge: applica la gerarchia (None = local owner)
        if not can_manage(actor_role, target.get("role", "member")):
            raise ValueError("permesso negato: ruolo insufficiente per gestire questo utente")
        if role in ("admin", "owner") and actor_role != "owner":
            raise ValueError("solo un owner può assegnare ruoli admin/owner")
    owners = sum(1 for u in data["users"] if u.get("role") == "owner")
    if target.get("role") == "owner" and role != "owner" and owners <= 1:
        raise ValueError("non puoi declassare l'ultimo owner")
    target["role"] = role
    _save_auth(hub_path, data)
    return _pub(target)


def delete_user(hub_path: Path, slug: str, *, actor_role: str | None = None) -> None:
    data = _load_auth(hub_path)
    target = next((u for u in data["users"] if u["slug"] == slug), None)
    if not target:
        raise ValueError("utente inesistente")
    if actor_role is not None and not can_manage(actor_role, target.get("role", "member")):
        raise ValueError("permesso negato: ruolo insufficiente per eliminare questo utente")
    owners = sum(1 for u in data["users"] if u.get("role") == "owner")
    if target.get("role") == "owner" and owners <= 1:
        raise ValueError("non puoi eliminare l'ultimo owner")
    data["users"] = [u for u in data["users"] if u["slug"] != slug]
    _save_auth(hub_path, data)


# --- sessioni (cookie HMAC stateless) ---------------------------------------

def _session_secret(hub_path: Path) -> bytes:
    p = Path(hub_path) / ".anjawiki" / ".session.key"
    if p.is_file():
        return p.read_bytes().strip()
    key = secrets.token_bytes(32)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return key


def make_session(hub_path: Path, slug: str, ttl: int = 7 * 24 * 3600, now: int | None = None) -> str:
    exp = int(now if now is not None else time.time()) + ttl
    payload = f"{slug}.{exp}".encode("utf-8")
    sig = hmac.new(_session_secret(hub_path), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload + b"." + sig).decode("ascii")


def read_session(hub_path: Path, token: str, now: int | None = None) -> str | None:
    """Ritorna lo slug se il token è valido e non scaduto, altrimenti None."""
    if not token:
        return None
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        # La firma è SEMPRE 32 byte (sha256) → si separa per lunghezza fissa, NON con
        # rsplit(b".") : la firma binaria può contenere il byte 0x2e (".") e spezzare lo
        # split nel punto sbagliato → ~12% dei token rifiutati a caso (fail-closed).
        if len(raw) < 33 or raw[-33:-32] != b".":
            return None
        payload, sig = raw[:-33], raw[-32:]
        expect = hmac.new(_session_secret(hub_path), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expect):
            return None
        slug, exp = payload.decode("utf-8").rsplit(".", 1)
        if int(exp) < int(now if now is not None else time.time()):
            return None
        return slug
    except Exception:
        return None
