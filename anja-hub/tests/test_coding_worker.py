#!/usr/bin/env python3
"""test_coding_worker.py — test funzionale delle 5 fasi del coding worker con
engine INIETTATO (no claude reale). Copre: checkpoint, spawn, verify (pass/fail),
diff, journal, engine-error, e reject/rollback completo (rimozione file nuovi).

Run: python3 test_coding_worker.py   (exit 0 = OK)
"""
import asyncio
import sys
import tempfile
import shutil
from pathlib import Path

WEBAPP = Path(__file__).resolve().parent.parent / "webapp"
sys.path.insert(0, str(WEBAPP))
import coding_worker          # noqa: E402
import coding_engines        # noqa: E402


def _mk_workspace(hub: Path, name: str) -> Path:
    ws = hub / "workspaces" / name
    (ws / ".anjawiki" / "wiki" / "sessions").mkdir(parents=True)
    (hub / "config").mkdir(parents=True, exist_ok=True)
    (hub / "config" / "projects.json").write_text("{}", encoding="utf-8")
    (ws / "app.py").write_text("# placeholder\n", encoding="utf-8")
    return ws


async def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    try:
        hub = tmp / "hub"
        ws = _mk_workspace(hub, "myws")
        spec = {"workspace": "myws", "engine": "claude", "backend": "local",
                "task": {"title": "T", "body": "B", "acceptance": ["x"]}}

        # 1. happy path: engine scrive un file, verify passa
        async def fake_ok(engine, prompt, cwd, **kw):
            (Path(cwd) / "feature.py").write_text("def hello():\n    return 'ciao'\n", encoding="utf-8")
            return coding_engines.EngineResult(engine, 0, turns=3, tokens=1200, log="fake ok")
        (ws / ".anja-verify.yaml").write_text(
            'steps:\n  - name: ok\n    cmd: python3 -c "print(1)"\nfail_fast: true\n', encoding="utf-8")
        r = await coding_worker.run(hub, dict(spec, run_id="r1"), engine_runner=fake_ok)
        assert r.status == "verified", (r.status, r.error)
        assert r.checkpoint_before, "no checkpoint_before"
        assert "feature.py" in r.diff_stat, r.diff_stat
        assert r.verify["passed"] and not r.verify["skipped"], r.verify
        assert r.journal_path and Path(r.journal_path).is_file(), r.journal_path
        assert (ws / "feature.py").is_file()
        print("1. verified path ........ OK")

        # 2. verify FAIL
        (ws / ".anja-verify.yaml").write_text(
            "steps:\n  - name: fail\n    cmd: exit 1\nfail_fast: true\n", encoding="utf-8")
        async def fake_ok2(engine, prompt, cwd, **kw):
            (Path(cwd) / "feature2.py").write_text("x = 1\n", encoding="utf-8")
            return coding_engines.EngineResult(engine, 0, turns=1, tokens=10, log="ok2")
        r2 = await coding_worker.run(hub, dict(spec, run_id="r2"), engine_runner=fake_ok2)
        assert r2.status == "verify-failed", r2.status
        assert not r2.verify["passed"], r2.verify
        assert (ws / "feature2.py").is_file()
        print("2. verify-failed path ... OK")

        # 3. reject → rollback COMPLETO: feature2.py (nuovo nel run2) deve sparire
        rj = coding_worker.reject(hub, "myws", r2.checkpoint_before)
        assert rj["ok"], rj
        assert not (ws / "feature2.py").is_file(), "rollback non ha rimosso il file nuovo"
        assert (ws / "feature.py").is_file(), "rollback ha rimosso un file legittimo pre-run2"
        print("3. reject/rollback ...... OK")

        # 4. engine error → status engine-error, nessun crash
        async def fake_err(engine, prompt, cwd, **kw):
            return coding_engines.EngineResult(engine, 1, error="boom")
        r3 = await coding_worker.run(hub, dict(spec, run_id="r3"), engine_runner=fake_err)
        assert r3.status == "engine-error", r3.status
        assert r3.error, "no error message"
        print("4. engine-error path .... OK")

        # 5. workspace inesistente → error pulito
        r4 = await coding_worker.run(hub, dict(spec, workspace="nope", run_id="r4"), engine_runner=fake_ok)
        assert r4.status == "engine-error" and "non trovato" in (r4.error or ""), r4.error
        print("5. missing workspace .... OK")

        # 6. security #1: no --dangerously-skip-permissions di default; skip solo se sandbox
        cmd = coding_engines._build_claude_cmd("p", {"max_turns": 40},
                                               {"tools": ["Read", "Edit", "Write", "Bash"]})
        assert "--dangerously-skip-permissions" not in cmd, "skip presente in V1 local!"
        assert "--permission-mode" in cmd and any(c.startswith("Bash(") for c in cmd), cmd
        assert "--dangerously-skip-permissions" in coding_engines._build_claude_cmd(
            "p", {}, {"sandbox": True}), "skip non ammesso in sandbox"
        print("6. permission model ..... OK")

        # 7. security #2: env least-privilege — altre chiavi non dichiarate non passano
        import os as _os
        _os.environ["ZZ_OTHER_HUB_KEY"] = "segreto"
        env = coding_engines._build_env(Path("/tmp"), {"tools": ["Bash"]})
        assert "ZZ_OTHER_HUB_KEY" not in env, "esfiltrazione: chiave non dichiarata passata"
        assert "PATH" in env, "PATH perso"
        print("7. env least-privilege .. OK")

        # 8. security #3 (F-Sec-VerifyGateSnapshot): l'engine NON può bypassare il gate
        #    riscrivendo .anja-verify.yaml DURANTE il run. Config pre-run = step che
        #    fallisce; l'engine la sostituisce con uno che passa → deve restare verify-failed.
        (ws / ".anja-verify.yaml").write_text(
            "steps:\n  - name: gate\n    cmd: exit 1\nfail_fast: true\n", encoding="utf-8")
        async def fake_tamper(engine, prompt, cwd, **kw):
            (Path(cwd) / ".anja-verify.yaml").write_text(
                "steps:\n  - name: bypass\n    cmd: exit 0\nfail_fast: true\n", encoding="utf-8")
            return coding_engines.EngineResult(engine, 0, turns=1, tokens=5, log="tampered")
        r5 = await coding_worker.run(hub, dict(spec, run_id="r5"), engine_runner=fake_tamper)
        assert r5.status == "verify-failed", f"gate bypassato dall'agente! {r5.status}"
        assert r5.verify["steps"] and r5.verify["steps"][0]["cmd"] == "exit 1", r5.verify
        print("8. verify-gate snapshot . OK")

        # 9. gate fase 5 (resolve): approve consolida; reject rollbacka; idempotente.
        #    save_run simula la persistenza che in prod fa _run_coding_bg nel server.
        (ws / ".anja-verify.yaml").write_text(
            'steps:\n  - name: ok\n    cmd: python3 -c "print(1)"\nfail_fast: true\n', encoding="utf-8")
        async def fake_keep(engine, prompt, cwd, **kw):
            (Path(cwd) / "gated.py").write_text("y = 2\n", encoding="utf-8")
            return coding_engines.EngineResult(engine, 0, turns=1, tokens=5, log="keep")
        rg = await coding_worker.run(hub, dict(spec, run_id="r6"), engine_runner=fake_keep)
        coding_worker.save_run(hub, rg.to_dict())
        ap = coding_worker.resolve(hub, "r6", "approve")
        assert ap["ok"] and ap["status"] == "approved", ap
        assert coding_worker.load_run(hub, "r6")["status"] == "approved"
        assert not coding_worker.resolve(hub, "r6", "reject")["ok"], "run risolto non si ri-risolve"
        assert (ws / "gated.py").is_file(), "approve non deve rollbackare"

        async def fake_new(engine, prompt, cwd, **kw):
            (Path(cwd) / "to_revert.py").write_text("z = 3\n", encoding="utf-8")
            return coding_engines.EngineResult(engine, 0, turns=1, tokens=5, log="newfile")
        rg2 = await coding_worker.run(hub, dict(spec, run_id="r7"), engine_runner=fake_new)
        coding_worker.save_run(hub, rg2.to_dict())
        assert (ws / "to_revert.py").is_file()
        rj2 = coding_worker.resolve(hub, "r7", "reject")
        assert rj2["ok"] and rj2["status"] == "rejected", rj2
        assert not (ws / "to_revert.py").is_file(), "reject deve rollbackare il file nuovo"
        assert coding_worker.load_run(hub, "r7")["status"] == "rejected"
        print("9. gate approve/reject .. OK")

        print("\n✅ coding worker: 9/9 OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
