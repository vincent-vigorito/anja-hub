"""backup.py — F-BackupDR — backup/restore versionato dell'intero hub.

Il git-shadow (`checkpoint.py`) è una rete anti-proattività: ESCLUDE secrets, i
`.db` e `data/`, ed è sullo stesso disco. Questo modulo è il vero **backup/DR**:
snapshot completo e portabile di `<hub>` in un singolo `.tar.gz`, con:

  - **copia consistente dei .db** (sqlite `.backup()`, WAL-safe — mai tarare un DB
    aperto in scrittura → corruzione);
  - **secrets cifrati** con una CHIAVE DI BACKUP DEDICATA (`ANJA_BACKUP_KEY` o
    `<hub>/config/backup.key`) che NON entra mai nell'archivio → il tar è safe
    off-site; senza la chiave (conservata a parte) i secrets restano illeggibili.
    Con `include_secrets=False` l'archivio è "sanitized" (nessuna credenziale);
  - **extra dirs** fuori-hub (es. le conversazioni webapp) come componenti a parte;
  - **manifest** + **retention** (i backup `pre-update` non si potano mai);
  - **mirror off-site** opzionale (`ANJA_BACKUP_MIRROR_DIR`).

Restore con protezione anti path-traversal (`tarfile` data-filter, Py3.12).
Deps: stdlib + `cryptography.Fernet` (già dep webapp, come vault_io).
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet

BACKUPS_DIRNAME = "backups"
MANIFEST_NAME = "MANIFEST.json"
SECRETS_PREFIX = "secrets.enc"      # dove finiscono i secrets cifrati nel tar
HUB_PREFIX = "hub"                  # radice dei file dell'hub nel tar
EXTRAS_PREFIX = "extras"            # dir fuori-hub (conversazioni webapp, ecc.)

# Pattern dei file-secret raccolti, cifrati e tenuti fuori dal payload in chiaro.
SECRET_GLOBS = ("**/.secrets.env", "**/.secrets.vault", "**/.fernet.key")

# Cosa non entra MAI nel payload hub (ricostruibile, rumore o gestito a parte).
_SKIP_DIRS = {BACKUPS_DIRNAME, ".anja-checkpoints.git", "__pycache__", ".git"}
_SKIP_SUFFIX = (".db-wal", ".db-shm", ".pyc")
_SKIP_NAMES = {"backup.key", ".DS_Store"}


# ----------------------------------------------------------------------
# Chiave di backup (dedicata, MAI nell'archivio)
# ----------------------------------------------------------------------

def _key_path(hub: Path) -> Path:
    return hub / "config" / "backup.key"


def load_backup_key(hub: Path) -> tuple[bytes, bool]:
    """Chiave Fernet di backup: env `ANJA_BACKUP_KEY` → file → genera+scrivi (0600).
    Ritorna (key, generated_now). Se generata ora l'utente DEVE conservarla off-site:
    senza, i secrets cifrati nel backup sono irrecuperabili."""
    env = os.environ.get("ANJA_BACKUP_KEY", "").strip()
    if env:
        return env.encode("utf-8"), False
    kp = _key_path(hub)
    if kp.is_file():
        return kp.read_bytes().strip(), False
    key = Fernet.generate_key()
    kp.parent.mkdir(parents=True, exist_ok=True)
    kp.write_bytes(key)
    os.chmod(kp, 0o600)
    return key, True


# ----------------------------------------------------------------------
# Helper interni
# ----------------------------------------------------------------------

def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _is_secret(path: Path) -> bool:
    name = path.name
    return name in (".secrets.env", ".secrets.vault", ".fernet.key")


def _skip_rel(rel: Path) -> bool:
    """True se il path relativo va escluso dal payload hub in chiaro."""
    parts = set(rel.parts)
    if parts & _SKIP_DIRS:
        return True
    if rel.name in _SKIP_NAMES:
        return True
    if rel.suffix in _SKIP_SUFFIX:
        return True
    return False


def _consistent_db_copy(src: Path, dst: Path) -> bool:
    """Copia WAL-safe di un DB sqlite via backup API. False se non è un sqlite valido."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as s, sqlite3.connect(dst) as d:
            s.backup(d)
        return True
    except sqlite3.Error:
        return False


def _iter_hub_files(hub: Path):
    """File dell'hub da mettere nel payload in chiaro (esclusi secret + skip)."""
    for p in hub.rglob("*"):
        if not p.is_file() or p.is_symlink():
            continue
        rel = p.relative_to(hub)
        if _skip_rel(rel) or _is_secret(p):
            continue
        yield p, rel


def _collect_secrets(hub: Path) -> list[tuple[Path, Path]]:
    seen: dict[Path, Path] = {}
    for pat in SECRET_GLOBS:
        for p in hub.glob(pat):
            if p.is_file() and not p.is_symlink():
                rel = p.relative_to(hub)
                if set(rel.parts) & _SKIP_DIRS:
                    continue
                seen[p] = rel
    return list(seen.items())


# ----------------------------------------------------------------------
# Backup
# ----------------------------------------------------------------------

def create_backup(hub: Path, reason: str = "manual", *, include_secrets: bool = True,
                  extra_dirs: Optional[list[tuple[str, Path]]] = None,
                  mirror_dir: Optional[Path] = None,
                  keep: int = 14) -> dict:
    """Crea un `.tar.gz` completo di `<hub>` in `<hub>/backups/`. Ritorna un dict di esito.

    reason: etichetta (es. 'manual', 'nightly', 'pre-update'); i 'pre-update' non si potano.
    include_secrets: se True cifra i secret con la backup key; se False li omette (sanitized).
    extra_dirs: [(label, path)] fuori-hub da includere (es. conversazioni webapp).
    mirror_dir: se dato, copia l'archivio anche lì (off-site). Env ANJA_BACKUP_MIRROR_DIR di default.
    """
    hub = Path(hub).resolve()
    if not hub.is_dir():
        return {"ok": False, "error": f"hub non trovato: {hub}"}
    backups_dir = hub / BACKUPS_DIRNAME
    backups_dir.mkdir(parents=True, exist_ok=True)

    safe_reason = "".join(c if (c.isalnum() or c in "-_") else "-" for c in reason)[:40] or "manual"
    stamp = _stamp()
    archive = backups_dir / f"anja-backup-{stamp}-{safe_reason}.tar.gz"
    n = 2  # anti-collisione: due backup nello stesso secondo non si sovrascrivono
    while archive.exists():
        archive = backups_dir / f"anja-backup-{stamp}-{safe_reason}-{n}.tar.gz"
        n += 1

    manifest = {
        "created": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "hub": str(hub),
        "include_secrets": include_secrets,
        "components": {"hub_files": 0, "dbs": 0, "secrets": 0, "extras": []},
        "version": 1,
    }

    key_generated = False
    fernet = None
    if include_secrets:
        key, key_generated = load_backup_key(hub)
        fernet = Fernet(key)

    with tempfile.TemporaryDirectory(prefix="anja-backup-") as tmp:
        tmpd = Path(tmp)
        # 1) DB con copia consistente (mappa rel→copia); il resto file-by-file.
        db_copies: dict[Path, Path] = {}
        for p, rel in _iter_hub_files(hub):
            if p.suffix == ".db":
                cp = tmpd / "dbcopy" / rel
                if _consistent_db_copy(p, cp):
                    db_copies[rel] = cp
                    manifest["components"]["dbs"] += 1
                    continue  # la copia consistente sostituisce il live
            manifest["components"]["hub_files"] += 1

        with tarfile.open(archive, "w:gz") as tar:
            # payload hub (file live, ma i .db rimpiazzati dalla copia consistente)
            for p, rel in _iter_hub_files(hub):
                arc = f"{HUB_PREFIX}/{rel.as_posix()}"
                if rel in db_copies:
                    tar.add(db_copies[rel], arcname=arc)
                else:
                    tar.add(p, arcname=arc)
            # secrets cifrati (mai in chiaro, mai la backup.key)
            if include_secrets:
                for p, rel in _collect_secrets(hub):
                    enc = fernet.encrypt(p.read_bytes())
                    ef = tmpd / "sec" / (rel.as_posix() + ".enc")
                    ef.parent.mkdir(parents=True, exist_ok=True)
                    ef.write_bytes(enc)
                    tar.add(ef, arcname=f"{SECRETS_PREFIX}/{rel.as_posix()}.enc")
                    manifest["components"]["secrets"] += 1
            else:
                note = tmpd / "SECRETS-EXCLUDED.txt"
                excluded = "\n".join(str(rel) for _, rel in _collect_secrets(hub))
                note.write_text("Backup sanitized: secrets NON inclusi.\n\n" + excluded + "\n")
                tar.add(note, arcname="SECRETS-EXCLUDED.txt")
            # extra dirs fuori-hub (conversazioni webapp, ecc.)
            for label, d in (extra_dirs or []):
                d = Path(d)
                if not d.is_dir():
                    continue
                count = 0
                for f in d.rglob("*"):
                    if f.is_file() and not f.is_symlink() and f.suffix not in _SKIP_SUFFIX:
                        rel = f.relative_to(d)
                        tar.add(f, arcname=f"{EXTRAS_PREFIX}/{label}/{rel.as_posix()}")
                        count += 1
                manifest["components"]["extras"].append({"label": label, "files": count})
            # manifest per ultimo
            mf = tmpd / MANIFEST_NAME
            mf.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
            tar.add(mf, arcname=MANIFEST_NAME)

    size = archive.stat().st_size
    result = {
        "ok": True, "archive": str(archive), "size": size, "reason": reason,
        "manifest": manifest, "backup_key_generated": key_generated,
    }

    # mirror off-site (best-effort, non fa fallire il backup locale)
    mirror = mirror_dir or (Path(os.environ["ANJA_BACKUP_MIRROR_DIR"])
                            if os.environ.get("ANJA_BACKUP_MIRROR_DIR") else None)
    if mirror:
        try:
            mirror.mkdir(parents=True, exist_ok=True)
            shutil.copy2(archive, mirror / archive.name)
            result["mirrored_to"] = str(mirror / archive.name)
        except OSError as e:
            result["mirror_error"] = str(e)

    result["pruned"] = prune_backups(hub, keep=keep)
    return result


def list_backups(hub: Path) -> list[dict]:
    """Elenca gli archivi in `<hub>/backups/`, più recenti prima, col manifest se leggibile."""
    hub = Path(hub)
    d = hub / BACKUPS_DIRNAME
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("anja-backup-*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True):
        info = {"archive": str(f), "name": f.name, "size": f.stat().st_size,
                "mtime": f.stat().st_mtime}
        info.update(_read_manifest(f))
        out.append(info)
    return out


def _read_manifest(archive: Path) -> dict:
    try:
        with tarfile.open(archive, "r:gz") as tar:
            m = tar.extractfile(MANIFEST_NAME)
            if m:
                man = json.loads(m.read().decode("utf-8"))
                return {"reason": man.get("reason"), "created": man.get("created"),
                        "include_secrets": man.get("include_secrets"),
                        "components": man.get("components")}
    except (tarfile.TarError, KeyError, json.JSONDecodeError, OSError):
        pass
    return {}


def prune_backups(hub: Path, keep: int = 14) -> list[str]:
    """Tiene gli ultimi `keep` archivi; i `pre-update` non si potano mai. Ritorna i rimossi."""
    hub = Path(hub)
    d = hub / BACKUPS_DIRNAME
    if not d.is_dir() or keep <= 0:
        return []
    archives = sorted(d.glob("anja-backup-*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    prunable = [a for a in archives if "pre-update" not in a.name]
    removed = []
    for a in prunable[keep:]:
        try:
            a.unlink()
            removed.append(a.name)
        except OSError:
            pass
    return removed


# ----------------------------------------------------------------------
# Restore
# ----------------------------------------------------------------------

def restore_backup(archive: Path, target_hub: Path, *, restore_extras: bool = False) -> dict:
    """Ripristina un archivio in `target_hub` (merge). Decifra i secrets con la backup key
    (env `ANJA_BACKUP_KEY` o `<target_hub>/config/backup.key`). Anti path-traversal via
    tarfile data-filter. `restore_extras`: ripristina anche le dir fuori-hub in <hub>/extras/.
    """
    archive = Path(archive)
    target_hub = Path(target_hub)
    if not archive.is_file():
        return {"ok": False, "error": f"archivio non trovato: {archive}"}
    target_hub.mkdir(parents=True, exist_ok=True)

    restored = {"hub_files": 0, "secrets": 0, "extras": 0}
    with tempfile.TemporaryDirectory(prefix="anja-restore-") as tmp:
        tmpd = Path(tmp)
        with tarfile.open(archive, "r:gz") as tar:
            try:
                tar.extractall(tmpd, filter="data")   # Py3.12: blocca ../ e path assoluti
            except TypeError:
                tar.extractall(tmpd)                   # fallback runtime più vecchi

        # 1) payload hub
        hub_src = tmpd / HUB_PREFIX
        if hub_src.is_dir():
            for f in hub_src.rglob("*"):
                if f.is_file():
                    rel = f.relative_to(hub_src)
                    dst = target_hub / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dst)
                    restored["hub_files"] += 1

        # 2) secrets cifrati → decifra e riscrivi 0600
        sec_src = tmpd / SECRETS_PREFIX
        if sec_src.is_dir():
            key, _ = load_backup_key(target_hub)
            fernet = Fernet(key)
            for f in sec_src.rglob("*.enc"):
                rel = f.relative_to(sec_src)
                dst = target_hub / str(rel)[:-4]   # toglie .enc
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    dst.write_bytes(fernet.decrypt(f.read_bytes()))
                    os.chmod(dst, 0o600)
                    restored["secrets"] += 1
                except Exception as e:
                    return {"ok": False, "error": f"decifratura secret fallita ({rel}): chiave errata? {e}"}

        # 3) extras (opzionale)
        if restore_extras:
            ex_src = tmpd / EXTRAS_PREFIX
            if ex_src.is_dir():
                dst_root = target_hub / EXTRAS_PREFIX
                for f in ex_src.rglob("*"):
                    if f.is_file():
                        rel = f.relative_to(ex_src)
                        dst = dst_root / rel
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(f, dst)
                        restored["extras"] += 1

    return {"ok": True, "archive": str(archive), "target": str(target_hub), "restored": restored}


# ----------------------------------------------------------------------
# CLI — il restore è un'operazione DR: si fa da fermo, da terminale
# ----------------------------------------------------------------------

def _cli(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="backup", description="Backup/DR dell'hub anja")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create", help="crea un backup")
    c.add_argument("hub"); c.add_argument("--reason", default="manual")
    c.add_argument("--no-secrets", action="store_true")
    l = sub.add_parser("list", help="elenca i backup"); l.add_argument("hub")
    r = sub.add_parser("restore", help="ripristina un archivio in un hub target")
    r.add_argument("archive"); r.add_argument("target")
    r.add_argument("--extras", action="store_true", help="ripristina anche le dir fuori-hub")
    a = ap.parse_args(argv)
    if a.cmd == "create":
        print(json.dumps(create_backup(Path(a.hub), a.reason, include_secrets=not a.no_secrets), indent=2))
    elif a.cmd == "list":
        for b in list_backups(Path(a.hub)):
            mb = b["size"] / (1024 * 1024)
            print(f"{b['name']}  {mb:6.1f} MB  reason={b.get('reason','?')}  secrets={b.get('include_secrets','?')}")
    elif a.cmd == "restore":
        res = restore_backup(Path(a.archive), Path(a.target), restore_extras=a.extras)
        print(json.dumps(res, indent=2))
        return 0 if res.get("ok") else 1
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv[1:]))
