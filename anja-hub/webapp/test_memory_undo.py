"""test_memory_undo.py — undo mirato memoria (git-shadow) + card steward (kanban).
Nessun server: esercita checkpoint.py + memory_undo.py su hub temporanei."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import checkpoint
import kanban_io as kio
import memory_undo as mu

PASS, FAIL = 0, 0
def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ✓ {name}")
    else:
        FAIL += 1; print(f"  ✗ {name}")


def _write(p: Path, s: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        hub = Path(tmp) / "hub"
        users = hub / "users"
        _write(users / "vincent.md", "# Vincent\n\nFatto vero e stabile.\n")
        _write(hub / ".anjawiki" / "wiki" / "log.md", "# log\n")

        # shadow preesistente (come in produzione) + una modifica, così il checkpoint
        # 'pre-dreaming' successivo committa davvero con la sua label
        checkpoint.ensure(hub)
        _write(hub / ".anjawiki" / "wiki" / "log.md", "# log\naggiornamento\n")

        # --- 1) snapshot pre-mutazione, poi promozione "sbagliata" ---
        pre = checkpoint.checkpoint(hub, "pre-dreaming: consolidamento memoria")
        print("\n[snapshot + mutazione memoria]")
        check("checkpoint pre-dreaming creato", bool(pre))
        snaps = mu.list_memory_snapshots(hub)
        check("snapshot listato come punto di ritorno", any(s["sha"] == pre for s in snaps))

        # il judge promuove qualcosa di SBAGLIATO a USER.md + tocca il dialectic
        cur = (users / "vincent.md").read_text()
        _write(users / "vincent.md", cur + "\nPreferenza INVENTATA promossa per errore. <!-- auto-promoted -->\n")
        _write(users / "vincent-dialectic.md", "## Promoted to USER.md\n- roba sbagliata\n")
        check("USER.md contiene la promozione errata", "INVENTATA" in (users / "vincent.md").read_text())

        # preview dell'undo (dry-run): deve vedere il cambiamento
        prev = mu.preview_memory_undo(hub, pre)
        check("preview rileva modifica", prev["changed"] is True)
        check("preview menziona users/vincent.md", "vincent.md" in prev["diff"])

        # --- 2) undo memoria: torna al pre-dreaming, SOLO users/ ---
        # modifica NON-memoria che NON deve essere toccata dall'undo chirurgico
        _write(hub / ".anjawiki" / "wiki" / "log.md", "# log\nnuova riga legittima\n")
        res = mu.undo_memory(hub, pre)
        print("\n[undo chirurgico memoria]")
        check("undo ok", res["ok"])
        check("USER.md ripristinato (no INVENTATA)", "INVENTATA" not in (users / "vincent.md").read_text())
        check("USER.md ha di nuovo il fatto vero", "stabile" in (users / "vincent.md").read_text())
        check("dialectic errato rimosso/ripristinato", not (users / "vincent-dialectic.md").exists()
              or "sbagliata" not in (users / "vincent-dialectic.md").read_text())
        check("modifica NON-memoria intatta (chirurgico)",
              "legittima" in (hub / ".anjawiki" / "wiki" / "log.md").read_text())
        check("pre-undo checkpoint creato (reversibile)", bool(res.get("pre_undo_checkpoint")))

        # --- 3) card steward: archiviazione reversibile ---
        print("\n[undo card steward]")
        # card autonome (origin noto) + una card manuale (NON deve essere toccata)
        c1 = kio.create_task(hub, title="Task spazzatura A", metadata={"origin": "commitment"})
        c2 = kio.create_task(hub, title="Task spazzatura B", metadata={"origin": "steward"})
        cm = kio.create_task(hub, title="Task mio manuale", metadata={"origin": "user"})
        cand = mu.list_steward_cards(hub)
        check("2 card autonome candidate", len(cand) == 2)
        check("card manuale esclusa", all(c["id"] != cm["id"] for c in cand))

        dry = mu.undo_steward_cards(hub, dry_run=True)
        check("dry-run non archivia", dry["dry_run"] and dry["count"] == 2)
        check("dry-run: card ancora attive", kio.get_task(hub, c1["id"])["status"] != "archived")

        real = mu.undo_steward_cards(hub, dry_run=False)
        check("archiviazione esegue (count=2)", real["count"] == 2 and not real["dry_run"])
        check("card A archiviata", kio.get_task(hub, c1["id"])["status"] == "archived")
        check("card B archiviata", kio.get_task(hub, c2["id"])["status"] == "archived")
        check("card manuale intatta", kio.get_task(hub, cm["id"])["status"] != "archived")
        check("archived reversibile (non delete)", kio.get_task(hub, c1["id"]) is not None)

        # --- 4) ref invalido → errore pulito (anti-injection) ---
        print("\n[validazione ref]")
        try:
            mu.undo_memory(hub, "--upload-pack=evil")
            check("ref malevolo rifiutato", False)
        except ValueError:
            check("ref malevolo rifiutato", True)

    print(f"\n{'='*40}\nPASS={PASS} FAIL={FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
