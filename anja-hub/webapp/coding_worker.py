"""coding_worker.py — orchestratore del coding-run (F-GoalCodingWorker, MVP).

Contratto puro (vedi anja-coding-worker-design.md §2): stesso comportamento da
qualunque ingresso (chat/goal/routine) e su qualunque backend.

5 fasi:
  1. CHECKPOINT   checkpoint.checkpoint(workspace_dir)            → rollback point
  2. SPAWN        engine headless nel workspace (coding_engines)
  3. VERIFY       deterministico (coding_verify)
  4. ESITO        diff_stat + verify + summary + journal
  (5. GATE        approve/apply/rollback → gestito dal layer d'ingresso, non qui:
                  il worker lascia i file nel workspace + checkpoint_before per il rollback)

L'engine è INIETTABILE (engine_runner) per testare le 5 fasi senza spawnare claude.
Stdlib + i moduli checkpoint / coding_engines / coding_verify.
"""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional

import checkpoint
import coding_engines
import coding_verify
import cost_store
import decision_trail
import pricing


@dataclass
class CodingRunResult:
    run_id: str
    workspace: str
    status: str                       # verified | verify-failed | engine-error | timeout
    checkpoint_before: Optional[str] = None
    diff_stat: str = ""
    diff_ref: Optional[str] = None
    verify: dict = field(default_factory=dict)
    engine: dict = field(default_factory=dict)
    summary: str = ""
    journal_path: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _gen_run_id() -> str:
    return f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"


def resolve_workspace_dir(hub_path: Path, workspace: str) -> Optional[Path]:
    """MVP: workspace interni `<hub>/workspaces/<name>`. (project/agent/esterni: dopo.)"""
    if not workspace or "/" in workspace or ".." in workspace:
        return None
    d = Path(hub_path) / "workspaces" / workspace
    return d if d.is_dir() else None


def _build_prompt(spec: dict) -> str:
    task = spec.get("task", {})
    acc = task.get("acceptance") or []
    acc_block = "\n".join(f"- {a}" for a in acc) if acc else "(nessuno esplicito)"
    return (
        f"Sei un coding agent autonomo nel workspace `{spec.get('workspace')}`.\n\n"
        f"# Task\n{task.get('title','')}\n\n{task.get('body','')}\n\n"
        f"# Criteri di accettazione\n{acc_block}\n\n"
        f"# Istruzioni\n"
        f"- Lavora SOLO dentro questo workspace (cwd corrente).\n"
        f"- Scrivi/modifica i file necessari, poi fermati. Non chiedere conferme.\n"
        f"- Se ci sono test, falli passare. Sii conciso nel report finale.\n"
    )


def _write_journal(workspace_dir: Path, result: CodingRunResult, prompt: str, engine_log: str) -> Optional[str]:
    """Scrive il journal del run nella wiki del workspace."""
    try:
        day = datetime.now().strftime("%Y-%m-%d")
        sess = workspace_dir / ".anjawiki" / "wiki" / "sessions" / day
        sess.mkdir(parents=True, exist_ok=True)
        f = sess / f"{result.run_id}.md"
        verify_line = ("skipped" if result.verify.get("skipped")
                       else ("✓ passed" if result.verify.get("passed") else "✗ failed"))
        body = (
            f"---\n"
            f"created: {day}\n"
            f"type: coding-run\n"
            f"run_id: {result.run_id}\n"
            f"workspace: {result.workspace}\n"
            f"status: {result.status}\n"
            f"engine: {result.engine.get('engine','')}\n"
            f"---\n\n"
            f"# Coding run {result.run_id}\n\n"
            f"- **status**: {result.status}\n"
            f"- **engine**: {result.engine.get('engine','')} "
            f"(turns={result.engine.get('turns',0)}, tokens={result.engine.get('tokens',0)}, "
            f"exit={result.engine.get('exit_code','?')})\n"
            f"- **verify**: {verify_line}\n"
            f"- **diff**: {result.diff_stat.strip() or '(nessuna modifica)'}\n"
            f"- **checkpoint_before**: {result.checkpoint_before}\n\n"
            f"## Task (prompt)\n\n{prompt}\n\n"
            f"## Verify\n\n" +
            "".join(f"- `{s['cmd']}` → exit {s['exit']}\n" for s in result.verify.get("steps", [])) +
            f"\n## Engine log (coda)\n\n```\n{engine_log[-3000:]}\n```\n"
        )
        f.write_text(body, encoding="utf-8")
        return str(f)
    except Exception:
        return None


EngineRunner = Callable[..., Awaitable["coding_engines.EngineResult"]]


async def run(hub_path: Path, spec: dict, *, engine_runner: Optional[EngineRunner] = None) -> CodingRunResult:
    """Esegue le fasi 1-4 del coding-run. Mai solleva: errori → status nel result."""
    hub_path = Path(hub_path)
    workspace = spec.get("workspace", "")
    run_id = spec.get("run_id") or _gen_run_id()
    res = CodingRunResult(run_id=run_id, workspace=workspace, status="engine-error")

    ws_dir = resolve_workspace_dir(hub_path, workspace)
    if not ws_dir:
        res.error = f"workspace non trovato: {workspace}"
        return res

    # Budget cap (M-CostObservability): se la spesa coding di oggi supera il cap, stop.
    bg = cost_store.check_budget(hub_path, "coding")
    if not bg["ok"]:
        res.status = "budget-exceeded"
        res.error = (f"budget giornaliero superato — coding ${bg['feature_spent']}/{bg['feature_cap']}, "
                     f"totale ${bg['total_spent']}/{bg['total_cap']}")
        try:
            import notification_bus
            notification_bus.publish(hub_path, source="coding", category="warning",
                                     title="Coding run bloccato: budget giornaliero superato", body=res.error)
        except Exception:
            pass
        return res

    # 1. CHECKPOINT
    try:
        res.checkpoint_before = checkpoint.checkpoint(ws_dir, f"pre coding-run {run_id}")
    except Exception as e:
        res.error = f"checkpoint fallito: {type(e).__name__}: {e}"
        return res

    # 1b. SNAPSHOT verify config — PRIMA di spawnare l'engine. L'agente lavora nel
    # workspace e potrebbe riscrivere .anja-verify.yaml per bypassare il gate o farci
    # eseguire cmd arbitrari: congeliamo le regole allo stato pre-run. (F-Sec-VerifyGateSnapshot)
    verify_cfg = coding_verify.load_verify_config(ws_dir)

    # 2. SPAWN engine
    prompt = _build_prompt(spec)
    runner = engine_runner or coding_engines.run_engine
    eng = await runner(
        spec.get("engine", "claude"), prompt, ws_dir,
        capabilities=spec.get("capabilities") or {},
        budget=spec.get("budget") or {},
        backend=spec.get("backend", "local"),
    )
    res.engine = {"engine": eng.engine, "turns": eng.turns, "tokens": eng.tokens,
                  "exit_code": eng.exit_code, "error": eng.error}
    if eng.input_tokens or eng.output_tokens:
        try:
            cost_store.record(hub_path, provider=pricing.provider_of(eng.model) or "anthropic",
                              model=eng.model or "claude", feature="coding", scope="coding",
                              input_tokens=eng.input_tokens, output_tokens=eng.output_tokens)
        except Exception:
            pass
    if not eng.ok:
        res.status = "timeout" if eng.exit_code == 124 else "engine-error"
        res.error = eng.error
        # diff comunque (l'engine può aver scritto prima di morire)
        try:
            res.diff_stat = checkpoint.diff_stat(ws_dir, res.checkpoint_before or "HEAD")
        except Exception:
            pass
        res.journal_path = _write_journal(ws_dir, res, prompt, eng.log)
        return res

    # 3. VERIFY (sulla config snapshottata pre-run, non riletta da disco)
    vr = await coding_verify.run_verify_config(verify_cfg, ws_dir)
    res.verify = {"passed": vr.passed, "skipped": vr.skipped, "steps": vr.steps, "error": vr.error}

    # 4. ESITO
    try:
        res.diff_stat = checkpoint.diff_stat(ws_dir, res.checkpoint_before or "HEAD")
    except Exception:
        res.diff_stat = ""
    # snapshot post-run come ref del diff (per la UI / apply)
    try:
        res.diff_ref = checkpoint.checkpoint(ws_dir, f"post coding-run {run_id}")
    except Exception:
        res.diff_ref = None
    res.status = "verified" if vr.passed else "verify-failed"
    res.summary = (f"Engine {eng.engine}: {eng.turns} turni. "
                   f"Verify {'skipped' if vr.skipped else ('OK' if vr.passed else 'FAILED')}. "
                   f"{res.diff_stat.strip().splitlines()[-1] if res.diff_stat.strip() else 'nessuna modifica'}")
    try:
        decision_trail.record(hub_path, actor="coding",
                              trigger=f"coding-run {run_id} su workspace {workspace}",
                              decision=res.status, rationale=res.summary,
                              alternative="rollback al checkpoint pre-run" if not vr.passed else "",
                              confidence=1.0 if vr.passed else 0.0, scope="coding", ref=run_id)
    except Exception:
        pass
    res.journal_path = _write_journal(ws_dir, res, prompt, eng.log)
    return res


def reject(hub_path: Path, workspace: str, checkpoint_before: str) -> dict:
    """Rollback di un run rifiutato: ripristina ESATTAMENTE lo stato pre-run (fase 5 —
    reject), rimuovendo anche i file nuovi creati dal run. Usa restore_hard."""
    ws_dir = resolve_workspace_dir(Path(hub_path), workspace)
    if not ws_dir:
        return {"ok": False, "error": f"workspace non trovato: {workspace}"}
    return checkpoint.restore_hard(ws_dir, checkpoint_before)


# --- Fase 5: gate (record di run persistito + verdict) -----------------------
# Il record JSON `<hub>/coding_runs/<run_id>.json` è la SOURCE OF TRUTH dello stato.
# resolve() è il punto unico del verdict, usato da entrambi i transport del gate
# (REST /api/coding/runs/{id}/{approve,reject} e bottoni Telegram cact:).

def run_record_path(hub_path: Path, run_id: str) -> Path:
    return Path(hub_path) / "coding_runs" / f"{run_id}.json"


def load_run(hub_path: Path, run_id: str) -> Optional[dict]:
    f = run_record_path(hub_path, run_id)
    if not f.is_file():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_run(hub_path: Path, data: dict) -> None:
    f = run_record_path(hub_path, data["run_id"])
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def resolve(hub_path: Path, run_id: str, verdict: str) -> dict:
    """Applica il verdict del gate a un run. verdict: approve | reject.
    approve = consolida (i file dell'engine sono già nel workspace); reject = rollback
    al checkpoint_before. Idempotente: un run già risolto non si ri-risolve."""
    if "/" in run_id or ".." in run_id:
        return {"ok": False, "error": "invalid run_id"}
    if verdict not in ("approve", "reject"):
        return {"ok": False, "error": f"verdict non valido: {verdict}"}
    data = load_run(hub_path, run_id)
    if not data:
        return {"ok": False, "error": f"run non trovato: {run_id}"}
    if data.get("status") in ("approved", "rejected"):
        return {"ok": False, "error": f"run già {data['status']}", "status": data["status"]}

    if verdict == "approve":
        data["status"] = "approved"
        save_run(hub_path, data)
        return {"ok": True, "status": "approved", "run_id": run_id}

    cb = data.get("checkpoint_before")
    if not cb:
        return {"ok": False, "error": "nessun checkpoint_before per il rollback"}
    r = reject(hub_path, data.get("workspace", ""), cb)
    if r.get("ok"):
        data["status"] = "rejected"
        save_run(hub_path, data)
        return {"ok": True, "status": "rejected", "run_id": run_id}
    return r
