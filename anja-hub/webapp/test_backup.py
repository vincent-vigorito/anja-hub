"""test_backup.py — round-trip di backup.py su hub temporanei. Nessun server."""
import json
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import backup as B


def _mk_hub(root: Path) -> Path:
    hub = root / "hub"
    (hub / "config").mkdir(parents=True)
    (hub / ".anjawiki" / "wiki").mkdir(parents=True)
    (hub / "data").mkdir()
    (hub / "workspaces" / "brandx" / ".anjawiki").mkdir(parents=True)
    (hub / "backups").mkdir()
    # markdown / wiki
    (hub / "SOUL.md").write_text("soul")
    (hub / ".anjawiki" / "wiki" / "log.md").write_text("# log\n")
    # DB reale con una riga
    db = hub / "data" / "kanban.db"
    with sqlite3.connect(db) as c:
        c.execute("create table cards(id int, title text)")
        c.execute("insert into cards values (1, 'ciao')")
    # secrets: hub + workspace + fernet key + vault
    (hub / ".secrets.env").write_text("HUB_SECRET=topsecret\n")
    (hub / ".anjawiki" / ".fernet.key").write_bytes(b"fernetkeybytes==")
    (hub / "workspaces" / "brandx" / ".anjawiki" / ".secrets.env").write_text("WP_APP_PASSWORD=abc\n")
    (hub / "workspaces" / "brandx" / ".anjawiki" / ".secrets.vault").write_bytes(b"encryptedblob")
    # rumore da NON includere
    (hub / "data" / "kanban.db-wal").write_text("wal")
    (hub / "__pycache__").mkdir()
    (hub / "__pycache__" / "x.pyc").write_text("junk")
    (hub / ".anja-checkpoints.git").mkdir()
    (hub / ".anja-checkpoints.git" / "HEAD").write_text("ref")
    return hub


def _tar_names(archive: Path) -> set:
    with tarfile.open(archive, "r:gz") as t:
        return set(t.getnames())


PASS, FAIL = 0, 0
def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ✓ {name}")
    else:
        FAIL += 1; print(f"  ✗ {name}")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        hub = _mk_hub(root)

        # --- 1) backup con secrets cifrati + extra dir ---
        extra = root / "webapp_conv"; extra.mkdir()
        (extra / "c1.json").write_text('{"id":"c1"}')
        res = B.create_backup(hub, reason="nightly", include_secrets=True,
                              extra_dirs=[("webapp_conversations", extra)], keep=14)
        print("\n[create_backup include_secrets=True]")
        check("ok", res["ok"])
        archive = Path(res["archive"])
        check("archivio creato", archive.is_file())
        check("backup.key generata", res["backup_key_generated"] is True)
        check("backup.key su disco 0600", oct((hub/"config"/"backup.key").stat().st_mode)[-3:] == "600")
        names = _tar_names(archive)
        check("MANIFEST presente", B.MANIFEST_NAME in names)
        check("SOUL.md nel payload hub", "hub/SOUL.md" in names)
        check("kanban.db nel payload", "hub/data/kanban.db" in names)
        check("secrets cifrati presenti", any(n.startswith("secrets.enc/") and n.endswith(".enc") for n in names))
        check("fernet.key raccolta come secret", "secrets.enc/.anjawiki/.fernet.key.enc" in names)
        check("secret hub NON in chiaro", "hub/.secrets.env" not in names)
        check("vault workspace NON in chiaro", not any(n == "hub/workspaces/brandx/.anjawiki/.secrets.vault" for n in names))
        check("backup.key NON nel tar", not any("backup.key" in n for n in names))
        check("db-wal escluso", not any(n.endswith(".db-wal") for n in names))
        check("__pycache__ escluso", not any("__pycache__" in n for n in names))
        check("shadow git escluso", not any(".anja-checkpoints.git" in n for n in names))
        check("extra conversazioni incluse", "extras/webapp_conversations/c1.json" in names)
        check("manifest dbs=1", res["manifest"]["components"]["dbs"] == 1)
        check("manifest secrets=4", res["manifest"]["components"]["secrets"] == 4)

        # --- 2) restore in hub pulito, stessa backup.key ---
        target = root / "restored"
        (target / "config").mkdir(parents=True)
        (target / "config" / "backup.key").write_bytes((hub/"config"/"backup.key").read_bytes())
        rr = B.restore_backup(archive, target, restore_extras=True)
        print("\n[restore_backup]")
        check("restore ok", rr["ok"])
        check("SOUL.md ripristinato", (target/"SOUL.md").read_text() == "soul")
        check("secret hub decifrato", (target/".secrets.env").read_text() == "HUB_SECRET=topsecret\n")
        check("secret hub 0600", oct((target/".secrets.env").stat().st_mode)[-3:] == "600")
        check("secret workspace decifrato", (target/"workspaces/brandx/.anjawiki/.secrets.env").read_text() == "WP_APP_PASSWORD=abc\n")
        check("fernet.key ripristinata", (target/".anjawiki/.fernet.key").read_bytes() == b"fernetkeybytes==")
        # DB integro e interrogabile
        with sqlite3.connect(target/"data"/"kanban.db") as c:
            row = c.execute("select title from cards where id=1").fetchone()
        check("kanban.db integro (query)", row and row[0] == "ciao")
        check("extra ripristinato", (target/"extras/webapp_conversations/c1.json").is_file())

        # --- 3) restore con chiave SBAGLIATA → errore pulito ---
        target2 = root / "restored_badkey"
        (target2 / "config").mkdir(parents=True)
        from cryptography.fernet import Fernet
        (target2 / "config" / "backup.key").write_bytes(Fernet.generate_key())
        rr2 = B.restore_backup(archive, target2, restore_extras=False)
        print("\n[restore con chiave errata]")
        check("restore fallisce", rr2["ok"] is False)
        check("errore menziona chiave", "chiave" in (rr2.get("error","").lower()))

        # --- 4) backup sanitized (no secrets) ---
        res3 = B.create_backup(hub, reason="manual", include_secrets=False, keep=14)
        names3 = _tar_names(Path(res3["archive"]))
        print("\n[create_backup sanitized]")
        check("nessun secret cifrato", not any(n.startswith("secrets.enc/") for n in names3))
        check("nota SECRETS-EXCLUDED", "SECRETS-EXCLUDED.txt" in names3)
        check("hub payload comunque presente", "hub/SOUL.md" in names3)

        # --- 5) retention: pre-update mai potato ---
        for i in range(3):
            B.create_backup(hub, reason="pre-update", include_secrets=False, keep=1)
        B.create_backup(hub, reason="manual", include_secrets=False, keep=1)
        lst = B.list_backups(hub)
        pu = [b for b in lst if b.get("reason") == "pre-update"]
        print("\n[retention keep=1]")
        check("pre-update preservati (>=3)", len(pu) >= 3)
        check("list_backups legge manifest", all("components" in b for b in lst if b.get("reason")))

    print(f"\n{'='*40}\nPASS={PASS} FAIL={FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
