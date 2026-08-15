"""coding_verify.py — verify gate DETERMINISTICO del coding worker.

Legge `<workspace>/.anja-verify.yaml` ed ESEGUE gli step (subprocess). Niente LLM:
è la differenza col risk-assessment di Hermes/OpenClaw — prova, non opinione.
Config assente → verify "soft" (skipped=True): nessun gate, si va sempre ad approve.

Formato (vedi anja-coding-worker-design.md §3):
    steps:
      - name: lint
        cmd: ruff check .
      - name: tests
        cmd: pytest -q
    fail_fast: true

Stdlib + asyncio.subprocess. PyYAML usato se presente, altrimenti mini-parser
per questo formato fisso (zero-dep).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

VERIFY_FILENAME = ".anja-verify.yaml"
DEFAULT_STEP_TIMEOUT = 600


@dataclass
class VerifyResult:
    passed: bool
    skipped: bool = False
    steps: list = field(default_factory=list)  # [{name, cmd, exit, output}]
    error: Optional[str] = None


def _mini_parse(text: str) -> dict:
    """Parser minimale per il formato verify (steps: lista di name/cmd + fail_fast).
    Sufficiente quando PyYAML non è installato."""
    cfg: dict = {"steps": [], "fail_fast": True}
    cur: Optional[dict] = None
    in_steps = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("steps:"):
            in_steps = True
            continue
        if stripped.startswith("fail_fast:"):
            cfg["fail_fast"] = stripped.split(":", 1)[1].strip().lower() in ("true", "1", "yes")
            in_steps = False
            continue
        if in_steps and stripped.startswith("- "):
            cur = {}
            cfg["steps"].append(cur)
            stripped = stripped[2:].strip()
            if not stripped:
                continue
        if cur is not None and ":" in stripped:
            k, v = stripped.split(":", 1)
            cur[k.strip().lstrip("- ").strip()] = v.strip().strip('"').strip("'")
    return cfg


def load_verify_config(workspace_dir: Path) -> Optional[dict]:
    f = Path(workspace_dir) / VERIFY_FILENAME
    if not f.is_file():
        return None
    text = f.read_text(encoding="utf-8", errors="replace")
    try:
        import yaml  # type: ignore
        cfg = yaml.safe_load(text) or {}
    except Exception:
        cfg = _mini_parse(text)
    steps = [s for s in (cfg.get("steps") or []) if s.get("cmd")]
    return {"steps": steps, "fail_fast": bool(cfg.get("fail_fast", True))}


_SECRET_ENV_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTH")
_SECRET_ENV_PREFIXES = ("ANTHROPIC_", "CLAUDE_", "OPENAI_", "OPENROUTER_", "XAI_", "GROQ_",
                        "GEMINI_", "GOOGLE_", "META_", "FB_", "HIGGSFIELD_", "SERPAPI",
                        "AWS_", "AZURE_", "GH_", "GITHUB_")


def _scrubbed_env() -> dict:
    """Env per gli step verify: os.environ MENO le variabili che paiono segreti.
    La shell dei verify è NON ristretta (a differenza dell'agente con allowedTools) →
    non deve poter leggere le chiavi/API dell'hub. PATH/HOME/LANG restano."""
    import os
    out = {}
    for k, v in os.environ.items():
        ku = k.upper()
        if any(h in ku for h in _SECRET_ENV_HINTS) or any(ku.startswith(p) for p in _SECRET_ENV_PREFIXES):
            continue
        out[k] = v
    return out


async def _run_step(cmd: str, cwd: Path, timeout: int) -> dict:
    # shell=True: i cmd vengono dal .anja-verify.yaml SNAPSHOTTATO pre-run dal coding
    # worker (non riletto da disco) → l'agente non può iniettarli durante il run.
    # Env scrubbato: gli step verify non ereditano i segreti dell'hub (blast-radius).
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd, cwd=str(cwd), env=_scrubbed_env(),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return {"exit": 124, "output": f"timeout dopo {timeout}s"}
        return {"exit": proc.returncode or 0,
                "output": out.decode("utf-8", "replace")[-4000:]}
    except Exception as e:
        return {"exit": 1, "output": f"{type(e).__name__}: {e}"}


async def run_verify_config(cfg: Optional[dict], workspace_dir: Path) -> VerifyResult:
    """Esegue una verify config GIÀ caricata (snapshot). cfg=None → skipped (soft).

    Separato da `load_verify_config` apposta: il coding worker snapshotta la config
    PRIMA di spawnare l'engine (fase 1) e passa qui lo snapshot, così l'agente che
    lavora nel workspace non può riscrivere `.anja-verify.yaml` per bypassare/iniettare
    il gate (F-Sec-VerifyGateSnapshot)."""
    if cfg is None:
        return VerifyResult(passed=True, skipped=True)
    if not cfg["steps"]:
        return VerifyResult(passed=True, skipped=True,
                            error="verify config presente ma senza step validi")

    results = []
    all_ok = True
    for step in cfg["steps"]:
        r = await _run_step(step["cmd"], Path(workspace_dir),
                            int(step.get("timeout", DEFAULT_STEP_TIMEOUT)))
        entry = {"name": step.get("name", step["cmd"][:30]), "cmd": step["cmd"],
                 "exit": r["exit"], "output": r["output"]}
        results.append(entry)
        if r["exit"] != 0:
            all_ok = False
            if cfg["fail_fast"]:
                break
    return VerifyResult(passed=all_ok, skipped=False, steps=results)


async def run_verify(workspace_dir: Path) -> VerifyResult:
    """Carica la config dal workspace ED esegue (uso standalone). NB: il coding worker
    NON usa questa — snapshotta la config pre-run e chiama run_verify_config, per non
    far manomettere il gate dall'agente durante il run."""
    return await run_verify_config(load_verify_config(workspace_dir), workspace_dir)
