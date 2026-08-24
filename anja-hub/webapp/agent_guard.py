"""agent_guard.py — F-DelegateHardening (b)/(c): il perimetro enumerato.

Sostituisce il bypass wholesale nella delega headless con decisioni
DETERMINISTICHE: una allowlist di comandi Bash per ruolo + deny sui segreti +
confinamento dei path allo scope. Funziona anche senza umano nel loop, perché
non chiede: decide.

Tre livelli d'uso:
  * `precheck_secrets(tool, input)` — deny-reason se il tool tocca materiale
    segreto (glob sui path, substring nei comandi Bash). Usato ANCHE nelle
    sessioni interattive ASP come pre-filtro (defense-in-depth).
  * `bash_denied(cmd, allowlist)` / `path_denied(path, roots)` — primitive.
  * `make_delegate_guard(...)` — callback `can_use_tool` completo per la
    delega headless: grant-set dei nativi, path confinati ai roots, Bash in
    allowlist. `bash_allowlist: []` = guard attivo ma Bash negato del tutto.

Limiti DICHIARATI del parsing Bash (v1): i comandi sono spezzati sui
concatenatori (&& || ; | \n) e OGNI segmento deve matchare l'allowlist;
substitution (`$(`, backtick) è negata sempre; le redirection non sono
analizzate — l'allowlist va tenuta stretta (è il punto).

Stdlib only.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Optional

# Stessi pattern dei --deny di grok_cli + mail/browser. fnmatch: '*' attraversa '/'.
SECRET_DENY_GLOBS = (
    "*.env", "*/.env*", "*backup.key",
    "*/config/connectors/*", "*/config/openai_oauth.json",
    "*token*.json", "*credentials*.json", "*oauth-client*.json",
    "*/.anjawiki/mail/*", "*/.browser/*", "*/.secrets.env*",
)
# Substring per il best-effort sui comandi Bash (i path nei comandi non si
# risolvono in modo affidabile: si nega la menzione stessa).
SECRET_SUBSTRINGS = (
    ".env", "backup.key", ".anjawiki/mail", ".browser/", ".secrets",
    "oauth-client", "credentials.json", "token.json",
)

# Tool nativi il cui input è un path da confinare.
PATH_TOOLS = {"Read": "file_path", "Write": "file_path", "Edit": "file_path",
              "MultiEdit": "file_path", "NotebookEdit": "notebook_path",
              "Grep": "path", "Glob": "path", "LS": "path"}

# Segmentazione comandi: concatenatori shell. La substitution è negata a parte.
_SPLIT_RE = re.compile(r"&&|\|\||;|\||\n")
_SUBSTITUTION_RE = re.compile(r"\$\(|`")

TMP_ROOTS = (Path("/tmp"), Path("/private/tmp"), Path("/var/folders"))


def _norm(p: str) -> str:
    return str(Path(p)).replace("\\", "/")


def secret_path(path_str: str) -> bool:
    n = _norm(path_str)
    return any(fnmatch.fnmatch(n, g) for g in SECRET_DENY_GLOBS)


def path_denied(path_str: str, roots: list[Path] | None) -> Optional[str]:
    """Motivo del deny per un path, o None. roots=None → solo il check segreti."""
    if not path_str:
        return None
    if secret_path(path_str):
        return f"path negato dalla policy segreti: {path_str}"
    if roots is None:
        return None
    base = Path(roots[0]) if roots else Path.cwd()
    p = Path(path_str)
    resolved = (p if p.is_absolute() else base / p).resolve()
    for r in list(roots) + list(TMP_ROOTS):
        try:
            resolved.relative_to(Path(r).resolve())
            return None
        except ValueError:
            continue
    return (f"path fuori dallo scope dell'agente: {path_str} "
            f"(consentiti: {', '.join(str(r) for r in roots)})")


def bash_denied(command: str, allowlist: Optional[list[str]]) -> Optional[str]:
    """Motivo del deny per un comando Bash, o None.
    allowlist None → nessun vincolo di forma (restano i deny segreti);
    allowlist []  → Bash interamente negato;
    altrimenti OGNI segmento deve matchare un pattern (fnmatch)."""
    cmd = (command or "").strip()
    low = cmd.lower()
    for s in SECRET_SUBSTRINGS:
        if s in low:
            return f"comando negato: riferimento a materiale segreto ('{s}')"
    if allowlist is None:
        return None
    if _SUBSTITUTION_RE.search(cmd):
        return "comando negato: command substitution ($(…)/backtick) non consentita in allowlist mode"
    if not allowlist:
        return "Bash non consentito per questo agente (bash_allowlist vuota)"
    for seg in _SPLIT_RE.split(cmd):
        seg = seg.strip()
        if not seg:
            continue
        if not any(fnmatch.fnmatch(seg, pat) or seg == pat for pat in allowlist):
            return (f"comando fuori allowlist: '{seg[:120]}' — pattern consentiti: "
                    f"{allowlist}")
    return None


def precheck_secrets(tool_name: str, input_data: dict) -> Optional[str]:
    """Pre-filtro per le sessioni interattive: nega SOLO il materiale segreto
    (niente confinamento roots: lì decide l'umano via ASP)."""
    if not isinstance(input_data, dict):
        return None
    if tool_name == "Bash":
        cmd = str(input_data.get("command", "")).lower()
        for s in SECRET_SUBSTRINGS:
            if s in cmd:
                return f"comando negato: riferimento a materiale segreto ('{s}')"
        return None
    key = PATH_TOOLS.get(tool_name)
    if key:
        p = str(input_data.get(key) or "")
        if p and secret_path(p):
            return f"path negato dalla policy segreti: {p}"
    return None


def delegate_permission_plan(cfg: dict, native_tools: list[str],
                             mcp_patterns: list[str]) -> dict:
    """Decide il regime della delega dalla config dell'agente. Ritorna
    {mode, allowed_tools, guarded(bool), bash_allowlist, granted}.

    - `bash_allowlist` presente (anche []) → GUARD MODE: permission_mode
      'default', SOLO gli MCP pre-approvati, ogni nativo passa dal callback
      (che decide da solo: niente umano richiesto). bypass_permissions ignorato.
    - assente → comportamento storico: bypass se dichiarato, altrimenti
      default con i nativi in allowed_tools.
    """
    allowlist = cfg.get("bash_allowlist")
    if isinstance(allowlist, list):
        return {"mode": "default",
                "allowed_tools": list(mcp_patterns) + ["TodoWrite"],
                "guarded": True,
                "bash_allowlist": [str(p) for p in allowlist],
                "granted": set(native_tools)}
    bypass = bool(cfg.get("bypass_permissions", False))
    return {"mode": "bypassPermissions" if bypass else "default",
            "allowed_tools": list(native_tools) + list(mcp_patterns),
            "guarded": False, "bash_allowlist": None,
            "granted": set(native_tools)}


def make_delegate_guard(roots: list[Path], granted: set[str],
                        bash_allowlist: Optional[list[str]],
                        agent: str = "", log=None):
    """Callback `can_use_tool` per la delega in guard mode. Import SDK lazy."""
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

    def _log(decision: str, tool: str, detail: str) -> None:
        if log:
            try:
                log(f"[guard:{agent}] {decision} {tool}: {detail[:160]}")
            except Exception:
                pass

    async def can_use_tool(tool_name: str, input_data, context):
        data = input_data if isinstance(input_data, dict) else {}
        if tool_name.startswith("mcp__"):          # gli MCP sono già allowlistati
            return PermissionResultAllow(updated_input=input_data)
        if tool_name not in granted:
            _log("deny", tool_name, "tool non concesso in delega")
            return PermissionResultDeny(
                message=f"tool '{tool_name}' non concesso a questo agente in delega",
                interrupt=False)
        if tool_name == "Bash":
            reason = bash_denied(str(data.get("command", "")), bash_allowlist)
            if reason:
                _log("deny", tool_name, reason)
                return PermissionResultDeny(message=reason, interrupt=False)
            _log("allow", tool_name, str(data.get("command", ""))[:120])
            return PermissionResultAllow(updated_input=input_data)
        key = PATH_TOOLS.get(tool_name)
        if key is not None:
            reason = path_denied(str(data.get(key) or ""), roots)
            if reason:
                _log("deny", tool_name, reason)
                return PermissionResultDeny(message=reason, interrupt=False)
        return PermissionResultAllow(updated_input=input_data)

    return can_use_tool
