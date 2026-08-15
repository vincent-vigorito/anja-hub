"""coding_engines.py — astrazione engine headless per il coding worker.

Spawn di un harness di coding in modalità headless nel cwd del workspace.
MVP: engine=claude (`claude -p`), backend=local. codex/grok + backend=ssh: dopo.
L'engine eredita la memoria anja del workspace (AGENTS.md + wiki + MCP) — è il
cross-harness già fatto. Vedi anja-coding-worker-design.md §3.

Stdlib + asyncio.subprocess.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# Env passato al subprocess: SOLO queste var (least-privilege) — niente os.environ.copy()
# che esfiltrerebbe le altre chiavi API dell'hub. Le creds claude vivono in ~/.claude
# (serve HOME) o in ANTHROPIC_*/CLAUDE_* (aggiunte sotto se presenti).
ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TERM",
                 "USER", "SHELL", "TMPDIR", "TZ")

# Bash consentiti in V1 local (no sandbox): l'agente headless non riceve Bash arbitrario
# ma solo questi comandi di sviluppo. Estendibile per-run via capabilities.bash_allow.
DEFAULT_BASH_ALLOW = ("python", "python3", "pytest", "ruff", "mypy", "pip", "pip3",
                      "ls", "cat", "head", "tail", "grep", "find", "mkdir", "touch",
                      "mv", "cp", "echo", "git", "node", "npm", "pnpm", "make", "sed")


@dataclass
class EngineResult:
    engine: str
    exit_code: int
    turns: int = 0
    tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    log: str = ""
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and self.error is None


def _build_claude_cmd(prompt: str, budget: dict, capabilities: dict) -> list:
    """Headless `claude -p`. NO `--dangerously-skip-permissions` in V1 local: si usa
    `--permission-mode acceptEdits` + `--allowedTools` (Bash ristretto a comandi dev).
    Lo skip è ammesso SOLO con capabilities.sandbox=true (V2 Incus/Docker: l'isolamento
    è il container)."""
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]
    mt = budget.get("max_turns")
    if mt:
        cmd += ["--max-turns", str(int(mt))]
    if capabilities.get("sandbox"):
        cmd.append("--dangerously-skip-permissions")
    else:
        cmd += ["--permission-mode", "acceptEdits"]
        tools = list(capabilities.get("tools") or ["Read", "Edit", "Write"])
        allowed = [t for t in tools if t != "Bash"]
        if "Bash" in tools:
            bash_allow = capabilities.get("bash_allow") or DEFAULT_BASH_ALLOW
            allowed += [f"Bash({c}:*)" for c in bash_allow]
        if allowed:
            cmd += ["--allowedTools", *allowed]  # variadic → per ultimo
    return cmd


def _parse_claude_stream(stdout: str) -> tuple:
    """Estrae (turns, input_tokens, output_tokens, model) dallo stream-json. Best-effort."""
    turns, in_tok, out_tok, model = 0, 0, 0, ""
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("model"):
            model = ev.get("model")
        if ev.get("type") == "result":
            turns = ev.get("num_turns", turns) or turns
            u = ev.get("usage") or {}
            in_tok = (u.get("input_tokens", 0) or 0) + (u.get("cache_creation_input_tokens", 0) or 0) + (u.get("cache_read_input_tokens", 0) or 0)
            out_tok = u.get("output_tokens", 0) or 0
    return turns, in_tok, out_tok, model


def _load_secrets(start: Path) -> dict:
    """Carica KEY=VALUE da .secrets.env: prima nel workspace, poi risalendo (hub)."""
    out: dict = {}
    for d in [start, *start.parents]:
        f = d / ".secrets.env"
        if f.is_file():
            for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
                ln = ln.strip()
                if not ln or ln.startswith("#") or "=" not in ln:
                    continue
                k, v = ln.split("=", 1)
                out.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        # fermati al primo hub root (ha config/projects.json) per non risalire troppo
        if (d / "config" / "projects.json").is_file():
            break
    return out


def _build_env(cwd: Path, capabilities: dict) -> dict:
    """Env del subprocess — least-privilege: parte VUOTO, allowlist esplicita (ENV_ALLOWLIST
    + creds claude ANTHROPIC_*/CLAUDE_*), poi i secrets dichiarati nel manifest. NON eredita
    os.environ → le altre chiavi API dell'hub (OpenAI/xAI/...) non finiscono all'agente."""
    env = {k: os.environ[k] for k in ENV_ALLOWLIST if k in os.environ}
    for k, v in os.environ.items():
        if k.startswith("ANTHROPIC_") or k.startswith("CLAUDE_"):
            env[k] = v
    wanted = list(capabilities.get("secrets") or [])
    if wanted:
        loaded = _load_secrets(cwd)
        for k in wanted:
            if k in loaded:
                env[k] = loaded[k]
    return env


async def run_engine(engine: str, prompt: str, cwd: Path, *,
                     capabilities: Optional[dict] = None,
                     budget: Optional[dict] = None,
                     backend: str = "local") -> EngineResult:
    """Spawn headless dell'engine nel cwd. Ritorna EngineResult (mai solleva)."""
    capabilities = capabilities or {}
    budget = budget or {}
    cwd = Path(cwd)

    if backend != "local":
        return EngineResult(engine, 1, error=f"backend '{backend}' non supportato nell'MVP (V2: ssh+Incus)")
    if engine != "claude":
        return EngineResult(engine, 1, error=f"engine '{engine}' non supportato nell'MVP (solo claude)")
    if not shutil.which("claude"):
        return EngineResult(engine, 1, error="claude CLI non trovato nel PATH")
    if not cwd.is_dir():
        return EngineResult(engine, 1, error=f"workspace dir non trovata: {cwd}")

    cmd = _build_claude_cmd(prompt, budget, capabilities)
    env = _build_env(cwd, capabilities)
    timeout = int(budget.get("timeout_sec", 1800))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(cwd), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return EngineResult(engine, 124, error=f"timeout dopo {timeout}s")
    except Exception as e:
        return EngineResult(engine, 1, error=f"spawn fallito: {type(e).__name__}: {e}")

    stdout = out.decode("utf-8", "replace")
    stderr = err.decode("utf-8", "replace")
    turns, in_tok, out_tok, model = _parse_claude_stream(stdout)
    log = stdout + ("\n[stderr]\n" + stderr if stderr.strip() else "")
    rc = proc.returncode or 0
    return EngineResult(engine, rc, turns=turns, tokens=in_tok + out_tok,
                        input_tokens=in_tok, output_tokens=out_tok, model=model, log=log,
                        error=None if rc == 0 else f"exit {rc}")
