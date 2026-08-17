#!/usr/bin/env python3
"""
anja Mission Control — FastAPI server.

Serve la webapp + endpoint REST che proxa agli script Python di anja/anja-hub.
Read-only in M1. Action endpoint (POST sync/lint) in M2. Chat WebSocket in M3.

Usage:
    python3 server.py --hub <hub-path> [--port 8765]

Esempio:
    python3 server.py --hub ~/Documents/TEST-HUB --port 8765
"""

import argparse
import asyncio
import contextlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Optional
from urllib.parse import urlsplit
import urllib.request
import urllib.error

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, Body, UploadFile, File, Form
from fastapi.responses import FileResponse, PlainTextResponse, JSONResponse, StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

import notification_bus as notif_bus
import chat_stream_registry as chat_streams
import cost_store
import self_health
import decision_trail

# claude-agent-sdk wrapper (lazy import: caricato solo quando serve la chat)
import importlib.util
_chat_module = None

def _get_chat_module():
    global _chat_module
    if _chat_module is None:
        try:
            spec = importlib.util.spec_from_file_location(
                "claude_chat",
                str(WEBAPP_DIR / "claude_chat.py"),
            )
            _chat_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(_chat_module)
        except Exception as e:
            print(f"[anja] WARNING: claude_chat module not available: {e}")
            _chat_module = False
    return _chat_module if _chat_module else None


_routines_modules = None

def _get_routines_modules():
    """Lazy load routine_validate + routine_registry from anja-routines plugin."""
    global _routines_modules
    if _routines_modules is None:
        try:
            scripts_dir = ANJA_ROUTINES_DIR / "scripts"
            if str(scripts_dir) not in sys.path:
                sys.path.insert(0, str(scripts_dir))
            import routine_validate as rv  # type: ignore
            import routine_registry as rr  # type: ignore
            _routines_modules = (rv, rr)
        except Exception as e:
            print(f"[anja] WARNING: routines modules not available: {e}")
            _routines_modules = False
    return _routines_modules if _routines_modules else None

# ============================================================
# config / paths
# ============================================================

WEBAPP_DIR = Path(__file__).parent.resolve()
STATIC_DIR = WEBAPP_DIR / "static"

# Root dei tre componenti AnjaHub, derivati da WEBAPP_DIR (no path hardcoded). NB: gli script di
# runtime (context_loader, tools_md, lint_checks, compose_claude_md) NON vivono più qui ma nel
# plugin anjadev → vedi ANJADEV_DIR.
ANJA_HUB_DIR = WEBAPP_DIR.parent
ANJA_DIR = ANJA_HUB_DIR.parent / "anja"
ANJA_ROUTINES_DIR = ANJA_HUB_DIR.parent / "anja-routines"

# Will be set at startup
HUB_PATH: Optional[Path] = None


# ============================================================
# helpers
# ============================================================

WIKI_SUBDIRS = ("entities", "concepts", "sources", "analysis", "sessions")


def _hub_config() -> dict:
    """Legge `<hub>/config.json` (root). Ritorna dict vuoto se assente/malformato."""
    if not HUB_PATH:
        return {}
    p = HUB_PATH / "config.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _needs_onboarding() -> bool:
    """Primo avvio = nessun utente configurato (no default_user e nessun users/*.md)."""
    if not HUB_PATH:
        return False
    if _hub_config().get("default_user"):
        return False
    users_dir = HUB_PATH / "users"
    if users_dir.is_dir() and any(users_dir.glob("*.md")):
        return False
    return True


def run_script(script_path: Path, args: list) -> dict:
    """Run a Python helper script, return parsed JSON or {error: ...}."""
    try:
        result = subprocess.run(
            ["python3", str(script_path)] + args,
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip() or "script failed", "exit": result.returncode}
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"error": "non-JSON output", "stdout": result.stdout[:500]}
    except subprocess.TimeoutExpired:
        return {"error": "script timeout"}
    except FileNotFoundError:
        return {"error": f"script not found: {script_path}"}


def read_md_file(path: Path) -> Optional[str]:
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return None


def find_page_in_project(hub: Path, project_name: str, page_slug: str) -> Optional[Path]:
    """Find page in project wiki: root or subdirs."""
    base = hub / "projects" / project_name / "wiki"
    if not base.is_dir():
        return None
    # root level (index, log, overview)
    candidate = base / f"{page_slug}.md"
    if candidate.is_file():
        return candidate
    # subdirs
    for sub in WIKI_SUBDIRS:
        candidate = base / sub / f"{page_slug}.md"
        if candidate.is_file():
            return candidate
    return None


def parse_log_entries(log_path: Path, limit: int = 5) -> list:
    if not log_path.is_file():
        return []
    text = log_path.read_text(encoding="utf-8")
    pattern = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})\] (\w[\w-]*) \| (.+?)$", re.M)
    entries = pattern.findall(text)
    return [{"date": d, "type": t, "desc": desc} for d, t, desc in entries[-limit:]][::-1]


def parse_cross_analyses(hub: Path) -> list:
    """Read cross/analysis/*.md, parse frontmatter (title, projects, tags)."""
    analysis_dir = hub / "cross" / "analysis"
    if not analysis_dir.is_dir():
        return []
    out = []
    fm_re = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
    for f in sorted(analysis_dir.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        m = fm_re.match(text)
        info = {"slug": f.stem, "title": f.stem, "date": "", "projects": [], "summary": "", "tags": []}
        if m:
            block = m.group(1)
            tm = re.search(r'^title:\s*"?([^"\n]+?)"?\s*$', block, re.M)
            if tm:
                info["title"] = tm.group(1)
            cm = re.search(r"^created:\s*(\d{4}-\d{2}-\d{2})", block, re.M)
            if cm:
                info["date"] = cm.group(1)
            pm = re.search(r"^projects:\s*\[([^\]]*)\]", block, re.M)
            if pm:
                info["projects"] = [t.strip().strip("\"'") for t in pm.group(1).split(",") if t.strip()]
        # summary: first non-frontmatter, non-heading paragraph
        body = fm_re.sub("", text, count=1).strip()
        for line in body.split("\n"):
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith(">"):
                info["summary"] = line[:140]
                break
        out.append(info)
    return out


def get_hub_last_sync(hub: Path) -> str:
    """Read last_sync from registry, return short formatted."""
    reg_path = hub / "config" / "projects.json"
    if not reg_path.is_file():
        return ""
    try:
        with reg_path.open() as f:
            reg = json.load(f)
        last = max((p.get("last_sync") or "" for p in reg.get("projects", [])), default="")
        return last[:16].replace("T", " ") if last else ""
    except Exception:
        return ""


def list_project_total_pages(hub: Path, project_name: str) -> int:
    """Count .md files in projects/<name>/wiki/."""
    wiki_dir = hub / "projects" / project_name / "wiki"
    if not wiki_dir.is_dir():
        return 0
    count = 0
    for f in wiki_dir.rglob("*.md"):
        count += 1
    return count


# ============================================================
# FastAPI app
# ============================================================

app = FastAPI(title="anja Mission Control", version="0.1.0")


# ============================================================
# CSRF / same-origin guard (F-Sec-WebappAuth-CSRF)
# ============================================================
# La webapp è single-user su localhost ma senza auth: una pagina malevola aperta
# nel browser dell'utente può fare POST/DELETE cross-origin verso :8765 (delete
# agent/workspace=rmtree, sovrascrittura API key, run routine). Il browser blocca
# la LETTURA della risposta cross-origin (Same-Origin Policy) ma il server
# processa comunque la richiesta. Difesa: sui metodi mutanti verso /api/*,
# rifiuta se Origin/Referer è cross-site. I browser inviano SEMPRE l'Origin sulle
# richieste non-GET e non possono sopprimerlo da JS → il CSRF drive-by è bloccato.
# I client non-browser (anja-cli, hub_api, WebFetch) non mandano Origin → passano
# (non sono un vettore CSRF). /hooks/* ha il suo bearer; i GET sono read-only e la
# SOP protegge già la lettura cross-origin della risposta.

_CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_CSRF_EXTRA_ORIGINS = {
    o.strip() for o in os.environ.get("ANJA_ALLOWED_ORIGINS", "").split(",") if o.strip()
}


def _csrf_origin_ok(request: Request) -> bool:
    """False solo per un CSRF cross-origin. Confronta l'host di Origin/Referer con
    l'Host del server (auto-adattivo a localhost/IP/tunnel); ANJA_ALLOWED_ORIGINS
    aggiunge netloc extra per setup con reverse proxy."""
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source:
        return True                         # niente Origin/Referer = client non-browser
    netloc = urlsplit(source).netloc
    if not netloc:
        return False
    return netloc == request.headers.get("host", "") or netloc in _CSRF_EXTRA_ORIGINS


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    if (request.method not in _CSRF_SAFE_METHODS
            and request.url.path.startswith("/api/")
            and not _csrf_origin_ok(request)):
        return JSONResponse(status_code=403,
                            content={"detail": "cross-origin request rejected (CSRF guard)"})
    response = await call_next(request)
    # /static: revalidate sempre (no-cache) → niente app.js/style.css stantii dopo un edit
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    # Security headers (defense-in-depth). CSP permette Alpine (unsafe-inline/eval) +
    # CDN script/style, ma connect-src 'self' impedisce a un eventuale XSS di esfiltrare
    # via fetch/XHR/ws verso host esterni; object-src/frame-ancestors chiudono
    # plugin e clickjacking.
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Content-Security-Policy", _CSP)
    return response


_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://unpkg.com; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "img-src 'self' data: https:; "
    "media-src 'self' https:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'"
)


# Auth gate (F4 Concierge). In personal mode è un NO-OP (zero attrito). In concierge
# risolve l'utente dalla sessione (cookie firmato) e fa fail-closed sulle /api/*
# (eccetto /api/auth/*): niente sessione valida → 401, il frontend mostra il login.
_AUTH_OPEN_PREFIXES = ("/api/auth/",)


def _is_registered_ws(name: str) -> bool:
    """Vero se `name` è un workspace/progetto registrato. Path-safe. Riconosce sia
    gli interni (meta.yaml) sia gli esterni (presenti nel registry projects.json ma
    senza meta.yaml) → niente fail-open sui ws esterni."""
    if not name or "/" in name or ".." in name or not HUB_PATH:
        return False
    if (HUB_PATH / "workspaces" / f"{name}.meta.yaml").is_file():
        return True
    try:
        with (HUB_PATH / "config" / "projects.json").open(encoding="utf-8") as f:
            return any(p.get("name") == name for p in json.load(f).get("projects", []))
    except Exception:
        return False


def _scoped_ws_from_request(request: Request) -> Optional[str]:
    """Estrae il workspace bersaglio da path/query (NO body — niente consumo).
    Copre i pattern a semantica chiara: /api/project/{ws}/…, /api/goals/{workspace|
    project}/{ws}/…, e query ?project=<ws>. Ritorna None per le richieste hub-level
    o non risolvibili senza body (quei POST sono gated esplicitamente)."""
    parts = request.url.path.split("/")
    if len(parts) >= 4 and parts[1] == "api" and parts[2] == "project" and _is_registered_ws(parts[3]):
        return parts[3]
    if len(parts) >= 5 and parts[1] == "api" and parts[2] == "goals" and parts[3] in ("workspace", "project"):
        return parts[4] or None
    p = request.query_params.get("project")
    return p or None


def _ws_access_denied(request: Request, slug: str) -> bool:
    """F4b slice 3b: True se l'utente loggato NON può accedere al workspace bersaglio
    della richiesta. owner/admin → mai negato; member → solo se non membro del ws."""
    import auth_io, membership_io
    u = auth_io.get_user(HUB_PATH, slug)
    role = u["role"] if u else None
    if role in ("owner", "admin"):
        return False
    ws = _scoped_ws_from_request(request)
    if not ws:
        return False
    return not membership_io.can_access(HUB_PATH, ws, slug, role)


def _ws_from_scope(scope: Optional[str]) -> Optional[str]:
    """Workspace-name da un valore scope (`workspace:X` | `project:X` | bare `X`).
    None per hub/user-global/vuoto o per uno scope che non è un ws registrato.
    Usato dagli endpoint scope-based (kanban/goals/media): la semantica di `scope`
    varia, quindi normalizziamo qui anziché nel middleware."""
    if not scope:
        return None
    s = scope.strip()
    if s in ("hub", "user-global"):
        return None
    name = s.split(":", 1)[1] if ":" in s else s
    return name if _is_registered_ws(name) else None


def _require_scope_access(request: Request, scope: Optional[str]) -> None:
    """Gata l'accesso se `scope` risolve a un workspace (F4b slice 3c)."""
    ws = _ws_from_scope(scope)
    if ws:
        _require_ws_access(request, ws)


def _require_target_access(request: Request, scope: Optional[str], target: Optional[str]) -> None:
    """sources/: `scope`='project'|'workspace'|'hub', il workspace è in `target`.
    Fail-closed: per qualunque scope diverso da hub con un target, gata l'accesso —
    se non è un ws con membership, can_access nega comunque il member (owner/admin ok)."""
    if scope and scope not in ("hub", "user-global") and target:
        _require_ws_access(request, target)


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    request.state.user = None
    if HUB_PATH:
        import auth_io
        # read_session/get_mode sono già robusti (None/"personal" su errore).
        slug = auth_io.read_session(HUB_PATH, request.cookies.get(auth_io.SESSION_COOKIE, ""))
        if slug:
            request.state.user = slug
        if auth_io.get_mode(HUB_PATH) == "concierge":
            path = request.url.path
            if path.startswith("/api/") and not path.startswith(_AUTH_OPEN_PREFIXES):
                if not slug:
                    return JSONResponse(status_code=401, content={"detail": "authentication required"})
                # F4b slice 3b: enforcement membership per-workspace (path/query).
                # FAIL-CLOSED: un errore nel calcolo membership nega, non lascia passare.
                try:
                    denied = _ws_access_denied(request, slug)
                except Exception as e:
                    print(f"[auth_gate] membership error: {e}")
                    denied = True
                if denied:
                    return JSONResponse(status_code=403, content={"detail": "workspace access denied"})
    return await call_next(request)


@app.get("/")
async def index():
    if _needs_onboarding():
        return RedirectResponse("/onboarding", status_code=307)
    # no-cache: l'HTML deve rivalidare sempre, altrimenti il browser serve un
    # index.html stantio (markup nuovo non visibile pur con app.js fresco).
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/onboarding")
async def onboarding_page():
    return FileResponse(STATIC_DIR / "onboarding.html")


@app.get("/costs")
async def costs_page():
    return FileResponse(STATIC_DIR / "costs.html")


# Mount static files at /static
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# -- M-CostObservability: viste costi + budget cap -------------------------
@app.get("/api/costs/summary")
async def api_costs_summary(days: int = 7):
    return cost_store.summary(HUB_PATH, days=max(1, min(int(days), 90)))


@app.get("/api/costs/budgets")
async def api_costs_budgets_get():
    return cost_store.load_budgets(HUB_PATH)


@app.put("/api/costs/budgets")
async def api_costs_budgets_put(payload: dict = Body(...)):
    feature = (payload.get("feature") or "").strip()
    if not feature:
        raise HTTPException(400, "feature required")
    cap = payload.get("cap")
    cap = None if cap in (None, "", 0, "0") else float(cap)
    return cost_store.set_budget(HUB_PATH, feature, cap)


# -- M-SelfHealth: diagnostics dell'always-on + alert sul degrado silenzioso ----
_SELF_HEALTH_TASK = None
_HEALTH_ALERTED: set = set()


def _daemons_status() -> dict:
    """Vitalità dei daemon asyncio (riusa la logica di /api/activity/summary)."""
    g = globals()

    def alive(t) -> bool:
        try:
            return t is not None and not t.done()
        except Exception:
            return False

    def dalive(name) -> bool:
        o = g.get(name)
        return alive(getattr(o, "task", None)) if o else False

    status = {
        "kanban_dispatcher": dalive("KANBAN_DISPATCHER"),
        "auto_ingest": dalive("AUTO_INGEST_DAEMON"),
        "telegram": dalive("TELEGRAM_DAEMON"),
        "script_supervisor": alive(g.get("SCRIPT_SUPERVISOR_TASK")),
        "goal_scheduler": alive(g.get("GOAL_SCHEDULER_TASK")),
        "notif_poller": alive(g.get("_NOTIF_POLLER_TASK")),
    }
    if os.environ.get("ANJA_DREAMING_ENABLED", "1") == "1":   # opzionale → nel report solo se on
        status["dreaming"] = alive(g.get("_DREAMING_TASK"))
    if os.environ.get("ANJA_BACKUP_ENABLED", "1") == "1":
        status["backup"] = alive(g.get("_BACKUP_TASK"))
    return status


@app.get("/api/health/self")
async def api_health_self():
    return self_health.collect(HUB_PATH, _daemons_status())


@app.get("/health")
async def health_page():
    return FileResponse(STATIC_DIR / "health.html")


async def _self_health_loop():
    interval = int(os.environ.get("ANJA_HEALTH_INTERVAL", "3600"))
    await asyncio.sleep(30)  # lascia stabilizzare i daemon all'avvio
    while True:
        try:
            res = self_health.collect(HUB_PATH, _daemons_status())
            failing = {c["name"]: c for c in res["failing"]}
            for name, c in failing.items():
                if name not in _HEALTH_ALERTED:       # dedup: 1 alert per degrado, non per ciclo
                    _HEALTH_ALERTED.add(name)
                    try:
                        notif_bus.publish(HUB_PATH, source="health",
                                          category="error" if c["severity"] == "error" else "warn",
                                          title=f"Self-health: {name}", body=c["detail"])
                    except Exception:
                        pass
            for name in list(_HEALTH_ALERTED):        # risolto → riarma l'alert
                if name not in failing:
                    _HEALTH_ALERTED.discard(name)
        except Exception as e:
            print(f"[self-health] error: {e}", flush=True)
        await asyncio.sleep(interval)


@app.on_event("startup")
async def _startup_self_health():
    global _SELF_HEALTH_TASK
    if not HUB_PATH:
        return
    _SELF_HEALTH_TASK = asyncio.create_task(_self_health_loop())
    print("[self-health] monitor started", flush=True)


@app.on_event("shutdown")
async def _shutdown_self_health():
    if _SELF_HEALTH_TASK:
        _SELF_HEALTH_TASK.cancel()


@app.on_event("startup")
async def _startup_media_credentials():
    """F-CLI-Media: materializza le chiavi media in credentials.env (hub +
    workspace) per la CLI giv usata dagli agent via Bash."""
    if not HUB_PATH:
        return
    try:
        import connectors_io
        written = connectors_io.write_media_credentials(HUB_PATH)
        if written:
            print(f"[media-cli] credentials.env materializzati: {len(written)}", flush=True)
    except Exception as e:
        print(f"[media-cli] materializzazione fallita: {e}", flush=True)


# -- F-Dreaming: consolidamento memoria notturno -------------------------------
_DREAMING_TASK = None


async def _run_dreaming() -> dict:
    """Un ciclo di consolidamento per l'utente di default dell'hub, con osservabilità
    (notifica + decision-trail) quando cambia qualcosa. Best-effort."""
    import dreaming
    slug = (_hub_config().get("default_user") or "").strip()
    if not slug:
        return {"skipped": "no default_user"}
    # F-BackupDR Fase 2: punto di ritorno della memoria PRIMA di mutarla (undo chirurgico
    # se il judge promuove qualcosa di sbagliato a USER.md). Best-effort, non blocca.
    pre_sha = None
    try:
        import checkpoint as _ckpt
        pre_sha = await asyncio.to_thread(_ckpt.checkpoint, HUB_PATH, "pre-dreaming: consolidamento memoria")
    except Exception as e:
        print(f"[dreaming] pre-checkpoint skipped: {e}", flush=True)
    report = await dreaming.consolidate(HUB_PATH, slug, projects=_build_projects_context())
    report["pre_dreaming_checkpoint"] = pre_sha
    if report.get("changed"):
        np_, nd_, nc_ = len(report["promoted"]), len(report["decayed"]), len(report["cross"])
        try:
            notif_bus.publish(HUB_PATH, source="dreaming", category="info",
                              title=f"Memory consolidation: +{np_} promoted · {nd_} decayed · {nc_} cross",
                              body=" · ".join((report["promoted"] + report["cross"])[:3]) or "-")
        except Exception:
            pass
        decision_trail.record(HUB_PATH, actor="dreaming",
                              trigger="consolidamento notturno memoria dialectic",
                              decision=f"promosse {np_} → USER.md · decadute {nd_} → Decayed · cross {nc_}",
                              rationale="observation mature promosse, stantie decadute, pattern cross-workspace distillati",
                              scope="hub")
    return report


async def _dreaming_loop():
    from datetime import datetime, timedelta
    hour = int(os.environ.get("ANJA_DREAMING_HOUR", "4"))
    await asyncio.sleep(60)  # non partire subito allo startup
    while True:
        now = datetime.now()
        nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        await asyncio.sleep(max(60, (nxt - now).total_seconds()))
        try:
            rep = await _run_dreaming()
            if rep.get("changed"):
                print(f"[dreaming] consolidato: {len(rep.get('promoted', []))} promosse, "
                      f"{len(rep.get('decayed', []))} decadute, {len(rep.get('cross', []))} cross", flush=True)
        except Exception as e:
            print(f"[dreaming] error: {e}", flush=True)


@app.on_event("startup")
async def _startup_dreaming():
    global _DREAMING_TASK
    if not HUB_PATH or os.environ.get("ANJA_DREAMING_ENABLED", "1") != "1":
        return
    _DREAMING_TASK = asyncio.create_task(_dreaming_loop())
    print(f"[dreaming] nightly consolidation enabled (hour={os.environ.get('ANJA_DREAMING_HOUR', '4')})", flush=True)


@app.on_event("shutdown")
async def _shutdown_dreaming():
    if _DREAMING_TASK:
        _DREAMING_TASK.cancel()


@app.post("/api/dreaming/run")
async def api_dreaming_run(request: Request):
    """Trigger manuale del consolidamento memoria (F-Dreaming). Solo admin/owner."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    _require_admin(request)
    return JSONResponse(await _run_dreaming())


# -- F-BackupDR: backup/disaster-recovery versionato dell'hub ------------------
_BACKUP_TASK = None


def _backup_extra_dirs() -> list:
    """Dir fuori-hub da includere nel backup: le conversazioni chat vivono in WEBAPP_DIR,
    non in <hub> → altrimenti si perderebbero (vedi finding F-BackupDR)."""
    conv = WEBAPP_DIR / "conversations"
    return [("webapp_conversations", conv)] if conv.is_dir() else []


async def _run_backup(reason: str = "nightly") -> dict:
    """Crea un backup completo dell'hub con osservabilità (notifica + decision-trail).
    Off-thread (I/O + gzip + sqlite). Best-effort."""
    import backup as backup_mod
    keep = int(os.environ.get("ANJA_BACKUP_KEEP", "14"))
    include_secrets = os.environ.get("ANJA_BACKUP_NO_SECRETS", "0") != "1"
    res = await asyncio.to_thread(backup_mod.create_backup, HUB_PATH, reason,
                                  include_secrets=include_secrets,
                                  extra_dirs=_backup_extra_dirs(), keep=keep)
    if res.get("ok"):
        mb = res["size"] / (1024 * 1024)
        comp = res["manifest"]["components"]
        off = res.get("mirrored_to")
        try:
            notif_bus.publish(HUB_PATH, source="backup", category="info",
                              title=f"Hub backup {mb:.1f} MB ({reason})",
                              body=f"{comp['hub_files']} files · {comp['dbs']} dbs · {comp['secrets']} secrets"
                                   + (" · off-site mirror ✓" if off else ""))
        except Exception:
            pass
        decision_trail.record(HUB_PATH, actor="backup", trigger=f"backup {reason}",
                              decision=f"snapshot {mb:.1f} MB in backups/ ({comp['dbs']} db consistenti, "
                                       f"{comp['secrets']} secret cifrati)" + (" + mirror off-site" if off else ""),
                              rationale="DR dell'hub: memoria/wiki/goals/kanban/costi/secrets vivono su un solo disco",
                              scope="hub")
        if res.get("backup_key_generated"):
            try:
                notif_bus.publish(HUB_PATH, source="backup", category="warn",
                                  title="New backup key generated",
                                  body="Keep <hub>/config/backup.key OFF-SITE: without it, the secrets in "
                                       "backups are unrecoverable.")
            except Exception:
                pass
    return res


async def _backup_loop():
    from datetime import datetime, timedelta
    hour = int(os.environ.get("ANJA_BACKUP_HOUR", "3"))  # prima del dreaming (04:00)
    await asyncio.sleep(90)  # non partire subito allo startup
    while True:
        now = datetime.now()
        nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        await asyncio.sleep(max(60, (nxt - now).total_seconds()))
        try:
            rep = await _run_backup("nightly")
            print(f"[backup] {rep.get('archive') or rep.get('error')}", flush=True)
        except Exception as e:
            print(f"[backup] error: {e}", flush=True)


@app.on_event("startup")
async def _startup_backup():
    global _BACKUP_TASK
    if not HUB_PATH or os.environ.get("ANJA_BACKUP_ENABLED", "1") != "1":
        return
    _BACKUP_TASK = asyncio.create_task(_backup_loop())
    print(f"[backup] nightly DR enabled (hour={os.environ.get('ANJA_BACKUP_HOUR', '3')})", flush=True)


@app.on_event("shutdown")
async def _shutdown_backup():
    if _BACKUP_TASK:
        _BACKUP_TASK.cancel()


@app.post("/api/backup/run")
async def api_backup_run(request: Request, payload: dict = Body(default={})):
    """Trigger manuale del backup dell'hub (F-BackupDR). Solo admin/owner."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    _require_admin(request)
    reason = (payload.get("reason") if isinstance(payload, dict) else None) or "manual"
    return JSONResponse(await _run_backup(str(reason)[:40]))


@app.get("/api/backups")
async def api_backups_list(request: Request):
    """Elenca i backup disponibili in <hub>/backups/. Solo admin/owner."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    _require_admin(request)
    import backup as backup_mod
    return {"backups": backup_mod.list_backups(HUB_PATH)}


# -- F-BackupDR Fase 2: undo mirato delle mutazioni autonome del cervello -------
@app.get("/api/memory/undo/snapshots")
async def api_memory_snapshots(request: Request, n: int = 30):
    """Punti di ritorno della memoria (checkpoint pre-mutazione). Solo admin/owner."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    _require_admin(request)
    import memory_undo
    return {"snapshots": memory_undo.list_memory_snapshots(HUB_PATH, n=max(1, min(int(n), 100)))}


@app.post("/api/memory/undo/memory")
async def api_memory_undo_memory(request: Request, payload: dict = Body(...)):
    """Undo chirurgico della memoria markdown (users/*.md) a un checkpoint.
    `preview=True` → ritorna solo il diff senza toccare nulla. Solo admin/owner."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    _require_admin(request)
    import memory_undo
    ref = (payload.get("ref") or "").strip()
    if not ref:
        raise HTTPException(400, "ref required")
    try:
        if payload.get("preview"):
            return JSONResponse(memory_undo.preview_memory_undo(HUB_PATH, ref))
        res = await asyncio.to_thread(memory_undo.undo_memory, HUB_PATH, ref)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if res.get("ok"):
        decision_trail.record(HUB_PATH, actor="memory-undo", trigger=f"undo memoria a {ref[:8]}",
                              decision=f"ripristinati users/*.md al checkpoint {res['restored_to'][:8]} "
                                       f"(pre-undo {(''+(res.get('pre_undo_checkpoint') or ''))[:8]})",
                              rationale="rollback mirato di una promozione/consolidamento memoria errato",
                              scope="hub")
    return JSONResponse(res)


@app.post("/api/memory/undo/cards")
async def api_memory_undo_cards(request: Request, payload: dict = Body(default={})):
    """Archivia (reversibile, status='archived') le card autonome recenti dello steward.
    `dry_run` default True → mostra cosa verrebbe archiviato. Solo admin/owner."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    _require_admin(request)
    import memory_undo
    p = payload if isinstance(payload, dict) else {}
    since = p.get("since") or None
    dry_run = bool(p.get("dry_run", True))
    res = memory_undo.undo_steward_cards(HUB_PATH, since_iso=since, dry_run=dry_run)
    if not dry_run and res["count"]:
        decision_trail.record(HUB_PATH, actor="memory-undo", trigger="undo card steward",
                              decision=f"archiviate {res['count']} card autonome (id {res['card_ids']})",
                              rationale="rollback di task spazzatura generati da azioni autonome",
                              scope="hub")
    return JSONResponse(res)


# -- F-BackupDR Fase 3: versioning + migrazioni dell'hub (fondamenta update) ----
@app.get("/api/version")
async def api_version():
    """Versione della piattaforma + stato migrazioni dell'hub (read-only)."""
    import updater
    if not HUB_PATH:
        return {"version": updater.current_version()}
    info = updater.check(HUB_PATH)
    return {"version": info["code_version"], **info}


@app.post("/api/update/migrate")
async def api_update_migrate(request: Request):
    """Applica l'update all'hub: backup pre-update + migrazioni + bump code_version.
    NON tocca il codice (quello lo porta il transport git/container). Solo admin/owner."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    _require_admin(request)
    import updater
    res = await asyncio.to_thread(updater.apply, HUB_PATH, backup=True, extra_dirs=_backup_extra_dirs())
    if res.get("ok"):
        n = len(res.get("migrations", {}).get("applied", []))
        decision_trail.record(HUB_PATH, actor="updater",
                              trigger=f"migrate hub {res.get('from')} → {res.get('to')}",
                              decision=f"backup pre-update + {n} migrazioni applicate · code_version={res.get('to')}",
                              rationale="allineamento schema dati dell'hub alla versione del codice",
                              scope="hub")
        try:
            notif_bus.publish(HUB_PATH, source="updater", category="info",
                              title=f"Hub updated to {res.get('to')}",
                              body=f"{n} migrations applied, pre-update backup created")
        except Exception:
            pass
    return JSONResponse(res)


# -- M-DecisionTrail: il "perché" delle azioni autonome ------------------------
@app.get("/api/decisions")
async def api_decisions(limit: int = 100, actor: str = None):
    return {"items": decision_trail.recent(HUB_PATH, limit=max(1, min(int(limit), 500)),
                                           actor=actor or None),
            "stats": decision_trail.stats(HUB_PATH)}


@app.get("/decisions")
async def decisions_page():
    return FileResponse(STATIC_DIR / "decisions.html")


@app.get("/hub/images/{date}/{filename}")
async def serve_hub_image(date: str, filename: str):
    """Servi un'immagine generata da anja_images: <hub>/raw/images/<date>/<file>."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    # safety: solo path matching atteso
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise HTTPException(400, "invalid date")
    if "/" in filename or ".." in filename or not filename.endswith((".png", ".jpg", ".jpeg", ".webp")):
        raise HTTPException(400, "invalid filename")
    img = HUB_PATH / "raw" / "images" / date / filename
    if not img.is_file():
        raise HTTPException(404, "image not found")
    return FileResponse(str(img))


@app.get("/api/registry")
async def api_registry(request: Request):
    """Combined registry + project metadata + cross analyses + recent activity."""
    if not HUB_PATH or not HUB_PATH.is_dir():
        raise HTTPException(500, "hub not configured")

    # Run list_projects.py for the registry
    list_script = ANJA_HUB_DIR / "scripts" / "list_projects.py"
    raw_projects = run_script(list_script, ["--hub", str(HUB_PATH), "--json"])

    if isinstance(raw_projects, dict) and "error" in raw_projects:
        # fallback: read projects.json directly
        try:
            with (HUB_PATH / "config" / "projects.json").open() as f:
                raw_projects = json.load(f).get("projects", [])
        except Exception as e:
            raise HTTPException(500, f"cannot read registry: {e}")

    if not isinstance(raw_projects, list):
        raw_projects = []

    # Enrich each project with totalPages + kind + responsabile (Fase 22)
    projects_enriched = []
    for p in raw_projects:
        name = p.get("name", "")
        ws_meta = _read_workspace_meta_yaml(name)
        projects_enriched.append({
            "id": p.get("id", ""),
            "name": name,
            "type": p.get("type", "dev"),
            "tags": p.get("tags", []),
            "lastSync": p.get("last_sync") or "",
            "totalPages": list_project_total_pages(HUB_PATH, name),
            "lintIssues": 0,
            "kind": ws_meta.get("kind") or _read_workspace_kind(name),
            "responsabile": ws_meta.get("responsabile") or None,
        })

    # F4b: scope filter — un member vede solo i ws di cui è membro (owner/admin/personal: tutti)
    import membership_io
    _me = getattr(request.state, "user", None)
    _allowed = set(membership_io.accessible_workspaces(
        HUB_PATH, [p["name"] for p in projects_enriched], _me, _acting_role(request)))
    projects_enriched = [p for p in projects_enriched if p["name"] in _allowed]

    # Cross analyses
    crosses = parse_cross_analyses(HUB_PATH)

    # Recent activity from cross/log.md
    recent = parse_log_entries(HUB_PATH / "cross" / "log.md", limit=10)

    return JSONResponse({
        "hub": {
            "name": HUB_PATH.name,
            "path": str(HUB_PATH),
            "user": (json.loads((HUB_PATH / "config.json").read_text(encoding="utf-8")).get("default_user", "")
                     if (HUB_PATH / "config.json").is_file() else ""),
            "lastSync": get_hub_last_sync(HUB_PATH),
            "kind": "hub",
        },
        "projects": projects_enriched,
        "workspaces": projects_enriched,  # Fase 22 alias: stessa lista, naming unificato
        "crossAnalyses": crosses,
        "recentActivity": recent,
    })


@app.post("/api/workspaces/create")
async def api_workspaces_create(request: Request, payload: dict = Body(...)):
    """Crea un workspace internal con responsabile agent (Fase 22).

    Body: {
      name, responsabile_name, role_description,
      ws_type? = 'office',
      responsabile_provider? = 'claude',
      responsabile_model? = 'sonnet',
      responsabile_effort? = None
    }
    """
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    _require_admin(request)
    name = (payload.get("name") or "").strip()
    resp_name = (payload.get("responsabile_name") or "").strip()
    role_desc = (payload.get("role_description") or "").strip()
    if not name or not resp_name or not role_desc:
        raise HTTPException(400, "name, responsabile_name, role_description required")
    ws_type = payload.get("ws_type") or "office"
    try:
        from workspace_scaffold import scaffold_workspace
    except Exception as e:
        raise HTTPException(500, f"scaffold module missing: {e}")
    result = scaffold_workspace(
        hub_path=HUB_PATH,
        name=name,
        responsabile_name=resp_name,
        role_description=role_desc,
        ws_type=ws_type,
        responsabile_provider=payload.get("responsabile_provider") or "claude",
        responsabile_model=payload.get("responsabile_model") or "sonnet",
        responsabile_effort=payload.get("responsabile_effort") or None,
    )
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "scaffold failed"))
    return JSONResponse(result)


@app.get("/api/blueprints")
async def api_blueprints():
    """Catalogo workspace attivabili (F5): metadata dei blueprint disponibili."""
    try:
        import blueprint_scaffold
    except Exception as e:
        raise HTTPException(500, f"blueprint_scaffold missing: {e}")
    return JSONResponse({"blueprints": blueprint_scaffold.list_blueprints(HUB_PATH)})


@app.post("/api/blueprints/{name}/validate")
async def api_blueprint_validate(request: Request, name: str):
    """F-BlueprintForge Step A: schema-check deterministico di un blueprint
    (hub o built-in) senza istanziarlo. Per gli agent che ne creano di nuovi
    in <hub>/blueprints/: scrivi → valida → istanzia."""
    if not HUB_PATH:
        raise HTTPException(400, "hub not configured")
    _require_admin(request)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
        raise HTTPException(400, "invalid blueprint name")
    try:
        import blueprint_scaffold
    except Exception as e:
        raise HTTPException(500, f"blueprint_scaffold missing: {e}")
    return JSONResponse({"name": name, **blueprint_scaffold.validate_blueprint(name, HUB_PATH)})


@app.post("/api/workspaces/from-blueprint")
async def api_workspaces_from_blueprint(request: Request, payload: dict = Body(...)):
    """Istanzia un workspace-brand da un blueprint (F-WorkspaceBlueprint).

    Body: { brand_name, blueprint? = 'marketing-site', backend? = 'wp',
            ecommerce? = false, lead_name?, provider?, model? }
    """
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    _require_admin(request)
    brand = (payload.get("brand_name") or "").strip()
    if not brand:
        raise HTTPException(400, "brand_name required")
    # brand/lead passano da _slugify (neutralizza ../) — blueprint finisce diretto in path → valida.
    blueprint = payload.get("blueprint") or "marketing-site"
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", blueprint):
        raise HTTPException(400, "invalid blueprint")
    try:
        import blueprint_scaffold
    except Exception as e:
        raise HTTPException(500, f"blueprint_scaffold missing: {e}")
    result = blueprint_scaffold.scaffold_from_blueprint(
        hub_path=HUB_PATH,
        brand_name=brand,
        blueprint_name=blueprint,
        backend=payload.get("backend") or "wp",
        ecommerce=bool(payload.get("ecommerce", False)),
        lead_name=payload.get("lead_name") or None,
        provider=payload.get("provider") or "claude",
        model=payload.get("model") or "sonnet",
    )
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "scaffold failed"))
    return JSONResponse(result)


@app.post("/api/pod/run")
async def api_pod_run(request: Request, payload: dict = Body(...)):
    """Pod marketing: il lead pianifica, gli specialisti girano isolati in parallelo
    (scoped sul brand, multi-provider), il lead sintetizza. Body: { workspace, brief }."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    ws = (payload.get("workspace") or "").strip()
    brief = (payload.get("brief") or "").strip()
    if not ws or not brief:
        raise HTTPException(400, "workspace e brief required")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", ws):
        raise HTTPException(400, "invalid workspace slug")
    _require_ws_access(request, ws)   # ws è nel body → gate esplicito (il middleware non lo vede)
    try:
        import pod_orchestrator
    except Exception as e:
        raise HTTPException(500, f"pod_orchestrator missing: {e}")
    result = await pod_orchestrator.run_pod_review(HUB_PATH, ws, brief)
    return JSONResponse(result)


@app.post("/api/workspace/query")
async def api_workspace_query(request: Request, payload: dict = Body(...)):
    """Delega lean: l'hub chiede DI un workspace senza entrarci. Un agente del workspace
    risponde coi suoi tool (kanban/goals/roadmap/marketing). Body: { project, question }."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    project = (payload.get("project") or payload.get("ws") or "").strip()
    question = (payload.get("question") or payload.get("q") or "").strip()
    if not project or not question:
        raise HTTPException(400, "project + question required")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", project):
        raise HTTPException(400, "invalid project slug")
    _require_ws_access(request, project)   # project è nel body → gate esplicito
    try:
        import pod_orchestrator
    except Exception as e:
        raise HTTPException(500, f"pod_orchestrator missing: {e}")
    res = await pod_orchestrator.run_workspace_query(HUB_PATH, project, question)
    return JSONResponse(res)


@app.post("/api/workspaces/onboard")
async def api_workspaces_onboard(request: Request, payload: dict = Body(...)):
    """Onboarding brand: popola i 'fatti' dal sito live — catalogo (deterministico) +
    ESPERTO (LLM) + BRAND stub. Body: { workspace, esperto? = true }."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    _require_admin(request)
    ws = (payload.get("workspace") or "").strip()
    if not ws:
        raise HTTPException(400, "workspace required")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", ws):
        raise HTTPException(400, "invalid workspace slug")
    try:
        import onboarding
    except Exception as e:
        raise HTTPException(500, f"onboarding missing: {e}")
    result = await onboarding.onboard_brand(HUB_PATH, ws, esperto=bool(payload.get("esperto", True)))
    return JSONResponse(result)


@app.post("/api/workspaces/{name}/archive")
async def api_workspaces_archive(name: str, request: Request):
    """Fase 22 — sposta workspace in `<hub>/workspaces/.archive/<name>/`.

    Solo per kind=internal. External: usa /unlink invece.
    Rimuove dal registry, conserva i file su disco.
    """
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    _require_admin(request)
    name = (name or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    ws_dir = HUB_PATH / "workspaces" / name
    if not ws_dir.exists():
        raise HTTPException(404, f"workspace '{name}' not found")
    if ws_dir.is_symlink():
        raise HTTPException(400, "use /api/workspaces/{name}/unlink for external workspaces")

    archive_root = HUB_PATH / "workspaces" / ".archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_dest = archive_root / f"{name}-{ts}"
    try:
        ws_dir.rename(archive_dest)
        # Marker file
        meta_marker = HUB_PATH / "workspaces" / f"{name}.meta.yaml"
        if meta_marker.is_file():
            meta_marker.rename(archive_root / f"{name}-{ts}.meta.yaml")
        # Rimuovi dal registry
        registry_path = HUB_PATH / "config" / "projects.json"
        if registry_path.is_file():
            with registry_path.open(encoding="utf-8") as f:
                reg = json.load(f)
            reg["projects"] = [p for p in reg.get("projects", []) if p.get("name") != name]
            with registry_path.open("w", encoding="utf-8") as f:
                json.dump(reg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        raise HTTPException(500, f"archive failed: {e}")
    return JSONResponse({"ok": True, "archived_to": str(archive_dest)})


@app.post("/api/workspaces/{name}/delete")
async def api_workspaces_delete(name: str, request: Request):
    """Fase 22 — cancella permanentemente un workspace internal.

    Body: {confirm: true} richiesto.
    External workspace: rimuove solo symlink (lascia .anjawiki reale).
    """
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not body.get("confirm"):
        raise HTTPException(400, "confirm: true required in body")

    name = (name or "").strip()
    if not name or "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "invalid workspace name")   # no traversal → rmtree confinato
    ws_dir = HUB_PATH / "workspaces" / name
    if not ws_dir.exists() and not ws_dir.is_symlink():
        raise HTTPException(404, f"workspace '{name}' not found")

    is_external = ws_dir.is_symlink()
    try:
        if is_external:
            ws_dir.unlink()  # rimuove solo symlink
        else:
            import shutil
            shutil.rmtree(ws_dir)
        # Rimuovi marker
        meta_marker = HUB_PATH / "workspaces" / f"{name}.meta.yaml"
        if meta_marker.is_file():
            meta_marker.unlink()
        # Rimuovi dal registry
        registry_path = HUB_PATH / "config" / "projects.json"
        if registry_path.is_file():
            with registry_path.open(encoding="utf-8") as f:
                reg = json.load(f)
            reg["projects"] = [p for p in reg.get("projects", []) if p.get("name") != name]
            with registry_path.open("w", encoding="utf-8") as f:
                json.dump(reg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        raise HTTPException(500, f"delete failed: {e}")
    return JSONResponse({"ok": True, "deleted": name, "kind": "external" if is_external else "internal"})


@app.get("/api/workspaces")
async def api_workspaces(request: Request):
    """Fase 22 — alias semantico di /api/registry per terminologia workspace.

    Ritorna solo `workspaces[]` con `kind` metadata, niente cross/recent.
    """
    if not HUB_PATH or not HUB_PATH.is_dir():
        raise HTTPException(500, "hub not configured")
    try:
        with (HUB_PATH / "config" / "projects.json").open() as f:
            raw = json.load(f).get("projects", [])
    except Exception:
        raw = []
    workspaces = []
    for p in raw:
        name = p.get("name", "")
        meta = _read_workspace_meta_yaml(name)
        workspaces.append({
            "id": p.get("id", ""),
            "name": name,
            "type": p.get("type", "dev"),
            "tags": p.get("tags", []),
            "kind": meta.get("kind") or _read_workspace_kind(name),
            "responsabile": meta.get("responsabile") or None,
            "location": p.get("location", {}),
        })
    # F4b: scope filter — member vede solo i ws di cui è membro (owner/admin/personal: tutti)
    import membership_io
    _me = getattr(request.state, "user", None)
    _allowed = set(membership_io.accessible_workspaces(
        HUB_PATH, [w["name"] for w in workspaces], _me, _acting_role(request)))
    workspaces = [w for w in workspaces if w["name"] in _allowed]
    return JSONResponse({
        "hub": {"name": HUB_PATH.name, "path": str(HUB_PATH), "kind": "hub"},
        "workspaces": workspaces,
    })


@app.get("/api/health")
async def api_health():
    """Run lint_hub.py and return summary."""
    if not HUB_PATH or not HUB_PATH.is_dir():
        raise HTTPException(500, "hub not configured")

    lint_script = ANJA_HUB_DIR / "scripts" / "lint_hub.py"
    result = run_script(lint_script, ["--hub", str(HUB_PATH)])

    if isinstance(result, dict) and "error" in result:
        return JSONResponse({"errors": 0, "warnings": 0, "suggestions": 0, "_error": result["error"]})

    by_sev = result.get("by_severity", {})
    return JSONResponse({
        "errors": by_sev.get("error", 0),
        "warnings": by_sev.get("warning", 0),
        "suggestions": by_sev.get("suggestion", 0),
        "issues_total": result.get("issues_total", 0),
    })


@app.get("/api/project/{project}/page/{page}")
async def api_project_page(project: str, page: str):
    """Serve markdown raw of a wiki page in a project."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")

    # safety: no path traversal
    if "/" in project or ".." in project or "/" in page or ".." in page:
        raise HTTPException(400, "invalid name")

    # Special handling for "Memory CC" tab
    if page == "memory-cc":
        # Read CC memory of the project
        proj_path = HUB_PATH / "workspaces" / project
        if not proj_path.exists():
            raise HTTPException(404, "project not found")
        # resolve symlink to find the real project root
        real_project_root = proj_path.resolve().parent  # .../<project>/.anjawiki/.. = .../<project>/
        # CC encoding: leading "/" → "-", then "/" → "-"
        path_str = str(real_project_root)
        encoded = "-" + path_str.lstrip("/").replace("/", "-")
        cc_memory_dir = Path.home() / ".claude" / "projects" / encoded / "memory"
        memory_md = cc_memory_dir / "MEMORY.md"
        if memory_md.is_file():
            content = memory_md.read_text(encoding="utf-8")
            # Aggiungi link agli altri file di memoria nella stessa dir
            other_files = sorted([f for f in cc_memory_dir.glob("*.md") if f.name != "MEMORY.md"])
            if other_files:
                content += "\n\n---\n\n## Other memory files\n\n"
                for f in other_files:
                    content += f"- `{f.name}`\n"
            return PlainTextResponse(content)
        return PlainTextResponse(f"# Memory CC\n\n*(No Claude Code memory found in `{memory_md}`)*")

    if page == "sessions":
        sessions_dir = HUB_PATH / "workspaces" / project / "wiki" / "sessions"
        if not sessions_dir.is_dir():
            return PlainTextResponse("# Sessions\n\n*(No sessions recorded.)*")
        files = sorted(sessions_dir.glob("*.md"), reverse=True)
        if not files:
            return PlainTextResponse("# Sessions\n\n*(No sessions recorded.)*")
        # Concatenate all sessions, most recent first
        out = ["# Sessions"]
        for f in files:
            out.append(f"\n## {f.stem}\n")
            out.append(f.read_text(encoding="utf-8"))
        return PlainTextResponse("\n".join(out))

    # Section directory listing: entities, concepts, sources, analysis
    SECTION_DIRS = {"entities", "concepts", "sources", "analysis"}
    if page in SECTION_DIRS:
        sec_dir = HUB_PATH / "workspaces" / project / "wiki" / page
        section_label = page.capitalize()
        if not sec_dir.is_dir():
            return PlainTextResponse(f"# {section_label}\n\n*(Empty section.)*")
        files = sorted(sec_dir.glob("*.md"))
        if not files:
            return PlainTextResponse(f"# {section_label}\n\n*(No pages in this section.)*")
        lines = [f"# {section_label}", "", f"_{len(files)} pagine in `wiki/{page}/`_", ""]
        for f in files:
            slug = f.stem
            # Estrai title da frontmatter o prima riga
            title = slug
            desc = ""
            try:
                text = f.read_text(encoding="utf-8")
                if text.startswith("---"):
                    end = text.find("\n---", 3)
                    if end > 0:
                        fm = text[3:end]
                        for line in fm.split("\n"):
                            if line.strip().startswith("title:"):
                                title = line.split(":", 1)[1].strip().strip('"').strip("'")
                            elif line.strip().startswith("description:"):
                                desc = line.split(":", 1)[1].strip().strip('"').strip("'")
            except Exception:
                pass
            link = f"[[{slug}]]"
            if desc:
                lines.append(f"- {link} **{title}** — {desc}")
            else:
                lines.append(f"- {link} **{title}**")
        return PlainTextResponse("\n".join(lines))

    # Regular wiki page lookup
    page_path = find_page_in_project(HUB_PATH, project, page)
    if page_path is None:
        raise HTTPException(404, f"page not found: {project}/{page}")

    return PlainTextResponse(page_path.read_text(encoding="utf-8"))


@app.get("/api/cross/analysis/{slug}")
async def api_cross_analysis(slug: str):
    """Serve markdown raw of a cross-analysis page."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    if "/" in slug or ".." in slug:
        raise HTTPException(400, "invalid slug")

    path = HUB_PATH / "cross" / "analysis" / f"{slug}.md"
    if not path.is_file():
        raise HTTPException(404, "analysis not found")

    return PlainTextResponse(path.read_text(encoding="utf-8"))


@app.get("/api/_status")
async def api_status():
    """Server health check."""
    return {
        "ok": True,
        "hub_path": str(HUB_PATH) if HUB_PATH else None,
        "anja_dir": str(ANJA_DIR),
        "anja_hub_dir": str(ANJA_HUB_DIR),
    }


# ============================================================
# M2 — Sessions endpoint
# ============================================================

@app.get("/api/sessions")
async def api_sessions():
    """Restituisce sessions/index.md aggregato del hub. Re-genera al volo se richiesto."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    sessions_md = HUB_PATH / "sessions" / "index.md"
    if sessions_md.is_file():
        return PlainTextResponse(sessions_md.read_text(encoding="utf-8"))
    return PlainTextResponse(
        "# Sessions\n\n*(Nessuna sessione aggregata. Usa il bottone \"Aggregate\" per generare.)*"
    )


# ============================================================
# M2 — Resources endpoints (read-only catalog)
# ============================================================

def _skills_catalog():
    """Lazy import (evita ciclo + permette esecuzione anche senza pyyaml in dev)."""
    import skills_catalog  # type: ignore
    return skills_catalog


@app.get("/api/skills")
@app.get("/api/resources/skills")
async def api_resources_skills():
    """Level 0 catalog. Multi-source (bundled plugins + user-global + hub + workspaces)."""
    sc = _skills_catalog()
    skills = sc.list_skills_as_dicts(HUB_PATH if HUB_PATH else None)
    return JSONResponse({"skills": skills})


@app.get("/api/skills/{name}")
async def api_skill_get(name: str):
    """Level 1: SKILL.md body completo + frontmatter."""
    if not SKILL_NAME_RE.match(name):
        raise HTTPException(400, "invalid skill name")
    sc = _skills_catalog()
    data = sc.load_skill(name, HUB_PATH if HUB_PATH else None)
    if not data:
        raise HTTPException(404, f"skill not found: {name}")
    return JSONResponse(data)


@app.get("/api/skills/{name}/file")
async def api_skill_file(name: str, path: str):
    """Level 2: file in references/scripts/templates (path relativo alla skill dir)."""
    if not SKILL_NAME_RE.match(name):
        raise HTTPException(400, "invalid skill name")
    sc = _skills_catalog()
    content = sc.load_skill_file(name, path, HUB_PATH if HUB_PATH else None)
    if content is None:
        raise HTTPException(404, "file not found or outside skill dir")
    return PlainTextResponse(content)


@app.get("/api/bundles")
async def api_bundles_list():
    """Skill bundles (composizione N skill + instruction wrapper)."""
    sc = _skills_catalog()
    bundles = sc.list_bundles_as_dicts(HUB_PATH if HUB_PATH else None)
    return JSONResponse({"bundles": bundles})


@app.get("/api/bundles/{name}")
async def api_bundle_get(name: str):
    """Bundle resolution: include body completo di tutte le skill referenziate."""
    if not SKILL_NAME_RE.match(name):
        raise HTTPException(400, "invalid bundle name")
    sc = _skills_catalog()
    data = sc.load_bundle(name, HUB_PATH if HUB_PATH else None)
    if not data:
        raise HTTPException(404, f"bundle not found: {name}")
    return JSONResponse(data)


@app.get("/api/skills/{name}/setup")
async def api_skill_setup_status(name: str):
    """Wizard status: cosa serve (config + env) e cosa è già impostato."""
    if not SKILL_NAME_RE.match(name):
        raise HTTPException(400, "invalid skill name")
    sc = _skills_catalog()
    out = sc.skill_setup_status(name, HUB_PATH if HUB_PATH else None)
    if "error" in out:
        raise HTTPException(404, out["error"])
    return JSONResponse(out)


@app.post("/api/skills/{name}/setup")
async def api_skill_setup_apply(name: str, request: Request):
    """Submit valori dal wizard. Body: {config: {key: value}, env: {VAR: value}}."""
    _require_admin(request)
    if not SKILL_NAME_RE.match(name):
        raise HTTPException(400, "invalid skill name")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    sc = _skills_catalog()
    out = sc.skill_setup_apply(
        name,
        config_values=body.get("config") or {},
        env_values=body.get("env") or {},
        hub_path=HUB_PATH if HUB_PATH else None,
    )
    if "error" in out:
        raise HTTPException(404, out["error"])
    return JSONResponse(out)


CC_PLUGINS_ROOT = Path.home() / ".claude" / "plugins" / "marketplaces"

# Plugin anjadev installato: context_loader / tools_md / lint_checks / compose_claude_md.
# Estratto da AnjaHub allo split (F-PluginSplit); override via ANJADEV_DIR per puntare a
# un checkout di sviluppo locale (es. ~/Documents/anjadev).
ANJADEV_DIR = Path(os.environ["ANJADEV_DIR"]) if os.environ.get("ANJADEV_DIR") else CC_PLUGINS_ROOT / "anjadev"


def _discover_installed_plugins() -> list:
    """Discovery dei plugin Claude Code installati in ~/.claude/plugins/marketplaces/.

    Per ogni marketplace, legge `.claude-plugin/marketplace.json` se presente, OR
    `.claude-plugin/plugin.json` se è un plugin single-package. Ritorna lista
    di plugin trovati con metadata (name, version, description, scripts_dir).
    """
    out = []
    if not CC_PLUGINS_ROOT.is_dir():
        return out
    for marketplace_dir in CC_PLUGINS_ROOT.iterdir():
        if not marketplace_dir.is_dir():
            continue
        # marketplace.json (multi-plugin marketplace)
        mk = marketplace_dir / ".claude-plugin" / "marketplace.json"
        plugin_file = marketplace_dir / ".claude-plugin" / "plugin.json"
        if mk.is_file():
            try:
                data = json.loads(mk.read_text(encoding="utf-8"))
                for p in data.get("plugins", []):
                    out.append({
                        "name": p.get("name", ""),
                        "version": p.get("version", ""),
                        "description": (p.get("description", "") or "")[:300],
                        "source": p.get("source", ""),
                        "marketplace": marketplace_dir.name,
                        "scripts_dir": str(marketplace_dir / "scripts") if (marketplace_dir / "scripts").is_dir() else None,
                        "installed": True,
                        "location": "remote",
                    })
            except Exception:
                pass
        elif plugin_file.is_file():
            try:
                data = json.loads(plugin_file.read_text(encoding="utf-8"))
                out.append({
                    "name": data.get("name", marketplace_dir.name),
                    "version": data.get("version", ""),
                    "description": (data.get("description", "") or "")[:300],
                    "marketplace": marketplace_dir.name,
                    "scripts_dir": str(marketplace_dir / "scripts") if (marketplace_dir / "scripts").is_dir() else None,
                    "installed": True,
                    "location": "remote",
                })
            except Exception:
                pass
    return out


@app.get("/api/resources/plugins")
async def api_resources_plugins(filter: Optional[str] = None):
    """Lista plugin: (a) marketplace locale di AnjaHub + (b) plugin remoti installati
    in ~/.claude/plugins/marketplaces/.

    Query param `filter=anja` filtra solo plugin Anja-related (`anja`, `anja-hub`,
    `anja-*`). Default: tutti.
    """
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")

    plugins = []
    seen = set()

    def _matches_filter(name: str) -> bool:
        if not filter:
            return True
        if filter == "anja":
            return name == "anja" or name.startswith("anja-") or name.startswith("anja_")
        return True

    # 1. Plugin locale di AnjaHub (anja-hub via marketplace.json locale)
    local_marketplace = ANJA_HUB_DIR.parent / ".claude-plugin" / "marketplace.json"
    if local_marketplace.is_file():
        try:
            data = json.loads(local_marketplace.read_text(encoding="utf-8"))
            for p in data.get("plugins", []):
                key = p.get("name", "")
                if key in seen or not _matches_filter(key):
                    continue
                seen.add(key)
                # Resolve scripts_dir per il plugin locale
                src = p.get("source", "")
                scripts_dir = None
                if src.startswith("./"):
                    plugin_root = (local_marketplace.parent.parent / src[2:]).resolve()
                    if (plugin_root / "scripts").is_dir():
                        scripts_dir = str(plugin_root / "scripts")
                plugins.append({
                    "name": key,
                    "description": (p.get("description", "") or "")[:300],
                    "version": p.get("version", ""),
                    "source": src,
                    "marketplace": "local",
                    "scripts_dir": scripts_dir,
                    "installed": True,
                    "location": "local",
                })
        except Exception as e:
            return JSONResponse({"plugins": [], "_error": str(e)})

    # 2. Plugin remoti installati da CC
    for p in _discover_installed_plugins():
        if p["name"] in seen or not _matches_filter(p["name"]):
            continue
        seen.add(p["name"])
        plugins.append(p)

    return JSONResponse({"plugins": plugins})


def _discover_plugin_mcp_servers() -> list:
    """Discovery dei MCP server forniti dai plugin Claude Code installati.

    Scansiona `~/.claude/plugins/marketplaces/<plugin>/scripts/mcp_*.py` per
    plugin remoti + `anja-hub/scripts/mcp_*.py` per il plugin locale anja-hub.
    Ritorna lista con scope=plugin:<plugin-name> + flag is_referenced (True se
    già in qualche `.mcp.json` del hub/progetti, False se solo disponibile).
    """
    found = []
    # Plugin remoti via marketplace
    if CC_PLUGINS_ROOT.is_dir():
        for marketplace_dir in CC_PLUGINS_ROOT.iterdir():
            if not marketplace_dir.is_dir():
                continue
            scripts_dir = marketplace_dir / "scripts"
            if not scripts_dir.is_dir():
                continue
            for mcp_file in sorted(scripts_dir.glob("mcp_*.py")):
                # Deriva nome canonico es. mcp_memory_server.py → anja_memory,
                # mcp_code_server.py → anja_code, mcp_bybit_lite.py → anja_bybit_lite
                stem = mcp_file.stem.replace("mcp_", "").replace("_server", "")
                canonical = stem if stem.startswith("anja_") else f"anja_{stem}"
                found.append({
                    "name": canonical,
                    "script_path": str(mcp_file),
                    "plugin": marketplace_dir.name,
                    "plugin_location": "remote",
                })
    # Plugin locale anja-hub
    local_anjahub_scripts = ANJA_HUB_DIR / "scripts"
    if local_anjahub_scripts.is_dir():
        for mcp_file in sorted(local_anjahub_scripts.glob("mcp_*.py")):
            stem = mcp_file.stem.replace("mcp_", "").replace("_server", "")
            canonical = stem if stem.startswith("anja_") else f"anja_{stem}"
            found.append({
                "name": canonical,
                "script_path": str(mcp_file),
                "plugin": "anja-hub",
                "plugin_location": "local",
            })
    return found


@app.get("/api/resources/mcp")
async def api_resources_mcp():
    """Lista MCP servers su 3 livelli:
    - scope=hub: dal `<hub>/.mcp.json`
    - scope=project:<name>: dai `.mcp.json` dei progetti registrati
    - scope=plugin:<plugin>: discovery automatica dei plugin Claude Code
      (anjadev remoto + anja-hub locale). Annotato `is_referenced` se è già
      attivato in un .mcp.json di hub/progetto."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")

    out = []
    referenced_paths: set[str] = set()  # script paths attivi in qualche .mcp.json

    # 1. MCP del hub
    hub_mcp = HUB_PATH / ".mcp.json"
    if hub_mcp.is_file():
        try:
            with hub_mcp.open() as f:
                cfg = json.load(f)
            for server_name, server_config in cfg.get("mcpServers", {}).items():
                cmd = server_config.get("command", "")
                args = server_config.get("args", [])
                url = server_config.get("url", "")
                args_str = args[-1] if args else ""
                if args_str:
                    referenced_paths.add(args_str)
                out.append({
                    "name": server_name,
                    "scope": "hub",
                    "project": None,
                    "plugin": None,
                    "command": cmd,
                    "url": url,
                    "remote": bool(url),
                    "args_summary": args_str[:120],
                })
        except Exception:
            pass

    # 2. MCP dei progetti
    try:
        with (HUB_PATH / "config" / "projects.json").open() as f:
            registry = json.load(f)
        for proj in registry.get("projects", []):
            if proj.get("location", {}).get("kind") != "local":
                continue
            proj_path = Path(proj["location"]["path"])
            mcp_path = proj_path / ".mcp.json"
            if not mcp_path.is_file():
                continue
            try:
                with mcp_path.open() as f:
                    mcp_config = json.load(f)
                for server_name, server_config in mcp_config.get("mcpServers", {}).items():
                    cmd = server_config.get("command", "")
                    args = server_config.get("args", [])
                    url = server_config.get("url", "")
                    args_str = args[-1] if args else ""
                    if args_str:
                        referenced_paths.add(args_str)
                    out.append({
                        "name": server_name,
                        "scope": f"project:{proj['name']}",
                        "project": proj["name"],
                        "plugin": None,
                        "command": cmd,
                        "url": url,
                        "remote": bool(url),
                        "args_summary": args_str[:120],
                    })
            except Exception:
                continue
    except Exception as e:
        return JSONResponse({"mcp": [], "_error": str(e)})

    # 3. Plugin MCP server (discovery automatica)
    for srv in _discover_plugin_mcp_servers():
        out.append({
            "name": srv["name"],
            "scope": f"plugin:{srv['plugin']}",
            "project": None,
            "plugin": srv["plugin"],
            "plugin_location": srv["plugin_location"],
            "command": "",
            "url": "",
            "remote": False,
            "args_summary": srv["script_path"][:120],
            "is_referenced": srv["script_path"] in referenced_paths,
        })

    return JSONResponse({"mcp": out})


# ============================================================
# M4 — Resources detail + create + copy
# ============================================================

SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
MCP_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


def _lookup_skill_dir(scope: str, name: str) -> Optional[Path]:
    """Per delete/detail: cerca la skill nel catalog e ritorna la dir effettiva
    (può essere `.anjawiki/skills/` anja-native o `.claude/skills/` legacy)."""
    if not SKILL_NAME_RE.match(name):
        return None
    sc = _skills_catalog()
    for s in sc.list_skills(HUB_PATH if HUB_PATH else None, apply_platform_filter=False):
        if s.name == name and s.scope == scope:
            return Path(s.path).parent
    return None


@app.get("/api/resources/skill")
async def api_resource_skill_detail(scope: str, name: str):
    """Read SKILL.md di una skill specifica. Query: ?scope=...&name=..."""
    skill_dir = _lookup_skill_dir(scope, name)
    if not skill_dir:
        raise HTTPException(404, f"skill not found: {scope}/{name}")
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise HTTPException(404, f"skill not found: {scope}/{name}")
    return PlainTextResponse(skill_md.read_text(encoding="utf-8"))


@app.get("/api/resources/mcp/detail")
async def api_resource_mcp_detail(name: str, project: str = "", scope: str = ""):
    """Read MCP server config (full json) di hub o di un progetto.

    Query params:
      ?name=...&scope=hub
      ?name=...&scope=project:foo
      ?name=...&project=foo  (legacy)
    """
    if not MCP_NAME_RE.match(name):
        raise HTTPException(400, "invalid mcp name")
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")

    if scope == "hub":
        target_root = HUB_PATH
        target_label = "hub"
    else:
        if scope.startswith("project:"):
            project = scope.split(":", 1)[1]
        if not project:
            raise HTTPException(400, "scope=hub or project required")
        target_root = resolve_project_path(project, HUB_PATH)
        if not target_root:
            raise HTTPException(404, "project not found")
        target_label = project

    mcp_path = target_root / ".mcp.json"
    if not mcp_path.is_file():
        raise HTTPException(404, f"no .mcp.json in {target_label}")
    try:
        data = json.loads(mcp_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(500, f"invalid mcp.json: {e}")
    server = data.get("mcpServers", {}).get(name)
    if not server:
        raise HTTPException(404, f"mcp server '{name}' not in {target_label}/.mcp.json")
    return JSONResponse({
        "name": name,
        "scope": "hub" if scope == "hub" else f"project:{project}",
        "project": project if scope != "hub" else None,
        "config": server,
        "path": str(mcp_path),
    })


@app.get("/api/resources/plugin/detail")
async def api_resource_plugin_detail(name: str):
    """Read plugin.json + README.md di un plugin nel marketplace."""
    if not SKILL_NAME_RE.match(name):
        raise HTTPException(400, "invalid plugin name")
    plugin_dir = ANJA_DIR.parent / name
    if not plugin_dir.is_dir():
        raise HTTPException(404, f"plugin dir not found: {plugin_dir}")
    out = {"name": name, "path": str(plugin_dir)}
    pj = plugin_dir / "plugin.json"
    if pj.is_file():
        try:
            out["plugin_json"] = json.loads(pj.read_text(encoding="utf-8"))
        except Exception as e:
            out["plugin_json_error"] = str(e)
    readme = plugin_dir / "README.md"
    if readme.is_file():
        out["readme"] = readme.read_text(encoding="utf-8")
    # commands count
    cmds = plugin_dir / "commands"
    if cmds.is_dir():
        out["commands"] = [f.stem for f in cmds.glob("*.md")]
    skills = plugin_dir / "skills"
    if skills.is_dir():
        out["skills"] = [d.name for d in skills.iterdir() if d.is_dir()]
    return JSONResponse(out)


@app.post("/api/resources/skills")
async def api_skill_create(request: Request):
    """Crea una nuova skill (frontmatter YAML standard).
    Body: {name, description, scope: 'user-global'|'hub'|'project:<name>',
           body?, version?, category?, tags?, platforms?}
    """
    _require_admin(request)   # definisce comandi/tool eseguiti dagli agenti
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    name = body.get("name", "").strip()
    description = body.get("description", "").strip()
    scope = body.get("scope", "")
    skill_body = body.get("body", "").strip()
    version = (body.get("version") or "0.1.0").strip()
    category = (body.get("category") or "").strip()
    tags = body.get("tags") or []
    platforms = body.get("platforms") or []

    if not SKILL_NAME_RE.match(name):
        raise HTTPException(400, "name must be kebab-case (lowercase, digits, dash, underscore)")
    if not description:
        raise HTTPException(400, "description required")
    if scope not in ("user-global", "hub") and not scope.startswith("project:"):
        raise HTTPException(400, "scope must be 'user-global', 'hub', or 'project:<name>'")

    sc = _skills_catalog()
    skill_dir = sc.resolve_writable_skill_dir(scope, name, HUB_PATH if HUB_PATH else None)
    if not skill_dir:
        raise HTTPException(400, "cannot resolve target dir (workspace not found or hub missing?)")
    if skill_dir.exists():
        raise HTTPException(409, f"skill already exists: {scope}/{name}")
    skill_dir.mkdir(parents=True, exist_ok=True)

    if not skill_body:
        skill_body = (
            f"# {name}\n\n"
            f"## When to use\n\n*Quando questa skill è applicabile.*\n\n"
            f"## Procedure\n\n1. Step uno\n2. Step due\n\n"
            f"## Pitfalls\n\n*Modalità di fallimento note.*\n\n"
            f"## Verification\n\n*Come confermare che ha funzionato.*\n"
        )

    fm = {"name": name, "description": description, "version": version}
    if category:
        fm["category"] = category
    if tags:
        fm["tags"] = list(tags)
    if platforms:
        fm["platforms"] = list(platforms)

    import yaml as _yaml
    fm_yaml = _yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    text = f"---\n{fm_yaml}\n---\n\n{skill_body}\n"
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")

    return JSONResponse({"status": "created", "scope": scope, "name": name, "path": str(skill_dir / "SKILL.md")})


@app.post("/api/resources/mcp")
async def api_mcp_create(request: Request):
    """Aggiunge un server MCP a <project>/.mcp.json (merge).

    Due modalità:
      • stdio (default): {project, name, command, args?, env?}
      • remote (HTTP/SSE): {project, name, type: 'http'|'sse', url, headers?}
    """
    _require_admin(request)   # registra un server MCP = nuovi tool eseguibili dagli agenti
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    scope = (body.get("scope") or "").strip().lower()
    project = body.get("project", "").strip()
    name = body.get("name", "").strip()
    transport = (body.get("type") or "").strip().lower()  # stdio | http | sse
    url = body.get("url", "").strip()
    headers = body.get("headers", {}) or {}
    command = body.get("command", "").strip()
    args = body.get("args", []) or []
    env = body.get("env", {}) or {}

    # scope=hub → install in <hub>/.mcp.json
    # scope=project:X o project=X (legacy) → install in <project>/.mcp.json
    if scope == "hub":
        target_root = HUB_PATH
    elif scope.startswith("project:"):
        project = scope.split(":", 1)[1]
    if not scope or scope.startswith("project:") or (not scope and project):
        if not project:
            raise HTTPException(400, "either scope='hub' or project (or scope='project:<name>') is required")
        target_root = None  # risolto sotto
    if not MCP_NAME_RE.match(name):
        raise HTTPException(400, "name must be alphanumeric/dash/underscore")

    # Determina automaticamente il type se non fornito
    if not transport:
        transport = "stdio" if command else ("http" if url else "stdio")

    is_remote = transport in ("http", "sse", "websocket")

    if is_remote:
        if not url:
            raise HTTPException(400, "url required for remote MCP")
        if not (url.startswith("https://") or url.startswith("http://") or url.startswith("ws://") or url.startswith("wss://")):
            raise HTTPException(400, "url must be http(s) or ws(s)")
        if not isinstance(headers, dict):
            raise HTTPException(400, "headers must be an object")
        server_obj = {"type": transport, "url": url}
        if headers:
            server_obj["headers"] = headers
    else:
        if not command:
            raise HTTPException(400, "command required for stdio MCP")
        if not isinstance(args, list):
            raise HTTPException(400, "args must be a list")
        if not isinstance(env, dict):
            raise HTTPException(400, "env must be an object")
        server_obj = {"command": command, "args": args}
        if env:
            server_obj["env"] = env
        # Esplicito type=stdio è opzionale; lo includiamo per chiarezza se richiesto
        if transport == "stdio" and body.get("explicit_type"):
            server_obj["type"] = "stdio"

    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")

    # Resolve target root: hub o project
    if scope == "hub":
        target_root = HUB_PATH
        target_label = "hub"
    else:
        target_root = resolve_project_path(project, HUB_PATH)
        if not target_root:
            raise HTTPException(404, "project not found")
        target_label = f"project:{project}"

    mcp_path = target_root / ".mcp.json"
    data = {"mcpServers": {}}
    if mcp_path.is_file():
        try:
            data = json.loads(mcp_path.read_text(encoding="utf-8"))
            if "mcpServers" not in data:
                data["mcpServers"] = {}
        except Exception as e:
            raise HTTPException(500, f"existing .mcp.json invalid: {e}")
    if name in data["mcpServers"]:
        raise HTTPException(409, f"mcp '{name}' already in {target_label}/.mcp.json")
    data["mcpServers"][name] = server_obj
    mcp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    return JSONResponse({
        "status": "created",
        "scope": scope or f"project:{project}",
        "project": project if scope != "hub" else None,
        "name": name,
        "type": transport,
        "remote": is_remote,
        "path": str(mcp_path),
    })


# ============================================================
# Fase 7i — AI-assisted MCP suggestion
# ============================================================

MCP_AI_SYSTEM_PROMPT = """You are an MCP (Model Context Protocol) configuration expert.

The user describes what they want to integrate. You return ONE JSON object with candidate
MCP servers that match. Be concrete: prefer well-known servers from the official Anthropic
MCP registry (https://github.com/modelcontextprotocol/servers) and verified community packages.

Output format (STRICT — only this JSON, no prose):
{
  "candidates": [
    {
      "name": "short-id",
      "label": "Human label",
      "description": "1-2 line what it does and which API/service it wraps",
      "install_cmd": "npx -y @modelcontextprotocol/server-X  OR  uvx mcp-server-X  OR null if no install",
      "transport": "stdio | http | sse",
      "config": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-X"],
        "env": {"API_KEY": "<your-api-key>"}
      },
      "env_vars_needed": ["API_KEY"],
      "docs_url": "https://...",
      "source_trust": "official | community | unknown"
    }
  ],
  "notes": "Optional: any caveats, alternatives, or required setup steps."
}

Rules:
- 1 to 3 candidates max, ranked by best fit.
- Prefer "official" (Anthropic registry) over community.
- For env vars, use placeholders like "<your-api-key>" in config.env, and list the keys in env_vars_needed.
- For HTTP/SSE remote MCPs, use {"url": "https://..."} instead of command/args.
- If you genuinely don't know a fitting server, return {"candidates": [], "notes": "explain why"}.
- Never invent package names — only return packages you're confident exist.

IMPORTANT — Reusing existing configs across scopes:
The user prompt starts with "# Target scope for the new MCP: ..." which tells you WHERE the MCP
will live (hub or a specific project). Pay attention: it determines `cwd` at runtime.

If the user references an existing MCP from another scope (e.g. "il server X del workspace Y" but
target is hub), you MUST adapt it to work in the new scope:

1. **Convert relative paths to absolute**:
   - Original args: `["vendor/some-mcp/dist/index.js"]` (relative to workspace cwd)
   - Adapted args: `["/abs/path/<workspace>/vendor/some-mcp/dist/index.js"]`
   - Use the workspace's absolute path from the "@ /abs/path" annotation in the existing list.

2. **Replace shell `.env` loading with explicit env vars** — and split secrets from public config:
   - Original command: `"sh"` with args `["-c", "set -a; [ -f .env ] && . ./.env; set +a; exec node ..."]`
     This relies on `<workspace>/.env` being in cwd.
   - Adapted: use the underlying command directly (e.g. `"node"` with args `["/abs/path/...js"]`).
   - **PUBLIC config vars** (booleans, mode flags) → put in `config.env`.
   - **SECRET vars** (anything ending in _KEY, _SECRET, _TOKEN, _PASSWORD, _PASS) → DO NOT put in `config.env`!
     Only list them in `env_vars_needed` so the user knows to set them in Settings → Custom Secrets.
     They'll be inherited via os.environ at runtime — no value should appear in the saved `.mcp.json`.

3. **Set source_trust = "user-existing"** and **install_cmd = null** (already installed).
4. **Label**: "Riusa: <original-name> (adattato per scope <target-scope>)".
5. **Add a clear note** in `notes` explaining what changed (paths absolutized, env vars to set in Settings).

If user reuses within the SAME scope (e.g. project source = project target), no adaptation needed —
just copy verbatim with label "Riusa: <name>".
"""


@app.post("/api/mcp/ai-suggest")
async def api_mcp_ai_suggest(request: Request):
    """AI-assisted MCP suggestion: user describe in NL, we return candidates."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    description = (body.get("description") or "").strip()
    target_scope = (body.get("scope") or "hub").strip()
    if not description or len(description) < 5:
        raise HTTPException(400, "description too short (min 5 chars)")
    if len(description) > 2000:
        raise HTTPException(400, "description too long (max 2000 chars)")

    try:
        from claude_agent_sdk import ClaudeAgentOptions, query
    except Exception:
        raise HTTPException(503, "claude-agent-sdk not available")

    # Raccogli MCP già configurati nell'hub + nei progetti registrati
    # così l'AI può riusare/copiare configs esistenti se l'utente li menziona.
    existing_context_lines = []
    workspace_paths = {}  # project_name -> abs path (per cross-scope adaptation)
    target_root_path = None
    if HUB_PATH:
        target_root_path = str(HUB_PATH) if target_scope == "hub" else None
        sources = [("hub", HUB_PATH / ".mcp.json", str(HUB_PATH))]
        try:
            registry = json.loads((HUB_PATH / "config" / "projects.json").read_text(encoding="utf-8"))
            for proj in registry.get("projects", []):
                if proj.get("location", {}).get("kind") == "local":
                    pname = proj["name"]
                    pabs = proj["location"]["path"]
                    workspace_paths[pname] = pabs
                    sources.append((f"project:{pname}", Path(pabs) / ".mcp.json", pabs))
                    if target_scope == f"project:{pname}":
                        target_root_path = pabs
        except Exception:
            pass
        for label, mcp_path, root_abs in sources:
            if not mcp_path.is_file():
                continue
            try:
                cfg = json.loads(mcp_path.read_text(encoding="utf-8"))
                servers = cfg.get("mcpServers", {})
                for name, srv in servers.items():
                    existing_context_lines.append(
                        f"  - **{name}** (in {label} @ {root_abs}): {json.dumps(srv, ensure_ascii=False)}"
                    )
            except Exception:
                continue

    augmented_prompt = f"# Target scope for the new MCP: `{target_scope}`"
    if target_root_path:
        augmented_prompt += f"  (cwd will be: `{target_root_path}`)"
    augmented_prompt += f"\n\n# User request:\n{description}"
    if existing_context_lines:
        augmented_prompt += "\n\n# MCP servers already configured in this hub & projects (REUSE/ADAPT these if the user references them):\n" + "\n".join(existing_context_lines)

    chunks = []
    try:
        opts = ClaudeAgentOptions(
            system_prompt=MCP_AI_SYSTEM_PROMPT,
            model="haiku",
            allowed_tools=[],  # niente tool, solo testo
            max_turns=1,
        )
        async for msg in query(prompt=augmented_prompt, options=opts):
            if hasattr(msg, "content"):
                for c in msg.content:
                    if hasattr(c, "text"):
                        chunks.append(c.text)
    except Exception as e:
        raise HTTPException(500, f"ai query failed: {type(e).__name__}: {e}")

    raw = "".join(chunks).strip()
    # Strip optional ```json fences
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)

    # Find the first JSON object even if there's prose before/after
    parsed = None
    parse_err = None
    # Try direct
    try:
        parsed = json.loads(raw)
    except Exception as e:
        parse_err = e
        # Find the substring from first '{' to last '}' and try raw_decode
        first = raw.find("{")
        if first >= 0:
            try:
                decoder = json.JSONDecoder()
                parsed, _end = decoder.raw_decode(raw[first:])
            except Exception as e2:
                parse_err = e2

    if parsed is None:
        return JSONResponse({
            "candidates": [],
            "notes": f"AI returned non-JSON output (parse error: {parse_err}). Raw: {raw[:300]}",
            "raw": raw,
        })

    if not isinstance(parsed, dict) or "candidates" not in parsed:
        return JSONResponse({
            "candidates": [],
            "notes": "AI output didn't include 'candidates' key.",
            "raw": raw,
        })

    return JSONResponse(parsed)


_DNS_PIN_LOCK = threading.Lock()


def _ssrf_check(url: str):
    """SSRF guard con pin: ritorna ((host, ip_validato), None) se l'URL è sicuro,
    altrimenti (None, msg). Risolve TUTTI gli IP (blocca se UNO è interno) e sceglie
    il primo IP pubblico da usare per la connessione (→ chiude il TOCTOU di rebinding)."""
    import ipaddress
    import socket
    host = urlsplit(url).hostname
    if not host:
        return None, "URL without a host"
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception as e:
        return None, f"unresolvable host: {e}"
    chosen = None
    for info in infos:
        ip = info[4][0]
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return None, f"invalid IP: {ip}"
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            return None, f"internal destination not allowed ({addr}): SSRF guard"
        if chosen is None:
            chosen = ip
    return (host, chosen), None


def _ssrf_safe_host(url: str) -> Optional[str]:
    """Compat: messaggio d'errore se l'URL non è SSRF-safe, altrimenti None."""
    _, err = _ssrf_check(url)
    return err


@contextlib.contextmanager
def _pin_dns(hostname: str, ip: str):
    """Forza hostname→ip per la durata del blocco: la connessione HTTP userà l'IP GIÀ
    validato invece di ri-risolvere il DNS (anti DNS-rebinding). Il monkeypatch di
    socket.getaddrinfo è globale al processo → serializzato con lock; la SNI/verifica
    cert restano sull'hostname reale (getaddrinfo tocca solo l'indirizzo)."""
    import socket as _sock
    real = _sock.getaddrinfo

    def pinned(host, *a, **k):
        return real(ip, *a, **k) if host == hostname else real(host, *a, **k)

    with _DNS_PIN_LOCK:
        _sock.getaddrinfo = pinned
        try:
            yield
        finally:
            _sock.getaddrinfo = real


class _SSRFRedirectGuard(urllib.request.HTTPRedirectHandler):
    """Ri-applica il guard a ogni redirect: un URL pubblico può rimbalzare su un IP interno."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        blocked = _ssrf_safe_host(newurl)
        if blocked:
            raise urllib.error.HTTPError(newurl, code, blocked, headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@app.post("/api/resources/skills/import")
async def api_skill_import(request: Request):
    """Import skill da URL.

    Body: {url, scope, name?}
    Supportati:
    - URL diretto a SKILL.md raw (qualsiasi origine HTTPS)
    - URL GitHub repo (es: https://github.com/user/repo) → tenta fetch di
      raw.githubusercontent.com/user/repo/<branch>/SKILL.md
    - URL a una dir GitHub skill (es: https://github.com/user/repo/tree/main/skills/foo)
    """
    _require_admin(request)
    import urllib.request
    import urllib.parse

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")

    url = (body.get("url") or "").strip()
    scope = body.get("scope", "")
    name_override = (body.get("name") or "").strip()

    if not url:
        raise HTTPException(400, "url required")
    if not (url.startswith("https://") or url.startswith("http://")):
        raise HTTPException(400, "url must be http(s)")
    if scope not in ("user-global", "hub") and not scope.startswith("project:"):
        raise HTTPException(400, "scope must be 'user-global', 'hub', or 'project:<name>'")

    # Risolvi URL → URL del raw SKILL.md
    raw_urls = []
    if url.endswith("SKILL.md") or url.endswith("skill.md"):
        raw_urls = [url]
    elif "github.com" in url:
        # github.com/user/repo[/tree/<branch>/<path>]
        # → raw.githubusercontent.com/user/repo/<branch>/<path>/SKILL.md
        m = re.match(r"https?://github\.com/([^/]+)/([^/]+?)(?:/tree/([^/]+)(?:/(.+))?)?/?$", url)
        if m:
            user, repo, branch, path = m.groups()
            branch = branch or "main"
            path = path or ""
            base = f"https://raw.githubusercontent.com/{user}/{repo}/{branch}"
            if path:
                raw_urls.append(f"{base}/{path.rstrip('/')}/SKILL.md")
            else:
                raw_urls.append(f"{base}/SKILL.md")
                # fallback: master
                raw_urls.append(f"https://raw.githubusercontent.com/{user}/{repo}/master/SKILL.md")
    else:
        raw_urls = [url]

    # Try fetch (con SSRF guard: blocca IP interni prima del fetch e su ogni redirect)
    content = None
    fetched_url = None
    last_error = None
    opener = urllib.request.build_opener(_SSRFRedirectGuard())
    for u in raw_urls:
        ok, err = _ssrf_check(u)
        if err:
            last_error = err
            continue
        try:
            host, ip = ok
            req = urllib.request.Request(u, headers={"User-Agent": "anja-import/0.1"})
            with _pin_dns(host, ip):   # connette all'IP validato (anti-rebinding); redirect ri-controllati dal guard
                with opener.open(req, timeout=15) as resp:
                    if resp.status >= 400:
                        last_error = f"HTTP {resp.status}"
                        continue
                    content = resp.read().decode("utf-8", errors="replace")
                    fetched_url = u
                    break
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            continue

    if content is None:
        raise HTTPException(400, f"cannot fetch SKILL.md from URL. Tried: {raw_urls}. Last error: {last_error}")

    # Parse frontmatter per estrarre name + description
    parsed_name = None
    parsed_desc = None
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end > 0:
            fm = content[3:end]
            for line in fm.split("\n"):
                ls = line.strip()
                if ls.startswith("name:"):
                    parsed_name = ls.split(":", 1)[1].strip().strip('"').strip("'")
                elif ls.startswith("description:"):
                    parsed_desc = ls.split(":", 1)[1].strip().strip('"').strip("'")

    final_name = name_override or parsed_name or ""
    if not final_name:
        raise HTTPException(400, "skill SKILL.md has no 'name:' in the frontmatter — provide a 'name' override in the request")
    if not SKILL_NAME_RE.match(final_name):
        raise HTTPException(400, f"name '{final_name}' is invalid (kebab-case required)")

    sc = _skills_catalog()
    skill_dir = sc.resolve_writable_skill_dir(scope, final_name, HUB_PATH if HUB_PATH else None)
    if not skill_dir:
        raise HTTPException(400, "cannot resolve target dir (writable scope required)")
    if skill_dir.exists():
        raise HTTPException(409, f"skill already exists: {scope}/{final_name}")
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    return JSONResponse({
        "status": "imported",
        "scope": scope,
        "name": final_name,
        "from_url": fetched_url,
        "description": parsed_desc or "",
        "path": str(skill_dir / "SKILL.md"),
    })


@app.post("/api/resources/copy")
async def api_resource_copy(request: Request):
    """Copia una resource tra scope. Solo skills supportate v1.

    Body: {kind: 'skill', from_scope, name, to_scope}
    """
    _require_admin(request)
    import shutil
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    kind = body.get("kind", "skill")
    name = body.get("name", "").strip()
    from_scope = body.get("from_scope", "")
    to_scope = body.get("to_scope", "")

    if kind != "skill":
        raise HTTPException(400, "only kind='skill' supported in v1")
    if from_scope == to_scope:
        raise HTTPException(400, "source and target scopes are equal")

    sc = _skills_catalog()
    src = _lookup_skill_dir(from_scope, name)
    dst = sc.resolve_writable_skill_dir(to_scope, name, HUB_PATH if HUB_PATH else None)
    if not src:
        raise HTTPException(404, f"source skill not found: {from_scope}/{name}")
    if not dst:
        raise HTTPException(400, f"invalid to_scope (must be writable): {to_scope}")
    if not src.is_dir():
        raise HTTPException(404, f"source skill not found: {from_scope}/{name}")
    if dst.exists():
        raise HTTPException(409, f"target skill already exists: {to_scope}/{name}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)

    return JSONResponse({
        "status": "copied",
        "kind": kind,
        "name": name,
        "from": from_scope,
        "to": to_scope,
        "path": str(dst),
    })


@app.delete("/api/resources/skill")
async def api_skill_delete(scope: str, name: str, request: Request):
    """Cancella una skill (con tutta la sua directory). Query: ?scope=...&name=..."""
    _require_admin(request)
    import shutil
    if scope.startswith("plugin:"):
        raise HTTPException(403, "plugin skills are read-only — modifica il plugin direttamente")
    skill_dir = _lookup_skill_dir(scope, name)
    if not skill_dir:
        raise HTTPException(404, f"skill not found: {scope}/{name}")
    if not skill_dir.is_dir():
        raise HTTPException(404, "skill not found")
    shutil.rmtree(skill_dir)
    return JSONResponse({"status": "deleted", "scope": scope, "name": name})


@app.put("/api/resources/mcp")
async def api_mcp_update(request: Request):
    """Update di un MCP esistente. Body identico a POST + scope/name source.

    {scope, name, command?, args?, env?, type?, url?, headers?}  → riscrive l'entry.
    """
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    scope = (body.get("scope") or "").strip().lower()
    project = body.get("project", "").strip()
    name = body.get("name", "").strip()
    if scope.startswith("project:"):
        project = scope.split(":", 1)[1]
    if not MCP_NAME_RE.match(name):
        raise HTTPException(400, "invalid mcp name")
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")

    if scope == "hub":
        target_root = HUB_PATH
        target_label = "hub"
    else:
        if not project:
            raise HTTPException(400, "scope=hub or project required")
        target_root = resolve_project_path(project, HUB_PATH)
        if not target_root:
            raise HTTPException(404, "project not found")
        target_label = project

    mcp_path = target_root / ".mcp.json"
    if not mcp_path.is_file():
        raise HTTPException(404, "no .mcp.json")
    try:
        data = json.loads(mcp_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(500, f"invalid .mcp.json: {e}")
    if name not in data.get("mcpServers", {}):
        raise HTTPException(404, f"mcp '{name}' not in {target_label}/.mcp.json")

    # Build new entry from body (same logic as create)
    transport = (body.get("type") or "").strip().lower()
    url = body.get("url", "").strip()
    headers = body.get("headers", {}) or {}
    command = body.get("command", "").strip()
    args = body.get("args", []) or []
    env = body.get("env", {}) or {}

    if not transport:
        transport = "stdio" if command else ("http" if url else "stdio")
    is_remote = transport in ("http", "sse", "websocket")

    if is_remote:
        if not url:
            raise HTTPException(400, "url required for remote MCP")
        new_entry = {"type": transport, "url": url}
        if headers:
            new_entry["headers"] = headers
    else:
        if not command:
            raise HTTPException(400, "command required for stdio MCP")
        new_entry = {"command": command, "args": args}
        if env:
            new_entry["env"] = env

    data["mcpServers"][name] = new_entry
    mcp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return JSONResponse({
        "status": "updated",
        "scope": "hub" if scope == "hub" else f"project:{project}",
        "name": name,
        "type": transport,
        "remote": is_remote,
    })


@app.delete("/api/resources/mcp")
async def api_mcp_delete(name: str, request: Request, project: str = "", scope: str = ""):
    """Rimuove un MCP server da hub o project /.mcp.json.

    Query: ?name=...&scope=hub  oppure  ?name=...&project=foo
    """
    _require_admin(request)
    if not MCP_NAME_RE.match(name):
        raise HTTPException(400, "invalid mcp name")
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")

    if scope == "hub":
        target_root = HUB_PATH
        target_label = "hub"
    else:
        if scope.startswith("project:"):
            project = scope.split(":", 1)[1]
        if not project:
            raise HTTPException(400, "scope=hub or project required")
        target_root = resolve_project_path(project, HUB_PATH)
        if not target_root:
            raise HTTPException(404, "project not found")
        target_label = project

    mcp_path = target_root / ".mcp.json"
    if not mcp_path.is_file():
        raise HTTPException(404, "no .mcp.json")
    try:
        data = json.loads(mcp_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(500, f"invalid mcp.json: {e}")
    if name not in data.get("mcpServers", {}):
        raise HTTPException(404, f"mcp '{name}' not found in {target_label}/.mcp.json")
    del data["mcpServers"][name]
    mcp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return JSONResponse({
        "status": "deleted",
        "scope": "hub" if scope == "hub" else f"project:{project}",
        "project": project if scope != "hub" else None,
        "name": name,
    })


# ============================================================
# M-PA 1 — Agents API
# ============================================================

import re as _re_pa
AGENT_NAME_RE = _re_pa.compile(r"^[a-z0-9][a-z0-9_-]*$")
VALID_AGENT_MODELS = ("haiku", "sonnet", "opus", "fable")


def _agents_root() -> Optional[Path]:
    if not HUB_PATH:
        return None
    root = HUB_PATH / "agents"
    return root if root.is_dir() else root  # always return path; may not exist yet


def _list_agents_in(root: Optional[Path], scope_tag: str = "hub") -> list:
    """Scan agents in a directory. Returns list of agent info dicts.
    `scope_tag` viene aggiunto a ogni agent come 'agent_scope' field per disambiguare hub vs project.
    """
    if not root or not root.is_dir():
        return []
    out = []
    for sub in sorted(root.iterdir()):
        if not sub.is_dir():
            continue
        info = {"name": sub.name, "path": str(sub), "agent_scope": scope_tag}
        cfg_path = sub / "config.json"
        if cfg_path.is_file():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                info["role"] = cfg.get("role", "")
                info["model"] = cfg.get("default_model", "?")
                info["provider"] = cfg.get("default_provider", "claude")
                info["effort"] = cfg.get("default_effort", "off")
                info["scope"] = cfg.get("scope", "hub")
                info["soul_inheritance"] = cfg.get("soul_inheritance", [])
                info["mcp_servers"] = cfg.get("mcp_servers", [])
            except Exception as e:
                info["_error"] = str(e)
        sessions_dir = sub / "sessions"
        info["sessions_count"] = 0
        if sessions_dir.is_dir():
            for f in sessions_dir.rglob("*.md"):
                if f.name != "index.md":
                    info["sessions_count"] += 1
        out.append(info)
    return out


def _list_agents() -> list:
    """Hub-level agents (compat)."""
    return _list_agents_in(_agents_root(), scope_tag="hub")


def _project_agents_root(project_name: str) -> Optional[Path]:
    """Project-scope agents in `<project>/.anjawiki/agents/`."""
    root = _project_root(project_name)
    if not root:
        return None
    return root / ".anjawiki" / "agents"


@app.get("/api/agents")
async def api_agents_list(project: str = ""):
    """Lista agents.
    - Senza `project`: hub agents (compat)
    - Con `project=<name>`: project-scope agents (Fase 13+)
    """
    if project:
        return JSONResponse({"agents": _list_agents_in(_project_agents_root(project), scope_tag=f"project:{project}")})
    # Vista hub = roster COMPLETO: hub-level + i team dei workspace. Senza questo,
    # gli agenti dei workspace risultano "inesistenti" alle sessioni hub (deleghe).
    # NB: workspace diversi possono avere agent omonimi (pod: dev/analyst/…) —
    # sono agent DIVERSI, niente dedup tra loro. Si dedup-a solo il mirror hub
    # (dir sessions/ senza config) rispetto all'agent vero del workspace.
    hub_list = _list_agents()
    hub_by_name = {a["name"]: a for a in hub_list}
    replaced = set()
    ws_list = []
    ws_root = HUB_PATH / "workspaces" if HUB_PATH else None
    if ws_root and ws_root.is_dir():
        for ws in sorted(ws_root.iterdir()):
            if not ws.is_dir():
                continue
            for a in _list_agents_in(ws / ".anjawiki" / "agents", scope_tag=f"project:{ws.name}"):
                cur = hub_by_name.get(a["name"])
                if cur is not None and "role" not in cur and "role" in a and a["name"] not in replaced:
                    if cur.get("sessions_count"):
                        a["sessions_count"] = a.get("sessions_count", 0) + cur["sessions_count"]
                    replaced.add(a["name"])
                ws_list.append(a)
    agents = [a for a in hub_list if a["name"] not in replaced] + ws_list
    return JSONResponse({"agents": agents})


@app.get("/api/agents/{name}")
async def api_agent_detail(name: str, project: str = ""):
    if not AGENT_NAME_RE.match(name):
        raise HTTPException(400, "invalid agent name")
    if project:
        # Project-scope agent (Fase 13+)
        if not HUB_PATH:
            raise HTTPException(500, "hub not configured")
        agent_dir = HUB_PATH / "workspaces" / project / ".anjawiki" / "agents" / name
    else:
        root = _agents_root()
        if not root:
            raise HTTPException(500, "hub not configured")
        agent_dir = root / name
    if not agent_dir.is_dir():
        raise HTTPException(404, f"agent '{name}' not found")
    cfg_path = agent_dir / "config.json"
    config = {}
    if cfg_path.is_file():
        try:
            config = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    triade = {}
    for fname in ("AGENTS.md", "SOUL.md", "TOOLS.md", "CLAUDE.md"):
        f = agent_dir / fname
        triade[fname] = {
            "exists": f.is_file(),
            "size_bytes": f.stat().st_size if f.is_file() else 0,
        }
    sessions = []
    sessions_dir = agent_dir / "sessions"
    if sessions_dir.is_dir():
        for f in sorted(sessions_dir.rglob("*.md"), reverse=True)[:10]:
            if f.name == "index.md":
                continue
            sessions.append({
                "id": f.stem,
                "path": str(f.relative_to(agent_dir)),
                "mtime": f.stat().st_mtime,
            })
    return JSONResponse({
        "name": name,
        "path": str(agent_dir),
        "config": config,
        "triade": triade,
        "sessions": sessions,
        "sessions_count": len(sessions),
    })


AGENT_AI_SYSTEM_PROMPT = """You design anja agents. Given a natural-language description,
return ONE JSON object with the agent config. STRICT format, no prose:

{
  "name":     "kebab-case-name (es: trader, writer, researcher)",
  "role":     "1-3 frasi descrittive del ruolo + personalità (italiano se utente scrive italiano)",
  "domain":   "area di specializzazione, breve (opzionale)",
  "provider": "claude | openai | xai | openrouter",
  "model":    "model identifier per quel provider (es: 'sonnet', 'gpt-5.5', 'grok-4.3')",
  "effort":   "off | low | medium | high  (solo per claude, off altrimenti)",
  "notes":    "opzionale: motivazione delle scelte"
}

Linee guida:
- Per task complessi (analisi, ragionamento profondo) → claude/sonnet con effort=medium o opus
- Per chat veloce/interattiva → claude/haiku o grok-4-fast-non-reasoning
- Per dominio molto tecnico (codice, math) → claude/opus o effort=high
- Per scrittura creativa → claude/sonnet o gpt-5.5
- Default safe: claude/sonnet con effort=off
- Name: deriva dalla descrizione, kebab-case, max 24 char (es. "writer-tech", "research-papers")
- Role: scrivi 2-3 frasi che incarnino la personalità
"""


@app.post("/api/agents/ai-suggest")
async def api_agent_ai_suggest(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    description = (body.get("description") or "").strip()
    if not description or len(description) < 10:
        raise HTTPException(400, "description too short (min 10 chars)")
    try:
        from claude_agent_sdk import ClaudeAgentOptions, query
    except Exception:
        raise HTTPException(503, "claude-agent-sdk not available")

    chunks = []
    try:
        opts = ClaudeAgentOptions(
            system_prompt=AGENT_AI_SYSTEM_PROMPT,
            model="haiku",
            allowed_tools=[],
            max_turns=1,
            permission_mode="bypassPermissions",
        )
        async for msg in query(prompt=description, options=opts):
            if hasattr(msg, "content"):
                for c in msg.content:
                    if hasattr(c, "text"):
                        chunks.append(c.text)
    except Exception as e:
        raise HTTPException(500, f"ai query failed: {type(e).__name__}: {e}")

    raw = "".join(chunks).strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)

    parsed = None
    parse_err = None
    try:
        parsed = json.loads(raw)
    except Exception as e:
        parse_err = e
        first = raw.find("{")
        if first >= 0:
            try:
                decoder = json.JSONDecoder()
                parsed, _ = decoder.raw_decode(raw[first:])
            except Exception as e2:
                parse_err = e2

    if not isinstance(parsed, dict) or "name" not in parsed:
        raise HTTPException(500, f"AI returned non-JSON or missing 'name'. Raw: {raw[:300]}")
    return JSONResponse(parsed)


@app.post("/api/agents")
async def api_agent_create(request: Request):
    """Body: {name, role, domain?, model?='sonnet', force?=false}"""
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    name = (body.get("name") or "").strip()
    role = (body.get("role") or "").strip()
    domain = (body.get("domain") or "").strip()
    provider = (body.get("provider") or "claude").strip()
    model = body.get("model") or "sonnet"
    effort = (body.get("effort") or "off").strip()
    force = bool(body.get("force", False))
    project_scope = (body.get("project") or "").strip()  # Fase 13+ project-scope agent

    if not AGENT_NAME_RE.match(name):
        raise HTTPException(400, "name must be kebab-case")
    if not role:
        raise HTTPException(400, "role required")
    # Per Claude validiamo lista nota; per altri provider il model è free-text
    if provider == "claude" and model not in VALID_AGENT_MODELS:
        # accetta comunque ma con warning silenzioso (l'utente potrebbe voler usare un alias)
        pass
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")

    script = ANJA_HUB_DIR / "scripts" / "agent_add.py"
    if not script.is_file():
        raise HTTPException(500, f"agent_add.py not found at {script}")

    cmd = [
        sys.executable, str(script),
        "--hub", str(HUB_PATH),
        "--name", name,
        "--role", role,
        "--model", model,
        "--provider", provider,
        "--effort", effort,
    ]
    if domain:
        cmd += ["--domain", domain]
    if force:
        cmd += ["--force"]
    if project_scope:
        cmd += ["--project", project_scope]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except Exception as e:
        raise HTTPException(500, f"agent_add subprocess failed: {e}")

    if r.returncode != 0:
        # parse error from stderr
        err_msg = (r.stderr or r.stdout or "").strip()
        if "ERROR: agent" in err_msg and "già esiste" in err_msg:
            raise HTTPException(409, err_msg)
        raise HTTPException(400, err_msg or "agent_add failed")

    return JSONResponse({
        "status": "created",
        "name": name,
        "stdout": r.stdout.strip(),
    })


@app.post("/api/agents/clone")
async def api_agent_clone(request: Request):
    """Clone agent esistente. Body: {source_name, source_project?, target_name, target_project?, include_config=true}.

    source_project=null → hub agent. target_project=null → hub (default).
    include_config: copia anche config.json + custom files. False = solo CLAUDE.md (identità).
    """
    _require_admin(request)
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON body")
    source_name = (body.get("source_name") or "").strip()
    source_project = (body.get("source_project") or "").strip() or None
    target_name = (body.get("target_name") or "").strip()
    target_project = (body.get("target_project") or "").strip() or None
    include_config = bool(body.get("include_config", True))
    if not source_name or not target_name:
        raise HTTPException(400, "source_name and target_name required")
    if not AGENT_NAME_RE.match(target_name):
        raise HTTPException(400, "target_name must be kebab-case")

    # Resolve source dir
    if source_project:
        src_dir = HUB_PATH / "workspaces" / source_project / ".anjawiki" / "agents" / source_name
    else:
        src_dir = HUB_PATH / "agents" / source_name
    if not src_dir.is_dir():
        raise HTTPException(404, f"source agent '{source_name}' not found")

    # Resolve target dir
    if target_project:
        tgt_dir = HUB_PATH / "workspaces" / target_project / ".anjawiki" / "agents" / target_name
    else:
        tgt_dir = HUB_PATH / "agents" / target_name
    if tgt_dir.exists():
        raise HTTPException(409, f"target agent '{target_name}' already exists")

    import shutil
    tgt_dir.parent.mkdir(parents=True, exist_ok=True)
    if include_config:
        shutil.copytree(src_dir, tgt_dir, ignore=shutil.ignore_patterns(
            "sessions", "journal*", "memory", "*.log",
        ))
    else:
        # Solo CLAUDE.md (identità)
        tgt_dir.mkdir(parents=True)
        for fname in ("CLAUDE.md", "AGENTS.md", "SOUL.md", "TOOLS.md"):
            src_f = src_dir / fname
            if src_f.is_file():
                shutil.copy2(src_f, tgt_dir / fname)
    return JSONResponse({
        "status": "cloned",
        "source": str(src_dir),
        "target": str(tgt_dir),
        "target_name": target_name,
        "target_project": target_project,
        "include_config": include_config,
    })


AGENT_VIEWABLE_FILES = ("AGENTS.md", "SOUL.md", "TOOLS.md", "CLAUDE.md")


@app.get("/api/agents/{name}/file")
async def api_agent_file(name: str, filename: str, project: str = ""):
    if not AGENT_NAME_RE.match(name):
        raise HTTPException(400, "invalid agent name")
    if filename not in AGENT_VIEWABLE_FILES:
        raise HTTPException(400, f"filename must be one of {AGENT_VIEWABLE_FILES}")
    # Risolvi sia agenti di PROGETTO (<ws>/.anjawiki/agents/) sia hub (<hub>/agents/).
    candidates = []
    if project and "/" not in project and ".." not in project and HUB_PATH:
        candidates.append(HUB_PATH / "workspaces" / project / ".anjawiki" / "agents" / name / filename)
    hub_root = _agents_root()
    if hub_root:
        candidates.append(hub_root / name / filename)
    for f in candidates:
        if f.is_file():
            return PlainTextResponse(f.read_text(encoding="utf-8"))
    raise HTTPException(404, f"{filename} not found in agent '{name}'")


@app.get("/api/agents/{name}/sessions")
async def api_agent_sessions_list(name: str, project: str = ""):
    if not AGENT_NAME_RE.match(name):
        raise HTTPException(400, "invalid agent name")
    agent_dir = None
    if project and "/" not in project and ".." not in project and HUB_PATH:
        cand = HUB_PATH / "workspaces" / project / ".anjawiki" / "agents" / name
        if cand.is_dir():
            agent_dir = cand
    if agent_dir is None:
        root = _agents_root()
        if root and (root / name).is_dir():
            agent_dir = root / name
    if agent_dir is None:
        raise HTTPException(404, "agent not found")
    sessions_dir = agent_dir / "sessions"
    out = []
    if sessions_dir.is_dir():
        for f in sorted(sessions_dir.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
            if f.name == "index.md":
                continue
            entry = {
                "id": f.stem,
                "path": str(f.relative_to(agent_dir)),
                "mtime": f.stat().st_mtime,
                "size_bytes": f.stat().st_size,
            }
            # estrai metadata da frontmatter (started/ended/duration)
            try:
                head = f.read_text(encoding="utf-8")[:2000]
                if head.startswith("---"):
                    end = head.find("\n---", 3)
                    if end > 0:
                        for line in head[3:end].split("\n"):
                            ls = line.strip()
                            for key in ("started", "ended", "duration", "end_reason", "messages_user", "messages_assistant"):
                                if ls.startswith(f"{key}:"):
                                    entry[key] = ls.split(":", 1)[1].strip()
            except Exception:
                pass
            out.append(entry)
    return JSONResponse({"sessions": out, "count": len(out)})


@app.get("/api/agents/{name}/session/{session_id}")
async def api_agent_session_read(name: str, session_id: str):
    if not AGENT_NAME_RE.match(name):
        raise HTTPException(400, "invalid agent name")
    if "/" in session_id or ".." in session_id:
        raise HTTPException(400, "invalid session id")
    root = _agents_root()
    if not root:
        raise HTTPException(500, "hub not configured")
    agent_dir = root / name
    # search in sessions/ recursively
    for f in (agent_dir / "sessions").rglob(f"{session_id}.md"):
        return PlainTextResponse(f.read_text(encoding="utf-8"))
    raise HTTPException(404, f"session '{session_id}' not found")


@app.delete("/api/agents/{name}")
async def api_agent_delete(name: str, request: Request):
    """Cancella un agent (rmtree). Safety: nessuna restrizione MVP, reversibile via re-create."""
    _require_admin(request)
    if not AGENT_NAME_RE.match(name):
        raise HTTPException(400, "invalid name")
    root = _agents_root()
    if not root:
        raise HTTPException(500, "hub not configured")
    agent_dir = root / name
    if not agent_dir.is_dir():
        raise HTTPException(404, "agent not found")
    import shutil
    shutil.rmtree(agent_dir)
    return JSONResponse({"status": "deleted", "name": name})


@app.patch("/api/agents/{name}")
async def api_agent_update(name: str, request: Request):
    """Update agent files. Body può contenere qualunque combinazione di:
      - `agents_md`: str → sovrascrive AGENTS.md
      - `soul_md`: str → sovrascrive SOUL.md
      - `tools_md`: str → sovrascrive TOOLS.md (di solito auto-generato, evitare)
      - `config_patch`: dict → merge in config.json (campi esistenti aggiornati)
    """
    _require_admin(request)
    if not AGENT_NAME_RE.match(name):
        raise HTTPException(400, "invalid name")
    root = _agents_root()
    if not root:
        raise HTTPException(500, "hub not configured")
    # hub-level prima, poi i team dei workspace. Due trappole: la dir hub può
    # esistere come solo mirror di sessioni (niente config → non è un agent), e i
    # pod hanno agent OMONIMI tra workspace (dev/analyst/…) → senza `project`
    # esplicito si patcherebbe il brand sbagliato: meglio 409 che indovinare.
    project = (request.query_params.get("project") or "").strip()
    agent_dir = root / name
    if not ((agent_dir / "config.json").is_file() or (agent_dir / "AGENTS.md").is_file()):
        ws_root = HUB_PATH / "workspaces" if HUB_PATH else None
        cands = []
        if ws_root and ws_root.is_dir():
            for ws in sorted(ws_root.iterdir()):
                if project and ws.name != project:
                    continue
                d = ws / ".anjawiki" / "agents" / name
                if (d / "config.json").is_file() or (d / "AGENTS.md").is_file():
                    cands.append((ws.name, d))
        if not cands:
            raise HTTPException(404, "agent not found")
        if len(cands) > 1:
            raise HTTPException(409, f"agent '{name}' exists in multiple workspaces "
                                     f"({[w for w, _ in cands]}): pass ?project=<workspace>")
        agent_dir = cands[0][1]
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "body must be a JSON object")

    written = []
    for key, fname in (("agents_md", "AGENTS.md"), ("soul_md", "SOUL.md"), ("tools_md", "TOOLS.md")):
        if key in body:
            val = body[key]
            if not isinstance(val, str):
                raise HTTPException(400, f"'{key}' must be string")
            (agent_dir / fname).write_text(val, encoding="utf-8")
            written.append(fname)

    if "config_patch" in body:
        patch = body["config_patch"]
        if not isinstance(patch, dict):
            raise HTTPException(400, "'config_patch' must be object")
        cfg_path = agent_dir / "config.json"
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}
        except Exception:
            cfg = {}
        for k, v in patch.items():
            if v is None:
                cfg.pop(k, None)
            else:
                cfg[k] = v
        cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        written.append("config.json")

    if not written:
        raise HTTPException(400, "no field to update (expected agents_md|soul_md|tools_md|config_patch)")

    return JSONResponse({"status": "updated", "name": name, "files_written": written})


@app.delete("/api/routines/{name}")
async def api_routine_delete(name: str, request: Request):
    """Cancella un yaml routine. Cerca in hub routines + workspace routines."""
    _require_admin(request)
    name = _safe_routine_name(name.strip())
    target = _routines_root() / f"{name}.yaml"
    if not target.is_file() and HUB_PATH:
        ws_root = HUB_PATH / "workspaces"
        if ws_root.is_dir():
            for ws in ws_root.iterdir():
                cand = ws / ".anjawiki" / "routines" / f"{name}.yaml"
                if cand.is_file():
                    target = cand
                    break
    if not target.is_file():
        raise HTTPException(404, f"routine '{name}' not found")
    target.unlink()
    return JSONResponse({"status": "deleted", "name": name, "file": target.name})


@app.get("/api/workspaces/{name}")
async def api_workspace_get(name: str):
    """Read-one di un workspace. Ritorna config + path + metadata + lista agents/routines/goals interni."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    ws_dir = HUB_PATH / "workspaces" / name
    if not ws_dir.is_dir():
        raise HTTPException(404, "workspace not found")
    target = ws_dir.resolve() if ws_dir.is_symlink() else ws_dir
    info: dict = {
        "name": name,
        "path": str(target),
        "is_symlink": ws_dir.is_symlink(),
        "type": "external" if ws_dir.is_symlink() else "internal",
    }
    # Optional config.json del workspace
    cfg = target / "config.json"
    if cfg.is_file():
        try:
            info["config"] = json.loads(cfg.read_text(encoding="utf-8"))
        except Exception:
            info["config"] = None
    # CLAUDE.md/AGENTS.md presenti?
    info["has_claude_md"] = (target / "CLAUDE.md").is_file()
    info["has_agents_md"] = (target / "AGENTS.md").is_file()
    # Agents, routines, goals interni
    anjawiki = target / ".anjawiki"
    info["agents"] = sorted([p.name for p in (anjawiki / "agents").iterdir()]) if (anjawiki / "agents").is_dir() else []
    info["routines"] = sorted([p.stem for p in (anjawiki / "routines").glob("*.yaml")]) if (anjawiki / "routines").is_dir() else []
    info["goals"] = sorted([p.stem for p in (anjawiki / "goals").glob("*.md")]) if (anjawiki / "goals").is_dir() else []
    return JSONResponse(info)


@app.patch("/api/skills/{name}")
async def api_skill_update(name: str, request: Request):
    """Update body di una skill esistente. Body: {content: str, scope?: "hub"|"user-global"}.
    Cerca la skill nello scope writable corrispondente, riscrive SKILL.md."""
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    if not isinstance(body, dict) or not isinstance(body.get("content"), str):
        raise HTTPException(400, "body must be {content: string, scope?: string}")
    scope = body.get("scope") or "hub"
    try:
        import skills_catalog
    except ImportError:
        raise HTTPException(500, "skills_catalog module unavailable")
    if scope == "hub" and HUB_PATH:
        target_dir = HUB_PATH / "skills" / name
    elif scope == "user-global":
        target_dir = Path.home() / ".anja" / "skills" / name
    else:
        raise HTTPException(400, f"unsupported scope '{scope}' (use 'hub' or 'user-global')")
    if not target_dir.is_dir():
        raise HTTPException(404, f"skill '{name}' not found in scope '{scope}'")
    skill_md = target_dir / "SKILL.md"
    # Backup + write
    if skill_md.is_file():
        skill_md.with_suffix(".md.bak").write_text(skill_md.read_text(encoding="utf-8"), encoding="utf-8")
    skill_md.write_text(body["content"], encoding="utf-8")
    return JSONResponse({"status": "updated", "name": name, "scope": scope, "path": str(skill_md)})


# ============================================================
# M-Mem 5 — Memory Inspector API
# ============================================================

# Triade filenames + valid scopes
TRIADE_FILES = ("AGENTS.md", "SOUL.md", "TOOLS.md")
VALID_MEM_SCOPES = ("hub",)  # MVP: hub + project; agent in Fase 9


def _mem_resolve_root(scope: str, target: str = "") -> Optional[Path]:
    """scope='hub' → HUB_PATH; scope='project' → resolve_project_path(target, HUB_PATH)."""
    if not HUB_PATH:
        return None
    if scope == "hub":
        return HUB_PATH
    if scope == "project":
        if not target:
            return None
        return resolve_project_path(target, HUB_PATH)
    return None


def _est_tokens(s: str) -> int:
    return int(len(s) / 3.5)


def _load_context_loader():
    """Lazy import context_loader dal plugin anjadev installato."""
    try:
        import importlib.util
        cl_path = ANJADEV_DIR / "scripts" / "context_loader.py"
        if not cl_path.is_file():
            print(f"[memory-inspector] context_loader.py non trovato: {cl_path}")
            return None
        spec = importlib.util.spec_from_file_location("context_loader", str(cl_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as e:
        print(f"[memory-inspector] context_loader load failed: {e}")
        return None


@app.get("/api/memory/inspect")
async def api_memory_inspect(scope: str = "hub", target: str = ""):
    """Overview triade + tier estimates + sessions count per scope/target."""
    root = _mem_resolve_root(scope, target)
    if not root or not root.is_dir():
        raise HTTPException(404, f"scope/target not found: {scope}/{target}")

    triade = {}
    for fname in TRIADE_FILES:
        f = root / fname
        triade[fname] = {
            "exists": f.is_file() or f.is_symlink(),
            "size_bytes": f.stat().st_size if f.is_file() else 0,
            "modified": f.stat().st_mtime if f.is_file() else None,
        }

    # tier estimates via context_loader
    cl = _load_context_loader()
    tiers = {"hot_tokens": 0, "warm_tokens": 0, "hot_truncated": False, "warm_truncated": False, "config": {}}
    if cl:
        ctx = cl.build_session_context(root, user_prompt=None)
        tiers = {
            "hot_tokens": _est_tokens(ctx.get("hot", "")),
            "warm_tokens": _est_tokens(ctx.get("warm", "")),
            "hot_truncated": ctx.get("hot_truncated", False),
            "warm_truncated": ctx.get("warm_truncated", False),
            "config": ctx.get("config", {}),
        }

    # sessions count
    sessions_dirs = [root / ".anjawiki" / "wiki" / "sessions", root / "wiki" / "sessions"]
    sessions_count = 0
    for sd in sessions_dirs:
        if sd.is_dir():
            for f in sd.rglob("*.md"):
                if f.name != "index.md":
                    sessions_count += 1

    # mcp count
    mcp_count = 0
    mcp_path = root / ".mcp.json"
    if mcp_path.is_file():
        try:
            data = json.loads(mcp_path.read_text(encoding="utf-8"))
            mcp_count = len(data.get("mcpServers", {}))
        except Exception:
            pass

    # Fase 12 — User profiles (solo scope hub)
    users_info = []
    default_user_slug = None
    if scope == "hub":
        users_dir = root / "users"
        try:
            cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))
            default_user_slug = cfg.get("default_user")
        except Exception:
            pass
        if users_dir.is_dir():
            seen = set()
            for f in sorted(users_dir.glob("*.md")):
                slug = f.stem
                if slug.endswith("-detail"):
                    continue
                if slug in seen:
                    continue
                seen.add(slug)
                hot = users_dir / f"{slug}.md"
                detail = users_dir / f"{slug}-detail.md"
                users_info.append({
                    "slug": slug,
                    "is_default": (slug == default_user_slug),
                    "hot": {
                        "exists": hot.is_file(),
                        "size_bytes": hot.stat().st_size if hot.is_file() else 0,
                    },
                    "detail": {
                        "exists": detail.is_file(),
                        "size_bytes": detail.stat().st_size if detail.is_file() else 0,
                    },
                })

    return JSONResponse({
        "scope": scope,
        "target": target,
        "root": str(root),
        "triade": triade,
        "tiers": tiers,
        "sessions_count": sessions_count,
        "mcp_servers_count": mcp_count,
        "users": users_info,
        "default_user": default_user_slug,
    })


@app.get("/api/memory/file")
async def api_memory_file_get(scope: str = "hub", target: str = "", filename: str = ""):
    """Read raw markdown of AGENTS/SOUL/TOOLS.md."""
    if filename not in TRIADE_FILES:
        raise HTTPException(400, f"filename must be one of {TRIADE_FILES}")
    root = _mem_resolve_root(scope, target)
    if not root:
        raise HTTPException(404, "scope/target not found")
    f = root / filename
    if not f.is_file():
        raise HTTPException(404, f"{filename} not found in {root}")
    return PlainTextResponse(f.read_text(encoding="utf-8"))


@app.put("/api/memory/file")
async def api_memory_file_put(request: Request):
    """Update raw markdown of AGENTS/SOUL/TOOLS.md.
    Body: {scope, target?, filename, content}
    NOTE: TOOLS.md è marked auto_generated — overwrite manuale possibile ma sconsigliato.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    scope = body.get("scope", "hub")
    target = body.get("target", "")
    filename = body.get("filename", "")
    content = body.get("content", "")

    if filename not in TRIADE_FILES:
        raise HTTPException(400, f"filename must be one of {TRIADE_FILES}")
    if content is None:
        raise HTTPException(400, "content required")
    root = _mem_resolve_root(scope, target)
    if not root:
        raise HTTPException(404, "scope/target not found")
    f = root / filename
    f.write_text(content, encoding="utf-8")
    return JSONResponse({"status": "saved", "path": str(f), "size": len(content)})


def _safe_user_slug(slug: str) -> str:
    """Slug utente path-safe: nessun separatore/traversal (verrebbe usato in
    HUB_PATH/users/<slug>.md → leggere/scrivere .md arbitrari altrimenti)."""
    slug = (slug or "").strip()
    if not slug or "/" in slug or "\\" in slug or ".." in slug or slug.startswith("."):
        raise HTTPException(400, "invalid slug")
    return slug


@app.get("/api/memory/user")
async def api_memory_user_get(slug: str = "", detail: bool = False):
    """Read user profile HOT (default) o DETAIL (Fase 12).
    Senza slug → usa default_user dal hub config.json.
    """
    if not HUB_PATH:
        raise HTTPException(404, "no hub")
    if not slug:
        try:
            cfg = json.loads((HUB_PATH / "config.json").read_text(encoding="utf-8"))
            slug = cfg.get("default_user", "")
        except Exception:
            pass
    if not slug:
        raise HTTPException(404, "no default_user set; pass ?slug=<name>")
    slug = _safe_user_slug(slug)
    suffix = "-detail" if detail else ""
    f = HUB_PATH / "users" / f"{slug}{suffix}.md"
    if not f.is_file():
        raise HTTPException(404, f"user file not found: {f.name}")
    return PlainTextResponse(f.read_text(encoding="utf-8"))


@app.put("/api/memory/user")
async def api_memory_user_put(request: Request):
    """Update user profile HOT o DETAIL.
    Body: {slug?, detail?, content}
    """
    if not HUB_PATH:
        raise HTTPException(404, "no hub")
    _require_admin(request)   # scrive l'identità utente iniettata negli agenti
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    slug = body.get("slug") or ""
    detail = bool(body.get("detail", False))
    content = body.get("content", "")
    if not slug:
        try:
            cfg = json.loads((HUB_PATH / "config.json").read_text(encoding="utf-8"))
            slug = cfg.get("default_user", "")
        except Exception:
            pass
    if not slug:
        raise HTTPException(400, "no default_user set; pass slug in body")
    slug = _safe_user_slug(slug)
    suffix = "-detail" if detail else ""
    f = HUB_PATH / "users" / f"{slug}{suffix}.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content, encoding="utf-8")
    return JSONResponse({"status": "saved", "path": str(f), "size": len(content)})


@app.post("/api/memory/regenerate-tools-md")
async def api_memory_regenerate_tools(request: Request):
    """Re-run tools_md.py per il scope/target. Aggiorna TOOLS.md."""
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    scope = body.get("scope", "hub")
    target = body.get("target", "")
    root = _mem_resolve_root(scope, target)
    if not root:
        raise HTTPException(404, "scope/target not found")

    script = ANJADEV_DIR / "scripts" / "tools_md.py"
    if not script.is_file():
        raise HTTPException(500, f"tools_md.py not found: {script}")

    flag = "--target" if scope == "project" else "--hub"
    try:
        r = subprocess.run(
            [sys.executable, str(script), flag, str(root)],
            capture_output=True, text=True, timeout=15,
        )
        return JSONResponse({
            "status": "regenerated" if r.returncode == 0 else "error",
            "stdout": r.stdout.strip(),
            "stderr": r.stderr.strip(),
            "exit": r.returncode,
        })
    except Exception as e:
        raise HTTPException(500, f"regenerate failed: {e}")


@app.post("/api/memory/preview-injection")
async def api_memory_preview_injection(request: Request):
    """Preview del context HOT+WARM che verrebbe iniettato.
    Body: {scope, target?, user_prompt?, hot_budget?, warm_budget?}
    """
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    scope = body.get("scope", "hub")
    target = body.get("target", "")
    user_prompt = body.get("user_prompt", "")

    root = _mem_resolve_root(scope, target)
    if not root:
        raise HTTPException(404, "scope/target not found")

    cl = _load_context_loader()
    if not cl:
        raise HTTPException(500, "context_loader not available")

    overrides = {}
    if "hot_budget" in body:
        overrides["hot_budget_tokens"] = int(body["hot_budget"])
    if "warm_budget" in body:
        overrides["warm_budget_tokens"] = int(body["warm_budget"])

    ctx = cl.build_session_context(root, user_prompt=user_prompt or None, config_override=overrides)
    return JSONResponse({
        "hot": ctx.get("hot", ""),
        "warm": ctx.get("warm", ""),
        "tokens_estimated": ctx.get("tokens_estimated", 0),
        "tokens_budget": ctx.get("tokens_budget", 0),
        "hot_truncated": ctx.get("hot_truncated", False),
        "warm_truncated": ctx.get("warm_truncated", False),
        "config": ctx.get("config", {}),
        "formatted": cl.format_for_prompt(ctx),
    })


# ============================================================
# M2 — Action endpoints (POST)
# ============================================================

def _spawn_script(script: Path, args: list, timeout: int = 60) -> dict:
    """Spawn a script subprocess, return result dict."""
    try:
        result = subprocess.run(
            ["python3", str(script)] + args,
            capture_output=True, text=True, timeout=timeout,
        )
        return {
            "status": "success" if result.returncode == 0 else "error",
            "exit": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "exit": -1, "stdout": "", "stderr": f"timeout after {timeout}s"}
    except FileNotFoundError as e:
        return {"status": "error", "exit": -1, "stdout": "", "stderr": str(e)}


@app.post("/api/action/sync")
async def api_action_sync(request: Request):
    """Spawn sync.py. Body: {"name"?: "<project>"} or empty for --all."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    try:
        body = await request.json()
    except Exception:
        body = {}
    name = body.get("name") if isinstance(body, dict) else None

    sync_script = ANJA_HUB_DIR / "scripts" / "sync.py"
    args = ["--hub", str(HUB_PATH)]
    if name:
        if "/" in name or ".." in name:
            raise HTTPException(400, "invalid name")
        args.extend(["--name", name])
    else:
        args.append("--all")

    return JSONResponse(_spawn_script(sync_script, args, timeout=60))


@app.post("/api/action/lint-hub")
async def api_action_lint_hub():
    """Spawn lint_hub.py. Output JSON parsato per restituirlo strutturato."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    lint_script = ANJA_HUB_DIR / "scripts" / "lint_hub.py"
    result = _spawn_script(lint_script, ["--hub", str(HUB_PATH)], timeout=30)
    # Try parse JSON output
    if result["status"] == "success" and result["stdout"]:
        try:
            result["data"] = json.loads(result["stdout"])
        except json.JSONDecodeError:
            pass
    return JSONResponse(result)


@app.post("/api/action/aggregate-sessions")
async def api_action_aggregate_sessions():
    """Spawn aggregate_sessions.py."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    script = ANJA_HUB_DIR / "scripts" / "aggregate_sessions.py"
    return JSONResponse(_spawn_script(script, ["--hub", str(HUB_PATH)], timeout=30))


@app.post("/api/action/lint-project")
async def api_action_lint_project(request: Request):
    """Spawn lint_checks.py per un progetto specifico."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    try:
        body = await request.json()
    except Exception:
        body = {}
    name = body.get("name") if isinstance(body, dict) else None
    if not name or "/" in name or ".." in name:
        raise HTTPException(400, "invalid name")

    wiki_root = HUB_PATH / "workspaces" / name / "wiki"
    if not wiki_root.exists():
        raise HTTPException(404, f"project wiki not found: {name}")

    lint_script = ANJADEV_DIR / "scripts" / "lint_checks.py"
    result = _spawn_script(lint_script, ["--wiki-root", str(wiki_root)], timeout=30)
    if result["status"] == "success" and result["stdout"]:
        try:
            result["data"] = json.loads(result["stdout"])
        except json.JSONDecodeError:
            pass
    return JSONResponse(result)


# ============================================================
# M3 — Chat WebSocket via claude-agent-sdk
# ============================================================

def _read_workspace_meta_yaml(name: str) -> dict:
    """Legge `<hub>/workspaces/<name>.meta.yaml` (Fase 22). Ritorna dict (kind, responsabile, type, ...)."""
    if not HUB_PATH:
        return {}
    meta_path = HUB_PATH / "workspaces" / f"{name}.meta.yaml"
    if not meta_path.is_file():
        return {}
    out = {}
    try:
        for line in meta_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if ":" in line and not line.startswith("#"):
                k, v = line.split(":", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return out


def _read_workspace_kind(name: str) -> str:
    """Legge `kind` dal file `<hub>/workspaces/<name>.meta.yaml`. Default: detection da path."""
    meta = _read_workspace_meta_yaml(name)
    if meta.get("kind"):
        return meta["kind"]
    if not HUB_PATH:
        return "external"
    p = HUB_PATH / "workspaces" / name
    if p.is_symlink():
        return "external"
    if p.is_dir():
        return "internal"
    return "external"


def _build_projects_context() -> list:
    """Read registry projects/workspaces for system prompt context (Fase 22).

    Augmenta ogni entry con `kind: hub | internal | external` letto da meta.yaml.
    Nome conservato per back-compat con codice esistente.
    """
    if not HUB_PATH:
        return []
    try:
        with (HUB_PATH / "config" / "projects.json").open() as f:
            registry = json.load(f)
        projects = registry.get("projects", [])
        # Augment with kind
        for p in projects:
            name = p.get("name", "")
            if name:
                p["kind"] = _read_workspace_kind(name)
        return projects
    except Exception:
        return []


# Alias semantico Fase 22 (uso preferito d'ora in poi)
def _build_workspaces_context() -> list:
    return _build_projects_context()


def _encoded_cc_path(project_real_path: Path) -> Path:
    """Calcola il path encoded di Claude Code per un progetto.
    Il path /Users/foo/bar diventa -Users-foo-bar (sostituisci / con -, mantieni il primo come -)."""
    s = str(project_real_path).lstrip("/")
    encoded = "-" + s.replace("/", "-")
    return Path.home() / ".claude" / "projects" / encoded


def _resolve_project_cc_dir(project_name: str) -> Optional[Path]:
    """Risolve la directory CC del progetto via registry → real path → encoded."""
    if not HUB_PATH:
        return None
    try:
        with (HUB_PATH / "config" / "projects.json").open() as f:
            registry = json.load(f)
        for p in registry.get("projects", []):
            if p.get("name") == project_name and p.get("location", {}).get("kind") == "local":
                proj_path = Path(p["location"]["path"])
                cc_dir = _encoded_cc_path(proj_path)
                if cc_dir.is_dir():
                    return cc_dir
    except Exception:
        pass
    return None


def _parse_cc_session_summary(jsonl_path: Path) -> Optional[dict]:
    """
    Parse summary di una sessione CC: id, first_user_msg preview, msg_count, mtime.
    Skip se sessione vuota / no user msg.
    """
    try:
        first_user = ""
        msg_count = 0
        first_ts = ""
        last_ts = ""
        with jsonl_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = d.get("type")
                if t in ("user", "assistant"):
                    msg_count += 1
                    ts = d.get("timestamp", "")
                    if not first_ts:
                        first_ts = ts
                    last_ts = ts
                    if t == "user" and not first_user:
                        msg = d.get("message", {})
                        content = msg.get("content", "") if isinstance(msg, dict) else ""
                        if isinstance(content, str):
                            first_user = content[:120]
                        elif isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    first_user = block.get("text", "")[:120]
                                    break
        if msg_count == 0:
            return None
        return {
            "id": jsonl_path.stem,
            "first_user": first_user or "(no text)",
            "msg_count": msg_count,
            "first_ts": first_ts,
            "last_ts": last_ts,
            "mtime": jsonl_path.stat().st_mtime,
            "size": jsonl_path.stat().st_size,
        }
    except Exception:
        return None


def _parse_cc_session_full(jsonl_path: Path) -> list:
    """
    Parse conversazione completa di una sessione CC.
    Ritorna lista di {role, content, timestamp} pronta per il rendering chat.
    """
    messages = []
    try:
        with jsonl_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = d.get("type")
                if t not in ("user", "assistant"):
                    continue
                ts = d.get("timestamp", "")
                msg = d.get("message", {}) if isinstance(d.get("message"), dict) else {}
                content = msg.get("content", "")
                # Normalizza in stringa
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    parts = []
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        bt = block.get("type")
                        if bt == "text":
                            parts.append(block.get("text", ""))
                        elif bt == "thinking":
                            # Skip thinking blocks (interno, non utile per read-only review)
                            continue
                        elif bt == "tool_use":
                            tool_name = block.get("name", "?")
                            parts.append(f"\n\n<span class=\"tool-chip\">🔧 {tool_name}</span>\n")
                        elif bt == "tool_result":
                            content_str = block.get("content", "")
                            if isinstance(content_str, list):
                                content_str = "\n".join(b.get("text", "") for b in content_str if isinstance(b, dict))
                            preview = (content_str or "")[:300]
                            parts.append(f"\n\n_tool result:_ ```\n{preview}\n```\n")
                    text = "".join(parts)
                else:
                    text = ""
                if not text.strip():
                    continue
                messages.append({
                    "role": "user" if t == "user" else "claude",
                    "content": text,
                    "timestamp": ts,
                })
    except Exception:
        pass
    return messages


FILE_TREE_EXCLUDE = {
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    "dist", "build", ".next", ".nuxt", "target", ".pytest_cache",
    ".DS_Store", "coverage", ".cache", ".idea", ".vscode",
    ".playwright-mcp",  # cache playwright
    ".secrets.env", ".secrets.env.example",  # mai esporre i vault nel tree
}
FILE_TREE_MAX_DEPTH = 3
FILE_TREE_MAX_FILES = 2000
FILE_TREE_MAX_PER_DIR = 200  # max children per directory


def _build_file_tree(root: Path, max_depth: int = FILE_TREE_MAX_DEPTH) -> list:
    """Build a tree-like structure of project files. Excludes common cruft."""
    counter = [0]

    def walk(path: Path, depth: int) -> list:
        if counter[0] >= FILE_TREE_MAX_FILES or depth > max_depth:
            return []
        out = []
        try:
            items = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except (PermissionError, FileNotFoundError):
            return []
        in_dir = 0
        for item in items:
            if counter[0] >= FILE_TREE_MAX_FILES:
                break
            if in_dir >= FILE_TREE_MAX_PER_DIR:
                out.append({"name": f"… +{len(items) - in_dir} more", "type": "more"})
                break
            if item.name in FILE_TREE_EXCLUDE:
                continue
            in_dir += 1
            if item.is_dir():
                # solo i FILE contano nel counter globale, non le dir
                node = {"name": item.name, "type": "dir"}
                children = walk(item, depth + 1)
                if children:
                    node["children"] = children
                out.append(node)
            elif item.is_file():
                counter[0] += 1
                try:
                    size = item.stat().st_size
                except OSError:
                    size = 0
                if size > 10 * 1024 * 1024:
                    continue
                out.append({"name": item.name, "type": "file", "size": size})
        return out

    return walk(root, 0)


@app.get("/api/project/{project}/files")
async def api_project_files(project: str):
    """Restituisce il file tree del progetto (escluse dir di build/cache, file >10MB)."""
    if "/" in project or ".." in project:
        raise HTTPException(400, "invalid name")
    cc_dir = None
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    try:
        with (HUB_PATH / "config" / "projects.json").open() as f:
            registry = json.load(f)
        for p in registry.get("projects", []):
            if p.get("name") == project and p.get("location", {}).get("kind") == "local":
                proj_path = Path(p["location"]["path"])
                if not proj_path.is_dir():
                    raise HTTPException(404, "project path not found")
                tree = _build_file_tree(proj_path)
                return JSONResponse({
                    "tree": tree,
                    "root": str(proj_path),
                    "truncated": False,  # TODO: detect via counter
                })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
    raise HTTPException(404, "project not found in registry")


@app.get("/api/project/{project}/cc-sessions")
async def api_project_cc_sessions(project: str, limit: int = 30):
    """Lista sessioni Claude Code reali del progetto, sorted by mtime desc."""
    if "/" in project or ".." in project:
        raise HTTPException(400, "invalid name")
    cc_dir = _resolve_project_cc_dir(project)
    if cc_dir is None:
        return JSONResponse({"sessions": [], "_note": "no CC project dir"})

    files = sorted(
        cc_dir.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]

    sessions = []
    for f in files:
        info = _parse_cc_session_summary(f)
        if info:
            sessions.append(info)
    return JSONResponse({"sessions": sessions, "total_files": len(files)})


@app.get("/api/project/{project}/cc-sessions/{sid}")
async def api_project_cc_session_detail(project: str, sid: str):
    """Carica conversazione completa di una sessione CC (read-only)."""
    if "/" in project or ".." in project or "/" in sid or ".." in sid:
        raise HTTPException(400, "invalid name")
    cc_dir = _resolve_project_cc_dir(project)
    if cc_dir is None:
        raise HTTPException(404, "no CC dir for project")
    jsonl = cc_dir / f"{sid}.jsonl"
    if not jsonl.is_file():
        raise HTTPException(404, "session not found")
    messages = _parse_cc_session_full(jsonl)
    return JSONResponse({
        "id": sid,
        "messages": messages,
        "msg_count": len(messages),
    })


@app.get("/api/project/{project}/context")
async def api_project_context(project: str):
    """Storico recente del progetto: log entries + sessions + conv count + recent files."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    if "/" in project or ".." in project:
        raise HTTPException(400, "invalid name")

    proj_link = HUB_PATH / "workspaces" / project
    if not proj_link.exists():
        raise HTTPException(404, "project not found")

    # log entries (latest first)
    log_path = proj_link / "wiki" / "log.md"
    log_entries = []
    if log_path.is_file():
        text = log_path.read_text(encoding="utf-8")
        pattern = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})\] (\w[\w-]*) \| (.+?)$", re.M)
        all_entries = pattern.findall(text)
        for d, t, desc in all_entries[-20:][::-1]:
            log_entries.append({"date": d, "type": t, "desc": desc})

    # sessions
    sessions_dir = proj_link / "wiki" / "sessions"
    sessions = []
    if sessions_dir.is_dir():
        files = sorted(sessions_dir.glob("*.md"), reverse=True)[:10]
        for f in files:
            sessions.append({
                "date": f.stem,
                "size": f.stat().st_size,
                "modified": f.stat().st_mtime,
            })

    # conversations count for this project
    conv_count = 0
    chat_mod = _get_chat_module()
    if chat_mod:
        all_convs = chat_mod.list_conversations(WEBAPP_DIR)
        for c in all_convs:
            if c.get("scope") == f"project:{project}":
                conv_count += 1

    return JSONResponse({
        "log_entries": log_entries,
        "sessions": sessions,
        "conversations": conv_count,
    })


# ============================================================
# Auto-ingest daemon (Fase 13+ — Plugin enrichment 1)
# ============================================================

AUTO_INGEST_DAEMON = None  # type: ignore  # Optional[AutoIngestDaemon]


async def _auto_ingest_on_changes(project_name: str, changes: list, cfg: dict):
    """Callback chiamato dal daemon quando rileva cambi.

    Mode 'active' → auto-run /anja-ingest sui file.
    Mode 'passive' → solo notify.
    """
    mode = cfg.get("mode", "passive")
    root = _project_root(project_name)
    files_to_ingest = [c["path"] for c in changes if c.get("action") != "deleted"]

    if mode == "active" and root and files_to_ingest:
        print(f"[auto_ingest] active mode: running /anja-ingest for {len(files_to_ingest)} files in {project_name}")
        try:
            result = await _run_anja_ingest(root, files_to_ingest, timeout_sec=600)
            ingested_set = set(result.get("files_ingested", []))
            if ingested_set:
                from auto_ingest_daemon import _load_pending, _save_pending
                pending = _load_pending(root)
                pending["files"] = [f for f in pending.get("files", []) if f["path"] not in ingested_set]
                pending["last_updated"] = time.time()
                _save_pending(root, pending)
                print(f"[auto_ingest] auto-ingested {len(ingested_set)} files in {project_name}")
        except Exception as e:
            print(f"[auto_ingest] active mode error: {e}")

    # Telegram notify (entrambi i mode)
    if cfg.get("notify_telegram") and TELEGRAM_DAEMON and TELEGRAM_DAEMON.token:
        chat_id = cfg.get("notify_telegram_chat_id")
        if not chat_id:
            allowed = (TELEGRAM_DAEMON.config or {}).get("allowed_chat_ids", [])
            if allowed:
                chat_id = allowed[0]
        if chat_id:
            try:
                from telegram_daemon import send_message as _tg_send
                paths = [c["path"] for c in changes[:5]]
                more = f" + {len(changes) - 5} more" if len(changes) > 5 else ""
                msg = (f"📂 *Auto-ingest* `{project_name}`\n\n"
                       f"{len(changes)} files detected:\n"
                       + "\n".join(f"• `{p}`" for p in paths) + more
                       + "\n\nOpen webapp → Project → Settings to run the ingest.")
                await _tg_send(TELEGRAM_DAEMON.token, int(chat_id), msg)
            except Exception as e:
                print(f"[auto_ingest] telegram notify error: {e}")


@app.on_event("startup")
async def _startup_auto_ingest():
    global AUTO_INGEST_DAEMON
    if not HUB_PATH:
        return
    try:
        from auto_ingest_daemon import AutoIngestDaemon
        AUTO_INGEST_DAEMON = AutoIngestDaemon(
            projects_provider=_build_projects_context,
            on_changes=_auto_ingest_on_changes,
        )
        await AUTO_INGEST_DAEMON.start()
    except Exception as e:
        print(f"[auto_ingest] startup error: {e}")


# Fase 15.2 — Kanban dispatcher daemon
KANBAN_DISPATCHER = None  # type: ignore


@app.on_event("startup")
async def _startup_kanban_dispatcher():
    global KANBAN_DISPATCHER
    if not HUB_PATH:
        return
    try:
        from kanban_dispatcher import KanbanDispatcher
        KANBAN_DISPATCHER = KanbanDispatcher(
            hub_path=HUB_PATH,
            on_event=_kanban_broadcast,
        )
        await KANBAN_DISPATCHER.start()
    except Exception as e:
        print(f"[kanban_dispatcher] startup error: {e}")


@app.on_event("shutdown")
async def _shutdown_kanban_dispatcher():
    global KANBAN_DISPATCHER
    if KANBAN_DISPATCHER:
        try:
            await KANBAN_DISPATCHER.stop()
        except Exception:
            pass


@app.on_event("shutdown")
async def _shutdown_auto_ingest():
    global AUTO_INGEST_DAEMON
    if AUTO_INGEST_DAEMON:
        try:
            await AUTO_INGEST_DAEMON.stop()
        except Exception:
            pass


@app.get("/api/project/auto-ingest/status")
async def api_auto_ingest_status(project: str = ""):
    """Returns config + pending files + daemon status per un progetto."""
    if not project:
        raise HTTPException(400, "project required")
    root = _project_root(project)
    if not root:
        raise HTTPException(404, f"project '{project}' not found")
    from auto_ingest_daemon import _load_project_config, _load_pending
    cfg = _load_project_config(root)
    pending = _load_pending(root)
    daemon_status = AUTO_INGEST_DAEMON.status() if AUTO_INGEST_DAEMON else {"running": False}
    return JSONResponse({
        "project": project,
        "project_root": str(root),
        "config": cfg,
        "pending": pending,
        "daemon": daemon_status,
    })


@app.post("/api/project/auto-ingest/config")
async def api_auto_ingest_config_post(request: Request):
    """Save config auto-ingest per un progetto. Body: {project, ...config fields}."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    project = body.get("project")
    if not project:
        raise HTTPException(400, "project required")
    _require_ws_access(request, project)
    root = _project_root(project)
    if not root:
        raise HTTPException(404, "project not found")
    from auto_ingest_daemon import _load_project_config, _save_project_config
    cur = _load_project_config(root)
    for key in ("enabled", "mode", "poll_interval_sec", "whitelist", "exclude_dirs",
                "notify_telegram", "notify_telegram_chat_id"):
        if key in body:
            cur[key] = body[key]
    _save_project_config(root, cur)
    return JSONResponse({"ok": True, "config": cur})


async def _run_anja_ingest(project_root: Path, files: list[str], timeout_sec: int = 600) -> dict:
    """Spawn `claude --print "/anja-ingest <file>"` subprocess per ogni file.

    Returns {ok, files_ingested, errors}. Async (non blocca event loop).
    """
    import shutil
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return {"ok": False, "error": "claude CLI not found in PATH"}

    ingested = []
    errors = []
    loop = asyncio.get_running_loop()
    for f in files:
        # Path safety: deve essere dentro project_root
        target = (project_root / f).resolve()
        try:
            target.relative_to(project_root)
        except ValueError:
            errors.append({"file": f, "error": "path outside project"})
            continue
        if not target.is_file():
            errors.append({"file": f, "error": "file not found"})
            continue

        prompt = f"/anja-ingest {f} --no-discuss"
        def _spawn():
            return subprocess.run(
                [claude_bin, "--print", prompt],
                cwd=str(project_root),
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
        try:
            r = await loop.run_in_executor(None, _spawn)
            if r.returncode == 0:
                ingested.append(f)
            else:
                errors.append({"file": f, "error": f"exit {r.returncode}: {r.stderr[:200]}"})
        except subprocess.TimeoutExpired:
            errors.append({"file": f, "error": f"timeout {timeout_sec}s"})
        except Exception as e:
            errors.append({"file": f, "error": f"{type(e).__name__}: {e}"})

    return {"ok": True, "files_ingested": ingested, "errors": errors}


@app.post("/api/project/auto-ingest/run")
async def api_auto_ingest_run(request: Request):
    """Esegui /anja-ingest sui file pending. Body: {project, files?: [...]}.
    Se files omesso → ingest tutti i pending.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    project = body.get("project")
    if not project:
        raise HTTPException(400, "project required")
    _require_ws_access(request, project)
    root = _project_root(project)
    if not root:
        raise HTTPException(404, "project not found")

    from auto_ingest_daemon import _load_pending, _save_pending
    pending = _load_pending(root)
    requested = body.get("files")
    if requested:
        files = [f for f in requested if isinstance(f, str)]
    else:
        files = [f["path"] for f in pending.get("files", []) if f.get("action") != "deleted"]

    if not files:
        return JSONResponse({"ok": True, "files_ingested": [], "errors": [], "_note": "no files to ingest"})

    # Run subprocess (blocca finché finisce ma async non event loop)
    result = await _run_anja_ingest(root, files)

    # Rimuovi i file ingested dalla pending queue
    ingested_set = set(result.get("files_ingested", []))
    if ingested_set:
        pending["files"] = [f for f in pending.get("files", []) if f["path"] not in ingested_set]
        pending["last_updated"] = time.time()
        _save_pending(root, pending)
    return JSONResponse(result)


@app.post("/api/project/auto-ingest/clear-pending")
async def api_auto_ingest_clear_pending(request: Request):
    """Svuota la pending queue (es. dopo aver fatto ingest manuale)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    project = body.get("project") or ""
    root = _project_root(project)
    if not root:
        raise HTTPException(404, "project not found")
    from auto_ingest_daemon import _save_pending
    _save_pending(root, {"files": [], "last_updated": time.time()})
    return JSONResponse({"ok": True})


# ============================================================
# AI-suggested questions (Fase 13+ — Plugin enrichment 2)
# ============================================================

SUGGESTED_Q_CACHE_TTL = 86400  # 24h
SUGGESTED_Q_FILENAME = ".suggested_questions.json"

SUGGESTED_Q_SYSTEM_PROMPT = """Sei un esperto di knowledge management. Dato il wiki di un progetto,
genera 5 DOMANDE INTERESSANTI che un membro del team chiederebbe per scoprire o ricordare
qualcosa di importante che il wiki contiene.

CRITERI:
- Domande SPECIFICHE al progetto (non generiche tipo "cosa fa X")
- Domande che richiedono di COLLEGARE 2-3 concetti/entity tra loro
- Domande su DECISIONI, TRADEOFF, ARCHITETTURA, PATTERN documentati
- Italiano naturale, tono colloquiale
- Ogni domanda 6-15 parole, finisce con punto interrogativo

OUTPUT: SOLO una lista JSON di 5 stringhe, niente altro.
Esempio: ["Come si confronta X con Y nel pattern Z?", "Quale tradeoff abbiamo scelto per W?", ...]"""


def _load_suggested_questions_cache(project_root: Path) -> Optional[dict]:
    f = project_root / ".anjawiki" / SUGGESTED_Q_FILENAME
    if not f.is_file():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if (time.time() - data.get("generated_at", 0)) < SUGGESTED_Q_CACHE_TTL:
            return data
    except Exception:
        pass
    return None


def _save_suggested_questions_cache(project_root: Path, data: dict) -> None:
    f = project_root / ".anjawiki" / SUGGESTED_Q_FILENAME
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _sample_wiki_for_questions(project_root: Path, max_pages: int = 4) -> str:
    """Compone un sample del wiki: index.md (truncato) + 3-4 pagine random."""
    wiki_root = project_root / ".anjawiki" / "wiki"
    if not wiki_root.is_dir():
        return ""

    parts: list[str] = []

    # Index
    idx = wiki_root / "index.md"
    if idx.is_file():
        try:
            txt = idx.read_text(encoding="utf-8", errors="replace")[:3000]
            parts.append(f"# index.md\n\n{txt}")
        except Exception:
            pass

    # Recent pages (escludendo sessions/log/index)
    pages: list[Path] = []
    for p in wiki_root.rglob("*.md"):
        rel = p.relative_to(wiki_root)
        parts_rel = rel.parts
        if any(x in ("sessions",) for x in parts_rel):
            continue
        if p.name in ("index.md", "log.md"):
            continue
        pages.append(p)

    # Ordina per mtime desc, prendi i 4 più recenti
    pages.sort(key=lambda p: p.stat().st_mtime if p.is_file() else 0, reverse=True)
    for p in pages[:max_pages]:
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")[:2000]
            rel = p.relative_to(wiki_root)
            parts.append(f"# {rel}\n\n{txt}")
        except Exception:
            continue

    return "\n\n---\n\n".join(parts)


async def _generate_suggested_questions(project_root: Path) -> list[str]:
    """Genera 5 domande via Claude haiku. Returns list of strings."""
    sample = _sample_wiki_for_questions(project_root)
    if not sample:
        return []

    questions: list[str] = []
    try:
        from claude_agent_sdk import ClaudeAgentOptions, query
        prompt = f"Wiki del progetto:\n\n{sample}\n\nGenera 5 domande interessanti."
        full_text = ""
        options = ClaudeAgentOptions(
            system_prompt=SUGGESTED_Q_SYSTEM_PROMPT,
            model="haiku",
            permission_mode="bypassPermissions",
            allowed_tools=[],
        )
        async for message in query(prompt=prompt, options=options):
            content = getattr(message, "content", None)
            if not content:
                continue
            if isinstance(content, str):
                full_text += content
            else:
                for block in content:
                    text = getattr(block, "text", None)
                    if text:
                        full_text += text
        # Parse JSON list
        full_text = full_text.strip()
        # Trim eventuali ```json wrappers
        if full_text.startswith("```"):
            lines = full_text.split("\n")
            full_text = "\n".join(l for l in lines if not l.strip().startswith("```"))
        # Find JSON list
        start = full_text.find("[")
        end = full_text.rfind("]")
        if start >= 0 and end > start:
            json_str = full_text[start:end + 1]
            parsed = json.loads(json_str)
            if isinstance(parsed, list):
                questions = [str(q).strip() for q in parsed if isinstance(q, str) and q.strip()]
    except Exception as e:
        print(f"[suggested_q] generation error: {e}")

    return questions[:5]


@app.get("/api/project/suggested-questions")
async def api_suggested_questions(project: str = "", regenerate: bool = False):
    """Ritorna 5 domande suggerite per esplorare il wiki del progetto.
    Cache 24h. ?regenerate=true forza ricomputo.
    """
    if not project:
        raise HTTPException(400, "project required")
    root = _project_root(project)
    if not root:
        raise HTTPException(404, "project not found")

    # Cache hit?
    if not regenerate:
        cached = _load_suggested_questions_cache(root)
        if cached:
            return JSONResponse({
                "project": project,
                "questions": cached.get("questions", []),
                "generated_at": cached.get("generated_at"),
                "cached": True,
            })

    # Generate
    questions = await _generate_suggested_questions(root)
    if not questions:
        return JSONResponse({"project": project, "questions": [], "_warning": "generation failed or empty wiki"})

    payload = {"questions": questions, "generated_at": time.time(), "project": project}
    _save_suggested_questions_cache(root, payload)
    return JSONResponse({
        "project": project,
        "questions": questions,
        "generated_at": payload["generated_at"],
        "cached": False,
    })


@app.get("/api/hub/files")
async def api_hub_files(path: str = ""):
    """Fase 22.9 — File browser hub-level per `<hub>/files/`, `data/`, `scripts/`.

    Whitelist subdirs: files, data, scripts. Stesso shape di /api/project/files.
    """
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    rel = (path or "").lstrip("/").lstrip("\\")
    if ".." in rel or rel.startswith("/"):
        raise HTTPException(400, "invalid path")

    # Top-level può essere "files", "data", "scripts", o vuoto (lista subdirs)
    ALLOWED_TOP = {"files", "data", "scripts"}
    parts = [p for p in rel.split("/") if p]
    if parts and parts[0] not in ALLOWED_TOP:
        raise HTTPException(403, f"only {ALLOWED_TOP} allowed at hub level")

    if not rel:
        # Root: lista subdirs whitelisted
        entries = []
        for sub in ("files", "data", "scripts"):
            d = HUB_PATH / sub
            if d.is_dir():
                try:
                    stat = d.stat()
                    entries.append({
                        "name": sub,
                        "type": "dir",
                        "size": 0,
                        "modified": stat.st_mtime,
                    })
                except Exception:
                    continue
        return JSONResponse({"type": "dir", "path": "", "scope": "hub", "entries": entries})

    target = (HUB_PATH / rel).resolve()
    try:
        target.relative_to(HUB_PATH.resolve())
    except ValueError:
        raise HTTPException(400, "path outside hub")
    if not target.exists():
        raise HTTPException(404, f"path not found: {rel}")

    if target.is_file():
        if target.name.startswith(".secrets"):
            raise HTTPException(403, "restricted file (vault) not readable")
        size = target.stat().st_size
        if size > 500_000:
            return JSONResponse({
                "type": "file", "path": rel, "size": size,
                "error": "file too large (>500KB)",
                "preview": "",
            })
        try:
            content = target.read_text(encoding="utf-8", errors="replace")[:200_000]
        except Exception as e:
            return JSONResponse({"type": "file", "path": rel, "error": str(e), "content": ""})
        return JSONResponse({"type": "file", "path": rel, "size": size, "content": content})

    # Directory listing
    entries = []
    try:
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            # Mostra `.anjawiki` (contenuto workspace) ma tieni nascosto ogni altro dotfile (vault incluso)
            if child.name.startswith(".") and child.name != ".anjawiki":
                continue
            try:
                stat = child.stat()
                entries.append({
                    "name": child.name,
                    "type": "dir" if child.is_dir() else "file",
                    "size": stat.st_size if child.is_file() else 0,
                    "modified": stat.st_mtime,
                })
            except Exception:
                continue
    except PermissionError:
        raise HTTPException(403, "permission denied")
    return JSONResponse({"type": "dir", "path": rel, "scope": "hub", "entries": entries})


@app.get("/api/hub/download")
async def api_hub_download(path: str = ""):
    """Download binario file in `<hub>/files|data|scripts/`. Whitelist subdirs."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    rel = (path or "").lstrip("/").lstrip("\\")
    if ".." in rel or rel.startswith("/"):
        raise HTTPException(400, "invalid path")
    parts = [p for p in rel.split("/") if p]
    if not parts or parts[0] not in {"files", "data", "scripts"}:
        raise HTTPException(403, "only files/data/scripts allowed")
    target = (HUB_PATH / rel).resolve()
    try:
        target.relative_to(HUB_PATH.resolve())
    except ValueError:
        raise HTTPException(400, "path outside hub")
    if not target.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(str(target), filename=target.name)


@app.get("/api/project/download")
async def api_project_download(project: str = "", path: str = ""):
    """Download binario file da un workspace registrato."""
    if not HUB_PATH or not project:
        raise HTTPException(400, "project required")
    rel = (path or "").lstrip("/").lstrip("\\")
    if ".." in rel or rel.startswith("/"):
        raise HTTPException(400, "invalid path")
    proj_root = None
    for p in _build_projects_context():
        if p.get("name") == project:
            loc = p.get("location") or {}
            if loc.get("kind") == "local" and loc.get("path"):
                proj_root = Path(loc["path"]).resolve()
            break
    if not proj_root or not proj_root.is_dir():
        raise HTTPException(404, f"project '{project}' not found")
    target = (proj_root / rel).resolve()
    try:
        target.relative_to(proj_root)
    except ValueError:
        raise HTTPException(400, "path outside project")
    if not target.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(str(target), filename=target.name)


# ============================================================
# Fase 15 — Kanban REST API
# ============================================================

def _kanban_io():
    try:
        import kanban_io
        return kanban_io
    except ImportError:
        return None


@app.get("/api/kanban/tasks")
async def api_kanban_list(request: Request, scope: str = "", status: str = "",
                          assignee: str = "", parent_id: Optional[int] = None,
                          due_within_h: Optional[int] = None,
                          include_archived: bool = False, limit: int = 200):
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    _require_scope_access(request, scope)
    kio = _kanban_io()
    if not kio:
        raise HTTPException(500, "kanban_io not available")
    tasks = kio.list_tasks(
        HUB_PATH,
        scope=kio.normalize_workspace_scope(HUB_PATH, scope) or None,
        status=status or None,
        assignee=assignee or None,
        parent_id=parent_id,
        due_within_h=due_within_h,
        include_archived=include_archived,
        limit=limit,
    )
    return JSONResponse({"tasks": tasks, "stats": kio.stats(HUB_PATH)})


@app.get("/api/kanban/tasks/{task_id}")
async def api_kanban_get(task_id: int, request: Request):
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    kio = _kanban_io()
    if not kio:
        raise HTTPException(500, "kanban_io not available")
    t = kio.get_task(HUB_PATH, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    _require_scope_access(request, t.get("scope"))
    return JSONResponse(t)


@app.post("/api/kanban/tasks")
async def api_kanban_create(request: Request, payload: dict = Body(...)):
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    kio = _kanban_io()
    if not kio:
        raise HTTPException(500, "kanban_io not available")
    _require_scope_access(request, payload.get("scope"))
    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "title required")
    try:
        t = kio.create_task(
            HUB_PATH,
            title=title,
            body=payload.get("body") or "",
            status=payload.get("status") or "todo",
            assignee=payload.get("assignee") or "",
            scope=kio.normalize_workspace_scope(HUB_PATH, payload.get("scope") or "hub"),
            parent_id=payload.get("parent_id"),
            priority=int(payload.get("priority", 1)),
            tags=payload.get("tags") or [],
            due_at=payload.get("due_at"),
        )
        for dep_id in (payload.get("depends_on") or []):
            try:
                kio.add_dependency(HUB_PATH, t["id"], int(dep_id))
            except Exception:
                pass
        # WS broadcast (se attivo)
        await _kanban_broadcast({"event": "task_created", "task": t})
        return JSONResponse(t)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.patch("/api/kanban/tasks/{task_id}")
async def api_kanban_update(task_id: int, request: Request, payload: dict = Body(...)):
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    kio = _kanban_io()
    if not kio:
        raise HTTPException(500, "kanban_io not available")
    _existing = kio.get_task(HUB_PATH, task_id)
    if _existing:
        _require_scope_access(request, _existing.get("scope"))
    if payload.get("scope"):
        _require_scope_access(request, payload.get("scope"))   # gata anche lo scope di destinazione

    # Status update separato (con block_reason)
    if "status" in payload:
        try:
            t = kio.update_status(
                HUB_PATH, task_id, payload["status"],
                block_reason=payload.get("block_reason"),
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        if not t:
            raise HTTPException(404, "task not found")
        if payload["status"] == "done":
            promoted = kio.auto_promote_ready(HUB_PATH)
            await _kanban_broadcast({"event": "task_completed", "task": t, "auto_promoted": promoted})
        else:
            await _kanban_broadcast({"event": "task_updated", "task": t})
        return JSONResponse(t)

    # Generic update
    fields = {k: v for k, v in payload.items()
              if k in ("title", "body", "assignee", "scope", "priority", "tags", "due_at", "metadata")}
    t = kio.update_task(HUB_PATH, task_id, **fields)
    if not t:
        raise HTTPException(404, "task not found")
    await _kanban_broadcast({"event": "task_updated", "task": t})
    return JSONResponse(t)


@app.delete("/api/kanban/tasks/{task_id}")
async def api_kanban_delete(task_id: int, request: Request):
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    kio = _kanban_io()
    if not kio:
        raise HTTPException(500, "kanban_io not available")
    _existing = kio.get_task(HUB_PATH, task_id)
    if _existing:
        _require_scope_access(request, _existing.get("scope"))
    ok = kio.delete_task(HUB_PATH, task_id)
    if not ok:
        raise HTTPException(404, "task not found")
    await _kanban_broadcast({"event": "task_deleted", "task_id": task_id})
    return JSONResponse({"ok": True})


@app.post("/api/kanban/tasks/{task_id}/comment")
async def api_kanban_comment(task_id: int, request: Request, payload: dict = Body(...)):
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    kio = _kanban_io()
    if not kio:
        raise HTTPException(500, "kanban_io not available")
    _existing = kio.get_task(HUB_PATH, task_id)
    if _existing:
        _require_scope_access(request, _existing.get("scope"))
    content = (payload.get("content") or "").strip()
    if not content:
        raise HTTPException(400, "content required")
    c = kio.add_comment(HUB_PATH, task_id, content, author=payload.get("author") or "")
    return JSONResponse(c)


@app.post("/api/kanban/promote")
async def api_kanban_promote():
    """Auto-promote todo→ready quando deps OK. Trigger manuale (debug)."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    kio = _kanban_io()
    if not kio:
        raise HTTPException(500, "kanban_io not available")
    promoted = kio.auto_promote_ready(HUB_PATH)
    if promoted:
        await _kanban_broadcast({"event": "auto_promoted", "task_ids": promoted})
    return JSONResponse({"promoted": promoted})


@app.post("/api/kanban/heartbeat-digest")
async def api_kanban_heartbeat_digest():
    """F-Proactive-5: task 'degni di segnalare' (score + soglia + backoff adattivo)
    e marca lo stato heartbeat. Lo stato vive in data/heartbeat_state.json, FUORI dai
    metadata del task (scriverli cambierebbe updated_at = segnale di reazione)."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    kio = _kanban_io()
    if not kio:
        raise HTTPException(500, "kanban_io not available")
    try:
        import proactive_scoring as ps
    except ImportError:
        raise HTTPException(500, "proactive_scoring not available")
    from datetime import datetime, timezone
    state_path = HUB_PATH / "data" / "heartbeat_state.json"
    try:
        hb_state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    except Exception:
        hb_state = {}
    now = datetime.now(timezone.utc)
    tasks = kio.list_tasks(HUB_PATH, status="active", limit=200)
    selected, new_state = ps.select_for_heartbeat(tasks, hb_state, now)
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(new_state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[heartbeat-digest] state write error: {e}")
    return JSONResponse({"selected": selected, "count": len(selected)})


@app.get("/api/checkpoints")
async def api_checkpoints_list(n: int = 30):
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    try:
        import checkpoint as ckpt
    except ImportError:
        raise HTTPException(500, "checkpoint not available")
    return JSONResponse({"checkpoints": ckpt.list_checkpoints(HUB_PATH, n)})


@app.post("/api/checkpoints")
async def api_checkpoint_create(payload: dict = Body(...)):
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    try:
        import checkpoint as ckpt
    except ImportError:
        raise HTTPException(500, "checkpoint not available")
    label = (payload.get("label") or "manual checkpoint").strip()
    return JSONResponse({"sha": ckpt.checkpoint(HUB_PATH, label), "label": label})


@app.post("/api/checkpoints/restore")
async def api_checkpoint_restore(request: Request, payload: dict = Body(...)):
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    _require_admin(request)   # git-restore dell'intero hub → solo admin/owner
    ref = (payload.get("ref") or "").strip()
    if not ref:
        raise HTTPException(400, "ref required")
    try:
        import checkpoint as ckpt
    except ImportError:
        raise HTTPException(500, "checkpoint not available")
    try:
        return JSONResponse(ckpt.restore(HUB_PATH, ref))
    except ValueError as e:
        raise HTTPException(400, str(e))


# WebSocket broadcast
_kanban_ws_clients: set = set()


@app.websocket("/ws/kanban")
async def ws_kanban(websocket: WebSocket):
    await websocket.accept()
    _kanban_ws_clients.add(websocket)
    try:
        while True:
            # Keep alive — no incoming messages expected per ora
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _kanban_ws_clients.discard(websocket)


async def _kanban_broadcast(event: dict):
    """Invia event a tutti i client WS connessi."""
    if not _kanban_ws_clients:
        return
    dead = []
    for ws in list(_kanban_ws_clients):
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _kanban_ws_clients.discard(ws)


# ============================================================
# F-Notify — Notification Bus REST + SSE
# ============================================================

@app.get("/api/notifications")
async def api_notifications_list(
    unread_only: bool = False,
    source: Optional[str] = None,
    category: Optional[str] = None,
    min_severity: Optional[int] = None,
    scope: Optional[str] = None,
    since_id: Optional[int] = None,
    limit: int = 50,
):
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    items = notif_bus.list_notifications(
        HUB_PATH, unread_only=unread_only, source=source,
        category=category, min_severity=min_severity,
        scope=scope, since_id=since_id, limit=max(1, min(limit, 500)),
    )
    return {
        "items": items,
        "unread_count": notif_bus.count_unread(HUB_PATH, scope=scope),
    }


@app.get("/api/notifications/stats")
async def api_notifications_stats():
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    return notif_bus.stats(HUB_PATH)


@app.post("/api/notifications/{notif_id}/read")
async def api_notifications_mark_read(notif_id: int):
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    ok = notif_bus.mark_read(HUB_PATH, notif_id)
    if not ok:
        raise HTTPException(404, "notification not found")
    return {"ok": True}


@app.post("/api/notifications/mark-all-read")
async def api_notifications_mark_all_read(scope: Optional[str] = None):
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    n = notif_bus.mark_all_read(HUB_PATH, scope=scope)
    return {"ok": True, "updated": n}


@app.delete("/api/notifications/{notif_id}")
async def api_notifications_delete(notif_id: int):
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    ok = notif_bus.delete_notification(HUB_PATH, notif_id)
    if not ok:
        raise HTTPException(404, "notification not found")
    return {"ok": True}


@app.post("/api/notifications/test")
async def api_notifications_test(
    title: str = Body("Test notification", embed=True),
    body: str = Body("Triggered from Settings", embed=True),
    category: str = Body("info", embed=True),
    scope: str = Body("hub", embed=True),
):
    """Endpoint helper per il bottone 'Test notification' di Settings."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    if category not in notif_bus.VALID_CATEGORIES:
        raise HTTPException(400, f"invalid category: {category}")
    n = notif_bus.publish(
        HUB_PATH, source="webapp", title=title, body=body,
        category=category, scope=scope,
    )
    return n


@app.get("/api/notifications/stream")
async def api_notifications_stream(request: Request, scope: Optional[str] = None):
    """SSE stream: snapshot iniziale + push real-time. Heartbeat ogni 15s."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    q = notif_bus.subscribe()

    async def gen():
        try:
            initial = notif_bus.list_notifications(
                HUB_PATH, unread_only=True, scope=scope, limit=20)
            yield f"event: snapshot\ndata: {json.dumps(initial)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    if scope and ev.get("scope") not in (scope, "hub"):
                        continue
                    yield f"data: {json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            notif_bus.unsubscribe(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


_NOTIF_CLEANUP_TASK = None
_NOTIF_POLLER_TASK = None


@app.on_event("startup")
async def _startup_notif_cleanup():
    """Daily cleanup di notifiche lette > 30 giorni + DB poller cross-process."""
    global _NOTIF_CLEANUP_TASK, _NOTIF_POLLER_TASK
    if not HUB_PATH:
        return

    async def cleanup_loop():
        while True:
            try:
                removed = notif_bus.cleanup(HUB_PATH, older_than_days=30, keep_unread=True)
                if removed:
                    print(f"[notif_bus] cleanup removed {removed} old notifications")
            except Exception as e:
                print(f"[notif_bus] cleanup error: {e}")
            await asyncio.sleep(24 * 3600)

    _NOTIF_CLEANUP_TASK = asyncio.create_task(cleanup_loop())
    _NOTIF_POLLER_TASK = asyncio.create_task(notif_bus.db_poller_loop(HUB_PATH, interval=3.0))


@app.on_event("shutdown")
async def _shutdown_notif_cleanup():
    global _NOTIF_CLEANUP_TASK, _NOTIF_POLLER_TASK
    if _NOTIF_CLEANUP_TASK:
        _NOTIF_CLEANUP_TASK.cancel()
    if _NOTIF_POLLER_TASK:
        _NOTIF_POLLER_TASK.cancel()


@app.get("/api/activity/summary")
async def api_activity_summary():
    """F-Notify-4 — Snapshot live di cosa sta girando ora: daemon health,
    streaming chat, routine running, goal pipeline running."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")

    def _task_alive(t) -> bool:
        try:
            return t is not None and not t.done()
        except Exception:
            return False

    daemons = {
        "kanban_dispatcher": _task_alive(getattr(KANBAN_DISPATCHER, "task", None)) if KANBAN_DISPATCHER else False,
        "auto_ingest": _task_alive(getattr(AUTO_INGEST_DAEMON, "task", None)) if AUTO_INGEST_DAEMON else False,
        "telegram": _task_alive(getattr(TELEGRAM_DAEMON, "task", None)) if TELEGRAM_DAEMON else False,
        "script_supervisor": _task_alive(SCRIPT_SUPERVISOR_TASK),
        "goal_scheduler": _task_alive(GOAL_SCHEDULER_TASK),
        "notif_poller": _task_alive(_NOTIF_POLLER_TASK),
    }

    # Chat streaming: snapshot dal registry F-Notify-5
    chat_streaming = [
        {"conv_id": s["conv_id"], "title": s.get("title") or s.get("user_msg", "")[:60],
         "scope": s["scope"], "model": s["model"], "started_ts": s["started_ts"]}
        for s in chat_streams.list_active()
    ]

    # Routines running: scan dello state file in <hub>/data/routines.json (last run)
    routines_running = []
    try:
        rj = HUB_PATH / "data" / "routines.json"
        if rj.is_file():
            data = json.loads(rj.read_text(encoding="utf-8"))
            for name, info in (data or {}).items():
                # Heuristic: started < 10min fa, no completed_at
                started = info.get("last_run_started_at") or info.get("started_at")
                completed = info.get("last_run_completed_at") or info.get("completed_at")
                if started and not completed:
                    routines_running.append({"name": name, "started_at": started})
    except Exception:
        pass

    # Goals pipeline running: leggi pipeline.lock se esiste
    goals_running = []
    try:
        workspaces_dir = HUB_PATH / "workspaces"
        for ws in (workspaces_dir.iterdir() if workspaces_dir.is_dir() else []):
            gdir = ws / "wiki" / "goals"
            if not gdir.is_dir():
                continue
            for goal_root in gdir.iterdir():
                lock = goal_root / "pipeline.lock"
                if lock.is_file():
                    try:
                        info = json.loads(lock.read_text(encoding="utf-8"))
                        goals_running.append({
                            "scope": f"workspace:{ws.name}",
                            "goal_id": goal_root.name,
                            "step": info.get("current_step", "?"),
                            "started_at": info.get("started_at"),
                        })
                    except Exception:
                        pass
        # Hub-scope goals
        hub_goals = HUB_PATH / "wiki" / "goals"
        if hub_goals.is_dir():
            for goal_root in hub_goals.iterdir():
                lock = goal_root / "pipeline.lock"
                if lock.is_file():
                    try:
                        info = json.loads(lock.read_text(encoding="utf-8"))
                        goals_running.append({
                            "scope": "hub", "goal_id": goal_root.name,
                            "step": info.get("current_step", "?"),
                            "started_at": info.get("started_at"),
                        })
                    except Exception:
                        pass
    except Exception:
        pass

    return {
        "daemons": daemons,
        "chat_streaming": chat_streaming,
        "routines_running": routines_running,
        "goals_running": goals_running,
        "ts": time.time(),
    }


@app.get("/api/hub/recent-files")
async def api_hub_recent_files(limit: int = 5):
    """Fase 22.9 — Ultimi N file in `<hub>/files/` per mtime desc."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    files_dir = HUB_PATH / "files"
    if not files_dir.is_dir():
        return JSONResponse({"files": []})
    items = []
    for p in files_dir.rglob("*"):
        if not p.is_file():
            continue
        if p.name.startswith(".") or p.name == "README.md":
            continue
        try:
            rel = p.relative_to(files_dir)
            stat = p.stat()
            items.append({
                "name": p.name,
                "path": str(rel),
                "size": stat.st_size,
                "modified": stat.st_mtime,
            })
        except Exception:
            continue
    items.sort(key=lambda x: x["modified"], reverse=True)
    return JSONResponse({"files": items[:max(1, int(limit))]})


@app.post("/api/hub/file")
async def api_hub_file_save(request: Request, payload: dict = Body(...)):
    """Fase 22.9 — Salva file in hub scope (files/scripts/data only). Mirror di /api/project/file."""
    _require_admin(request)
    rel_path = (payload.get("path") or "").lstrip("/").lstrip("\\")
    content = payload.get("content")
    if not rel_path or content is None:
        raise HTTPException(400, "path, content required")
    if not isinstance(content, str):
        raise HTTPException(400, "content must be string")
    if len(content.encode("utf-8")) > 5 * 1024 * 1024:
        raise HTTPException(413, "file too large (>5MB)")
    if ".." in rel_path or rel_path.startswith("/"):
        raise HTTPException(400, "invalid path")
    parts = [p for p in rel_path.split("/") if p]
    if not parts or parts[0] not in {"files", "data", "scripts"}:
        raise HTTPException(403, "write only in files/, data/, scripts/")
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    target = (HUB_PATH / rel_path).resolve()
    try:
        target.relative_to(HUB_PATH.resolve())
    except ValueError:
        raise HTTPException(400, "path outside hub")

    backup_path = None
    if target.is_file():
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = target.with_suffix(target.suffix + f".anja-bak.{ts}")
        try:
            backup_path.write_bytes(target.read_bytes())
        except Exception:
            backup_path = None
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(content, encoding="utf-8")
    except Exception as e:
        raise HTTPException(500, f"write failed: {e}")
    return JSONResponse({
        "ok": True, "path": rel_path,
        "size": target.stat().st_size,
        "backup": backup_path.name if backup_path else None,
        "saved_at": time.time(),
    })


# ============================================================
# Fase 22.9+ — File UPLOAD (multipart)
# ============================================================

UPLOAD_MAX_BYTES = 50 * 1024 * 1024  # 50MB
UPLOAD_ALLOWED_SUBDIRS = {"files", "data", "scripts"}


def _validate_upload(target_path: Path, scope_root: Path, subdir: str) -> Optional[str]:
    """Common validation. Ritorna error str o None se OK."""
    if subdir not in UPLOAD_ALLOWED_SUBDIRS:
        return f"subdir must be one of {UPLOAD_ALLOWED_SUBDIRS}"
    try:
        target_path.resolve().relative_to(scope_root.resolve())
    except ValueError:
        return "path outside scope"
    return None


# ============================================================
# Generated media static serving (Fase 23.c — inline render in chat)
# ============================================================

_MEDIA_EXT_TO_MIME = {
    ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif",
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@app.get("/api/media/{kind}/{date}/{filename}")
async def api_media_serve(kind: str, date: str, filename: str):
    """Serve mp4/png/jpg generati da anja_videos / anja_images.

    Security: kind ∈ {videos, images}, date YYYY-MM-DD, filename basename only
    + estensione media whitelisted. No path traversal.
    """
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    if kind not in ("videos", "images"):
        raise HTTPException(400, "kind must be videos|images")
    if not _DATE_RE.match(date):
        raise HTTPException(400, "invalid date format (YYYY-MM-DD required)")
    # Basename safety
    safe = Path(filename).name
    if safe != filename or safe.startswith(".") or "/" in safe or ".." in safe:
        raise HTTPException(400, "invalid filename")
    ext = Path(safe).suffix.lower()
    if ext not in _MEDIA_EXT_TO_MIME:
        raise HTTPException(400, f"unsupported media extension: {ext}")
    target = HUB_PATH / "raw" / kind / date / safe
    # Confine within hub/raw
    try:
        resolved = target.resolve()
        base = (HUB_PATH / "raw" / kind).resolve()
        if not str(resolved).startswith(str(base)):
            raise HTTPException(400, "path escape")
    except Exception:
        raise HTTPException(400, "invalid path")
    if not target.is_file():
        raise HTTPException(404, "media not found")
    from fastapi.responses import FileResponse
    return FileResponse(target, media_type=_MEDIA_EXT_TO_MIME[ext], filename=safe)


@app.get("/api/media/list")
async def api_media_list(kind: str = "all", limit: int = 200):
    """Lista media files generati. kind: 'videos'|'images'|'all'."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    kinds_to_scan = ["videos", "images"] if kind == "all" else [kind]
    for k in kinds_to_scan:
        if k not in ("videos", "images"):
            raise HTTPException(400, "kind must be videos|images|all")

    items = []
    for k in kinds_to_scan:
        root = HUB_PATH / "raw" / k
        if not root.is_dir():
            continue
        for date_dir in sorted(root.iterdir(), reverse=True):
            if not date_dir.is_dir() or not _DATE_RE.match(date_dir.name):
                continue
            for f in sorted(date_dir.iterdir(), reverse=True):
                if not f.is_file():
                    continue
                ext = f.suffix.lower()
                if ext not in _MEDIA_EXT_TO_MIME:
                    continue
                try:
                    stat = f.stat()
                except Exception:
                    continue
                items.append({
                    "kind": k,
                    "date": date_dir.name,
                    "filename": f.name,
                    "path": str(f),
                    "rel_path": f"raw/{k}/{date_dir.name}/{f.name}",
                    "web_url": f"/api/media/{k}/{date_dir.name}/{f.name}",
                    "size_bytes": stat.st_size,
                    "mtime": stat.st_mtime,
                    "ext": ext.lstrip("."),
                    "mime": _MEDIA_EXT_TO_MIME.get(ext, "application/octet-stream"),
                })
                if len(items) >= limit:
                    break
            if len(items) >= limit:
                break
    # sort cumulative by mtime desc
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return JSONResponse({"items": items[:limit], "count": len(items)})


@app.delete("/api/media/{kind}/{date}/{filename}")
async def api_media_delete(kind: str, date: str, filename: str):
    """Elimina un file media. Security come api_media_serve."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    if kind not in ("videos", "images"):
        raise HTTPException(400, "kind must be videos|images")
    if not _DATE_RE.match(date):
        raise HTTPException(400, "invalid date")
    safe = Path(filename).name
    if safe != filename or safe.startswith(".") or "/" in safe or ".." in safe:
        raise HTTPException(400, "invalid filename")
    if Path(safe).suffix.lower() not in _MEDIA_EXT_TO_MIME:
        raise HTTPException(400, "unsupported extension")
    target = HUB_PATH / "raw" / kind / date / safe
    try:
        resolved = target.resolve()
        base = (HUB_PATH / "raw" / kind).resolve()
        if not str(resolved).startswith(str(base)):
            raise HTTPException(400, "path escape")
    except Exception:
        raise HTTPException(400, "invalid path")
    if not target.is_file():
        raise HTTPException(404, "file not found")
    try:
        target.unlink()
        return JSONResponse({"ok": True, "deleted": safe})
    except Exception as e:
        raise HTTPException(500, f"delete failed: {e}")


# ============================================================
# Chat attachments (Fase 24)
# ============================================================

@app.post("/api/chat/upload")
async def api_chat_upload(
    file: UploadFile = File(...),
    conv_id: str = Form("default"),
):
    """Upload allegato per chat. Salva in webapp/uploads/<conv_id>/ + estrae contenuto."""
    if not file.filename:
        raise HTTPException(400, "filename required")
    try:
        import chat_attachments
    except ImportError as e:
        raise HTTPException(500, f"chat_attachments module missing: {e}")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    descriptor = chat_attachments.save_upload(
        WEBAPP_DIR, conv_id, file.filename, data, mime=file.content_type,
    )
    if descriptor.get("error"):
        raise HTTPException(400, descriptor["error"])
    # Strip path completo dalla response (security)
    descriptor.pop("path", None)
    # Strip image_b64 dal response JSON: troppo grosso da rimandare ogni volta.
    # Server lo ha già salvato su disco. Il preview è sufficient.
    if descriptor.get("image_b64"):
        descriptor["has_image_b64"] = True
        del descriptor["image_b64"]
    return JSONResponse(descriptor)


@app.post("/api/chat/upload/delete")
async def api_chat_upload_delete(request: Request):
    body = await request.json()
    conv_id = (body.get("conv_id") or "default").strip()
    saved_filename = (body.get("saved_filename") or "").strip()
    if not saved_filename:
        raise HTTPException(400, "saved_filename required")
    try:
        import chat_attachments
    except ImportError as e:
        raise HTTPException(500, f"chat_attachments missing: {e}")
    ok = chat_attachments.delete_upload(WEBAPP_DIR, conv_id, saved_filename)
    if not ok:
        raise HTTPException(404, "file not found")
    return JSONResponse({"status": "ok"})


@app.post("/api/hub/upload")
async def api_hub_upload(
    request: Request,
    file: UploadFile = File(...),
    subdir: str = Form("files"),
    overwrite: bool = Form(False),
):
    """Upload file in `<hub>/<subdir>/`. subdir whitelist: files/data/scripts."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    _require_admin(request)
    if not file.filename:
        raise HTTPException(400, "filename required")
    # Sanitize: no path traversal, basename only
    safe_name = Path(file.filename).name
    if not safe_name or safe_name.startswith("."):
        raise HTTPException(400, "invalid filename")

    target = HUB_PATH / subdir / safe_name
    err = _validate_upload(target, HUB_PATH, subdir)
    if err:
        raise HTTPException(400, err)
    if target.exists() and not overwrite:
        raise HTTPException(409, f"file already exists: {subdir}/{safe_name} (set overwrite=true to replace)")

    # Stream save with size cap
    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with target.open("wb") as out:
            while True:
                chunk = await file.read(64 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > UPLOAD_MAX_BYTES:
                    out.close()
                    try: target.unlink()
                    except Exception: pass
                    raise HTTPException(413, f"file too large (>{UPLOAD_MAX_BYTES} bytes)")
                out.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"upload failed: {e}")

    return JSONResponse({
        "ok": True,
        "scope": "hub",
        "path": f"{subdir}/{safe_name}",
        "size": written,
        "uploaded_at": time.time(),
    })


@app.post("/api/project/upload")
async def api_project_upload(
    request: Request,
    project: str = Form(...),
    file: UploadFile = File(...),
    subdir: str = Form("files"),
    overwrite: bool = Form(False),
):
    """Upload file in `<workspace>/.anjawiki/<subdir>/` (per workspace internal).
    Per workspace external (symlink): salva nella linked path.
    """
    if not HUB_PATH or not project:
        raise HTTPException(400, "project required")
    _require_ws_access(request, project)   # project è un Form field → gate esplicito
    if not file.filename:
        raise HTTPException(400, "filename required")
    safe_name = Path(file.filename).name
    if not safe_name or safe_name.startswith("."):
        raise HTTPException(400, "invalid filename")

    # Risolvi workspace root via registry
    proj_root = None
    for p in _build_projects_context():
        if p.get("name") == project:
            loc = p.get("location") or {}
            if loc.get("kind") == "local" and loc.get("path"):
                proj_root = Path(loc["path"]).resolve()
            break
    if not proj_root or not proj_root.is_dir():
        raise HTTPException(404, f"project '{project}' not found locally")

    # Determina scope_root: per internal il path nel registry punta già a .anjawiki/
    # Per external pure (i.e. symlink), il path è la .anjawiki del progetto esterno
    scope_root = proj_root
    target = scope_root / subdir / safe_name
    err = _validate_upload(target, scope_root, subdir)
    if err:
        raise HTTPException(400, err)
    if target.exists() and not overwrite:
        raise HTTPException(409, f"file already exists: {subdir}/{safe_name}")

    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with target.open("wb") as out:
            while True:
                chunk = await file.read(64 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > UPLOAD_MAX_BYTES:
                    out.close()
                    try: target.unlink()
                    except Exception: pass
                    raise HTTPException(413, f"file too large (>{UPLOAD_MAX_BYTES} bytes)")
                out.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"upload failed: {e}")

    return JSONResponse({
        "ok": True,
        "scope": f"project:{project}",
        "path": f"{subdir}/{safe_name}",
        "size": written,
        "uploaded_at": time.time(),
    })


@app.get("/api/project/files")
async def api_project_files(project: str = "", path: str = ""):
    """File tree o file content per un progetto registrato (Fase 13 Workspace).
    - Senza `path`: ritorna tree (lista dir/file livello richiesto)
    - Con `path`: ritorna file content (max 200k char)
    """
    if not HUB_PATH or not project:
        raise HTTPException(400, "project required")
    # Risolvi project root via registry
    projects = _build_projects_context()
    proj_root = None
    for p in projects:
        if p.get("name") == project:
            loc = p.get("location") or {}
            if loc.get("kind") == "local" and loc.get("path"):
                proj_root = Path(loc["path"]).resolve()
            break
    if not proj_root or not proj_root.is_dir():
        raise HTTPException(404, f"project '{project}' not found locally")

    # Skip hidden/heavy directories
    SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
                 ".next", ".cache", ".pytest_cache", ".mypy_cache", "target", ".idea", ".vscode"}

    rel_path = (path or "").lstrip("/").lstrip("\\")
    # Safety: vieta path traversal
    if ".." in rel_path or rel_path.startswith("/"):
        raise HTTPException(400, "invalid path")

    target = (proj_root / rel_path).resolve() if rel_path else proj_root
    # Safety: target deve essere dentro proj_root
    try:
        target.relative_to(proj_root)
    except ValueError:
        raise HTTPException(400, "path outside project")

    if not target.exists():
        raise HTTPException(404, f"path not found: {rel_path}")

    if target.is_file():
        if target.name.startswith(".secrets"):
            raise HTTPException(403, "restricted file (vault) not readable")
        # File content
        size = target.stat().st_size
        if size > 500_000:
            return JSONResponse({
                "type": "file", "path": rel_path, "size": size,
                "error": "file too large (>500KB), open in editor",
                "preview": "",
            })
        try:
            content = target.read_text(encoding="utf-8", errors="replace")[:200_000]
        except Exception as e:
            return JSONResponse({"type": "file", "path": rel_path, "error": str(e), "content": ""})
        return JSONResponse({
            "type": "file",
            "path": rel_path,
            "size": size,
            "content": content,
        })

    # Directory listing
    entries = []
    try:
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if child.name in SKIP_DIRS or (child.name.startswith(".") and child.name != ".anjawiki"):
                continue
            try:
                stat = child.stat()
                entries.append({
                    "name": child.name,
                    "type": "dir" if child.is_dir() else "file",
                    "size": stat.st_size if child.is_file() else 0,
                    "modified": stat.st_mtime,
                })
            except Exception:
                continue
    except PermissionError:
        raise HTTPException(403, "permission denied")

    return JSONResponse({
        "type": "dir",
        "path": rel_path,
        "project": project,
        "root": str(proj_root),
        "entries": entries,
    })


# Galleria Media del workspace: immagini/video/PDF da <ws>/files/ (deliverable).
# Il file browser mostra solo testo → serve un endpoint binario per il preview.
_PROJ_MEDIA_MIME = {**_MEDIA_EXT_TO_MIME, ".pdf": "application/pdf"}


@app.get("/api/project/media")
async def api_project_media(project: str = ""):
    """Lista i media (immagini/video/PDF) sotto <ws>/files/ per la galleria Media."""
    if not HUB_PATH or not project:
        raise HTTPException(400, "project required")
    proj_root = _project_root(project)
    if not proj_root or not proj_root.is_dir():
        raise HTTPException(404, f"project '{project}' not found locally")
    from urllib.parse import quote
    # Radici media del workspace: files/ (deliverable pod) + le dir media di data/
    # (media = generazioni, social = kit, brand = loghi). Path workspace-relative.
    roots = [proj_root / "files",
             proj_root / "data" / "media",
             proj_root / "data" / "social",
             proj_root / "data" / "brand"]
    items = []
    for root in roots:
        if not root.is_dir():
            continue
        for fp in sorted(root.rglob("*")):
            if not fp.is_file() or fp.name.startswith("."):
                continue
            ext = fp.suffix.lower()
            if ext not in _PROJ_MEDIA_MIME:
                continue
            try:
                st = fp.stat()
            except Exception:
                continue
            rel = fp.relative_to(proj_root).as_posix()
            parent = fp.parent.relative_to(proj_root).as_posix() or "."
            kind = "pdf" if ext == ".pdf" else ("videos" if ext in (".mp4", ".webm", ".mov") else "images")
            items.append({
                "filename": fp.name, "path": rel, "dir": parent,
                "ext": ext.lstrip("."), "kind": kind,
                "size_bytes": st.st_size, "mtime": st.st_mtime,
                "web_url": f"/api/project/media/file?project={quote(project)}&path={quote(rel)}",
            })
            if len(items) >= 1000:
                break
    return JSONResponse({"project": project, "count": len(items), "items": items})


@app.get("/api/project/media/file")
async def api_project_media_file(project: str = "", path: str = ""):
    """Serve un singolo media binario del workspace (files/ o data/{media,social,brand}).
    Guard: traversal + whitelist radici + .secrets + ext. Path workspace-relative."""
    if not HUB_PATH or not project:
        raise HTTPException(400, "project required")
    proj_root = _project_root(project)
    if not proj_root or not proj_root.is_dir():
        raise HTTPException(404, f"project '{project}' not found locally")
    rel = (path or "").lstrip("/").lstrip("\\")
    if not rel or ".." in rel or ".secrets" in rel.lower():
        raise HTTPException(400, "invalid path")
    parts = PurePosixPath(rel).parts
    allowed = (parts and parts[0] == "files") or \
              (len(parts) >= 2 and parts[0] == "data" and parts[1] in ("media", "social", "brand"))
    # retrocompatibilità: path senza prefisso = relativo a files/ (vecchi link)
    if not allowed and parts and parts[0] not in ("files", "data"):
        rel = f"files/{rel}"
        allowed = True
    if not allowed:
        raise HTTPException(400, "path outside media roots (files/, data/{media,social,brand})")
    base_root = proj_root.resolve()
    target = (base_root / rel).resolve()
    try:
        target.relative_to(base_root)
    except ValueError:
        raise HTTPException(400, "path outside workspace")
    if not target.is_file():
        raise HTTPException(404, "media not found")
    ext = target.suffix.lower()
    if ext not in _PROJ_MEDIA_MIME:
        raise HTTPException(400, f"unsupported media extension: {ext}")
    from fastapi.responses import FileResponse
    return FileResponse(target, media_type=_PROJ_MEDIA_MIME[ext], filename=target.name)


@app.get("/api/project/piano")
async def api_project_piano(project: str = ""):
    """Vista Piano editoriale: legge <ws>/data/PIANO.md (fonte di verità) e ritorna
    gli item editoriali parsati — indipendente dal kanban.

    Ogni item: {date, title, channel(blog|instagram|facebook|linkedin),
    status(idea|brief|bozza|pubblicato|repurposed), keyword}.
    """
    if not HUB_PATH or not project:
        raise HTTPException(400, "project required")
    proj_root = _project_root(project)
    if not proj_root or not proj_root.is_dir():
        raise HTTPException(404, f"project '{project}' not found locally")
    try:
        import piano_kanban
    except Exception as e:
        raise HTTPException(500, f"piano_kanban not available: {e}")
    piano_path = proj_root / "data" / "PIANO.md"
    if not piano_path.is_file():
        return JSONResponse({"project": project, "items": [], "exists": False})
    items = piano_kanban.parse_piano_items(piano_path.read_text(encoding="utf-8"))
    return JSONResponse({"project": project, "items": items, "exists": True})


@app.post("/api/project/piano/item")
async def api_project_piano_item(request: Request, payload: dict = Body(...)):
    """Write-back stato di un item del Piano → data/PIANO.md (F1b editing).
    Body: {project, kind: blog|social, anchor, status}. blog→colonna Stato,
    social→emoji del bullet."""
    project = (payload.get("project") or "").strip()
    kind = (payload.get("kind") or "").strip()
    anchor = (payload.get("anchor") or "").strip()
    status = (payload.get("status") or "").strip()
    if not HUB_PATH or not project:
        raise HTTPException(400, "project required")
    _require_ws_access(request, project)
    proj_root = _project_root(project)
    if not proj_root or not proj_root.is_dir():
        raise HTTPException(404, f"project '{project}' not found locally")
    try:
        import piano_kanban
    except Exception as e:
        raise HTTPException(500, f"piano_kanban not available: {e}")
    res = piano_kanban.set_item_status(proj_root / "data" / "PIANO.md", kind, anchor, status)
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "update failed"))
    return JSONResponse(res)


@app.get("/api/project/metrics")
async def api_project_metrics(project: str = "", range_days: int = 28):
    """Statistiche/dashboard workspace (F1c): KPI + serie + insight da
    <ws>/data/metrics.db. exists=False se il db è assente/vuoto (empty-state UI)."""
    if not HUB_PATH or not project:
        raise HTTPException(400, "project required")
    proj_root = _project_root(project)
    if not proj_root or not proj_root.is_dir():
        raise HTTPException(404, f"project '{project}' not found locally")
    try:
        import metrics_io
    except Exception as e:
        raise HTTPException(500, f"metrics_io not available: {e}")
    days = 90 if range_days >= 90 else (7 if range_days <= 7 else 28)
    data = metrics_io.dashboard(proj_root / "data" / "metrics.db", days=days)
    try:
        import catalogo_io
        data["content"] = catalogo_io.content_stats(proj_root / "data" / "catalogo")
    except Exception:
        data["content"] = None
    data["project"] = project
    return JSONResponse(data)


@app.post("/api/project/metrics/refresh")
async def api_project_metrics_refresh(request: Request, payload: dict = Body(...)):
    """Aggiorna le metriche: tenta la raccolta GSC/GA/Ads → metrics.db. Reale solo
    con OAuth Google collegato; altrimenti riporta lo stato delle sorgenti."""
    project = (payload.get("project") or "").strip()
    if not HUB_PATH or not project:
        raise HTTPException(400, "project required")
    _require_ws_access(request, project)
    proj_root = _project_root(project)
    if not proj_root or not proj_root.is_dir():
        raise HTTPException(404, f"project '{project}' not found locally")
    try:
        import metrics_collector
        import connectors_io
    except Exception as e:
        raise HTTPException(500, f"module not available: {e}")
    vals = connectors_io._load_values(HUB_PATH, proj_root / ".anjawiki")
    res = await asyncio.get_event_loop().run_in_executor(
        None, lambda: metrics_collector.refresh(
            proj_root / "data" / "metrics.db", vals,
            scope_dir=proj_root / ".anjawiki", hub_dir=HUB_PATH / ".anjawiki"))
    res["project"] = project
    return JSONResponse(res)


@app.post("/api/project/audit")
async def api_project_audit(request: Request, payload: dict = Body(...)):
    """Audit SEO/E-E-A-T/GEO dei prodotti (Tier 2): scoring sul contenuto WooCommerce
    incrociato con GSC (gsc_pages) → priority + quick-win. Body: {project}."""
    project = (payload.get("project") or "").strip()
    kind = (payload.get("kind") or "products").strip()
    if not HUB_PATH or not project:
        raise HTTPException(400, "project required")
    _require_ws_access(request, project)
    proj_root = _project_root(project)
    if not proj_root or not proj_root.is_dir():
        raise HTTPException(404, f"project '{project}' not found locally")
    try:
        import audit_io
        import connectors_io
    except Exception as e:
        raise HTTPException(500, f"module not available: {e}")
    vals = connectors_io._load_values(HUB_PATH, proj_root / ".anjawiki")
    res = await asyncio.get_event_loop().run_in_executor(
        None, lambda: audit_io.audit(vals, proj_root / "data" / "metrics.db", kind=kind))
    if not res.get("ok"):
        raise HTTPException(400, res.get("error") or "audit failed")
    res["project"] = project
    return JSONResponse(res)


@app.get("/api/project/catalogo")
async def api_project_catalogo(project: str = ""):
    """Catalogo contenuti del sito (workspace marketing): articoli/pagine/prodotti
    da <ws>/data/catalogo/*.md. Distinto dal Marketplace (galleria blueprint)."""
    if not HUB_PATH or not project:
        raise HTTPException(400, "project required")
    proj_root = _project_root(project)
    if not proj_root or not proj_root.is_dir():
        raise HTTPException(404, f"project '{project}' not found locally")
    try:
        import catalogo_io
    except Exception as e:
        raise HTTPException(500, f"catalogo_io not available: {e}")
    data = catalogo_io.read_catalogo(proj_root / "data" / "catalogo")
    data["project"] = project
    return JSONResponse(data)


@app.post("/api/project/catalogo/sync")
async def api_project_catalogo_sync(request: Request, payload: dict = Body(...)):
    """Rigenera data/catalogo/*.md dal CMS (WordPress REST), leggendo le credenziali
    WP dal vault. Read-only sul CMS."""
    project = (payload.get("project") or "").strip()
    if not HUB_PATH or not project:
        raise HTTPException(400, "project required")
    _require_ws_access(request, project)
    proj_root = _project_root(project)
    if not proj_root or not proj_root.is_dir():
        raise HTTPException(404, f"project '{project}' not found locally")
    try:
        import catalogo_sync
        import connectors_io
    except Exception as e:
        raise HTTPException(500, f"module not available: {e}")
    vals = connectors_io._load_values(HUB_PATH, proj_root / ".anjawiki")
    meta = _read_workspace_meta_yaml(project)
    # backend/ecommerce del blueprint vivono in <ws>/.anjawiki/meta.yaml, non nel
    # meta top-level → fondi (il top-level vince sulle chiavi comuni).
    _aw_meta = proj_root / ".anjawiki" / "meta.yaml"
    if _aw_meta.is_file():
        for _ln in _aw_meta.read_text(encoding="utf-8").splitlines():
            if ":" in _ln and not _ln.strip().startswith("#"):
                _k, _v = _ln.split(":", 1)
                meta.setdefault(_k.strip(), _v.strip())
    if str(meta.get("backend", "")).strip() == "swerpi":
        res = catalogo_sync.sync_catalogo_swerpi(
            proj_root / "data" / "catalogo",
            base_url=vals.get("SWERPICOMMERCE_BASE_URL", ""),
            api_id=vals.get("SWERPICOMMERCE_API_ID", ""),
            api_secret=vals.get("SWERPICOMMERCE_API_SECRET", ""),
            bearer=vals.get("SWERPICOMMERCE_BEARER_AUTH", ""),
            ws_slug=project,
        )
    else:
        res = catalogo_sync.sync_catalogo(
            proj_root / "data" / "catalogo",
            wp_base_url=vals.get("WP_BASE_URL", ""),
            wp_user=vals.get("WP_USERNAME", ""),
            wp_app_password=vals.get("WP_APP_PASSWORD", ""),
            ws_slug=project,
            ecommerce=str(meta.get("ecommerce", "")).lower() == "true",
        )
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "sync failed"))
    res["project"] = project
    return JSONResponse(res)


@app.get("/api/project/social")
async def api_project_social(project: str = ""):
    """Performance social organica: post pubblicati (dal log PIANO) + engagement
    raccolto (social_insights.json)."""
    if not HUB_PATH or not project:
        raise HTTPException(400, "project required")
    proj_root = _project_root(project)
    if not proj_root or not proj_root.is_dir():
        raise HTTPException(404, f"project '{project}' not found locally")
    try:
        import social_io
    except Exception as e:
        raise HTTPException(500, f"social_io not available: {e}")
    data = social_io.read_social(proj_root / "data")
    data["project"] = project
    return JSONResponse(data)


@app.post("/api/project/social/refresh")
async def api_project_social_refresh(request: Request, payload: dict = Body(...)):
    """Raccoglie l'engagement dei post social via Meta Insights (token dal vault)."""
    project = (payload.get("project") or "").strip()
    if not HUB_PATH or not project:
        raise HTTPException(400, "project required")
    _require_ws_access(request, project)
    proj_root = _project_root(project)
    if not proj_root or not proj_root.is_dir():
        raise HTTPException(404, f"project '{project}' not found locally")
    try:
        import social_io
        import meta_insights
        import connectors_io
    except Exception as e:
        raise HTTPException(500, f"module not available: {e}")
    piano = proj_root / "data" / "PIANO.md"
    posts = social_io.parse_social_log(piano.read_text(encoding="utf-8")) if piano.is_file() else []
    vals = connectors_io._load_values(HUB_PATH, proj_root / ".anjawiki")
    res = meta_insights.collect(posts, token=vals.get("META_ACCESS_TOKEN", ""),
                                ig_user_id=vals.get("META_IG_USER_ID", ""))
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "collect failed"))
    social_io.save_insights(proj_root / "data", res["insights"])
    return JSONResponse({"ok": True, "collected": res["collected"], "errors": res["errors"], "project": project})


def _connectors_secrets_dir(project: str) -> Path:
    """Valida il project e ritorna la dir `<ws>/.anjawiki` (vault + .secrets.env)."""
    if not HUB_PATH or not project:
        raise HTTPException(400, "project required")
    proj_root = _project_root(project)
    if not proj_root or not proj_root.is_dir():
        raise HTTPException(404, f"project '{project}' not found locally")
    return proj_root / ".anjawiki"


@app.get("/api/project/connectors")
async def api_project_connectors(project: str = ""):
    """Settings/Connettori del workspace (F1a+F2): stato backend (WP/Meta/Google)
    dal VAULT cifrato. I valori secret NON vengono esposti (solo set).
    `materialized` = segreti in chiaro nel .secrets.env per il runtime."""
    secrets_dir = _connectors_secrets_dir(project)
    try:
        import connectors_io
    except Exception as e:
        raise HTTPException(500, f"connectors_io not available: {e}")
    data = connectors_io.read_status(HUB_PATH, secrets_dir)
    data["project"] = project
    return JSONResponse(data)


@app.post("/api/project/connectors")
async def api_project_connectors_save(request: Request, payload: dict = Body(...)):
    """Salva i connettori nel VAULT cifrato (F2). Body: {project, values}. Secret
    con valore vuoto = invariato. Se materializzato, ri-materializza il runtime."""
    project = (payload.get("project") or "").strip()
    values = payload.get("values") or {}
    if not isinstance(values, dict):
        raise HTTPException(400, "values must be an object")
    _require_ws_access(request, project)
    secrets_dir = _connectors_secrets_dir(project)
    try:
        import connectors_io
    except Exception as e:
        raise HTTPException(500, f"connectors_io not available: {e}")
    try:
        data = connectors_io.save(HUB_PATH, secrets_dir, values)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # F-CLI-Media: un override workspace delle key media cambia il credentials.env
    try:
        connectors_io.write_media_credentials(HUB_PATH)
    except Exception as e:  # noqa: BLE001
        print(f"[media-cli] rematerializzazione fallita: {e}", flush=True)
    data["project"] = project
    data["ok"] = True
    return JSONResponse(data)


@app.post("/api/project/connectors/materialize")
async def api_project_connectors_materialize(request: Request, payload: dict = Body(...)):
    """Materializza i segreti del vault in chiaro su `.secrets.env` per il runtime MCP."""
    project = (payload.get("project") or "").strip()
    on = bool(payload.get("on", True))
    _require_ws_access(request, project)
    secrets_dir = _connectors_secrets_dir(project)
    try:
        import connectors_io
    except Exception as e:
        raise HTTPException(500, f"connectors_io not available: {e}")
    data = (connectors_io.materialize if on else connectors_io.dematerialize)(HUB_PATH, secrets_dir)
    data["project"] = project
    data["ok"] = True
    return JSONResponse(data)


# --- Connettori condivisi a livello hub (default + override per-workspace) -------

@app.get("/api/hub/connectors")
async def api_hub_connectors():
    """Connettori CONDIVISI a livello hub (es. key dei modelli immagine): default usati
    da tutti i workspace, con override per-workspace via i Connettori del workspace."""
    if not HUB_PATH:
        raise HTTPException(400, "hub not configured")
    import connectors_io
    data = connectors_io.read_status(HUB_PATH, connectors_io.hub_secrets_dir(HUB_PATH))
    data["connectors"] = [c for c in data["connectors"] if c.get("shared")]
    data["scope"] = "hub"
    return JSONResponse(data)


@app.post("/api/hub/connectors")
async def api_hub_connectors_save(request: Request, payload: dict = Body(...)):
    """Salva i connettori condivisi nel vault hub. Body: {values}."""
    if not HUB_PATH:
        raise HTTPException(400, "hub not configured")
    _require_admin(request)   # scrive segreti nel vault hub condiviso
    values = payload.get("values") or {}
    if not isinstance(values, dict):
        raise HTTPException(400, "values must be an object")
    import connectors_io
    try:
        data = connectors_io.save(HUB_PATH, connectors_io.hub_secrets_dir(HUB_PATH), values)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # Le chiavi AI condivise servono anche a chat/model-fetcher, che leggono
    # <hub>/.secrets.env (non il vault): sync delle chiavi valorizzate.
    try:
        ai_keys = {f["key"] for con in connectors_io.CONNECTORS if con.get("shared")
                   for f in con["fields"]}
        vals_now = connectors_io._load_values(HUB_PATH, connectors_io.hub_secrets_dir(HUB_PATH))
        sf = HUB_PATH / ".secrets.env"
        lines = sf.read_text(encoding="utf-8").splitlines() if sf.is_file() else []
        changed = False
        for k in sorted(ai_keys):
            v = (vals_now.get(k) or "").strip()
            if not v:
                continue
            pat = re.compile(rf"^\s*{k}\s*=")
            kept = [ln for ln in lines if not pat.match(ln)]
            new_line = f"{k}={v}"
            if kept != lines or new_line not in lines:
                lines = kept + [new_line]
                changed = True
        if changed:
            sf.write_text("\n".join(lines) + "\n", encoding="utf-8")
            try:
                os.chmod(sf, 0o600)
            except OSError:
                pass
    except Exception as e:  # noqa: BLE001
        print(f"[hub-connectors] sync .secrets.env fallito: {e}", flush=True)
    # F-CLI-Media: rigenera i credentials.env (hub + workspace) per la CLI giv
    try:
        connectors_io.write_media_credentials(HUB_PATH)
    except Exception as e:  # noqa: BLE001
        print(f"[media-cli] rematerializzazione fallita: {e}", flush=True)
    data["connectors"] = [c for c in data["connectors"] if c.get("shared")]
    data["scope"] = "hub"
    data["ok"] = True
    return JSONResponse(data)


# --- Generazione immagini multi-modello, scope-aware ----------

def _media_scope_dirs(scope: str):
    """(secrets_dir, media_dir) per uno scope: 'hub' o un project name."""
    if not scope or scope == "hub":
        import connectors_io
        return connectors_io.hub_secrets_dir(HUB_PATH), HUB_PATH / "data" / "media"
    proj_root = _project_root(scope)
    if not proj_root or not proj_root.is_dir():
        raise HTTPException(404, f"scope '{scope}' not found locally")
    return proj_root / ".anjawiki", proj_root / "data" / "media"


@app.get("/api/media/engines")
async def api_media_engines(request: Request, scope: str = "hub"):
    """Engine immagine utilizzabili per lo scope (cosa è configurato ws→hub)."""
    if not HUB_PATH:
        raise HTTPException(400, "hub not configured")
    _require_scope_access(request, scope)
    import image_gen
    secrets_dir, _ = _media_scope_dirs(scope)
    return JSONResponse({"scope": scope, "engines": image_gen.available(HUB_PATH, secrets_dir)})


@app.get("/api/media/models")
async def api_media_models(request: Request, scope: str = "hub"):
    """Catalogo modelli immagine con flag `ready` (key configurata) per lo scope."""
    if not HUB_PATH:
        raise HTTPException(400, "hub not configured")
    _require_scope_access(request, scope)
    import image_gen
    secrets_dir, _ = _media_scope_dirs(scope)
    return JSONResponse({"scope": scope, "models": image_gen.catalog(HUB_PATH, secrets_dir)})


@app.post("/api/media/generate")
async def api_media_generate(request: Request, payload: dict = Body(...)):
    """Genera un'immagine. Body: {scope, prompt, model, options?}. Credenziali
    risolte ws→hub. L'asset finisce in <scope>/data/media/."""
    if not HUB_PATH:
        raise HTTPException(400, "hub not configured")
    scope = (payload.get("scope") or "hub").strip()
    if scope and scope != "hub":
        _require_ws_access(request, scope)
    prompt = (payload.get("prompt") or "").strip()
    model = (payload.get("model") or "").strip()
    engine = (payload.get("engine") or "").strip()   # legacy fallback
    if not prompt:
        raise HTTPException(400, "prompt required")
    opts = payload.get("options") or {}
    if not isinstance(opts, dict):
        raise HTTPException(400, "options must be an object")
    opts.pop("engine", None); opts.pop("model", None)
    import image_gen
    secrets_dir, media_dir = _media_scope_dirs(scope)
    # generazione bloccante → threadpool
    res = await asyncio.get_event_loop().run_in_executor(
        None, lambda: image_gen.generate(HUB_PATH, secrets_dir, media_dir, prompt, model=model, engine=engine, **opts))
    res["scope"] = scope
    if not res.get("ok"):
        raise HTTPException(400, res.get("error") or "generation failed")
    return JSONResponse(res)


# --- OAuth Google in-app (collega GSC/GA dalla UI) ------------------------------

def _oauth_redirect_uri(request: Request) -> str:
    return str(request.base_url).rstrip("/") + "/api/google/oauth/callback"


@app.get("/api/google/oauth/status")
async def api_google_oauth_status(request: Request, scope: str = "hub"):
    """Stato collegamento Google per lo scope: client configurato? token presente?"""
    if not HUB_PATH:
        raise HTTPException(400, "hub not configured")
    _require_scope_access(request, scope)   # scope non è nel path/query 'project' → gate esplicito
    import google_oauth
    secrets_dir, _ = _media_scope_dirs(scope)
    return JSONResponse(google_oauth.status(secrets_dir, HUB_PATH / ".anjawiki"))


@app.get("/api/google/resources")
async def api_google_resources(request: Request, scope: str = "hub"):
    """Siti GSC + proprietà GA4 visibili col token collegato — alimenta i picker
    dei Connettori (niente resource-ID incollati a mano)."""
    if not HUB_PATH:
        raise HTTPException(400, "hub not configured")
    _require_scope_access(request, scope)
    import google_collect
    secrets_dir, _ = _media_scope_dirs(scope)
    token = google_collect.find_token(secrets_dir, HUB_PATH / ".anjawiki")
    if not token:
        raise HTTPException(400, "Google not connected for this scope")
    try:
        session, _creds = google_collect._session(token)
        r = session.get("https://www.googleapis.com/webmasters/v3/sites", timeout=20)
        sites = sorted(e.get("siteUrl", "") for e in (r.json().get("siteEntry") or []))
        r2 = session.get("https://analyticsadmin.googleapis.com/v1beta/accountSummaries?pageSize=200", timeout=20)
        props = []
        for a in (r2.json().get("accountSummaries") or []):
            for p in (a.get("propertySummaries") or []):
                pid = (p.get("property") or "").split("/")[-1]
                if pid:
                    props.append({"id": pid, "name": p.get("displayName") or pid})
        props.sort(key=lambda x: x["name"].lower())
        # Merchant Center: best-effort — il token può non avere ancora lo scope
        # content (ricollega Google) e non deve rompere i picker GSC/GA4.
        merchant = []
        try:
            r3 = session.get("https://merchantapi.googleapis.com/accounts/v1/accounts?pageSize=200", timeout=20)
            if r3.status_code == 200:
                for a in (r3.json().get("accounts") or []):
                    aid = (a.get("name") or "").split("/")[-1]
                    if aid:
                        merchant.append({"id": aid, "name": a.get("accountName") or aid})
                merchant.sort(key=lambda x: x["name"].lower())
        except Exception:
            pass
        return JSONResponse({"gsc_sites": sites, "ga4_properties": props,
                             "merchant_accounts": merchant})
    except Exception as e:
        raise HTTPException(502, f"Google resources listing failed: {e}")


@app.get("/api/google/oauth/start")
async def api_google_oauth_start(request: Request, scope: str = "hub"):
    """Avvia il consenso OAuth Google → redirect a Google. Il token verrà salvato
    nello scope indicato."""
    if not HUB_PATH:
        raise HTTPException(400, "hub not configured")
    _require_scope_access(request, scope)
    import google_oauth
    secrets_dir, _ = _media_scope_dirs(scope)
    url = google_oauth.start(HUB_PATH / ".anjawiki", _oauth_redirect_uri(request), secrets_dir)
    if not url:
        raise HTTPException(400, "Google OAuth client not configured: upload "
                                 "google-oauth-client.json at hub level (.anjawiki/).")
    return RedirectResponse(url)


@app.get("/api/google/oauth/callback")
async def api_google_oauth_callback(request: Request, code: str = "", state: str = ""):
    """Callback OAuth: scambia il code per il token e lo salva; torna alla UI."""
    if not HUB_PATH:
        raise HTTPException(400, "hub not configured")
    import google_oauth
    res = google_oauth.callback(HUB_PATH / ".anjawiki", _oauth_redirect_uri(request), code, state)
    if not res.get("ok"):
        print(f"[google-oauth] callback FAILED: {res.get('error')} "
              f"(redirect_uri={_oauth_redirect_uri(request)})", flush=True)
    return RedirectResponse("/?google=" + ("ok" if res.get("ok") else "err"), status_code=303)


# --- Brain personale/condiviso (F3) -----------------------------------------

def _default_user(request: Request = None) -> str:
    """L'utente corrente. In Concierge è il principal di SESSIONE (request.state.user,
    validato dall'auth_gate); altrimenti il default_user da config.json. Mai da input
    client (anti-IDOR)."""
    if request is not None and getattr(request.state, "user", None):
        try:
            import auth_io
            if auth_io.get_mode(HUB_PATH) == "concierge":
                return request.state.user
        except Exception:
            pass
    try:
        cfg = json.loads((HUB_PATH / "config.json").read_text(encoding="utf-8"))
        return (cfg.get("default_user") or "user").strip()
    except Exception:
        return "user"


def _brain_dir(scope: str, request: Request = None) -> Optional[Path]:
    """Risolve la dir delle note brain. hub → <hub>/brain (condiviso).
    user → <hub>/users/<slug>-brain, dove <slug> è risolto server-side (sessione in
    Concierge, default in personal), MAI dal client (anti-IDOR)."""
    if not HUB_PATH:
        return None
    if scope == "hub":
        return HUB_PATH / "brain"
    if scope == "user":
        slug = _default_user(request)
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", slug):
            return None
        return HUB_PATH / "users" / f"{slug}-brain"
    return None


@app.get("/api/brain/notes")
async def api_brain_notes(request: Request, scope: str = "user", q: str = ""):
    """Lista (o cerca) le note del brain. scope=user (personale, owner di sessione) |
    hub (condiviso)."""
    d = _brain_dir(scope, request)
    if d is None:
        raise HTTPException(400, "invalid scope")
    import brain_io
    if q.strip():
        # semantico auto-attivo quando c'è una key embedding (graceful → lessicale senza)
        emb = None
        if os.environ.get("ANJA_BRAIN_SEMANTIC", "1") == "1":
            import embeddings
            emb = embeddings.get_embedder(HUB_PATH)
        notes = brain_io.search_notes(d, q, embedder=emb)
    else:
        notes = brain_io.list_notes(d)
    resolved = _default_user(request) if scope == "user" else ""
    return JSONResponse({"scope": scope, "user": resolved, "notes": notes})


@app.get("/api/brain/note")
async def api_brain_note(request: Request, scope: str = "user", slug: str = ""):
    d = _brain_dir(scope, request)
    if d is None:
        raise HTTPException(400, "invalid scope")
    import brain_io
    note = brain_io.read_note(d, slug)
    if note is None:
        raise HTTPException(404, "note not found")
    return JSONResponse(note)


@app.post("/api/brain/note")
async def api_brain_note_save(request: Request, payload: dict = Body(...)):
    d = _brain_dir(payload.get("scope", "user"), request)
    if d is None:
        raise HTTPException(400, "invalid scope")
    import brain_io
    res = brain_io.save_note(d, (payload.get("slug") or "").strip(),
                             payload.get("title") or "", payload.get("body") or "")
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "save failed"))
    return JSONResponse(res)


@app.post("/api/brain/note/delete")
async def api_brain_note_delete(request: Request, payload: dict = Body(...)):
    d = _brain_dir(payload.get("scope", "user"), request)
    if d is None:
        raise HTTPException(400, "invalid scope")
    import brain_io
    return JSONResponse(brain_io.delete_note(d, (payload.get("slug") or "").strip()))


@app.post("/api/brain/promote")
async def api_brain_promote(request: Request, payload: dict = Body(...)):
    """Promuove una nota personale → brain condiviso (con provenienza, originale resta).
    L'utente sorgente è il principal di sessione (anti-IDOR), non dal body."""
    ud, sd = _brain_dir("user", request), _brain_dir("hub")
    if ud is None or sd is None:
        raise HTTPException(400, "invalid scope")
    import brain_io
    res = brain_io.promote(ud, sd, (payload.get("slug") or "").strip(), by=_default_user(request))
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "promote failed"))
    return JSONResponse(res)


# --- Auth / identità (F4 Concierge) -----------------------------------------

def _require_admin(request: Request) -> None:
    """In concierge: serve sessione owner/admin. In personal: NO-OP (local owner)."""
    import auth_io
    if auth_io.get_mode(HUB_PATH) != "concierge":
        return
    u = auth_io.get_user(HUB_PATH, request.state.user) if getattr(request.state, "user", None) else None
    if not u or u["role"] not in ("owner", "admin"):
        raise HTTPException(403, "permission denied (requires admin/owner)")


def _acting_role(request: Request) -> str | None:
    """Ruolo del chiamante in concierge (per la gerarchia auth_io.can_manage).
    None in personal = local owner onnipotente, nessun vincolo gerarchico."""
    import auth_io
    if auth_io.get_mode(HUB_PATH) != "concierge":
        return None
    me = getattr(request.state, "user", None)
    u = auth_io.get_user(HUB_PATH, me) if me else None
    return u["role"] if u else None


def _require_ws_access(request: Request, ws_name: str) -> None:
    """F4b: in concierge l'utente deve essere owner/admin o membro del workspace,
    altrimenti 403. In personal è NO-OP (local owner)."""
    import auth_io, membership_io
    if auth_io.get_mode(HUB_PATH) != "concierge":
        return
    me = getattr(request.state, "user", None)
    u = auth_io.get_user(HUB_PATH, me) if me else None
    role = u["role"] if u else None
    if not membership_io.can_access(HUB_PATH, ws_name, me, role):
        raise HTTPException(403, "workspace access denied")


@app.get("/api/auth/me")
async def api_auth_me(request: Request):
    """Stato auth per il bootstrap del frontend: mode + utente di sessione."""
    import auth_io
    user = auth_io.get_user(HUB_PATH, request.state.user) if getattr(request.state, "user", None) else None
    return JSONResponse({
        "mode": auth_io.get_mode(HUB_PATH),
        "authenticated": bool(user),
        "user": user,
        "has_users": bool(auth_io.list_users(HUB_PATH)),
    })


# Rate-limit login: lockout in-memory per (IP|slug) contro il brute-force.
_LOGIN_ATTEMPTS: dict[str, list] = {}
_LOGIN_MAX_FAILS = 5
_LOGIN_WINDOW = 300      # finestra 5 min


def _login_key(request: Request, slug: str) -> str:
    ip = request.client.host if request.client else "?"
    return f"{ip}|{(slug or '').strip().lower()}"


def _login_throttle(key: str) -> None:
    now = time.time()
    if len(_LOGIN_ATTEMPTS) > 5000:   # guard anti-crescita (spray IP/slug)
        _LOGIN_ATTEMPTS.clear()
    fails = [t for t in _LOGIN_ATTEMPTS.get(key, []) if now - t < _LOGIN_WINDOW]
    _LOGIN_ATTEMPTS[key] = fails
    if len(fails) >= _LOGIN_MAX_FAILS:
        raise HTTPException(429, "too many failed attempts, try again in a few minutes")


@app.post("/api/auth/login")
async def api_auth_login(request: Request, payload: dict = Body(...)):
    import auth_io
    key = _login_key(request, payload.get("slug", ""))
    _login_throttle(key)
    u = auth_io.verify(HUB_PATH, payload.get("slug", ""), payload.get("password", ""))
    if not u:
        _LOGIN_ATTEMPTS.setdefault(key, []).append(time.time())
        raise HTTPException(401, "invalid credentials")
    _LOGIN_ATTEMPTS.pop(key, None)   # login riuscito → azzera il contatore
    token = auth_io.make_session(HUB_PATH, u["slug"])
    resp = JSONResponse({"ok": True, "user": u})
    # Secure quando la connessione è HTTPS (diretta o dietro proxy TLS): il cookie di
    # sessione non deve viaggiare in chiaro. Su http://localhost resta non-Secure (dev).
    is_https = (request.url.scheme == "https"
                or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"
                or os.environ.get("ANJA_COOKIE_SECURE") == "1")
    resp.set_cookie(auth_io.SESSION_COOKIE, token, httponly=True, samesite="lax",
                    secure=is_https, max_age=7 * 24 * 3600, path="/")
    return resp


@app.post("/api/auth/logout")
async def api_auth_logout():
    import auth_io
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth_io.SESSION_COOKIE, path="/")
    return resp


@app.get("/api/auth/users")
async def api_auth_list_users(request: Request):
    import auth_io
    if auth_io.get_mode(HUB_PATH) == "concierge":
        _require_admin(request)
    return JSONResponse({"users": auth_io.list_users(HUB_PATH)})


@app.post("/api/auth/users")
async def api_auth_create_user(request: Request, payload: dict = Body(...)):
    """Crea un utente. Bootstrap: il primo utente (= owner) è creabile senza auth;
    poi serve owner/admin (in concierge)."""
    import auth_io
    actor = None
    if auth_io.list_users(HUB_PATH):
        _require_admin(request)
        actor = _acting_role(request)   # gerarchia: solo owner conia admin/owner
    try:
        u = auth_io.create_user(HUB_PATH, payload.get("slug", ""), payload.get("name", ""),
                                payload.get("password", ""), payload.get("role"),
                                actor_role=actor)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return JSONResponse({"ok": True, "user": u})


@app.post("/api/auth/mode")
async def api_auth_set_mode(request: Request, payload: dict = Body(...)):
    """Cambia il master-switch personal⇄concierge. Solo owner (in concierge)."""
    import auth_io
    if auth_io.get_mode(HUB_PATH) == "concierge":
        u = auth_io.get_user(HUB_PATH, request.state.user) if getattr(request.state, "user", None) else None
        if not u or u["role"] != "owner":
            raise HTTPException(403, "only an owner can change mode")
    try:
        new_mode = auth_io.set_mode(HUB_PATH, payload.get("mode", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return JSONResponse({"ok": True, "mode": new_mode})


@app.post("/api/auth/users/{slug}/password")
async def api_auth_set_password(slug: str, request: Request, payload: dict = Body(...)):
    """Cambio password. Self-service (cambi la TUA → serve `current`) oppure reset
    admin/owner di un utente di ruolo INFERIORE (gerarchia). Personal = NO-OP gate."""
    import auth_io
    me = getattr(request.state, "user", None)
    if auth_io.get_mode(HUB_PATH) == "concierge" and slug == me:
        if not auth_io.verify(HUB_PATH, slug, payload.get("current", "")):
            raise HTTPException(403, "current password is incorrect")
        actor = None   # self-service: nessun vincolo gerarchico
    else:
        _require_admin(request)
        actor = _acting_role(request)
    try:
        auth_io.set_password(HUB_PATH, slug, payload.get("password", ""), actor_role=actor)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return JSONResponse({"ok": True})


@app.patch("/api/auth/users/{slug}/role")
async def api_auth_set_role(slug: str, request: Request, payload: dict = Body(...)):
    """Cambia ruolo. Solo admin/owner; gerarchia: solo un owner può toccare/creare
    admin-owner; guard: non declassare l'ultimo owner."""
    import auth_io
    _require_admin(request)
    try:
        u = auth_io.set_role(HUB_PATH, slug, payload.get("role", ""), actor_role=_acting_role(request))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return JSONResponse({"ok": True, "user": u})


@app.delete("/api/auth/users/{slug}")
async def api_auth_delete_user(slug: str, request: Request):
    """Elimina utente. Solo admin/owner; gerarchia (solo owner elimina admin/owner);
    no auto-eliminazione; guard: non eliminare l'ultimo owner."""
    import auth_io
    _require_admin(request)
    if auth_io.get_mode(HUB_PATH) == "concierge" and slug == getattr(request.state, "user", None):
        raise HTTPException(400, "you cannot delete yourself")
    try:
        auth_io.delete_user(HUB_PATH, slug, actor_role=_acting_role(request))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return JSONResponse({"ok": True})


@app.get("/api/workspaces/{name}/members")
async def api_ws_members_get(name: str, request: Request):
    """Membri di un workspace (F4b). Solo admin/owner."""
    import membership_io
    _require_admin(request)
    return JSONResponse({"members": membership_io.workspace_members(HUB_PATH, name)})


@app.post("/api/workspaces/{name}/members")
async def api_ws_members_set(name: str, request: Request, payload: dict = Body(...)):
    """Sovrascrive i membri di un workspace (lista di slug utente). Solo admin/owner.
    Valida che ogni slug sia un utente esistente."""
    import auth_io, membership_io
    _require_admin(request)
    members = payload.get("members")
    if not isinstance(members, list):
        raise HTTPException(400, "members must be a list of slugs")
    valid = {u["slug"] for u in auth_io.list_users(HUB_PATH)}
    unknown = [str(m) for m in members if str(m).strip().lower() not in valid]
    if unknown:
        raise HTTPException(400, f"unknown users: {', '.join(unknown)}")
    try:
        saved = membership_io.set_workspace_members(HUB_PATH, name, members)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return JSONResponse({"ok": True, "members": saved})


@app.post("/api/project/piano/sync")
async def api_project_piano_sync(project: str = ""):
    """[DEPRECATO — non più usato dalla UI] Sync data/PIANO.md → card kanban.
    Il piano editoriale ora ha la sua fonte (PIANO.md via GET /api/project/piano)
    e il kanban mostra solo task operativi. Endpoint lasciato attivo per
    compat/uso manuale, ma scollegato dalla webapp."""
    if not HUB_PATH or not project:
        raise HTTPException(400, "project required")
    proj_root = _project_root(project)
    if not proj_root or not proj_root.is_dir():
        raise HTTPException(404, f"project '{project}' not found locally")
    try:
        import piano_kanban
    except Exception as e:
        raise HTTPException(500, f"piano_kanban not available: {e}")
    res = piano_kanban.sync_piano_to_kanban(HUB_PATH, project, proj_root / "data" / "PIANO.md")
    if not res.get("ok"):
        raise HTTPException(404, res.get("error", "sync failed"))
    return JSONResponse(res)


@app.post("/api/project/file")
async def api_project_file_save(request: Request, payload: dict = Body(...)):
    """Salva contenuto di un file in un progetto registrato (Fase 4-IDE+ L1.6).

    Body: {project, path, content, expected_size?}
    - Backup automatico: `<file>.anja-bak.<timestamp>` (keep only last 5)
    - Size limit: 5MB
    - Path traversal guard
    - Optimistic concurrency check via expected_size se fornito
    """
    project = (payload.get("project") or "").strip()
    rel_path = (payload.get("path") or "").lstrip("/").lstrip("\\")
    content = payload.get("content")
    if not project or not rel_path or content is None:
        raise HTTPException(400, "project, path, content required")
    _require_ws_access(request, project)
    if not isinstance(content, str):
        raise HTTPException(400, "content must be string")
    if len(content.encode("utf-8")) > 5 * 1024 * 1024:
        raise HTTPException(413, "file too large (>5MB)")
    if ".." in rel_path or rel_path.startswith("/"):
        raise HTTPException(400, "invalid path")

    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    projects = _build_projects_context()
    proj_root = None
    for p in projects:
        if p.get("name") == project:
            loc = p.get("location") or {}
            if loc.get("kind") == "local" and loc.get("path"):
                proj_root = Path(loc["path"]).resolve()
            break
    if not proj_root or not proj_root.is_dir():
        raise HTTPException(404, f"project '{project}' not found locally")

    target = (proj_root / rel_path).resolve()
    try:
        target.relative_to(proj_root)
    except ValueError:
        raise HTTPException(400, "path outside project")

    expected = payload.get("expected_size")
    if expected is not None and target.exists():
        try:
            actual = target.stat().st_size
            if int(expected) != actual:
                raise HTTPException(409, f"file changed on disk (expected {expected}, found {actual})")
        except (ValueError, TypeError):
            pass

    backup_path = None
    if target.is_file():
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = target.with_suffix(target.suffix + f".anja-bak.{ts}")
        try:
            backup_path.write_bytes(target.read_bytes())
        except Exception as e:
            print(f"[project/file] backup error: {e}")
            backup_path = None
        try:
            existing_backups = sorted(
                target.parent.glob(f"{target.name}.anja-bak.*"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for old in existing_backups[5:]:
                try:
                    old.unlink()
                except Exception:
                    pass
        except Exception:
            pass

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(content, encoding="utf-8")
    except Exception as e:
        raise HTTPException(500, f"write failed: {e}")

    return JSONResponse({
        "ok": True,
        "path": rel_path,
        "size": target.stat().st_size,
        "backup": backup_path.name if backup_path else None,
        "saved_at": time.time(),
    })


@app.get("/api/conversations")
async def api_conversations_list(scope: str = ""):
    """Lista conversazioni persistite. Filtrabile per scope (hub | project:<name>)."""
    chat = _get_chat_module()
    if not chat:
        return JSONResponse({"conversations": [], "_error": "claude-agent-sdk not available"})
    convs = chat.list_conversations(WEBAPP_DIR)
    if scope:
        convs = [c for c in convs if c.get("scope") == scope]
    return JSONResponse({"conversations": convs})


@app.get("/api/conversations/{conv_id}")
async def api_conversation_get(conv_id: str):
    if "/" in conv_id or ".." in conv_id:
        raise HTTPException(400, "invalid id")
    chat = _get_chat_module()
    if not chat:
        raise HTTPException(503, "claude-agent-sdk not available")
    conv = chat.load_conversation(WEBAPP_DIR, conv_id)
    if not conv:
        raise HTTPException(404, "conversation not found")
    return JSONResponse(conv)


async def _persist_chat_turn_done(state, conv_id: str, scope: str, user_msg: str,
                                   provider: str, model: str, effort: str,
                                   projects: list) -> None:
    """F-Notify-5 — Persist completion side-effects in background (idle persistence
    opzione A). Idempotent — flag `state._persisted` previene double-write."""
    if getattr(state, "_persisted", False):
        return
    state._persisted = True
    chat = _get_chat_module()
    if not chat:
        return
    try:
        existing = chat.load_conversation(WEBAPP_DIR, conv_id) or {
            "id": conv_id, "title": user_msg[:60], "scope": scope, "messages": [],
        }
        existing["messages"].append({"role": "user", "content": user_msg})
        existing["messages"].append({"role": "claude", "content": state.full_response})
        if not existing.get("title"):
            existing["title"] = user_msg[:60]
        if "scope" not in existing:
            existing["scope"] = scope
        chat.save_conversation(
            WEBAPP_DIR, conv_id, existing["messages"], existing["title"],
            existing.get("scope", "hub"),
            provider=provider, model=model, effort=effort or "",
        )
        # last_usage in conv.json
        if state.last_usage:
            conv_path = WEBAPP_DIR / "conversations" / f"{conv_id}.json"
            if conv_path.is_file():
                try:
                    payload = json.loads(conv_path.read_text(encoding="utf-8"))
                    payload["last_usage"] = state.last_usage
                    conv_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
        # Session mirror (rate-limited)
        try:
            from session_mirror import mirror_from_file
            mirror_from_file(conv_id, WEBAPP_DIR, HUB_PATH, projects=projects)
        except Exception as e:
            print(f"[anja] session_mirror error (bg): {e}")
        # F-Notify: notification per chat completed
        try:
            if HUB_PATH:
                chat_scope = existing.get("scope", "hub")
                notif_scope = chat_scope if chat_scope.startswith("workspace:") else "hub"
                notif_bus.publish(
                    HUB_PATH, source="chat", category="success",
                    title="Chat completed",
                    body=(existing.get("title") or user_msg)[:120],
                    action={"label": "Open", "url": f"/#chat/{conv_id}", "type": "navigate"},
                    payload={"conv_id": conv_id, "scope": chat_scope,
                             "model": model, "provider": provider},
                    scope=notif_scope,
                )
        except Exception:
            pass
        # F-Proactive-3: sensore commitments impliciti (fire-and-forget, post-risposta)
        try:
            if HUB_PATH:
                import commitment_sensor as _cs
                _cs.schedule_commitment_sensor(
                    user_msg, state.full_response,
                    existing.get("scope", "hub"), HUB_PATH, source_conv=conv_id)
        except Exception as e:
            print(f"[commitment] schedule error: {e}")
    except Exception as e:
        print(f"[anja] _persist_chat_turn_done error: {e}")


@app.get("/api/chat/active_streams")
async def api_chat_active_streams(scope: Optional[str] = None):
    """F-Notify-5 — Snapshot di stream attualmente attivi (per scope o globali)."""
    items = chat_streams.list_active()
    if scope:
        items = [s for s in items if s["scope"] == scope]
    return {"items": items, "stats": chat_streams.stats()}


@app.post("/api/chat/cancel")
async def api_chat_cancel(payload: dict = Body(...)):
    """F-Notify-5 — Cancel attivo stream per conv_id."""
    conv_id = (payload.get("conv_id") or "").strip()
    if not conv_id:
        raise HTTPException(400, "conv_id required")
    ok = chat_streams.cancel(conv_id)
    return {"ok": ok}


# === F-AgentSessions SPIKE — controlli sessione persistente (flag ANJA_ASP_ENABLED) ===
# Auth: coperti dal middleware auth_gate come tutti gli /api/* (401/403 fail-closed
# in Concierge). Ownership per-conversazione (member che steera conv altrui via
# conv_id noto): stesso residuo del preesistente /api/chat/cancel — si chiude in
# Fase 2 col control-plane permessi (design §6/§11), insieme al can_use_tool.

def _asp_or_404() -> None:
    """La superficie /api/session/* esiste solo a feature attiva."""
    if os.environ.get("ANJA_ASP_ENABLED") != "1":
        raise HTTPException(404, "agent sessions disabled (ANJA_ASP_ENABLED)")


@app.post("/api/session/steer")
async def api_session_steer(payload: dict = Body(...)):
    """Inietta un messaggio nel turno in corso della sessione ASP."""
    _asp_or_404()
    conv_id = (payload.get("conv_id") or "").strip()
    message = (payload.get("message") or "").strip()
    if not conv_id or not message:
        raise HTTPException(400, "conv_id and message required")
    import claude_session
    ok = await claude_session.pool.steer(conv_id, message)
    return {"ok": ok, "reason": None if ok else "no active turn for conv_id"}


@app.post("/api/session/interrupt")
async def api_session_interrupt(payload: dict = Body(...)):
    """Interrompe il turno in corso; la sessione resta viva per il turno dopo."""
    _asp_or_404()
    conv_id = (payload.get("conv_id") or "").strip()
    if not conv_id:
        raise HTTPException(400, "conv_id required")
    import claude_session
    ok = await claude_session.pool.interrupt(conv_id)
    return {"ok": ok, "reason": None if ok else "no active turn for conv_id"}


@app.get("/api/session/stats")
async def api_session_stats():
    """Stato del pool sessioni ASP (debug spike)."""
    _asp_or_404()
    import claude_session
    return claude_session.pool.stats()


@app.post("/api/session/set")
async def api_session_set(request: Request, payload: dict = Body(...)):
    """Fase 1/3 ASP — session.set runtime sulla sessione viva: `model` e/o
    `permission_mode`. `auto` (= bypassPermissions, "consenti sempre tutto")
    è un'azione da approver: owner/admin in Concierge, e viene audita."""
    _asp_or_404()
    conv_id = (payload.get("conv_id") or "").strip()
    model = (payload.get("model") or "").strip()
    pmode = (payload.get("permission_mode") or "").strip()
    if not conv_id or (not model and not pmode):
        raise HTTPException(400, "conv_id + model and/or permission_mode required")
    if pmode:
        if os.environ.get("ANJA_ASP_PERMISSIONS") != "1":
            raise HTTPException(400, "permission_mode requires ANJA_ASP_PERMISSIONS=1")
        if pmode not in ("default", "acceptEdits", "plan", "auto"):
            raise HTTPException(400, "permission_mode: default|acceptEdits|plan|auto")
        if pmode == "auto":
            by = _asp_require_approver(request)
            import asp_permissions
            asp_permissions.record_decision(
                tool="session.set", target=conv_id, decision="mode-auto",
                by=by, scope="", conv_id=conv_id)
            pmode = "bypassPermissions"
    import claude_session
    return await claude_session.pool.set(conv_id, model=model or None,
                                         permission_mode=pmode or None)


def _asp_require_approver(request: Request) -> str:
    """In Concierge approvare (permessi/piani) è owner/admin, fail-closed.
    Ritorna l'identità per l'audit."""
    try:
        import auth_io
        if HUB_PATH and auth_io.get_mode(HUB_PATH) == "concierge":
            slug = getattr(request.state, "user", None)
            u = auth_io.get_user(HUB_PATH, slug) if slug else None
            role = (u or {}).get("role", "member")
            if role not in ("owner", "admin"):
                raise HTTPException(403, "approval restricted to owner/admin")
            return slug or "concierge"
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(403, f"role check failed (fail-closed): {e}")
    return "owner"


@app.post("/api/session/permission")
async def api_session_permission(request: Request, payload: dict = Body(...)):
    """Fase 2 ASP — risolve una permission.requested (allow|always_allow|deny)."""
    _asp_or_404()
    request_id = (payload.get("request_id") or "").strip()
    decision = (payload.get("decision") or "").strip()
    if not request_id or decision not in ("allow", "always_allow", "deny"):
        raise HTTPException(400, "request_id + decision (allow|always_allow|deny) required")
    by = _asp_require_approver(request)
    import asp_permissions
    meta = asp_permissions.pending.resolve(
        request_id, decision, message=(payload.get("message") or ""), by=by)
    if meta is None:
        raise HTTPException(404, "request not found or already resolved")
    return {"ok": True, "request_id": request_id, "decision": decision,
            "tool": meta["tool"]}


@app.post("/api/session/plan")
async def api_session_plan(request: Request, payload: dict = Body(...)):
    """Fase 3 ASP — risolve una plan.proposed: approve | replan (+feedback)."""
    _asp_or_404()
    request_id = (payload.get("request_id") or "").strip()
    decision = (payload.get("decision") or "").strip()
    if not request_id or decision not in ("approve", "replan"):
        raise HTTPException(400, "request_id + decision (approve|replan) required")
    by = _asp_require_approver(request)
    import asp_permissions
    meta = asp_permissions.pending.resolve(
        request_id, "approve" if decision == "approve" else "deny",
        message=(payload.get("feedback") or ""), by=by)
    if meta is None:
        raise HTTPException(404, "plan not found or already resolved")
    return {"ok": True, "request_id": request_id, "decision": decision}


@app.get("/api/session/permissions/pending")
async def api_session_permissions_pending():
    """Fase 2 ASP — richieste in attesa (per resume UI dopo reconnect)."""
    _asp_or_404()
    import asp_permissions
    return {"pending": asp_permissions.pending.snapshot()}


@app.get("/api/session/diff")
async def api_session_diff(conv_id: str):
    """Fase 4 ASP — summary + patch del branch di sessione (vs base)."""
    _asp_or_404()
    import asp_git
    ctx = asp_git.get_ctx(conv_id.strip())
    if ctx is None:
        raise HTTPException(404, "no git session for this conversation")
    summary = await asyncio.to_thread(asp_git.finalize_turn, ctx)
    patch = await asyncio.to_thread(asp_git.full_patch, ctx) if summary else ""
    return {"conv_id": conv_id, "summary": summary, "patch": patch}


@app.post("/api/session/merge")
async def api_session_merge(request: Request, payload: dict = Body(...)):
    """Fase 4 ASP — chiude la git-sessione: merge nel branch base o discard.
    Azione da approver (owner/admin in Concierge), audit nel decision-trail."""
    _asp_or_404()
    conv_id = (payload.get("conv_id") or "").strip()
    decision = (payload.get("decision") or "").strip()
    if not conv_id or decision not in ("merge", "discard"):
        raise HTTPException(400, "conv_id + decision (merge|discard) required")
    by = _asp_require_approver(request)
    import asp_git
    ctx = asp_git.get_ctx(conv_id)
    if ctx is None:
        raise HTTPException(404, "no git session for this conversation")
    if decision == "merge":
        res = await asyncio.to_thread(asp_git.merge, ctx)
    else:
        res = await asyncio.to_thread(asp_git.discard, ctx)
    import asp_permissions
    asp_permissions.record_decision(
        tool="session.merge", target=ctx.get("branch", "?"),
        decision=f"{decision}:{'ok' if res.get('ok') else 'fail'}",
        by=by, scope="", conv_id=conv_id)
    state = chat_streams.get(conv_id)
    if state is not None:
        state.append({"type": "merge.completed", "decision": decision, **res})
    return res


@app.get("/api/session/log")
async def api_session_log(conv_id: str, since_seq: int = 0, limit: int = 2000):
    """Fase 0 ASP — replay dell'event-log persistito di una conversazione."""
    _asp_or_404()
    if not conv_id.strip():
        raise HTTPException(400, "conv_id required")
    import asp_log
    events = asp_log.get_log(HUB_PATH).read(conv_id.strip(), since_seq=since_seq,
                                            limit=max(1, min(limit, 5000)))
    return {"conv_id": conv_id, "count": len(events), "events": events}


_CHAT_PRUNE_TASK = None


@app.on_event("startup")
async def _startup_chat_prune():
    global _CHAT_PRUNE_TASK
    _CHAT_PRUNE_TASK = asyncio.create_task(chat_streams.prune_loop(interval_sec=60))
    # Fase 0 ASP: persistenza event-log su <hub>/sessions-log/ (flag-gated)
    import asp_log
    if asp_log.enabled() and HUB_PATH:
        chat_streams.set_persist(asp_log.get_log(HUB_PATH).append)
        print(f"[asp] event-log persistence ON → {HUB_PATH}/sessions-log/")
        # Fase 4 ASP: git-sessione (terzo flag, default off)
        import asp_git
        asp_git.configure(HUB_PATH)
        if asp_git.enabled():
            print("[asp] git-sessione ON (worktree per conversazione)")
        # Fase 2 ASP: control-plane permessi (secondo flag, default off)
        import asp_permissions
        if asp_permissions.enabled():
            asp_permissions.configure(HUB_PATH)
            import claude_session
            claude_session.notify_ask_fn = _asp_notify_permission_ask
            print("[asp] permission control-plane ON (can_use_tool + policy)")


async def _asp_notify_diff_ready(conv_id: str, summary: dict):
    """Push best-effort su Telegram quando un diff di sessione è pronto."""
    token = TELEGRAM_DAEMON.token if TELEGRAM_DAEMON else None
    if not token:
        return
    chat_id = None
    if conv_id.startswith("telegram-"):
        try:
            chat_id = int(conv_id.split("-")[1])
        except (IndexError, ValueError):
            chat_id = None
    if chat_id is None:
        try:
            import json as _json
            cfg = _json.loads((HUB_PATH / "config.json").read_text(encoding="utf-8"))
            if cfg.get("notify_telegram"):
                chat_id = int(cfg.get("notify_telegram_chat_id") or 0) or None
        except Exception:
            chat_id = None
    if chat_id is None:
        return
    from telegram_daemon import send_message as _tg_send
    n = len(summary.get("files", []))
    await _tg_send(token, chat_id,
                   f"📝 *Session diff ready* — {n} files "
                   f"(+{summary.get('additions', 0)}/−{summary.get('deletions', 0)}) "
                   f"on `{summary.get('branch', '?')}`\n"
                   f"Review in the UI, or: /merge · /discard")


async def _asp_notify_permission_ask(conv_id: str, tool: str, target: str,
                                     request_id: str):
    """Push best-effort su Telegram quando una permission resta in attesa:
    per le conv telegram-* verso quella chat, altrimenti verso la chat di
    notifica configurata (notify_telegram_chat_id, come il kanban notifier)."""
    token = TELEGRAM_DAEMON.token if TELEGRAM_DAEMON else None
    if not token:
        return
    chat_id = None
    if conv_id.startswith("telegram-"):
        try:
            chat_id = int(conv_id.split("-")[1])
        except (IndexError, ValueError):
            chat_id = None
    if chat_id is None:
        try:
            import json as _json
            cfg = _json.loads((HUB_PATH / "config.json").read_text(encoding="utf-8"))
            if cfg.get("notify_telegram"):
                chat_id = int(cfg.get("notify_telegram_chat_id") or 0) or None
        except Exception:
            chat_id = None
    if chat_id is None:
        return
    from telegram_daemon import send_message as _tg_send
    await _tg_send(token, chat_id,
                   f"🔐 *Permission requested* — `{tool}`\n"
                   f"`{target[:150]}`\n"
                   f"Reply: /allow · /allow always · /deny")


@app.on_event("shutdown")
async def _shutdown_chat_prune():
    global _CHAT_PRUNE_TASK
    if _CHAT_PRUNE_TASK:
        _CHAT_PRUNE_TASK.cancel()


@app.websocket("/api/chat")
async def ws_chat(websocket: WebSocket):
    """
    WebSocket endpoint per chat streaming.

    Client → server: {"message": "...", "conversation_id"?: "...", "model"?: "sonnet|opus"}
    Server → client (stream):
        {"type": "text", "content": "..."}
        {"type": "tool_use", "name": "...", "input": {...}}
        {"type": "done"}
        {"type": "error", "message": "..."}
    """
    await websocket.accept()

    chat = _get_chat_module()
    if not chat:
        await websocket.send_json({"type": "error", "message": "claude-agent-sdk not available on the server"})
        await websocket.close()
        return

    if not HUB_PATH:
        await websocket.send_json({"type": "error", "message": "hub not configured"})
        await websocket.close()
        return

    # F-Notify-5: snapshot di stream attivi al connect; il client popola tab da background.
    try:
        await websocket.send_json({
            "type": "active_streams_snapshot",
            "streams": chat_streams.list_active(),
        })
    except Exception:
        pass

    try:
        while True:
            data = await websocket.receive_json()
            # F-Notify-5: resume su reconnect — client riallaccia uno stream attivo per replay buffer
            if data.get("action") == "resume":
                rconv = (data.get("conv_id") or "").strip()
                since_seq = int(data.get("since_seq", 0) or 0)
                state = chat_streams.get(rconv)
                if not state:
                    await websocket.send_json({"type": "error", "message": f"no active stream for {rconv}"})
                    continue
                for ev in state.events_since(since_seq):
                    payload = {k: v for k, v in ev.items() if k != "seq"}
                    payload["_seq"] = ev["seq"]
                    payload["_conv_id"] = rconv
                    try:
                        await websocket.send_json(payload)
                    except Exception:
                        break
                # Continue tailing if not yet completed
                last_seq_sent = state.last_seq
                while not state.completed:
                    new_evs = state.events_since(last_seq_sent)
                    if not new_evs:
                        await asyncio.sleep(0.04)
                        continue
                    for ev in new_evs:
                        payload = {k: v for k, v in ev.items() if k != "seq"}
                        payload["_seq"] = ev["seq"]
                        payload["_conv_id"] = rconv
                        try:
                            await websocket.send_json(payload)
                            last_seq_sent = ev["seq"]
                        except Exception:
                            break
                continue
            user_msg = data.get("message", "").strip()
            if not user_msg:
                continue
            # Fase 13 — project preferences override hub defaults
            _scope_for_defaults = data.get("scope", "hub")
            _hub_def = _resolve_defaults_for_scope(_scope_for_defaults)
            model = data.get("model") or _hub_def["model"]
            provider = data.get("provider") or _hub_def["provider"]  # "claude" | "openai" | "openrouter" | "xai"
            conv_id = data.get("conversation_id")
            scope = data.get("scope", "hub")  # "hub" | "project:<name>" | "agent:<name>"
            effort = data.get("effort")  # None | "low" | "medium" | "high"
            # F-ASP — preferenza permission_mode dal client (sticky nel payload):
            # si applica alla creazione della sessione o al cambio tra i turni.
            # NB Concierge: come il resto del WS chat; il gate per-ruolo su
            # 'auto' via WS rientra nel residuo ownership (Fase 2, design §11).
            asp_mode = (data.get("asp_mode") or "").strip()
            if asp_mode not in ("default", "acceptEdits", "plan", "auto"):
                asp_mode = ""
            if asp_mode == "auto":
                # Fail-closed (security review): 'auto' = bypassPermissions è
                # un'escalation — via WS solo in Personal mode; in Concierge
                # passa esclusivamente dall'endpoint REST role-gated (owner/admin).
                try:
                    import auth_io
                    if HUB_PATH and auth_io.get_mode(HUB_PATH) == "concierge":
                        asp_mode = ""
                except Exception:
                    asp_mode = ""
            if not effort:
                _de = _hub_def.get("effort", "off")
                effort = _de if _de and _de != "off" else None
            # Fase 7k — resume conversation: client passa sdk_session_id se vuole continuity
            sdk_session_id = data.get("sdk_session_id")
            # Fase 7s — toggle media: on = hint CLI giv nel system prompt
            enable_image_gen = bool(data.get("enable_image_gen", False))
            # Fase 23.b — preferenza modello media (image/video). Inietta hint nel system prompt.
            media_model_pref = (data.get("media_model") or "").strip()
            # Fase 24 — Chat attachments: estrai contenuto + injecta nel user_prompt
            raw_attachments = data.get("attachments") or []
            attachment_descriptors = []
            image_attachments = []
            if raw_attachments:
                try:
                    import chat_attachments as _ca
                    for ref in raw_attachments:
                        sf = (ref.get("saved_filename") or "").strip()
                        cv = data.get("conversation_id") or "default"
                        # Path lookup conv-local + re-read extracted text (server-side single source of truth)
                        cdir = _ca.conv_uploads_dir(WEBAPP_DIR, cv)
                        fpath = cdir / sf
                        if not fpath.is_file() or "/" in sf or ".." in sf:
                            continue
                        # Re-extract on-the-fly per coerenza con server
                        # Salviamo descriptor minimal — text è re-extract via chat_attachments
                        try:
                            data_bytes = fpath.read_bytes()
                            d2 = _ca.save_upload(WEBAPP_DIR, cv, ref.get("filename") or sf, data_bytes, mime=ref.get("mime"))
                            # save_upload crea un nuovo file_id — rimuovi duplicato
                            if d2.get("path") and Path(d2["path"]) != fpath:
                                try: Path(d2["path"]).unlink()
                                except Exception: pass
                            d2["path"] = str(fpath)  # restore reale path
                            attachment_descriptors.append(d2)
                            if d2.get("category") == "image":
                                image_attachments.append(d2)
                            # F24.b — Audio STT: trascrivi audio attachment riusando pipeline Telegram
                            if d2.get("category") == "audio" and d2.get("needs_stt"):
                                try:
                                    from telegram_daemon import transcribe_audio
                                    transcript, model_used = await transcribe_audio(
                                        Path(d2["path"]),
                                        hub_path=HUB_PATH,
                                    )
                                    if transcript:
                                        d2["extracted_text"] = transcript
                                        d2["stt_model"] = model_used
                                        d2["preview"] = f"🎤 {transcript[:160]}{'…' if len(transcript) > 160 else ''}"
                                        d2["needs_stt"] = False
                                except Exception as e:
                                    print(f"[chat_attachments] STT failed for {sf}: {e}", flush=True)
                                    d2["extract_error"] = f"STT failed: {e}"
                        except Exception as e:
                            print(f"[chat_attachments] re-extract error for {sf}: {e}", flush=True)
                except ImportError:
                    pass

            if attachment_descriptors:
                try:
                    import chat_attachments as _ca
                    text_block = _ca.attachments_to_prompt_block(attachment_descriptors)
                    if text_block:
                        user_msg = (user_msg or "") + text_block
                except Exception:
                    pass
            # F24 debug log (visible in server stdout)
            if raw_attachments:
                _img_n = len(image_attachments)
                _doc_n = len(attachment_descriptors) - _img_n
                _missing = len(raw_attachments) - len(attachment_descriptors)
                print(f"[chat_attachments] turn: {len(raw_attachments)} requested → {_doc_n} docs + {_img_n} images extracted, {_missing} missing/skipped", flush=True)
                if _missing > 0:
                    print(f"[chat_attachments] WARN: {_missing} attachment(s) not found on disk — check conv_id consistency upload↔send", flush=True)

            # Fase 7r — guard: blocca modelli non-chat (image/video/audio) selezionati per errore
            NON_CHAT_PATTERN = re.compile(r"(image|imagine|video|tts|whisper|speech|audio|embedding|moderation|dall-e|stable-diffusion)", re.I)
            if NON_CHAT_PATTERN.search(model):
                await websocket.send_json({
                    "type": "error",
                    "message": (
                        f"Model '{model}' is not chat-compatible (it's for image/video/audio). "
                        f"Pick a conversational model (e.g. 'grok-4.3', 'sonnet', 'gpt-5.5'). "
                        f"To generate images, ask the AI 'generate an image of...' — it will use the giv CLI automatically."
                    ),
                })
                continue

            projects = _build_projects_context()

            # Slash skill/bundle invocation `/skill <slug>` / `/bundle <slug>`:
            # load body into system_prompt, replace user_msg with args (or default).
            _skill_extra_system = ""
            if user_msg.startswith("/skill "):
                user_msg, _skill_extra_system = chat.resolve_skill_invocation(user_msg, HUB_PATH)
            elif user_msg.startswith("/bundle "):
                user_msg, _skill_extra_system = chat.resolve_bundle_invocation(user_msg, HUB_PATH)

            # Resolve cwd + system prompt + tool set in base allo scope
            cwd, kind_target = chat.resolve_chat_cwd(HUB_PATH, scope, projects)
            kind, target = kind_target if isinstance(kind_target, tuple) else ("hub", None)

            # F-ASP Fase 4 — git-sessione: il worktree deve diventare il cwd
            # PRIMA di costruire system prompt/mcp scoping (il modello usa i
            # path assoluti del prompt: col base come cwd scriverebbe lì,
            # bypassando il branch di sessione — trovato dall'e2e).
            asp_git_ctx = None
            if (os.environ.get("ANJA_ASP_ENABLED") == "1"
                    and (provider or "claude") == "claude" and conv_id):
                import asp_git
                if asp_git.enabled():
                    asp_git_ctx = await asyncio.to_thread(
                        asp_git.prepare, Path(cwd), conv_id)
                    if asp_git_ctx:
                        cwd = Path(asp_git_ctx["worktree"])

            providers_chain = None
            no_fallback = False
            agent_cfg_for_scope = None

            # Fase 7u M-Cx 5: identità utente per il composer (placeholder fino a Fase 12)
            user_name = data.get("user_name") or "user"
            user_timezone = data.get("timezone") or ""

            # Fase 4-IDE+ L1.5 — file context injection (chat-with-file)
            file_ctx = data.get("file_context") or None
            file_ctx_block = ""
            if file_ctx and isinstance(file_ctx, dict):
                fc_path = (file_ctx.get("path") or "").strip()
                fc_proj = (file_ctx.get("project") or "").strip()
                fc_content = file_ctx.get("content") or ""
                fc_lang = (file_ctx.get("language") or "").strip()
                fc_cursor = file_ctx.get("cursor") or None
                # Trunca a 50k char per safety
                MAX_FILE_CTX = 50_000
                truncated = False
                if len(fc_content) > MAX_FILE_CTX:
                    fc_content = fc_content[:MAX_FILE_CTX]
                    truncated = True
                if fc_path and fc_proj:
                    cur_line_info = ""
                    if fc_cursor and isinstance(fc_cursor, dict):
                        cur_line_info = f" (cursor: line {fc_cursor.get('line')})"
                    trunc_note = " [TRUNCATED at 50k char]" if truncated else ""
                    file_ctx_block = (
                        f"\n\n## ACTIVE FILE CONTEXT\n"
                        f"The user is chatting about a specific file open in the editor.\n"
                        f"Project: `{fc_proj}` · Path: `{fc_path}` · Language: `{fc_lang or 'unknown'}`{cur_line_info}\n"
                        f"You CAN modify this file directly using Edit/Write tools. "
                        f"Changes apply to disk and the editor reloads automatically.\n"
                        f"Current file content{trunc_note}:\n"
                        f"```{fc_lang}\n{fc_content}\n```\n"
                    )

            if kind == "agent" and target:
                agent_cfg = chat.load_agent_config(HUB_PATH, target)
                agent_cfg_for_scope = agent_cfg
                system_prompt = chat.build_agent_system_prompt(
                    HUB_PATH, target, cwd, agent_cfg,
                    user_prompt=user_msg, image_gen_enabled=enable_image_gen,
                    user_name=user_name, timezone=user_timezone,
                )
                if "model" not in data and agent_cfg.get("default_model"):
                    model = agent_cfg["default_model"]
                if "provider" not in data and agent_cfg.get("default_provider"):
                    provider = agent_cfg["default_provider"]
                if effort is None and agent_cfg.get("default_effort") and agent_cfg["default_effort"] != "off":
                    effort = agent_cfg["default_effort"]
                cfg_tools = agent_cfg.get("allowed_tools", [])
                allowed_tools = cfg_tools if cfg_tools else chat.PROJECT_TOOLS_FULL
                providers_chain = agent_cfg.get("providers", None)
                no_fallback = bool(agent_cfg.get("no_fallback", False))
            elif kind == "project" and target:
                system_prompt = chat.build_project_system_prompt(
                    target, cwd, user_prompt=user_msg, image_gen_enabled=enable_image_gen,
                    hub_name=HUB_PATH.name, user_name=user_name, timezone=user_timezone,
                    hub_path=HUB_PATH,
                )
                allowed_tools = chat.PROJECT_TOOLS_FULL
            else:
                system_prompt = chat.build_system_prompt(
                    HUB_PATH, projects, user_prompt=user_msg, image_gen_enabled=enable_image_gen,
                    user_name=user_name, timezone=user_timezone,
                )
                allowed_tools = chat.HUB_TOOLS_READONLY

            # Append file context block (Fase 4-IDE+ L1.5)
            if file_ctx_block:
                system_prompt = (system_prompt or "") + file_ctx_block

            # Append skill body (B1: /skill <slug> invocation)
            if _skill_extra_system:
                system_prompt = (system_prompt or "") + _skill_extra_system

            # F-CLI-Media — gli agent generano con la CLI `giv` via Bash
            # (skill gen-image-video), non più via MCP anja_images/anja_videos.
            if enable_image_gen:
                system_prompt = (system_prompt or "") + _giv_media_hint(media_model_pref)

            # F24.c — Image-to-video: se ci sono image attachments + media toggle ON,
            # esponi i path locali all'LLM (frame iniziale per giv video --image).
            if enable_image_gen and image_attachments:
                paths = []
                for img in image_attachments:
                    p = img.get("path")
                    fn = img.get("filename", "?")
                    if p:
                        paths.append(f"- `{fn}` → `{p}`")
                if paths:
                    i2v_hint = (
                        f"\n\n## Image-to-video reference\n"
                        f"User attached {len(paths)} image(s) — usale come frame "
                        f"iniziale/reference: `giv video --image <path> …` "
                        f"(o `giv image --input <path> …` per editing/stile):\n"
                        + "\n".join(paths) + "\n"
                    )
                    system_prompt = (system_prompt or "") + i2v_hint

            # Fase 7u M-Cx 5: scope MCP server list (Tier 0 + Tier 1 + Tier 2)
            try:
                from mcp_scoper import scope_mcps as _scope_mcps
                scoped_servers, scope_meta = _scope_mcps(
                    hub_path=HUB_PATH,
                    scope_kind=kind,
                    target_name=target,
                    cwd=cwd,
                    user_prompt=user_msg,
                    active_mcps=data.get("active_mcps") or [],
                    agent_config=agent_cfg_for_scope,
                )
                # F-CLI-Media: gli agent generano via CLI giv (Bash + skill), i
                # server MCP media restano solo per la UI → strip sempre.
                scoped_servers = [s for s in scoped_servers if s not in ("anja_images", "anja_videos")]
                print(f"[anja] mcp_scoper scope={kind}/{target} → {scoped_servers} (reasons: {scope_meta.get('reasons')})")
            except Exception as e:
                print(f"[anja] mcp_scoper failed, fallback to all servers: {e}")
                scoped_servers = None

            # Auto-allowlist MCP tool patterns filtrati per scoped_servers
            allowed_tools = chat.augment_with_mcp(
                allowed_tools, cwd, provider=provider, scoped_servers=scoped_servers
            )
            if not enable_image_gen:
                allowed_tools = [t for t in allowed_tools if not (t.startswith("mcp__anja_images__") or t.startswith("mcp__anja_videos__"))]

            # Inietta catalogo MCP runtime (attivi vs dormienti) — l'LLM sa cosa esiste
            # in questo hub anche quando il keyword routing ne ha esclusi alcuni dal turno.
            cap_block = chat.mcp_capabilities_block(cwd, scoped_servers)
            if cap_block:
                system_prompt = (system_prompt or "") + cap_block

            # Stream
            full_response = ""
            try:
                # F-Notify-5: drainer task + WS reader loop disaccoppiati.
                # Lo stream gira come asyncio.Task indipendente; il WS reader
                # pulla dal buffer. Se la WS muore mid-stream, il task continua
                # in background (opzione A idle persistence).
                stream_kwargs = dict(
                    user_prompt=user_msg, system_prompt=system_prompt, cwd=cwd,
                    model=model, allowed_tools=allowed_tools, effort=effort,
                    providers_chain=providers_chain, no_fallback=no_fallback,
                    provider=provider, resume_session_id=sdk_session_id,
                    scoped_servers=scoped_servers, image_attachments=image_attachments,
                )
                state = chat_streams.register(
                    conv_id or f"_anon-{int(time.time()*1000)}",
                    scope=scope, model=model, provider=provider,
                    user_msg=user_msg, title=user_msg[:60],
                )
                state.completed = False
                state.error = None

                max_duration_sec = int(os.environ.get("ANJA_CHAT_MAX_DURATION_SEC", "600"))
                max_tool_iter = int(os.environ.get("ANJA_CHAT_MAX_TOOL_ITER", "30"))

                async def _drain_and_persist(_state=state, _kwargs=stream_kwargs,
                                              _conv_id=conv_id, _scope=scope,
                                              _user_msg=user_msg, _provider=provider,
                                              _model=model, _effort=effort,
                                              _projects=projects, _max_dur=max_duration_sec,
                                              _max_tool=max_tool_iter,
                                              _asp_mode=asp_mode,
                                              _git_ctx=asp_git_ctx):
                    _saw_done = False
                    try:
                        # F-AgentSessions SPIKE: sessione SDK persistente dietro flag,
                        # solo route claude. Il path esistente resta il default.
                        _use_asp = (
                            os.environ.get("ANJA_ASP_ENABLED") == "1"
                            and (_kwargs.get("provider") or "claude") == "claude"
                        )
                        if _use_asp:
                            import claude_session
                            # Fase 4: _git_ctx arriva dal handler (il cwd nei
                            # kwargs È già il worktree quando attivo)
                            _gen = claude_session.stream_turn(
                                conv_id=_state.conv_id,
                                user_prompt=_kwargs["user_prompt"],
                                system_prompt=_kwargs["system_prompt"],
                                cwd=_kwargs["cwd"],
                                model=_kwargs["model"],
                                allowed_tools=_kwargs["allowed_tools"],
                                effort=_kwargs["effort"],
                                resume_session_id=_kwargs["resume_session_id"],
                                scoped_servers=_kwargs["scoped_servers"],
                                image_attachments=_kwargs["image_attachments"],
                                permission_mode=_asp_mode or None,
                            )
                        else:
                            _gen = chat.stream_response(**_kwargs)
                        async with asyncio.timeout(_max_dur):
                            async for event in _gen:
                                # Fase 4: diff.ready DEVE precedere il done
                                # (i client smettono di leggere al done)
                                if event.get("type") == "done" and _git_ctx:
                                    try:
                                        import asp_git
                                        _summary = await asyncio.to_thread(
                                            asp_git.finalize_turn, _git_ctx)
                                        if _summary:
                                            _state.append({"type": "diff.ready",
                                                           **_summary})
                                            await _asp_notify_diff_ready(
                                                _state.conv_id, _summary)
                                    except Exception as _ge:
                                        print(f"[asp-git] finalize error: {_ge}")
                                _state.append(event)
                                if event.get("type") == "done":
                                    _saw_done = True
                                if _state.tool_iter_count > _max_tool:
                                    raise RuntimeError(f"tool iterations capped at {_max_tool}")
                    except asyncio.TimeoutError:
                        _state.append({"type": "error", "message": f"chat timeout after {_max_dur}s"})
                        _state.error = f"timeout {_max_dur}s"
                    except asyncio.CancelledError:
                        _state.append({"type": "error", "message": "stream cancelled"})
                        _state.error = "cancelled"
                    except Exception as e:
                        import traceback as _tb
                        print(f"[anja] stream_response error:\n{_tb.format_exc()}")
                        _state.append({"type": "error", "message": f"{type(e).__name__}: {e}"})
                        _state.error = f"{type(e).__name__}: {e}"
                    finally:
                        # done solo se il generatore non l'ha già emesso: il
                        # double-done lasciava un frame orfano nella WS che
                        # sfasava di un turno i client (trovato dall'e2e ASP).
                        if not _saw_done:
                            _state.append({"type": "done"})
                        _state.completed = True
                        if _conv_id and not _state.error:
                            try:
                                await _persist_chat_turn_done(
                                    _state, _conv_id, _scope, _user_msg,
                                    _provider, _model, _effort, _projects,
                                )
                            except Exception as e:
                                print(f"[anja] bg persist error: {e}")

                state.task = asyncio.create_task(_drain_and_persist())

                last_seq_sent = 0
                ws_alive = True
                while True:
                    if state.completed:
                        # Drain remaining events
                        for ev in state.events_since(last_seq_sent):
                            try:
                                payload = {k: v for k, v in ev.items() if k != "seq"}
                                payload["_seq"] = ev["seq"]
                                payload["_conv_id"] = conv_id   # F-MultiChatView: tag sempre
                                await websocket.send_json(payload)
                                last_seq_sent = ev["seq"]
                            except Exception:
                                ws_alive = False
                                break
                        break
                    new_evs = state.events_since(last_seq_sent)
                    if not new_evs:
                        await asyncio.sleep(0.04)
                        continue
                    for ev in new_evs:
                        try:
                            payload = {k: v for k, v in ev.items() if k != "seq"}
                            payload["_seq"] = ev["seq"]
                            payload["_conv_id"] = conv_id   # F-MultiChatView: tag sempre → il client dirama al pane giusto
                            await websocket.send_json(payload)
                            last_seq_sent = ev["seq"]
                            # Capture usage/full_response from state already accumulated
                            if ev.get("type") == "text":
                                full_response += ev.get("content", "")
                            elif ev.get("type") == "usage":
                                last_usage = state.last_usage
                                cost_store.record_usage_event(HUB_PATH, ev, feature="chat")
                        except WebSocketDisconnect:
                            ws_alive = False
                            raise
                if state.error:
                    # Errore stream già emesso al client come event; salta persistence WS-side
                    continue
            except WebSocketDisconnect:
                # Stream task continues in background (opzione A idle persistence);
                # rilascia il WS handler.
                raise
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                print(f"[anja] stream loop error:\n{tb}")
                try:
                    await websocket.send_json({"type": "error", "message": f"{type(e).__name__}: {e}"})
                except Exception:
                    pass
                continue

            # Auto-compact UI feedback se WS ancora viva (la save+notif sono già
            # avvenute nel bg task via _persist_chat_turn_done).
            if conv_id and ws_alive:
                try:
                    updated = chat.load_conversation(WEBAPP_DIR, conv_id) or {}
                    if updated.get("auto_compact", True):
                        recently = (time.time() - (updated.get("compacted_at") or 0)) < 60
                        if not recently:
                            pct_threshold = float(updated.get("auto_compact_pct", 0.55))
                            usage = updated.get("last_usage") or {}
                            in_tok = int(usage.get("context_input_tokens") or usage.get("input_tokens", 0) or 0)
                            ctx_win = int(usage.get("context_window", 0) or 0)
                            pct_used = (in_tok / ctx_win) if ctx_win > 0 else 0
                            msgs = updated.get("messages", [])
                            fallback = (in_tok == 0 or ctx_win == 0) and len(msgs) >= 50
                            if (pct_used >= pct_threshold) or fallback:
                                reason = f"{int(pct_used*100)}% ctx" if not fallback else f"{len(msgs)} msg"
                                print(f"[anja] auto-compact WS triggered: {reason}")
                                result = await compact_conversation(conv_id, keep_last_n=3)
                                if result.get("ok"):
                                    await websocket.send_json({
                                        "type": "auto_compact",
                                        "reason": reason,
                                        "messages_before": result["messages_before"],
                                        "messages_after": result["messages_after"],
                                    })
                except Exception as e:
                    print(f"[anja] auto-compact UI error: {e}")

    except WebSocketDisconnect:
        pass
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[anja] WS chat handler error:\n{tb}")
        try:
            await websocket.send_json({"type": "error", "message": f"server error: {type(e).__name__}: {e}"})
        except Exception:
            pass


# ============================================================
# routines API
# ============================================================

def _yaml_dump_value(v, indent: int = 0) -> str:
    """Mini yaml serializer (no pyyaml). Supporta scalar/list/dict/multi-line."""
    sp = " " * indent
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        # multi-line → block scalar |
        if "\n" in v:
            lines = v.rstrip("\n").split("\n")
            inner = "\n".join((sp + "  ") + ln for ln in lines)
            return "|\n" + inner
        # quote se contiene caratteri speciali yaml
        if any(c in v for c in [":", "#", "{", "}", "[", "]", ",", "&", "*", "!", "|", ">", "'", '"', "%", "@", "`"]) or v.strip() != v or v.lower() in ("true", "false", "null", "yes", "no"):
            esc = v.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{esc}"'
        return v
    if isinstance(v, list):
        if not v:
            return "[]"
        # se tutti scalari semplici → inline
        if all(isinstance(x, (str, int, float, bool)) and (not isinstance(x, str) or "\n" not in x) for x in v):
            parts = [_yaml_dump_value(x, 0) for x in v]
            return "[" + ", ".join(parts) + "]"
        # altrimenti block list
        out = []
        for item in v:
            if isinstance(item, dict):
                lines = _yaml_dump_dict(item, indent + 2).splitlines()
                if not lines:
                    out.append(f"{sp}- {{}}")
                else:
                    first = lines[0].lstrip()
                    out.append(f"{sp}- {first}")
                    for ln in lines[1:]:
                        out.append(ln)
            else:
                out.append(f"{sp}- {_yaml_dump_value(item, 0)}")
        return "\n" + "\n".join(out)
    if isinstance(v, dict):
        return _yaml_dump_dict(v, indent)
    return str(v)


def _yaml_dump_dict(d: dict, indent: int = 0) -> str:
    sp = " " * indent
    lines = []
    for k, v in d.items():
        if v is None or v == "" or (isinstance(v, list) and not v) or (isinstance(v, dict) and not v):
            continue
        if isinstance(v, dict):
            nested = _yaml_dump_dict(v, indent + 2)
            if nested:
                lines.append(f"{sp}{k}:")
                lines.append(nested)
            continue
        if isinstance(v, list):
            rendered = _yaml_dump_value(v, indent + 2)
            if rendered.startswith("\n"):
                lines.append(f"{sp}{k}:")
                lines.append(rendered.lstrip("\n"))
            else:
                lines.append(f"{sp}{k}: {rendered}")
            continue
        if isinstance(v, str) and "\n" in v:
            rendered = _yaml_dump_value(v, indent)
            lines.append(f"{sp}{k}: {rendered}")
            continue
        lines.append(f"{sp}{k}: {_yaml_dump_value(v, indent)}")
    return "\n".join(lines)


def _routines_root() -> Path:
    """Resolve <hub>/routines (auto-create if first call)."""
    if not HUB_PATH:
        raise HTTPException(503, "hub not configured")
    rd = HUB_PATH / "routines"
    rd.mkdir(parents=True, exist_ok=True)
    return rd


def _safe_routine_name(name: str) -> str:
    if not re.match(r"^[a-z0-9][a-z0-9_-]*$", name):
        raise HTTPException(400, "invalid routine name")
    return name


@app.get("/api/routines")
async def api_routines_list():
    mods = _get_routines_modules()
    if not mods:
        return JSONResponse({"routines": [], "_error": "routines plugin not available"})
    _, rr = mods
    try:
        items = rr.list_routines(HUB_PATH)
    except Exception as e:
        return JSONResponse({"routines": [], "_error": str(e)})

    out = []
    for r in items:
        y = r.get("yaml") or {}
        st = r.get("state") or {}
        out.append({
            "name": r["name"],
            "valid": r["valid"],
            "scope": y.get("scope", ""),
            "schedule": y.get("schedule", ""),
            "description": y.get("description", ""),
            "model": y.get("model", "sonnet"),
            "tags": y.get("tags", []),
            "enabled": st.get("enabled", True),
            "last_run": st.get("last_run"),
            "last_status": st.get("last_status"),
            "last_duration_sec": st.get("last_duration_sec"),
            "last_log": st.get("last_log"),
            "file": Path(r["file"]).name,
            "outputs": [a.get("type") for a in (y.get("output") or []) if isinstance(a, dict)],
        })
    return JSONResponse({"routines": out})


@app.get("/api/routines/{name}")
async def api_routine_detail(name: str):
    name = _safe_routine_name(name)
    mods = _get_routines_modules()
    if not mods:
        raise HTTPException(503, "routines plugin not available")
    _, rr = mods
    routine = rr.get_routine(name, HUB_PATH)
    if not routine:
        raise HTTPException(404, f"routine '{name}' not found")

    y = routine.get("yaml") or {}
    st = routine.get("state") or {}

    # carica yaml raw text
    yaml_path = Path(routine["file"])
    yaml_text = yaml_path.read_text(encoding="utf-8") if yaml_path.is_file() else ""

    # ultimi 20 run files
    runs_dir = _routines_root() / "runs"
    runs = []
    if runs_dir.is_dir():
        prefix = f"{name}-"
        files = sorted(
            [f for f in runs_dir.iterdir() if f.name.startswith(prefix) and f.suffix == ".md"],
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )[:20]
        for f in files:
            try:
                head = f.read_text(encoding="utf-8").splitlines()[:8]
            except Exception:
                head = []
            status = "?"
            duration = ""
            for h in head:
                if h.startswith("- **status**:"):
                    status = h.split(":", 1)[1].strip()
                if h.startswith("- **duration**:"):
                    duration = h.split(":", 1)[1].strip()
            runs.append({
                "filename": f.name,
                "mtime": f.stat().st_mtime,
                "status": status,
                "duration": duration,
            })

    return JSONResponse({
        "name": name,
        "valid": routine["valid"],
        "yaml_text": yaml_text,
        "yaml": y,
        "state": st,
        "runs": runs,
    })


@app.get("/api/routines/{name}/runs/{filename}")
async def api_routine_run_log(name: str, filename: str):
    name = _safe_routine_name(name)
    if "/" in filename or ".." in filename or not filename.endswith(".md"):
        raise HTTPException(400, "invalid filename")
    if not filename.startswith(f"{name}-"):
        raise HTTPException(400, "filename does not match routine")
    runs_dir = _routines_root() / "runs"
    f = runs_dir / filename
    if not f.is_file():
        raise HTTPException(404, "run log not found")
    return PlainTextResponse(f.read_text(encoding="utf-8"))


# Fase 7n — registry processi routine in esecuzione (in-memory)
_RUNNING_ROUTINES: dict = {}  # {routine_name: {"pid": int, "started_at": iso, "log": str, "dry_run": bool}}


def _scan_running_routines():
    """Aggiorna il registry rimuovendo entries il cui processo è morto."""
    import os as _os
    dead = []
    for name, info in _RUNNING_ROUTINES.items():
        pid = info.get("pid")
        if not pid:
            dead.append(name)
            continue
        try:
            _os.kill(pid, 0)  # signal 0 = test esistenza
        except ProcessLookupError:
            dead.append(name)
        except PermissionError:
            pass  # esiste ma non posso segnalarlo (improbabile per nostri figli)
    for n in dead:
        _RUNNING_ROUTINES.pop(n, None)


@app.post("/api/routines/{name}/run")
async def api_routine_trigger(name: str, request: Request):
    _require_admin(request)   # esegue un agente con bypassPermissions → admin/owner
    name = _safe_routine_name(name)
    mods = _get_routines_modules()
    if not mods:
        raise HTTPException(503, "routines plugin not available")
    _, rr = mods
    routine = rr.get_routine(name, HUB_PATH)
    if not routine:
        raise HTTPException(404, "routine not found")

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    dry_run = bool(body.get("dry_run", False))

    runner = ANJA_ROUTINES_DIR / "scripts" / "runner.py"
    if not runner.is_file():
        raise HTTPException(500, f"runner.py not found at {runner}")

    # spawn detached subprocess; returns immediately
    cmd = [sys.executable, str(runner), "--name", name]
    if dry_run:
        cmd.append("--dry-run")

    import os
    env = os.environ.copy()
    env["ANJA_HUB"] = str(HUB_PATH)

    runs_dir = _routines_root() / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    log_path = runs_dir / f"{name}-{ts}.stdout.log"
    f = open(log_path, "wb")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=f,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=str(HUB_PATH),
            start_new_session=True,
        )
    except Exception as e:
        return JSONResponse({"status": "failed", "error": str(e)}, status_code=500)

    # Track running process (Fase 7n)
    _RUNNING_ROUTINES[name] = {
        "pid": proc.pid,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "log": log_path.name,
        "dry_run": dry_run,
    }

    return JSONResponse({
        "status": "started",
        "pid": proc.pid,
        "stdout_log": log_path.name,
        "dry_run": dry_run,
    })


@app.get("/api/routines/{name}/status")
async def api_routine_status(name: str):
    """Ritorna lo stato di esecuzione di una routine (running | idle).

    Live: scansiona il registry, rimuove processi morti, ritorna info se vivo.
    """
    name = _safe_routine_name(name)
    _scan_running_routines()
    info = _RUNNING_ROUTINES.get(name)
    if not info:
        return JSONResponse({"name": name, "running": False})
    # Calcola duration
    from datetime import datetime, timezone
    try:
        start = datetime.fromisoformat(info["started_at"])
        dur = (datetime.now(timezone.utc) - start).total_seconds()
    except Exception:
        dur = None
    # Tail recent stdout (max 4KB)
    tail = ""
    try:
        log_file = (_routines_root() / "runs" / info["log"])
        if log_file.is_file():
            with log_file.open("rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - 4096))
                tail = fh.read().decode("utf-8", errors="replace")
    except Exception:
        pass
    return JSONResponse({
        "name": name,
        "running": True,
        "pid": info["pid"],
        "started_at": info["started_at"],
        "duration_sec": dur,
        "log": info["log"],
        "dry_run": info["dry_run"],
        "tail": tail[-4096:],
    })


@app.post("/api/routines")
async def api_routine_create(request: Request):
    """Crea nuova routine yaml in <hub>/routines/<name>.yaml.

    Body atteso: {name, scope, schedule, description?, prompt, model?, tools?, output: [...], timeout_sec?, tags?, enabled?}
    """
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON body")

    name = body.get("name", "").strip()
    name = _safe_routine_name(name)

    # Fase 22.10 — Se scope=project:<name> e workspace internal, salva nel workspace
    scope = (body.get("scope") or "hub").strip()
    rd = _routines_root()  # default hub
    if scope.startswith("project:") and HUB_PATH:
        ws_name = scope.split(":", 1)[1].strip()
        ws_routines_dir = HUB_PATH / "workspaces" / ws_name / ".anjawiki" / "routines"
        # Se workspace internal esiste e ha la cartella routines/, usa quella
        if ws_routines_dir.is_dir() or (HUB_PATH / "workspaces" / ws_name).is_dir():
            ws_routines_dir.mkdir(parents=True, exist_ok=True)
            rd = ws_routines_dir
            # README placeholder se nuovo
            readme = rd / "README.md"
            if not readme.exists():
                readme.write_text(
                    f"# routines/\n\nRoutine schedulate del workspace `{ws_name}` (auto-scope: `{scope}`).\n",
                    encoding="utf-8")

    target = rd / f"{name}.yaml"
    if target.exists():
        raise HTTPException(409, f"routine '{name}' already exists at {target}")

    # ordering canonico
    order = ["name", "description", "scope", "schedule", "enabled", "provider", "model", "effort", "prompt", "context", "tools", "output", "timeout_sec", "max_retries", "tags"]
    obj = {}
    for k in order:
        if k in body and body[k] not in (None, "", []):
            obj[k] = body[k]
    # eventuali campi non in order list
    for k, v in body.items():
        if k not in obj and v not in (None, "", []):
            obj[k] = v

    # required fields check (basic)
    for f in ("name", "scope", "schedule", "prompt"):
        if f not in obj or not obj[f]:
            raise HTTPException(400, f"missing required field: '{f}'")

    yaml_text = _yaml_dump_dict(obj) + "\n"
    target.write_text(yaml_text, encoding="utf-8")

    # valida riloggando con routine_validate
    mods = _get_routines_modules()
    if mods:
        rv, _ = mods
        loaded = rv.load_and_validate(target)
        if loaded is None:
            target.unlink()
            raise HTTPException(400, "yaml validation failed (see server logs)")

    return JSONResponse({
        "status": "created",
        "name": name,
        "file": target.name,
        "yaml_text": yaml_text,
    })


@app.patch("/api/routines/{name}")
async def api_routine_update(name: str, request: Request):
    """Update fields of an existing routine yaml. Body = dict of fields to patch.

    Merge-style: campi non menzionati nel body restano invariati. Campi col valore
    None vengono rimossi dal yaml. Re-valida dopo write.

    Esempio body: {"schedule": "30 19 * * *", "prompt": "nuovo prompt..."}
    """
    _require_admin(request)
    name = _safe_routine_name(name.strip())
    try:
        patch = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON body")
    if not isinstance(patch, dict):
        raise HTTPException(400, "body must be a JSON object")
    # Locate yaml: cerca prima in hub routines, poi nei workspace routines
    target = _routines_root() / f"{name}.yaml"
    if not target.is_file() and HUB_PATH:
        ws_root = HUB_PATH / "workspaces"
        if ws_root.is_dir():
            for ws in ws_root.iterdir():
                cand = ws / ".anjawiki" / "routines" / f"{name}.yaml"
                if cand.is_file():
                    target = cand
                    break
    if not target.is_file():
        raise HTTPException(404, f"routine '{name}' not found")

    mods = _get_routines_modules()
    if not mods:
        raise HTTPException(500, "routine_validate module not available")
    rv, _ = mods

    current = rv.load_and_validate(target)
    if current is None:
        raise HTTPException(400, f"current yaml of '{name}' is invalid, fix manually first")

    # Merge: campi a None vengono rimossi, gli altri sovrascritti.
    for k, v in patch.items():
        if v is None:
            current.pop(k, None)
        else:
            current[k] = v

    # Re-ordering canonico
    order = ["name", "description", "scope", "schedule", "enabled", "provider", "model", "effort", "prompt", "context", "tools", "research", "output", "timeout_sec", "max_retries", "tags"]
    obj = {}
    for k in order:
        if k in current and current[k] not in (None, "", []):
            obj[k] = current[k]
    for k, v in current.items():
        if k not in obj and k not in ("_path", "_warnings") and v not in (None, "", []):
            obj[k] = v

    yaml_text = _yaml_dump_dict(obj) + "\n"
    # Backup + write
    backup = target.with_suffix(target.suffix + ".bak")
    try:
        backup.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:
        pass
    target.write_text(yaml_text, encoding="utf-8")

    # Re-validate; se fallisce, rollback
    loaded = rv.load_and_validate(target)
    if loaded is None:
        if backup.is_file():
            target.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
        raise HTTPException(400, f"patched yaml failed validation, rolled back. Check fields: {list(patch.keys())}")
    try:
        backup.unlink()
    except Exception:
        pass

    return JSONResponse({
        "status": "updated",
        "name": name,
        "file": target.name,
        "patched_fields": list(patch.keys()),
        "yaml_text": yaml_text,
    })


@app.get("/api/wizard/tools")
async def api_wizard_tools(scope: str = "hub"):
    """Tool catalog filtrato per scope routine.

    scope = "hub" → tool generici + plugin anja-hub
    scope = "project:<name>" → builtins + plugin anja + skills/MCP del progetto
    """
    builtin_hub = [
        {"id": "Read", "label": "Read", "desc": "Legge file (read-only)"},
        {"id": "Grep", "label": "Grep", "desc": "Search regex su file"},
        {"id": "Glob", "label": "Glob", "desc": "Pattern matching path"},
        {"id": "WebFetch", "label": "WebFetch", "desc": "HTTP GET di una URL"},
        {"id": "WebSearch", "label": "WebSearch", "desc": "Ricerca web"},
    ]
    builtin_project = builtin_hub + [
        {"id": "Write", "label": "Write", "desc": "Scrive file"},
        {"id": "Edit", "label": "Edit", "desc": "Modifica file (find/replace)"},
        {"id": "MultiEdit", "label": "MultiEdit", "desc": "Multi-edit su un file"},
        {"id": "Bash", "label": "Bash", "desc": "Esegue comandi shell"},
        {"id": "LS", "label": "LS", "desc": "Lista directory"},
        {"id": "TodoWrite", "label": "TodoWrite", "desc": "Gestione TODO list"},
    ]

    skills = []
    plugins = []
    mcp = []

    is_project = scope.startswith("project:")
    project_name = scope.split(":", 1)[1] if is_project else None

    # plugin disponibili (riusa endpoint esistente con stub locale)
    try:
        plugin_marketplace = HUB_PATH.parent.parent / "Documents" / "llm-wiki" / ".claude-plugin" / "marketplace.json"
        if plugin_marketplace.is_file():
            data = json.loads(plugin_marketplace.read_text(encoding="utf-8"))
            for p in data.get("plugins", []):
                pname = p.get("name", "")
                # filtro per scope
                if is_project and pname == "anja-hub":
                    continue
                if not is_project and pname == "anja":
                    continue
                plugins.append({"id": pname, "label": pname, "desc": p.get("description", "")})
    except Exception:
        pass

    if is_project and project_name and HUB_PATH:
        proj_dir = resolve_project_path(project_name, HUB_PATH) if "resolve_project_path" in globals() else None
        if not proj_dir:
            # fallback
            symlink = HUB_PATH / "workspaces" / project_name
            if symlink.is_dir():
                proj_dir = symlink.resolve()

        if proj_dir and proj_dir.is_dir():
            # MCP da .mcp.json
            mcp_file = proj_dir / ".mcp.json"
            if mcp_file.is_file():
                try:
                    data = json.loads(mcp_file.read_text(encoding="utf-8"))
                    for srv_name in (data.get("mcpServers") or {}).keys():
                        mcp.append({"id": f"mcp__{srv_name}", "label": srv_name, "desc": "MCP server"})
                except Exception:
                    pass

            # Skills da .claude/skills/
            sk_dir = proj_dir / ".claude" / "skills"
            if sk_dir.is_dir():
                for d in sk_dir.iterdir():
                    if d.is_dir() and (d / "SKILL.md").is_file():
                        # parse description from frontmatter
                        first = (d / "SKILL.md").read_text(encoding="utf-8")[:500]
                        desc = ""
                        for line in first.split("\n"):
                            if line.startswith("description:"):
                                desc = line.split(":", 1)[1].strip().strip('"').strip("'")
                                break
                        skills.append({"id": d.name, "label": d.name, "desc": desc or "(skill)"})

    return JSONResponse({
        "scope": scope,
        "builtin": builtin_project if is_project else builtin_hub,
        "plugins": plugins,
        "skills": skills,
        "mcp": mcp,
    })


def resolve_project_path(name: str, hub: Path):
    """Risolve la project root (NON il .anjawiki dir)."""
    # 1) config/projects.json (formato anja-hub canonico)
    cfg = hub / "config" / "projects.json"
    if cfg.is_file():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            for proj in data.get("projects", []):
                if proj.get("name") == name:
                    p = Path(proj.get("location", {}).get("path", "")).expanduser()
                    if p.is_dir():
                        # Normalizza: il contratto è ritornare la project root, NON il
                        # .anjawiki dir. Alcuni registry (workspace interni pre-fix) registrano
                        # già `.../.anjawiki` → sali di un livello per evitare doppio .anjawiki.
                        return p.parent if p.name == ".anjawiki" else p
        except Exception:
            pass
    # 2) registry/hub.json (alt format)
    reg = hub / "registry" / "hub.json"
    if reg.is_file():
        try:
            data = json.loads(reg.read_text(encoding="utf-8"))
            for proj in data.get("projects", []):
                if proj.get("name") == name:
                    p = Path(proj["path"]).expanduser()
                    if p.is_dir():
                        return p.parent if p.name == ".anjawiki" else p
        except Exception:
            pass
    # 3) symlink projects/<name> punta a <project>/.anjawiki — risolvi e sali di un livello
    pl = hub / "projects" / name
    if pl.is_symlink() or pl.is_dir():
        resolved = pl.resolve()
        if resolved.name == ".anjawiki":
            return resolved.parent
        return resolved
    return None


@app.post("/api/routines/{name}/toggle")
async def api_routine_toggle(name: str, request: Request):
    _require_admin(request)
    name = _safe_routine_name(name)
    mods = _get_routines_modules()
    if not mods:
        raise HTTPException(503, "routines plugin not available")
    _, rr = mods
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    enabled = bool(body.get("enabled", True))
    rr.set_enabled(name, enabled, HUB_PATH)
    return JSONResponse({"status": "ok", "name": name, "enabled": enabled})


# ============================================================
# Fase 7l — Clone MCP cross-scope
# ============================================================


def _resolve_scope_root(scope: str) -> Optional[Path]:
    """scope = 'hub' | 'project:<name>'  → directory radice."""
    if scope == "hub":
        return HUB_PATH
    if scope.startswith("project:"):
        pname = scope.split(":", 1)[1]
        return resolve_project_path(pname, HUB_PATH) if HUB_PATH else None
    return None


def _is_local_binary_arg(args: list, source_root: Path) -> Optional[Path]:
    """Se un MCP usa un binario locale (path assoluto a un file dentro source_root o
    relativo a source_root), ritorna il Path della cartella che lo contiene
    (cioè il vendor dir). None se è npx/altro."""
    if not isinstance(args, list):
        return None
    for a in args:
        if not isinstance(a, str):
            continue
        # Path assoluto?
        if a.startswith("/") and "/" in a and (a.endswith(".js") or a.endswith(".py") or a.endswith(".mjs")):
            p = Path(a)
            if p.is_file():
                # Risali alla "vendor dir" — la dir 2 livelli sopra il file
                # (es. .../vendor/<name>/dist/index.js → vendor dir = .../vendor/<name>/)
                # Heuristic: prendi la dir che contiene un package.json
                cur = p.parent
                for _ in range(4):
                    if (cur / "package.json").is_file():
                        return cur
                    cur = cur.parent
                return p.parent  # fallback
    return None


@app.post("/api/resources/mcp/clone")
async def api_mcp_clone(request: Request):
    """Duplica un MCP server da uno scope all'altro.

    Body:
      {source_scope: 'hub'|'project:X', source_name: '...',
       target_scope: 'hub'|'project:Y', target_name?: '...'}

    Comportamento:
      - Se il MCP usa un binario locale (path assoluto a file dentro un vendor dir),
        copia la vendor dir intera in <target_root>/vendor/<name>/, aggiusta gli args,
        e crea entry .mcp.json nel target.
      - Se usa npx -y package@version o altro command remoto, copia solo l'entry json
        (no file copy).
    """
    _require_admin(request)
    import shutil
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")

    source_scope = (body.get("source_scope") or "").strip()
    source_name = (body.get("source_name") or "").strip()
    target_scope = (body.get("target_scope") or "").strip()
    target_name = (body.get("target_name") or source_name).strip()
    # Env override: dict {KEY: VALUE} → merged sopra quello source. None=remove key.
    env_override = body.get("env_override") or {}
    if not isinstance(env_override, dict):
        raise HTTPException(400, "env_override must be an object")

    if not source_scope or not source_name or not target_scope:
        raise HTTPException(400, "source_scope, source_name, target_scope required")
    if not MCP_NAME_RE.match(target_name):
        raise HTTPException(400, "invalid target_name")

    source_root = _resolve_scope_root(source_scope)
    target_root = _resolve_scope_root(target_scope)
    if not source_root or not target_root:
        raise HTTPException(404, "source or target scope not resolvable")

    # Carica entry source dal .mcp.json
    src_mcp_file = source_root / ".mcp.json"
    if not src_mcp_file.is_file():
        raise HTTPException(404, f"no .mcp.json in {source_scope}")
    try:
        src_cfg = json.loads(src_mcp_file.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(500, f"invalid source .mcp.json: {e}")
    src_entry = (src_cfg.get("mcpServers") or {}).get(source_name)
    if not src_entry:
        raise HTTPException(404, f"mcp '{source_name}' not in {source_scope}/.mcp.json")

    # Detect local binary
    args = src_entry.get("args", []) or []
    vendor_src = _is_local_binary_arg(args, source_root)
    new_entry = json.loads(json.dumps(src_entry))  # deep copy

    copied_vendor = None
    if vendor_src:
        # Copy vendor dir into <target_root>/vendor/<target_name>/
        vendor_dst = target_root / "vendor" / target_name
        if vendor_dst.exists():
            raise HTTPException(409, f"vendor dir already exists: {vendor_dst}")
        vendor_dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copytree(vendor_src, vendor_dst, symlinks=False, ignore_dangling_symlinks=True)
        except Exception as e:
            raise HTTPException(500, f"copy failed: {e}")
        copied_vendor = str(vendor_dst)
        # Replace any path arg pointing into vendor_src with new path under vendor_dst
        new_args = []
        vendor_src_str = str(vendor_src.resolve())
        for a in args:
            if isinstance(a, str) and a.startswith(vendor_src_str):
                new_args.append(a.replace(vendor_src_str, str(vendor_dst.resolve())))
            else:
                new_args.append(a)
        new_entry["args"] = new_args

    # Apply env override (Fase 7o)
    if env_override:
        existing_env = new_entry.get("env", {}) or {}
        for k, v in env_override.items():
            if v is None or v == "":
                existing_env.pop(k, None)
            else:
                existing_env[k] = str(v)
        if existing_env:
            new_entry["env"] = existing_env
        elif "env" in new_entry:
            del new_entry["env"]

    # Write into target .mcp.json (merge)
    tgt_mcp_file = target_root / ".mcp.json"
    tgt_cfg = {"mcpServers": {}}
    if tgt_mcp_file.is_file():
        try:
            tgt_cfg = json.loads(tgt_mcp_file.read_text(encoding="utf-8"))
            if "mcpServers" not in tgt_cfg:
                tgt_cfg["mcpServers"] = {}
        except Exception as e:
            raise HTTPException(500, f"invalid target .mcp.json: {e}")
    if target_name in tgt_cfg["mcpServers"]:
        raise HTTPException(409, f"mcp '{target_name}' already in {target_scope}/.mcp.json")
    tgt_cfg["mcpServers"][target_name] = new_entry
    tgt_mcp_file.write_text(json.dumps(tgt_cfg, indent=2, ensure_ascii=False), encoding="utf-8")

    return JSONResponse({
        "status": "cloned",
        "source": {"scope": source_scope, "name": source_name},
        "target": {"scope": target_scope, "name": target_name, "path": str(tgt_mcp_file)},
        "binary_copied": copied_vendor,
    })


# ============================================================
# Provider models catalog (Fase 7e)
# ============================================================

import time as _time
import urllib.request as _urlreq

_MODELS_CACHE: dict = {}  # {provider: (timestamp, list[str])}
_MODELS_TTL = 3600  # 1h

_STATIC_MODELS = {
    "claude": ["sonnet", "opus", "haiku"],
}

# Provider con endpoint REST live per i modelli (richiede API key in .secrets.env)
_PROVIDER_MODEL_ENDPOINTS = {
    "openai":     {"url": "https://api.openai.com/v1/models", "key_env": "OPENAI_API_KEY"},
    "xai":        {"url": "https://api.x.ai/v1/models",       "key_env": "XAI_API_KEY"},
    "openrouter": {"url": "https://openrouter.ai/api/v1/models", "key_env": None},  # no auth
    # Gemini via layer OpenAI-compatibile (Bearer supportato)
    "gemini":     {"url": "https://generativelanguage.googleapis.com/v1beta/openai/models", "key_env": "GEMINI_API_KEY"},
    "mistral":    {"url": "https://api.mistral.ai/v1/models",  "key_env": "MISTRAL_API_KEY"},
    "groq":       {"url": "https://api.groq.com/openai/v1/models", "key_env": "GROQ_API_KEY"},
}

# Fallback statici se l'API rifiuta o non c'è key
_STATIC_FALLBACK = {
    "openai": ["gpt-5.5", "gpt-5.5-pro", "gpt-5.4", "gpt-5.4-mini", "o4", "o4-mini"],
    "xai":    ["grok-4", "grok-4-fast", "grok-3", "grok-3-mini", "grok-2"],
    "openrouter": [
        "anthropic/claude-haiku-4.5", "anthropic/claude-opus-4.5",
        "google/gemini-2.5-pro", "google/gemini-2.5-flash",
        "deepseek/deepseek-r1",
    ],
}


def _fetch_provider_models(provider: str) -> list:
    """Fetch live dei modelli dal provider /v1/models. Auth con key da .secrets.env se richiesta."""
    spec = _PROVIDER_MODEL_ENDPOINTS.get(provider)
    if not spec:
        return []
    headers = {"User-Agent": "anja-hub/1.0"}
    key_env = spec.get("key_env")
    if key_env:
        # Carica dalle secrets dell'hub (sopra os.environ ha priorità in caso di update runtime)
        key = os.environ.get(key_env) or _load_secrets_dict().get(key_env)
        if key:
            headers["Authorization"] = f"Bearer {key}"
    try:
        req = _urlreq.Request(spec["url"], headers=headers)
        with _urlreq.urlopen(req, timeout=10) as r:
            payload = json.loads(r.read().decode("utf-8"))
        # Schema OpenAI-style: {"data": [{"id": "..."}]}
        # il layer OpenAI-compat di Gemini restituisce id "models/<nome>":
        # normalizza, o l'API li rifiuta con models/models/… (404)
        ids = [m.get("id", "").removeprefix("models/")
               for m in payload.get("data", []) if m.get("id")]
        ids.sort()
        return ids
    except Exception as e:
        print(f"[anja] {provider} models fetch failed: {e}")
        return []


@app.get("/api/providers/{provider}/models")
async def api_provider_models(provider: str, refresh: int = 0):
    provider = provider.lower().strip()
    now = _time.time()
    cached = _MODELS_CACHE.get(provider)
    if not refresh and cached and (now - cached[0]) < _MODELS_TTL:
        return JSONResponse({"provider": provider, "models": cached[1], "cached": True})

    if provider in _STATIC_MODELS:
        models = _STATIC_MODELS[provider]
    elif provider in _PROVIDER_MODEL_ENDPOINTS:
        models = _fetch_provider_models(provider)
        if not models:
            models = _STATIC_FALLBACK.get(provider, [])
    elif provider == "ollama":
        # Fase 7t — local Ollama: hit /api/tags
        cfg = _load_ollama_config()
        _online, ml, _err = _ollama_fetch_tags(cfg["base_url"], timeout=3.0)
        models = [m["name"] for m in ml]
    else:
        raise HTTPException(404, f"unknown provider: {provider}")

    _MODELS_CACHE[provider] = (now, models)
    return JSONResponse({"provider": provider, "models": models, "cached": False})


# ============================================================
# Settings — Provider API keys (Fase 7e)
# ============================================================

PROVIDER_KEY_FIELDS = [
    {"id": "openai",     "label": "OpenAI",     "env": "OPENAI_API_KEY",     "url": "https://platform.openai.com/api-keys"},
    {"id": "openrouter", "label": "OpenRouter", "env": "OPENROUTER_API_KEY", "url": "https://openrouter.ai/keys"},
    {"id": "xai",        "label": "xAI (Grok)", "env": "XAI_API_KEY",        "url": "https://console.x.ai"},
    {"id": "anthropic",  "label": "Anthropic (override)", "env": "ANTHROPIC_API_KEY", "url": "https://console.anthropic.com/settings/keys"},
    {"id": "gemini",     "label": "Google Gemini (AI Studio)", "env": "GEMINI_API_KEY", "url": "https://aistudio.google.com/apikey"},
    {"id": "mistral",    "label": "Mistral",    "env": "MISTRAL_API_KEY",    "url": "https://console.mistral.ai/api-keys"},
    {"id": "groq",       "label": "Groq",       "env": "GROQ_API_KEY",       "url": "https://console.groq.com/keys"},
]


def _secrets_path() -> Path:
    return HUB_PATH / ".secrets.env"


def _load_secrets_dict() -> dict:
    out: dict = {}
    f = _secrets_path()
    if not f.is_file():
        return out
    for raw in f.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        if k:
            out[k] = v
    return out


def _save_secrets_dict(d: dict):
    f = _secrets_path()
    lines = ["# anja providers — generato da Mission Control. Non committare in git.", ""]
    for k in sorted(d.keys()):
        v = d[k]
        if v:
            lines.append(f'{k}="{v}"')
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(f, 0o600)
    except Exception:
        pass
    # Sync into os.environ so newly-spawned MCP children (Claude SDK / opencode subprocesses)
    # see the updated values without server restart.
    for k, v in d.items():
        if v:
            os.environ[k] = v
    # Remove env vars that were dropped from the dict (from a previous superset)
    # only if they look like our managed keys (uppercase + underscore + alphanumerics)
    # — be conservative: don't unset arbitrary system env vars.


def _mask_key(v: str) -> str:
    if not v:
        return ""
    # Sotto i 16 char, mostrare 4+4 rivelerebbe metà o più del secret: maschera tutto.
    if len(v) < 16:
        return "***"
    return f"{v[:4]}...{v[-4:]}"


# ============================================================
# Media models aggregator (Fase 23.b — image + video gen)
# ============================================================

# Hint prezzi indicativi per il picker (il catalogo vero è image_gen.IMAGE_MODELS)
# F-CLI-Media: provider del catalogo coperti dalla CLI giv (gli agent generano
# via Bash, non più via MCP). sd35/qwen restano solo per la UI (Media → Genera).
_GIV_PROVIDERS = {"gemini", "openai", "xai"}


def _giv_model_flags(model_id: str) -> str:
    """Flag `--provider X --model Y` per giv da un id del catalogo immagini
    ('' se giv non copre quel provider)."""
    import image_gen
    m = next((x for x in image_gen.IMAGE_MODELS if x["id"] == model_id), None)
    if not m or m["provider"] not in _GIV_PROVIDERS:
        return ""
    raw = m["model"].split("/", 1)[-1]
    return f"--provider {m['provider']} --model {raw}"


def _giv_media_hint(media_model_pref: str = "", telegram: bool = False) -> str:
    """Blocco system prompt per la generazione media via CLI giv."""
    today = time.strftime("%Y-%m-%d")
    img_out = HUB_PATH / "raw" / "images" / today
    vid_out = HUB_PATH / "raw" / "videos" / today
    hint = (
        "\n\n## Media generation\n"
        "Per generare immagini/video usa la CLI `giv` via Bash "
        "(carica la skill `gen-image-video` per flag, ricette e "
        "matrice provider). Le chiavi le legge da solo da "
        "`./credentials.env` nella cwd (materializzato dall'hub).\n"
        f"- immagini: `giv image --out {img_out} --name <slug> \"<prompt>\"`\n"
        f"- video: `giv video --out {vid_out} --name <slug> \"<prompt>\"`\n"
        "Con quei `--out` i file compaiono nella tab Media della UI. "
        "stdout è un manifest JSON {files:[{path,…}]}. "
        "NON usare i connettori claude.ai (`mcp__claude_ai_*`) né "
        "altri servizi esterni per generare media: consumano crediti "
        "esterni e i file non finiscono nell'hub.\n"
    )
    if telegram:
        hint += (
            "Sei in una chat Telegram: dopo la generazione includi nel testo "
            "della risposta il path completo di ogni file generato — l'hub li "
            "allega automaticamente come foto/video al messaggio.\n"
        )
    if media_model_pref:
        flags = _giv_model_flags(media_model_pref)
        if flags:
            hint += (
                f"L'utente ha scelto il modello `{media_model_pref}` "
                f"dal picker: passa `{flags}` a giv, salvo sua "
                f"diversa richiesta (preferenza sticky).\n"
            )
    return hint


_IMAGE_PRICE_HINTS = {
    "nano-banana": "~$0.04 / img",
    "nano-banana-3": "~$0.02-$0.05 / img",
    "gemini-pro-image": "~$0.12-$0.24 / img",
    "imagen-4": "$0.02-$0.06 / img",
    "gpt-image-1": "$0.04-$0.19 / img (tier)",
    "dall-e-3": "$0.04-$0.08 / img",
    "grok-image": "see docs.x.ai",
    "grok-image-hq": "see docs.x.ai",
}


def _image_models_for_picker() -> list:
    """Modelli immagine READY dal catalogo unico (stessi id del tool image.generate)."""
    import connectors_io
    import image_gen
    out = []
    for m in image_gen.catalog(HUB_PATH, connectors_io.hub_secrets_dir(HUB_PATH)):
        if not m["ready"]:
            continue
        out.append({"provider": m["provider"], "slug": m["id"], "name": m["label"],
                    "modality": "image",
                    "pricing_hint": _IMAGE_PRICE_HINTS.get(m["id"], "")})
    return out


@app.get("/api/media-models")
async def api_media_models(refresh: int = 0):
    """Aggregator video (live OpenRouter) + image (static curated list).

    Cache video list 5min in _MODELS_CACHE['video_or'].
    """
    out = {"image": [], "video": []}
    # Image: catalogo unico (image_gen), solo modelli con key pronta
    try:
        out["image"] = _image_models_for_picker()
    except Exception as e:  # noqa: BLE001
        print(f"[media-models] catalogo immagini fallito: {e}", flush=True)

    # Video: live da OpenRouter
    now = _time.time()
    cached = _MODELS_CACHE.get("video_or")
    if not refresh and cached and (now - cached[0]) < 300:
        out["video"] = cached[1]
    else:
        try:
            req = _urlreq.Request(
                "https://openrouter.ai/api/v1/videos/models",
                headers={"User-Agent": "anja-hub/1.0"},
            )
            with _urlreq.urlopen(req, timeout=10) as r:
                payload = json.loads(r.read().decode("utf-8"))
            data = payload.get("data") or payload.get("models") or []
            models = []
            for m in data:
                if not isinstance(m, dict):
                    continue
                durations = m.get("supported_durations") or []
                max_dur = max(durations) if durations else None
                resolutions = m.get("supported_resolutions") or []
                pricing_skus = m.get("pricing_skus") or {}
                # Compute pricing hint from skus
                pricing_hint = ""
                for k in ("text_to_video_duration_seconds_720p", "duration_seconds"):
                    if k in pricing_skus:
                        pricing_hint = f"${pricing_skus[k]}/sec @720p"
                        break
                models.append({
                    "provider": "openrouter",
                    "slug": m.get("id"),
                    "name": m.get("name") or m.get("id"),
                    "modality": "video",
                    "max_duration_sec": max_dur,
                    "supported_durations": durations,
                    "resolutions": resolutions,
                    "aspect_ratios": m.get("supported_aspect_ratios") or [],
                    "audio_supported": bool(m.get("generate_audio")),
                    "pricing_hint": pricing_hint,
                    "pricing_skus": pricing_skus,
                })
            # Sort by name
            models.sort(key=lambda x: (x.get("name") or "").lower())
            out["video"] = models
            _MODELS_CACHE["video_or"] = (now, models)
        except Exception as e:
            out["video_error"] = f"{type(e).__name__}: {e}"
            out["video"] = cached[1] if cached else []

    # xAI video (no public list endpoint)
    if os.environ.get("XAI_API_KEY"):
        out["video"].append({
            "provider": "xai",
            "slug": "grok-imagine-video",
            "name": "xAI Grok Imagine Video",
            "modality": "video",
            "max_duration_sec": 15,
            "resolutions": ["720p", "1080p"],
            "aspect_ratios": ["16:9", "9:16", "1:1"],
            "audio_supported": False,
            "pricing_hint": "see docs.x.ai",
        })

    out["counts"] = {"image": len(out["image"]), "video": len(out["video"])}
    return JSONResponse(out)


# ============================================================
# Ollama local models (Fase 7t)
# ============================================================

_OLLAMA_DEFAULT_URL = "http://localhost:11434"


def _ollama_config_path() -> Path:
    return HUB_PATH / "config" / "ollama.json"


def _load_ollama_config() -> dict:
    f = _ollama_config_path()
    if not f.is_file():
        return {"enabled": False, "base_url": _OLLAMA_DEFAULT_URL}
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        return {
            "enabled": bool(d.get("enabled", False)),
            "base_url": (d.get("base_url") or _OLLAMA_DEFAULT_URL).rstrip("/"),
        }
    except Exception:
        return {"enabled": False, "base_url": _OLLAMA_DEFAULT_URL}


def _save_ollama_config(cfg: dict):
    f = _ollama_config_path()
    f.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "enabled": bool(cfg.get("enabled", False)),
        "base_url": (cfg.get("base_url") or _OLLAMA_DEFAULT_URL).rstrip("/"),
    }
    f.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    # Export to env so LiteLLM / subprocess MCP children pick it up
    os.environ["OLLAMA_API_BASE"] = payload["base_url"]


def _ollama_fetch_tags(base_url: str, timeout: float = 5.0) -> tuple:
    """Hit GET <base>/api/tags. Return (online: bool, models: list[dict], error: str|None).

    Each model dict: {"name": "qwen2.5:7b", "size": int, "modified_at": str, "family": str, ...}
    """
    url = base_url.rstrip("/") + "/api/tags"
    try:
        req = _urlreq.Request(url, headers={"User-Agent": "anja-hub/1.0"})
        with _urlreq.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read().decode("utf-8"))
        raw = payload.get("models") or []
        models = []
        for m in raw:
            details = m.get("details") or {}
            models.append({
                "name": m.get("name") or m.get("model"),
                "size": m.get("size", 0),
                "modified_at": m.get("modified_at"),
                "family": details.get("family") or details.get("families", [None])[0],
                "parameter_size": details.get("parameter_size"),
                "quantization": details.get("quantization_level"),
            })
        models = [m for m in models if m["name"]]
        models.sort(key=lambda x: x["name"])
        return True, models, None
    except Exception as e:
        return False, [], f"{type(e).__name__}: {e}"


@app.get("/api/ollama/status")
async def api_ollama_status():
    """Stato Ollama: enabled flag, endpoint, online/offline, num modelli."""
    if not HUB_PATH or not HUB_PATH.is_dir():
        raise HTTPException(500, "hub path not configured")
    cfg = _load_ollama_config()
    online, models, err = _ollama_fetch_tags(cfg["base_url"], timeout=2.0)
    return JSONResponse({
        "enabled": cfg["enabled"],
        "base_url": cfg["base_url"],
        "online": online,
        "model_count": len(models),
        "error": err,
    })


@app.get("/api/ollama/models")
async def api_ollama_models(refresh: int = 0):
    """Lista modelli locali (chiama GET /api/tags). Cache 60s nel modulo."""
    if not HUB_PATH or not HUB_PATH.is_dir():
        raise HTTPException(500, "hub path not configured")
    cfg = _load_ollama_config()
    now = _time.time()
    cached = _MODELS_CACHE.get("ollama")
    if not refresh and cached and (now - cached[0]) < 60:
        return JSONResponse({"provider": "ollama", "models": cached[1], "cached": True, "base_url": cfg["base_url"]})
    online, models, err = _ollama_fetch_tags(cfg["base_url"], timeout=5.0)
    _MODELS_CACHE["ollama"] = (now, models)
    return JSONResponse({
        "provider": "ollama",
        "models": models,
        "online": online,
        "cached": False,
        "base_url": cfg["base_url"],
        "error": err,
    })


@app.get("/api/ollama/config")
async def api_ollama_config_get():
    if not HUB_PATH or not HUB_PATH.is_dir():
        raise HTTPException(500, "hub path not configured")
    return JSONResponse(_load_ollama_config())


@app.post("/api/ollama/config")
async def api_ollama_config_post(request: Request):
    """Body: {enabled: bool, base_url: str}."""
    if not HUB_PATH or not HUB_PATH.is_dir():
        raise HTTPException(500, "hub path not configured")
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON body")
    enabled = bool(body.get("enabled", False))
    base_url = (body.get("base_url") or _OLLAMA_DEFAULT_URL).strip().rstrip("/")
    if not re.match(r"^https?://[\w\.\-:]+(?::\d+)?(?:/.*)?$", base_url):
        raise HTTPException(400, "invalid base_url (must be http(s)://host[:port])")
    _save_ollama_config({"enabled": enabled, "base_url": base_url})
    _MODELS_CACHE.pop("ollama", None)
    return JSONResponse({"status": "ok", "enabled": enabled, "base_url": base_url})


# ============================================================
# Goals — REST endpoints (Fase 18.A)
# ============================================================

@app.get("/api/hub/anja-status")
async def api_hub_anja_status():
    """F22.9.3 — Aggregator card per hub home: stato Anja (default agent).

    Ritorna:
      - agent_name: nome agent default
      - provider/model/effort: defaults da hub config
      - goals_active: count goal hub-scope status=active
      - kanban_in_progress: count kanban tasks status=in_progress assigned ad Anja
      - dialectic_active: ultime 3 active observations
    """
    if not HUB_PATH:
        raise HTTPException(500, "hub path not configured")
    cfg = {}
    try:
        cfg_path = HUB_PATH / "config.json"
        if cfg_path.is_file():
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    agent_name = cfg.get("default_agent_name") or "Anja"
    provider = cfg.get("default_provider") or "claude"
    model = cfg.get("default_model") or "sonnet"
    effort = cfg.get("default_effort") or "off"
    user_name = cfg.get("default_user") or "user"

    # Goals: count hub-scope active
    goals_active = 0
    try:
        import goal_io
        goals = goal_io.list_goals(HUB_PATH, scope="hub", status="active")
        goals_active = len(goals)
    except Exception:
        pass

    # Kanban: count in_progress assigned to agent
    kanban_in_progress = 0
    try:
        import kanban_io
        all_tasks = kanban_io.list_tasks(HUB_PATH, status="in_progress")
        agent_lower = agent_name.lower()
        kanban_in_progress = sum(
            1 for t in all_tasks
            if (t.get("assignee") or "").lower() in (agent_lower, "")
        )
    except Exception:
        pass

    # Dialectic: top 3 active obs hub-scope
    dialectic_active = []
    try:
        import dialectic_io
        dpath = HUB_PATH / "wiki" / f"{HUB_PATH.name}-dialectic.md"
        if dpath.is_file():
            d = dialectic_io.read_dialectic(dpath)
            obs = d.get("active") or []
            for o in obs[:3]:
                dialectic_active.append({
                    "text": (o.get("text") or "")[:140],
                    "sightings": o.get("sightings", 0),
                    "sessions": o.get("sessions", 0),
                })
    except Exception:
        pass

    return JSONResponse({
        "agent_name": agent_name,
        "user_name": user_name,
        "provider": provider,
        "model": model,
        "effort": effort,
        "goals_active": goals_active,
        "kanban_in_progress": kanban_in_progress,
        "dialectic_active": dialectic_active,
    })


@app.get("/api/goals/matrix")
async def api_goals_matrix(request: Request):
    """Hub dashboard: matrix workspaces × goal status. Per overview cross-workspace."""
    if not HUB_PATH:
        raise HTTPException(500, "hub path not configured")
    _require_admin(request)
    try:
        import goal_io
    except ImportError as e:
        raise HTTPException(500, f"goal_io missing: {e}")

    all_goals = goal_io.list_goals(HUB_PATH, scope=None, status=None)
    # Aggrega per scope
    by_scope: dict = {}
    for g in all_goals:
        sc = g.get("scope", "hub")
        if sc not in by_scope:
            by_scope[sc] = {"scope": sc, "total": 0, "by_status": {}, "by_verdict": {}, "goals": []}
        by_scope[sc]["total"] += 1
        st = g.get("status", "active")
        by_scope[sc]["by_status"][st] = by_scope[sc]["by_status"].get(st, 0) + 1
        lv = g.get("last_verdict")
        if lv:
            v = lv.get("verdict", "?")
            by_scope[sc]["by_verdict"][v] = by_scope[sc]["by_verdict"].get(v, 0) + 1
        by_scope[sc]["goals"].append({
            "id": g["id"], "title": g["title"],
            "status": g["status"], "priority": g["priority"],
            "last_verdict": (lv or {}).get("verdict"),
        })
    return JSONResponse({"matrix": list(by_scope.values()), "total_goals": len(all_goals)})


@app.get("/api/goals")
async def api_goals_list(request: Request, scope: Optional[str] = None, status: Optional[str] = None):
    if not HUB_PATH or not HUB_PATH.is_dir():
        raise HTTPException(500, "hub path not configured")
    _require_scope_access(request, scope)
    try:
        import goal_io
        import kanban_io
    except ImportError as e:
        raise HTTPException(500, f"goal_io missing: {e}")
    scope = kanban_io.normalize_workspace_scope(HUB_PATH, scope)
    return JSONResponse({"goals": goal_io.list_goals(HUB_PATH, scope=scope, status=status)})


@app.get("/api/goals/{scope_kind}/{scope_target}/{goal_id}/linked-tasks")
async def api_goal_linked_tasks(scope_kind: str, scope_target: str, goal_id: str):
    if not HUB_PATH:
        raise HTTPException(500, "hub path not configured")
    try:
        import kanban_io
    except ImportError as e:
        raise HTTPException(500, f"kanban_io missing: {e}")
    tasks = kanban_io.list_tasks(HUB_PATH, linked_goal=goal_id, limit=200)
    return JSONResponse({"tasks": tasks})


@app.get("/api/goals/{scope_kind}/{scope_target}/{goal_id}")
async def api_goal_detail(scope_kind: str, scope_target: str, goal_id: str):
    """scope_kind='hub'|'workspace', scope_target='_'|<name>."""
    if not HUB_PATH or not HUB_PATH.is_dir():
        raise HTTPException(500, "hub path not configured")
    try:
        import goal_io
    except ImportError as e:
        raise HTTPException(500, f"goal_io missing: {e}")
    scope = "hub" if scope_kind == "hub" else f"workspace:{scope_target}"
    g = goal_io.read_goal(HUB_PATH, scope, goal_id)
    if not g:
        raise HTTPException(404, f"goal '{goal_id}' not found in scope '{scope}'")
    return JSONResponse(g)


@app.post("/api/goals/create")
async def api_goal_create(request: Request):
    if not HUB_PATH or not HUB_PATH.is_dir():
        raise HTTPException(500, "hub path not configured")
    try:
        import goal_io
    except ImportError as e:
        raise HTTPException(500, f"goal_io missing: {e}")
    body = await request.json()
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "title required")
    scope = body.get("scope") or "hub"
    _require_scope_access(request, scope)   # scope='workspace:X' nel body → gate esplicito
    try:
        return JSONResponse(goal_io.create_goal(
            HUB_PATH, scope, title,
            deadline=body.get("deadline"),
            priority=body.get("priority") or "medium",
            responsabile=body.get("responsabile"),
            responsabile_llm=body.get("responsabile_llm") or {},
            success_criteria=body.get("success_criteria") or [],
            judge_agent=body.get("judge_agent"),
            judge_cron=body.get("judge_cron") or "0 18 * * 0",
            judge_model=body.get("judge_model"),
            judge_provider=body.get("judge_provider"),
            judge_effort=body.get("judge_effort"),
            anti_patterns=body.get("anti_patterns") or [],
            judge_rubric=body.get("judge_rubric") or "",
            body_md=body.get("body_md") or "",
            tags=body.get("tags") or [],
            owner=body.get("owner") or "vincent",
            assigned_agents=body.get("assigned_agents") or [],
            escalation_to=body.get("escalation_to"),
            escalation_llm=body.get("escalation_llm") or {},
            escalation_trigger=body.get("escalation_trigger") or "drift_consecutive_3",
            # Phase A — autonomy + pipeline
            autonomy_level=int(body.get("autonomy_level", 1)),
            pipeline_cron=body.get("pipeline_cron") or "",
            execution_budget=body.get("execution_budget") or {},
        ))
    except Exception as e:
        raise HTTPException(400, f"create failed: {type(e).__name__}: {e}")


@app.post("/api/goals/{scope_kind}/{scope_target}/{goal_id}/update")
async def api_goal_update(scope_kind: str, scope_target: str, goal_id: str, request: Request):
    if not HUB_PATH:
        raise HTTPException(500, "hub path not configured")
    try:
        import goal_io
    except ImportError as e:
        raise HTTPException(500, f"goal_io missing: {e}")
    body = await request.json()
    scope = "hub" if scope_kind == "hub" else f"workspace:{scope_target}"
    res = goal_io.update_goal(HUB_PATH, scope, goal_id, body or {})
    if not res:
        raise HTTPException(404, "goal not found")
    return JSONResponse(res)


@app.post("/api/goals/{scope_kind}/{scope_target}/{goal_id}/judge")
async def api_goal_judge(scope_kind: str, scope_target: str, goal_id: str, request: Request):
    """Run judge on-demand. Body opzionale: {provider, model} overrides."""
    if not HUB_PATH:
        raise HTTPException(500, "hub path not configured")
    try:
        from goal_judge import run_judge_async
    except ImportError as e:
        raise HTTPException(500, f"goal_judge missing: {e}")
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    scope = "hub" if scope_kind == "hub" else f"workspace:{scope_target}"
    res = await run_judge_async(
        HUB_PATH, scope, goal_id,
        provider_override=body.get("provider"),
        model_override=body.get("model"),
        agent_override=body.get("agent"),  # M4 — opzionale, invoca un agent specifico
    )
    if res.get("error"):
        raise HTTPException(400, res["error"])
    return JSONResponse(res)


@app.post("/api/goals/{scope_kind}/{scope_target}/{goal_id}/pipeline")
async def api_goal_pipeline(scope_kind: str, scope_target: str, goal_id: str):
    """F4 — Esegue la pipeline ufficio completa (analyst → risk-officer → executor)."""
    if not HUB_PATH:
        raise HTTPException(500, "hub path not configured")
    try:
        from goal_office import run_pipeline_async
    except ImportError as e:
        raise HTTPException(500, f"goal_office missing: {e}")
    scope = "hub" if scope_kind == "hub" else f"workspace:{scope_target}"
    res = await run_pipeline_async(HUB_PATH, scope, goal_id)
    if res.get("error"):
        raise HTTPException(400, res["error"])
    return JSONResponse(res)


@app.get("/api/goals/{scope_kind}/{scope_target}/{goal_id}/pending-actions")
async def api_goal_pending_actions(scope_kind: str, scope_target: str, goal_id: str,
                                  status: str = "pending"):
    """Phase B — Lista pending actions. Default solo status='pending', '' per tutte."""
    if not HUB_PATH:
        raise HTTPException(500, "hub path not configured")
    try:
        import goal_io
    except ImportError as e:
        raise HTTPException(500, f"goal_io missing: {e}")
    scope = "hub" if scope_kind == "hub" else f"workspace:{scope_target}"
    actions = goal_io.list_pending_actions(HUB_PATH, scope, goal_id, status=(status or None))
    return JSONResponse({"actions": actions})


@app.post("/api/goals/{scope_kind}/{scope_target}/{goal_id}/pending-actions/{action_id}/resolve")
async def api_goal_resolve_action(scope_kind: str, scope_target: str, goal_id: str,
                                 action_id: str, request: Request):
    """Phase B — Approve/reject action. Body: {verdict: 'approved'|'rejected', note?}"""
    if not HUB_PATH:
        raise HTTPException(500, "hub path not configured")
    try:
        import goal_io
    except ImportError as e:
        raise HTTPException(500, f"goal_io missing: {e}")
    try:
        body = await request.json()
    except Exception:
        body = {}
    verdict = (body.get("verdict") or "").lower()
    if verdict not in ("approved", "rejected"):
        raise HTTPException(400, "verdict must be 'approved' or 'rejected'")
    scope = "hub" if scope_kind == "hub" else f"workspace:{scope_target}"
    res = goal_io.resolve_pending_action(
        HUB_PATH, scope, goal_id, action_id,
        resolution=verdict,
        note=body.get("note") or "via UI",
        by="user:webapp",
    )
    if not res:
        raise HTTPException(404, "action not found")
    # Log activity
    try:
        goal_io.append_activity(HUB_PATH, scope, goal_id, {
            "agent": "user",
            "level": "success" if verdict == "approved" else "warn",
            "event_type": f"action_{verdict.replace('ed','')}d",
            "msg": f"action {action_id} {verdict} via UI",
            "payload": {"action_id": action_id},
        })
    except Exception:
        pass
    return JSONResponse(res)


@app.get("/api/goals/{scope_kind}/{scope_target}/{goal_id}/scripts")
async def api_goal_scripts(scope_kind: str, scope_target: str, goal_id: str):
    """D2 — Lista monitor scripts per goal."""
    if not HUB_PATH:
        raise HTTPException(500, "hub path not configured")
    try:
        import script_runtime
    except ImportError as e:
        raise HTTPException(500, f"script_runtime missing: {e}")
    scope = "hub" if scope_kind == "hub" else f"workspace:{scope_target}"
    scripts = script_runtime.list_scripts_for_goal(HUB_PATH, scope, goal_id)
    return JSONResponse({"scripts": scripts})


@app.post("/api/goals/{scope_kind}/{scope_target}/{goal_id}/scripts/start")
async def api_goal_script_start(scope_kind: str, scope_target: str, goal_id: str, request: Request):
    if not HUB_PATH:
        raise HTTPException(500, "hub path not configured")
    try:
        import script_runtime
    except ImportError as e:
        raise HTTPException(500, f"script_runtime missing: {e}")
    body = await request.json()
    path = body.get("path", "")
    if not path:
        raise HTTPException(400, "path required")
    scope = "hub" if scope_kind == "hub" else f"workspace:{scope_target}"
    from pathlib import Path as _P
    res = script_runtime.start_script(HUB_PATH, scope, goal_id, _P(path))
    return JSONResponse(res)


@app.post("/api/goals/{scope_kind}/{scope_target}/{goal_id}/scripts/stop")
async def api_goal_script_stop(scope_kind: str, scope_target: str, goal_id: str, request: Request):
    if not HUB_PATH:
        raise HTTPException(500, "hub path not configured")
    try:
        import script_runtime
    except ImportError as e:
        raise HTTPException(500, f"script_runtime missing: {e}")
    body = await request.json()
    path = body.get("path", "")
    if not path:
        raise HTTPException(400, "path required")
    from pathlib import Path as _P
    res = script_runtime.stop_script(HUB_PATH, _P(path))
    return JSONResponse(res)


@app.get("/api/goals/{scope_kind}/{scope_target}/{goal_id}/scripts/log")
async def api_goal_script_log(scope_kind: str, scope_target: str, goal_id: str, path: str, tail: int = 100):
    if not HUB_PATH:
        raise HTTPException(500, "hub path not configured")
    try:
        import script_runtime
    except ImportError as e:
        raise HTTPException(500, f"script_runtime missing: {e}")
    from pathlib import Path as _P
    log = script_runtime.read_script_log(HUB_PATH, _P(path), tail_lines=tail)
    return JSONResponse({"log": log})


@app.get("/api/goals/{scope_kind}/{scope_target}/{goal_id}/notes")
async def api_goal_notes(scope_kind: str, scope_target: str, goal_id: str,
                         run_id: str = "", limit: int = 12):
    """F4 — Lista notes specialist. Se run_id passed, filtra solo quel run."""
    if not HUB_PATH:
        raise HTTPException(500, "hub path not configured")
    try:
        import goal_io
    except ImportError as e:
        raise HTTPException(500, f"goal_io missing: {e}")
    scope = "hub" if scope_kind == "hub" else f"workspace:{scope_target}"
    if run_id:
        return JSONResponse({"notes": goal_io.read_notes_for_run(HUB_PATH, scope, goal_id, run_id)})
    return JSONResponse({"notes": goal_io.read_recent_notes(HUB_PATH, scope, goal_id, limit=limit)})


@app.post("/api/goals/{scope_kind}/{scope_target}/{goal_id}/judge/{agent_name}")
async def api_goal_judge_per_agent(scope_kind: str, scope_target: str, goal_id: str, agent_name: str):
    """M4 — Run judge invocando un agent specifico del team (specialist o responsabile).

    Cerca l'agent nelle assigned_agents per usare il suo LLM dedicato.
    """
    if not HUB_PATH:
        raise HTTPException(500, "hub path not configured")
    try:
        from goal_judge import run_judge_async
        import goal_io
    except ImportError as e:
        raise HTTPException(500, f"goal_judge missing: {e}")
    scope = "hub" if scope_kind == "hub" else f"workspace:{scope_target}"
    g = goal_io.read_goal(HUB_PATH, scope, goal_id)
    if not g:
        raise HTTPException(404, "goal not found")
    # Find agent config (responsabile o specialist) per estrarre LLM dedicato
    meta = g["meta"]
    llm_cfg = None
    if (meta.get("responsabile") or "").lower() == agent_name.lower():
        llm_cfg = meta.get("responsabile_llm") or {}
    else:
        for a in (meta.get("assigned_agents") or []):
            if (a.get("agent") or "").lower() == agent_name.lower():
                llm_cfg = a.get("llm") or {}
                break
    if llm_cfg is None:
        raise HTTPException(404, f"agent '{agent_name}' not found in the goal team")
    provider = llm_cfg.get("provider") if isinstance(llm_cfg, dict) else None
    model = llm_cfg.get("model") if isinstance(llm_cfg, dict) else None
    res = await run_judge_async(
        HUB_PATH, scope, goal_id,
        provider_override=provider,
        model_override=model,
        agent_override=agent_name,
    )
    if res.get("error"):
        raise HTTPException(400, res["error"])
    return JSONResponse(res)


@app.post("/api/goals/{scope_kind}/{scope_target}/{goal_id}/reflect")
async def api_goal_reflect(scope_kind: str, scope_target: str, goal_id: str, request: Request):
    if not HUB_PATH:
        raise HTTPException(500, "hub path not configured")
    try:
        import goal_io
    except ImportError as e:
        raise HTTPException(500, f"goal_io missing: {e}")
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text required")
    scope = "hub" if scope_kind == "hub" else f"workspace:{scope_target}"
    ok = goal_io.append_reflection(HUB_PATH, scope, goal_id, text)
    if not ok:
        raise HTTPException(404, "goal not found")
    return JSONResponse({"status": "ok"})


# Fase 18.C — Suggested actions inbox
@app.get("/api/goals/suggestions")
async def api_goals_suggestions_list(status: Optional[str] = None):
    if not HUB_PATH:
        raise HTTPException(500, "hub path not configured")
    try:
        from goal_judge import list_suggestions
    except ImportError as e:
        raise HTTPException(500, f"goal_judge missing: {e}")
    return JSONResponse({"suggestions": list_suggestions(HUB_PATH, status=status)})


@app.post("/api/goals/suggestions/{sug_id}/resolve")
async def api_goals_suggestion_resolve(sug_id: str, request: Request):
    if not HUB_PATH:
        raise HTTPException(500, "hub path not configured")
    try:
        from goal_judge import resolve_suggestion
    except ImportError as e:
        raise HTTPException(500, f"goal_judge missing: {e}")
    body = await request.json()
    action = body.get("action")
    note = body.get("note") or ""
    res = resolve_suggestion(HUB_PATH, sug_id, action, note)
    if res.get("error"):
        raise HTTPException(400, res["error"])
    return JSONResponse(res)


@app.get("/api/goals/{scope_kind}/{scope_target}/{goal_id}/activity")
async def api_goal_activity(scope_kind: str, scope_target: str, goal_id: str,
                            since: str = "", limit: int = 200):
    """M3 — Legge activity.jsonl del goal. since=ISO ts filtra eventi più recenti."""
    if not HUB_PATH:
        raise HTTPException(500, "hub path not configured")
    try:
        import goal_io
    except ImportError as e:
        raise HTTPException(500, f"goal_io missing: {e}")
    scope = "hub" if scope_kind == "hub" else f"workspace:{scope_target}"
    events = goal_io.read_activity(HUB_PATH, scope, goal_id, since_ts=since, limit=limit)
    return JSONResponse({"events": events, "count": len(events)})


# M3 — Activity stream WebSocket subscribers (per goal_key)
# Tail file polling + broadcast a tutti i subscribers di quel goal.
GOAL_ACTIVITY_SUBSCRIBERS: dict = {}  # goal_key -> set of WebSocket


@app.websocket("/ws/goals/{scope_kind}/{scope_target}/{goal_id}/activity")
async def ws_goal_activity(websocket: WebSocket, scope_kind: str, scope_target: str, goal_id: str):
    """Stream live di activity events di un goal specifico.

    Tail dell'activity.jsonl: pollata ogni 1s, ogni nuovo evento broadcast al client.
    """
    await websocket.accept()
    if not HUB_PATH:
        await websocket.close(code=1011, reason="hub not configured")
        return
    try:
        import goal_io
    except ImportError:
        await websocket.close(code=1011, reason="goal_io missing")
        return
    scope = "hub" if scope_kind == "hub" else f"workspace:{scope_target}"
    activity_path = goal_io.activity_log_path(HUB_PATH, scope, goal_id)
    last_pos = 0
    # Snapshot iniziale (ultimi 50 eventi)
    try:
        initial = goal_io.read_activity(HUB_PATH, scope, goal_id, limit=50)
        await websocket.send_json({"type": "snapshot", "events": initial})
        # Update last_pos al fine file
        if activity_path.is_file():
            last_pos = activity_path.stat().st_size
    except Exception as e:
        await websocket.send_json({"type": "error", "message": str(e)})
    try:
        while True:
            await asyncio.sleep(1.0)
            if not activity_path.is_file():
                continue
            try:
                size = activity_path.stat().st_size
                if size > last_pos:
                    with open(activity_path, "r", encoding="utf-8") as f:
                        f.seek(last_pos)
                        new_lines = f.read()
                    last_pos = size
                    events = []
                    for line in new_lines.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            events.append(json.loads(line))
                        except Exception:
                            continue
                    if events:
                        await websocket.send_json({"type": "events", "events": events})
                elif size < last_pos:
                    # File troncato (clear) — resetta
                    last_pos = 0
            except WebSocketDisconnect:
                break
            except Exception as e:
                try:
                    await websocket.send_json({"type": "error", "message": f"tail error: {e}"})
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[ws/goals/activity] error: {e}", flush=True)


@app.delete("/api/goals/{scope_kind}/{scope_target}/{goal_id}")
async def api_goal_delete(scope_kind: str, scope_target: str, goal_id: str):
    if not HUB_PATH:
        raise HTTPException(500, "hub path not configured")
    try:
        import goal_io
    except ImportError as e:
        raise HTTPException(500, f"goal_io missing: {e}")
    scope = "hub" if scope_kind == "hub" else f"workspace:{scope_target}"
    res = goal_io.delete_goal(HUB_PATH, scope, goal_id)
    if res is None:
        raise HTTPException(404, "goal not found")
    if res.get("error"):
        raise HTTPException(500, res["error"])
    return JSONResponse(res)


@app.post("/api/goals/{scope_kind}/{scope_target}/{goal_id}/archive")
async def api_goal_archive(scope_kind: str, scope_target: str, goal_id: str, request: Request):
    if not HUB_PATH:
        raise HTTPException(500, "hub path not configured")
    try:
        import goal_io
    except ImportError as e:
        raise HTTPException(500, f"goal_io missing: {e}")
    body = await request.json()
    outcome = (body.get("outcome") or "").strip()
    if outcome not in ("achieved", "abandoned", "failed"):
        raise HTTPException(400, "outcome must be achieved|abandoned|failed")
    scope = "hub" if scope_kind == "hub" else f"workspace:{scope_target}"
    res = goal_io.archive_goal(HUB_PATH, scope, goal_id, outcome, body.get("reflection") or "")
    if not res:
        raise HTTPException(404, "goal not found")
    return JSONResponse(res)


# ============================================================
# Anthropic Claude subscription detection (Fase 7v.b)
# ============================================================

@app.get("/api/claude-oauth/status")
async def api_claude_oauth_status():
    """Detect Claude CLI subscription auth (Pro/Max). Read-only — claude-agent-sdk
    gestisce internamente token management."""
    try:
        from claude_oauth import claude_auth_summary
    except ImportError as e:
        raise HTTPException(500, f"claude_oauth module missing: {e}")
    return JSONResponse(claude_auth_summary())


@app.post("/api/claude-oauth/login/start")
async def api_claude_oauth_login_start(request: Request):
    """Login della subscription Claude DALLA UI: lancia `claude auth login`
    sull'host (PTY) e ritorna l'URL OAuth da aprire. Il browser mostra un
    codice → l'utente lo incolla in /login/complete. Admin only."""
    _require_admin(request)
    import claude_oauth
    res = await asyncio.to_thread(claude_oauth.login_start)
    if not res.get("ok"):
        raise HTTPException(502, res.get("error", "login start failed"))
    return JSONResponse(res)


@app.post("/api/claude-oauth/login/complete")
async def api_claude_oauth_login_complete(request: Request, payload: dict = Body(...)):
    _require_admin(request)
    import claude_oauth
    res = await asyncio.to_thread(claude_oauth.login_complete, payload.get("code", ""))
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "login failed"))
    # le sessioni SDK vive hanno il vecchio token: riciclo pigro al prossimo turno
    try:
        import claude_session
        await claude_session.pool.close_all()
    except Exception:
        pass
    return JSONResponse(res)


@app.post("/api/claude-oauth/login/cancel")
async def api_claude_oauth_login_cancel(request: Request):
    _require_admin(request)
    import claude_oauth
    claude_oauth.login_cancel()
    return JSONResponse({"ok": True})


@app.get("/api/claude-oauth/login/pending")
async def api_claude_oauth_login_pending():
    import claude_oauth
    return JSONResponse(claude_oauth.login_pending())


# ============================================================
# OpenAI ChatGPT subscription OAuth (Fase 7v)
# ============================================================

@app.get("/api/openai-oauth/status")
async def api_openai_oauth_status():
    """Stato auth Codex CLI + flag anja. UI usage."""
    if not HUB_PATH or not HUB_PATH.is_dir():
        raise HTTPException(500, "hub path not configured")
    try:
        from openai_oauth import codex_auth_summary, load_openai_oauth_config
    except ImportError as e:
        raise HTTPException(500, f"openai_oauth module missing: {e}")
    summary = codex_auth_summary()
    cfg = load_openai_oauth_config(HUB_PATH)
    return JSONResponse({
        **summary,
        "anja_enabled": cfg.get("enabled", False),
        "use_codex_cli": cfg.get("use_codex_cli", True),
    })


@app.post("/api/openai-oauth/config")
async def api_openai_oauth_config(request: Request):
    """Body: {enabled: bool}. Toggle use of ChatGPT subscription for OpenAI calls."""
    if not HUB_PATH or not HUB_PATH.is_dir():
        raise HTTPException(500, "hub path not configured")
    _require_admin(request)
    try:
        from openai_oauth import save_openai_oauth_config, has_codex_auth
    except ImportError as e:
        raise HTTPException(500, f"openai_oauth module missing: {e}")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON body")
    enabled = bool(body.get("enabled", False))
    if enabled and not has_codex_auth():
        raise HTTPException(400, "Codex CLI auth not found. Run `codex login` first.")
    save_openai_oauth_config(HUB_PATH, {"enabled": enabled, "use_codex_cli": True})
    return JSONResponse({"status": "ok", "enabled": enabled})


@app.post("/api/openai-oauth/refresh")
async def api_openai_oauth_refresh():
    """Trigger refresh manuale del token. Utile se token expired e Codex CLI non è stato usato di recente."""
    if not HUB_PATH or not HUB_PATH.is_dir():
        raise HTTPException(500, "hub path not configured")
    try:
        from openai_oauth import refresh_token, codex_auth_summary
    except ImportError as e:
        raise HTTPException(500, f"openai_oauth module missing: {e}")
    ok, err = refresh_token()
    if not ok:
        raise HTTPException(400, f"refresh failed: {err}")
    return JSONResponse({"status": "ok", "summary": codex_auth_summary()})


# ============================================================
# Onboarding (Fase 12b) — primo avvio guidato
# ============================================================

# env var per provider a API key, in ordine di priorità per default suggerito
_ONBOARD_KEY_PROVIDERS = [
    ("openai", "OPENAI_API_KEY"),
    ("xai", "XAI_API_KEY"),
    ("openrouter", "OPENROUTER_API_KEY"),
    ("gemini", "GEMINI_API_KEY"),
    ("anthropic", "ANTHROPIC_API_KEY"),
]


@app.get("/api/onboarding/status")
async def api_onboarding_status():
    """Stato per il wizard: serve onboarding? quali provider sono già utilizzabili?

    providers_available = lista di id provider pronti all'uso senza altra config.
    suggested_provider = primo della lista (priorità claude > codex > key)."""
    available: list[str] = []

    # Claude subscription (Pro/Max) o ANTHROPIC_API_KEY → claude SDK
    try:
        from claude_oauth import claude_auth_summary
        cs = claude_auth_summary()
        if cs.get("subscription_active") or cs.get("api_key_set"):
            available.append("claude")
    except Exception:
        pass

    # Codex/ChatGPT subscription
    try:
        from openai_oauth import has_codex_auth
        if has_codex_auth():
            available.append("openai_oauth")
    except Exception:
        pass

    # Provider a API key (env o .secrets.env)
    secrets = _load_secrets_dict()
    for prov_id, env in _ONBOARD_KEY_PROVIDERS:
        if os.environ.get(env) or secrets.get(env):
            available.append(prov_id)

    return JSONResponse({
        "needs_onboarding": _needs_onboarding(),
        "providers_available": available,
        "suggested_provider": available[0] if available else None,
    })


@app.post("/api/onboarding/complete")
async def api_onboarding_complete(request: Request):
    """Crea identità (user + agent name + profilo). Riusa users_init.py.

    Body: {name, agent_name?, profile?, language?}
    L'auth/provider config è gestita separatamente dal wizard via gli endpoint
    /api/settings/secrets + /api/settings/defaults esistenti."""
    if not HUB_PATH or not HUB_PATH.is_dir():
        raise HTTPException(500, "hub path not configured")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON body")

    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    agent_name = (body.get("agent_name") or "Anja").strip() or "Anja"
    profile = (body.get("profile") or "").strip()
    language = (body.get("language") or "it").strip() or "it"

    # 1. crea user profile via users_init.py (--default setta default_user nel config)
    script = ANJA_HUB_DIR / "scripts" / "users_init.py"
    proc = subprocess.run(
        ["python3", str(script), "--hub", str(HUB_PATH), "--name", name,
         "--language", language, "--default", "--force"],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise HTTPException(500, f"users_init failed: {proc.stderr.strip()[:300]}")

    slug = _hub_config().get("default_user")

    # 2. setta default_agent_name nel config.json
    cfg = _hub_config()
    cfg["default_agent_name"] = agent_name
    (HUB_PATH / "config.json").write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # 3. profilo libero → append nel body del file user HOT
    if profile and slug:
        user_file = HUB_PATH / "users" / f"{slug}.md"
        if user_file.is_file():
            with user_file.open("a", encoding="utf-8") as f:
                f.write(f"\n## About (onboarding)\n\n{profile}\n")

    return JSONResponse({"status": "ok", "slug": slug, "agent_name": agent_name})


@app.get("/api/settings/defaults")
async def api_settings_defaults_get():
    """Read default_provider + default_model + default_effort from hub config.json."""
    if not HUB_PATH:
        raise HTTPException(500, "hub path not configured")
    cfg_path = HUB_PATH / "config.json"
    cfg = {}
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    return JSONResponse({
        "default_provider": cfg.get("default_provider", "claude"),
        "default_model": cfg.get("default_model", "sonnet"),
        "default_effort": cfg.get("default_effort", "off"),
        "default_agent_name": cfg.get("default_agent_name", "Anja"),
        "default_user": cfg.get("default_user", ""),
    })


@app.post("/api/settings/defaults")
async def api_settings_defaults_post(request: Request):
    """Persisti default_provider/model/effort/agent_name in hub config.json."""
    if not HUB_PATH:
        raise HTTPException(500, "hub path not configured")
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    cfg_path = HUB_PATH / "config.json"
    cfg = {}
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    if "default_provider" in body:
        cfg["default_provider"] = str(body["default_provider"]).strip()
    if "default_model" in body:
        cfg["default_model"] = str(body["default_model"]).strip()
    if "default_effort" in body:
        cfg["default_effort"] = str(body["default_effort"]).strip() or "off"
    if "default_agent_name" in body:
        cfg["default_agent_name"] = str(body["default_agent_name"]).strip() or "Anja"
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return JSONResponse({
        "default_provider": cfg.get("default_provider", "claude"),
        "default_model": cfg.get("default_model", "sonnet"),
        "default_effort": cfg.get("default_effort", "off"),
        "default_agent_name": cfg.get("default_agent_name", "Anja"),
        "default_user": cfg.get("default_user", ""),
    })


_PROVIDER_MODELS_SHORTLIST = {
    "claude": ["sonnet", "opus", "haiku"],
    "openai": ["gpt-5", "gpt-5-mini", "gpt-4.1", "o3", "o3-mini"],
    "openai_oauth": ["gpt-5.5"],  # Fase 7v — solo modello whitelisted da OpenAI per ChatGPT account
    "xai": ["grok-4", "grok-4-fast", "grok-3"],
    "openrouter": ["anthropic/claude-sonnet-4-5", "openai/gpt-5", "x-ai/grok-4", "google/gemini-2.5-pro"],
    "gemini": ["gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.1-pro-preview"],
    "mistral": ["mistral-large-latest", "mistral-medium-latest"],
    "groq": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
}


def _get_provider_models_short(provider: str) -> list[str]:
    """Lista corta di model popolari per inline keyboard Telegram.

    Per `ollama` legge runtime i modelli installati (variano per ogni utente).
    Per gli altri ritorna shortlist hardcoded.
    """
    p = provider.lower()
    if p == "ollama":
        # Runtime: leggi /api/tags di Ollama
        try:
            cfg = _load_ollama_config()
            if not cfg.get("enabled"):
                return []
            _online, ml, _err = _ollama_fetch_tags(cfg["base_url"], timeout=2.0)
            # Top 12 modelli by nome (ordinato per filtering Telegram)
            return [m["name"] for m in ml[:12]]
        except Exception:
            return []
    return _PROVIDER_MODELS_SHORTLIST.get(p, [])


AUDIO_CONFIG_DEFAULTS = {
    "stt": {"provider": "openai", "model": "whisper-1"},
    "tts": {"provider": "openai", "model": "tts-1", "voice": "nova"},
    "realtime": {"provider": "openai", "model": "gpt-4o-realtime-preview", "voice": "alloy", "enabled": False},
}


def _load_audio_config() -> dict:
    """Read audio block from hub config.json, merge with defaults."""
    if not HUB_PATH:
        return dict(AUDIO_CONFIG_DEFAULTS)
    try:
        cfg = json.loads((HUB_PATH / "config.json").read_text(encoding="utf-8"))
        audio = cfg.get("audio") or {}
        merged = {}
        for k in ("stt", "tts", "realtime"):
            merged[k] = {**AUDIO_CONFIG_DEFAULTS[k], **(audio.get(k) or {})}
        return merged
    except Exception:
        return dict(AUDIO_CONFIG_DEFAULTS)


@app.get("/api/settings/audio")
async def api_settings_audio_get():
    if not HUB_PATH:
        raise HTTPException(500, "hub path not configured")
    return JSONResponse(_load_audio_config())


_RESEARCH_SETTINGS_DEFAULTS = {
    "preferred": "duckduckgo",  # duckduckgo | serpapi | fallback
}


def _load_hub_secrets() -> dict:
    """Load <hub>/.secrets.env as {KEY: value}."""
    if not HUB_PATH:
        return {}
    secrets_file = HUB_PATH / ".secrets.env"
    if not secrets_file.is_file():
        return {}
    out = {}
    try:
        for line in secrets_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return out


@app.get("/api/settings/research")
async def api_settings_research_get():
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    cfg_path = HUB_PATH / "config.json"
    cfg = {}
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    saved = cfg.get("research") or {}
    merged = dict(_RESEARCH_SETTINGS_DEFAULTS)
    merged.update({k: v for k, v in saved.items() if k in _RESEARCH_SETTINGS_DEFAULTS})
    secrets = _load_hub_secrets()
    merged["serpapi_configured"] = bool(secrets.get("SERPAPI_KEY") or secrets.get("SERP_API_KEY"))
    merged["gemini_configured"] = bool(secrets.get("GEMINI_API_KEY"))
    return merged


@app.post("/api/settings/research")
async def api_settings_research_post(request: Request):
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    cfg_path = HUB_PATH / "config.json"
    cfg = {}
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    cur = cfg.get("research") or {}
    for k in ("preferred",):
        if k in body:
            cur[k] = body[k]
    cfg["research"] = cur
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return cur


@app.post("/api/settings/research/test")
async def api_settings_research_test(request: Request):
    """Test live di una research skill: invoca lo script e ritorna primo risultato."""
    if not HUB_PATH:
        raise HTTPException(500, "hub not configured")
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    skill = (body.get("skill") or "").strip()
    query = (body.get("query") or "anja personal AI hub").strip()
    _research_scripts = {"research-duckduckgo": "ddg_search.py",
                         "research-serpapi": "serpapi_search.py",
                         "research-gemini": "gemini_search.py"}
    if skill not in _research_scripts:
        raise HTTPException(400, f"skill must be one of {sorted(_research_scripts)}")

    # Resolve script path: anja-hub plugin (skill research vivono qui, non in anjadev)
    script_name = _research_scripts[skill]
    script = ANJA_HUB_DIR / "skills" / skill / "scripts" / script_name
    if not script.is_file():
        return {"ok": False, "error": f"script not found at {script}"}

    # Inject secrets nell'env
    env = dict(os.environ)
    secrets = _load_hub_secrets()
    for k, v in secrets.items():
        env[k] = v

    try:
        r = subprocess.run(
            [sys.executable, str(script), query, "3"],
            capture_output=True, text=True, timeout=50, env=env,
        )
        out = json.loads(r.stdout) if r.stdout.strip() else {"error": "no output"}
        if "error" in out:
            return {"ok": False, "error": out["error"], "skill": skill}
        return {
            "ok": True,
            "skill": skill,
            "query": query,
            "count": out.get("count", 0),
            "preview": (out.get("results") or [{}])[0] if out.get("results") else None,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout 25s", "skill": skill}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "skill": skill}


# --- F-DeepResearch: Gemini Deep Research via Interactions API -------------------
# Task async (~20 min, max 60): create → poll in background → report .md in
# <hub>/raw/research/<data>/ + notifica bell/Telegram. Stato persistito su file
# così un restart riprende il polling (l'interaction resta su Google).

_DR_AGENTS = {"standard": "deep-research-preview-04-2026",
              "max": "deep-research-max-preview-04-2026"}
_DR_BASE = "https://generativelanguage.googleapis.com/v1beta/interactions"


def _dr_state_path() -> Path:
    return HUB_PATH / "data" / "deep_research.json"


def _dr_load() -> dict:
    try:
        return json.loads(_dr_state_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _dr_save(state: dict) -> None:
    p = _dr_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _dr_update(task_id: str, **fields) -> dict:
    state = _dr_load()
    task = state.setdefault(task_id, {})
    task.update(fields)
    _dr_save(state)
    return task


def _dr_extract_report(interaction: dict) -> str:
    """Report finale = ultimo step model_output, parts testuali concatenate."""
    steps = interaction.get("steps") or []
    for step in reversed(steps):
        if step.get("type") != "model_output":
            continue
        text = "".join(c.get("text", "") for c in (step.get("content") or [])
                       if c.get("type") == "text")
        if text.strip():
            return text
    # fallback: campo output piatto se presente
    out = interaction.get("output")
    return out if isinstance(out, str) else ""


async def _dr_poll_loop(task_id: str, interaction_id: str, api_key: str) -> None:
    import httpx
    deadline = time.time() + 75 * 60
    async with httpx.AsyncClient(timeout=60) as client:
        while time.time() < deadline:
            await asyncio.sleep(30)
            try:
                r = await client.get(f"{_DR_BASE}/{interaction_id}",
                                     headers={"x-goog-api-key": api_key})
                data = r.json()
            except Exception as e:
                print(f"[deep-research] poll {task_id}: {e}", flush=True)
                continue
            status = data.get("status", "")
            if status == "in_progress" or not status:
                continue
            if status == "failed":
                err = (data.get("error") or {}).get("message", "") or "failed"
                _dr_update(task_id, status="failed", error=err,
                           completed=datetime.now().isoformat(timespec="seconds"))
                notif_bus.publish(HUB_PATH, source="webapp", category="error",
                                  title="Deep Research failed",
                                  body=f"{_dr_load().get(task_id, {}).get('query', '')[:120]} — {err}")
                return
            # completed
            report = _dr_extract_report(data)
            task = _dr_load().get(task_id, {})
            slug = re.sub(r"[^a-z0-9]+", "-", task.get("query", "report").lower())[:48].strip("-")
            day = datetime.now().strftime("%Y-%m-%d")
            dest_dir = HUB_PATH / "raw" / "research" / day
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{slug}-{datetime.now().strftime('%H%M')}.md"
            header = (f"# Deep Research — {task.get('query', '')}\n\n"
                      f"> agent: {task.get('agent', '')} · interaction: {interaction_id} · {day}\n\n")
            dest.write_text(header + (report or "(empty report)"), encoding="utf-8")
            _dr_update(task_id, status="completed", report_path=str(dest),
                       report_chars=len(report),
                       completed=datetime.now().isoformat(timespec="seconds"))
            notif_bus.publish(HUB_PATH, source="webapp", category="success",
                              title="Deep Research completed",
                              body=f"{task.get('query', '')[:120]} — report in {dest}",
                              payload={"report_path": str(dest), "task_id": task_id})
            print(f"[deep-research] {task_id} completed → {dest}", flush=True)
            return
    _dr_update(task_id, status="timeout",
               completed=datetime.now().isoformat(timespec="seconds"))
    notif_bus.publish(HUB_PATH, source="webapp", category="warn",
                      title="Deep Research timeout (75 min)",
                      body=_dr_load().get(task_id, {}).get("query", "")[:120])


@app.post("/api/research/deep")
async def api_research_deep_start(request: Request, payload: dict = Body(...)):
    """Lancia una Gemini Deep Research in background. Body: {query, mode?}.
    mode: 'standard' (default, ~$1-3) | 'max' (~$3-7). Il report arriva come
    notifica + file in <hub>/raw/research/."""
    if not HUB_PATH:
        raise HTTPException(400, "hub not configured")
    _require_admin(request)
    query = (payload.get("query") or "").strip()
    if not query:
        raise HTTPException(400, "query required")
    mode = (payload.get("mode") or "standard").strip()
    agent = _DR_AGENTS.get(mode)
    if not agent:
        raise HTTPException(400, f"mode must be one of {sorted(_DR_AGENTS)}")
    api_key = (_load_hub_secrets().get("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise HTTPException(400, "GEMINI_API_KEY not configured (Settings → Integrations)")

    import httpx
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(_DR_BASE, headers={"x-goog-api-key": api_key},
                                  json={"input": query, "agent": agent,
                                        "background": True, "store": True})
            data = r.json()
            if r.status_code >= 400:
                detail = (data.get("error") or {}).get("message", r.text[:200])
                raise HTTPException(502, f"Interactions API: {detail}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Interactions API: {type(e).__name__}: {e}")

    interaction_id = data.get("id", "")
    if not interaction_id:
        raise HTTPException(502, f"response without id: {str(data)[:200]}")
    task_id = f"dr-{int(time.time())}"
    _dr_update(task_id, interaction_id=interaction_id, query=query, mode=mode,
               agent=agent, status="in_progress",
               created=datetime.now().isoformat(timespec="seconds"))
    asyncio.create_task(_dr_poll_loop(task_id, interaction_id, api_key))
    return JSONResponse({"ok": True, "task_id": task_id,
                         "interaction_id": interaction_id, "status": "in_progress",
                         "eta": "~20 min (standard) / ~40 min (max) — report via notification"})


@app.get("/api/research/deep")
async def api_research_deep_list():
    if not HUB_PATH:
        raise HTTPException(400, "hub not configured")
    state = _dr_load()
    items = [{"task_id": k, **v} for k, v in state.items()]
    items.sort(key=lambda x: x.get("created", ""), reverse=True)
    return JSONResponse({"tasks": items[:50]})


@app.get("/api/research/deep/{task_id}")
async def api_research_deep_get(task_id: str):
    if not HUB_PATH:
        raise HTTPException(400, "hub not configured")
    task = _dr_load().get(task_id)
    if not task:
        raise HTTPException(404, "task not found")
    return JSONResponse({"task_id": task_id, **task})


@app.get("/api/research/deep/{task_id}/report")
async def api_research_deep_report(request: Request, task_id: str):
    """Contenuto markdown del report. Path confinato in <hub>/raw/research/."""
    if not HUB_PATH:
        raise HTTPException(400, "hub not configured")
    _require_admin(request)   # stesso gate di lancio/delete (contenuto hub-level)
    task = _dr_load().get(task_id)
    if not task or not task.get("report_path"):
        raise HTTPException(404, "report not available")
    p = Path(task["report_path"]).resolve()
    base = (HUB_PATH / "raw" / "research").resolve()
    if not p.is_relative_to(base) or not p.is_file():
        raise HTTPException(404, "report not found")
    return JSONResponse({"task_id": task_id, "path": str(p),
                         "content": p.read_text(encoding="utf-8")})


@app.delete("/api/research/deep/{task_id}")
async def api_research_deep_delete(request: Request, task_id: str):
    """Rimuove la task dalla lista e cancella il file report se esiste."""
    if not HUB_PATH:
        raise HTTPException(400, "hub not configured")
    _require_admin(request)
    state = _dr_load()
    task = state.pop(task_id, None)
    if not task:
        raise HTTPException(404, "task not found")
    rp = task.get("report_path")
    if rp:
        p = Path(rp).resolve()
        base = (HUB_PATH / "raw" / "research").resolve()
        if p.is_relative_to(base):
            p.unlink(missing_ok=True)
    _dr_save(state)
    return JSONResponse({"ok": True})


@app.on_event("startup")
async def _startup_deep_research_resume():
    """Riprende il polling delle Deep Research rimaste in_progress a un restart."""
    if not HUB_PATH:
        return
    try:
        state = _dr_load()
        api_key = (_load_hub_secrets().get("GEMINI_API_KEY") or "").strip()
        for tid, task in state.items():
            if task.get("status") == "in_progress" and task.get("interaction_id") and api_key:
                asyncio.create_task(_dr_poll_loop(tid, task["interaction_id"], api_key))
                print(f"[deep-research] resume polling {tid}", flush=True)
    except Exception as e:
        print(f"[deep-research] resume fallito: {e}", flush=True)


_NOTIF_SETTINGS_DEFAULTS = {
    "sources": {s: True for s in ("goal", "kanban", "routine", "chat", "script",
                                    "telegram", "daemon", "webapp", "mcp")},
    "min_severity": 0,
    "mute_telegram_echo": False,
    "auto_cleanup_days": 30,
}


@app.get("/api/settings/notifications")
async def api_settings_notif_get():
    if not HUB_PATH:
        raise HTTPException(500, "hub path not configured")
    cfg_path = HUB_PATH / "config.json"
    cfg = {}
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    saved = cfg.get("notifications") or {}
    # Merge defaults
    merged = dict(_NOTIF_SETTINGS_DEFAULTS)
    merged.update({k: v for k, v in saved.items() if k in _NOTIF_SETTINGS_DEFAULTS})
    if "sources" in saved:
        merged["sources"] = {**_NOTIF_SETTINGS_DEFAULTS["sources"], **(saved.get("sources") or {})}
    return merged


@app.post("/api/settings/notifications")
async def api_settings_notif_post(request: Request):
    if not HUB_PATH:
        raise HTTPException(500, "hub path not configured")
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    cfg_path = HUB_PATH / "config.json"
    cfg = {}
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    cur = cfg.get("notifications") or {}
    for k in ("sources", "min_severity", "mute_telegram_echo", "auto_cleanup_days"):
        if k in body:
            cur[k] = body[k]
    cfg["notifications"] = cur
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return cur


@app.post("/api/settings/audio")
async def api_settings_audio_post(request: Request):
    if not HUB_PATH:
        raise HTTPException(500, "hub path not configured")
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    cfg_path = HUB_PATH / "config.json"
    cfg = {}
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    cur_audio = cfg.get("audio") or {}
    # Merge sezione-per-sezione (stt, tts, realtime)
    for section in ("stt", "tts", "realtime"):
        if section in body:
            cur_audio[section] = {**(cur_audio.get(section) or {}), **(body.get(section) or {})}
    cfg["audio"] = cur_audio
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return JSONResponse(_load_audio_config())


def _project_root(name: str) -> Optional[Path]:
    """Risolvi project root path da nome registrato. Returns None se non locale.

    Normalizza: ritorna SEMPRE la dir CONTENENTE `.anjawiki/`, anche se il registry
    punta direttamente a `.anjawiki/` (capita per workspace internal scaffolded via
    workspace_scaffold.scaffold_workspace).
    """
    projects = _build_projects_context()
    for p in projects:
        if p.get("name") == name:
            loc = p.get("location") or {}
            if loc.get("kind") == "local" and loc.get("path"):
                raw = Path(loc["path"]).resolve()
                # Se il path finisce con `.anjawiki`, sali di un livello
                if raw.name == ".anjawiki":
                    return raw.parent
                return raw
    return None


def _load_project_preferences(name: str) -> dict:
    """Read `<project>/.anjawiki/preferences.json`. Returns dict (vuoto se assente).
    Schema: {default_provider, default_model, default_effort, auto_compact_pct, ...}
    """
    root = _project_root(name)
    if not root:
        return {}
    pf = root / ".anjawiki" / "preferences.json"
    if not pf.is_file():
        return {}
    try:
        return json.loads(pf.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_project_preferences(name: str, prefs: dict) -> bool:
    """Write `<project>/.anjawiki/preferences.json`."""
    root = _project_root(name)
    if not root:
        return False
    pf = root / ".anjawiki" / "preferences.json"
    pf.parent.mkdir(parents=True, exist_ok=True)
    try:
        pf.write_text(json.dumps(prefs, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


def _resolve_defaults_for_scope(scope: str) -> dict:
    """Returns provider/model/effort considerando project prefs > hub defaults."""
    hub = _load_hub_defaults()
    if scope and scope.startswith("project:"):
        proj_name = scope.split(":", 1)[1]
        prefs = _load_project_preferences(proj_name)
        out = dict(hub)
        if prefs.get("default_provider"):
            out["provider"] = prefs["default_provider"]
        if prefs.get("default_model"):
            out["model"] = prefs["default_model"]
        if prefs.get("default_effort"):
            out["effort"] = prefs["default_effort"]
        return out
    return hub


@app.get("/api/project/preferences")
async def api_project_preferences_get(project: str = ""):
    if not project:
        raise HTTPException(400, "project required")
    prefs = _load_project_preferences(project)
    hub = _load_hub_defaults()
    # Effective = project override su hub
    effective = dict(hub)
    if prefs.get("default_provider"): effective["provider"] = prefs["default_provider"]
    if prefs.get("default_model"): effective["model"] = prefs["default_model"]
    if prefs.get("default_effort"): effective["effort"] = prefs["default_effort"]
    return JSONResponse({
        "project": project,
        "preferences": prefs,
        "hub_defaults": hub,
        "effective": effective,
    })


@app.post("/api/project/preferences")
async def api_project_preferences_post(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    project = body.get("project") or ""
    if not project:
        raise HTTPException(400, "project required")
    _require_ws_access(request, project)   # project è nel body → gate esplicito
    cur = _load_project_preferences(project)
    # Aggiorna solo i campi forniti; "" o null rimuove override
    for key in ("default_provider", "default_model", "default_effort", "auto_compact_pct"):
        if key in body:
            val = body[key]
            if val in (None, ""):
                cur.pop(key, None)
            else:
                cur[key] = val
    if not _save_project_preferences(project, cur):
        raise HTTPException(500, "save failed (project not found?)")
    return JSONResponse({"ok": True, "preferences": cur})


def _load_hub_defaults() -> dict:
    """Helper: legge i default LLM dal hub config. Usato come fallback ovunque."""
    if not HUB_PATH:
        return {"provider": "claude", "model": "sonnet", "effort": "off"}
    try:
        cfg = json.loads((HUB_PATH / "config.json").read_text(encoding="utf-8"))
        return {
            "provider": cfg.get("default_provider", "claude"),
            "model": cfg.get("default_model", "sonnet"),
            "effort": cfg.get("default_effort", "off"),
        }
    except Exception:
        return {"provider": "claude", "model": "sonnet", "effort": "off"}


@app.get("/api/settings/providers")
async def api_settings_providers_get(request: Request):
    if not HUB_PATH or not HUB_PATH.is_dir():
        raise HTTPException(500, "hub path not configured")
    _require_admin(request)
    secrets = _load_secrets_dict()
    items = []
    for p in PROVIDER_KEY_FIELDS:
        v = secrets.get(p["env"], "")
        items.append({
            "id": p["id"],
            "label": p["label"],
            "env": p["env"],
            "url": p["url"],
            "configured": bool(v),
            "preview": _mask_key(v),
        })
    return JSONResponse({"providers": items, "secrets_path": str(_secrets_path())})


@app.get("/api/settings/secrets")
async def api_settings_secrets_get(request: Request):
    """Lista tutti i secrets generici (escludendo i provider keys gestiti separatamente)."""
    if not HUB_PATH or not HUB_PATH.is_dir():
        raise HTTPException(500, "hub path not configured")
    _require_admin(request)
    secrets = _load_secrets_dict()
    provider_envs = {p["env"] for p in PROVIDER_KEY_FIELDS}
    items = []
    for k in sorted(secrets.keys()):
        if k in provider_envs:
            continue
        items.append({"key": k, "preview": _mask_key(secrets[k])})
    return JSONResponse({"secrets": items, "secrets_path": str(_secrets_path())})


@app.post("/api/settings/secrets")
async def api_settings_secrets_post(request: Request):
    """Body: {key: '<NAME>', value: '<VAL>'}. Empty value = delete."""
    if not HUB_PATH or not HUB_PATH.is_dir():
        raise HTTPException(500, "hub path not configured")
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON body")
    key = (body.get("key") or "").strip()
    value = body.get("value", "")
    if not key:
        raise HTTPException(400, "key required")
    if not re.match(r"^[A-Z][A-Z0-9_]*$", key):
        raise HTTPException(400, "key must match ^[A-Z][A-Z0-9_]*$ (uppercase, digits, underscore)")
    provider_envs = {p["env"] for p in PROVIDER_KEY_FIELDS}
    if key in provider_envs:
        raise HTTPException(400, f"'{key}' is managed in /api/settings/providers")
    secrets = _load_secrets_dict()
    if isinstance(value, str) and value.strip():
        secrets[key] = value.strip()
    else:
        secrets.pop(key, None)
    _save_secrets_dict(secrets)
    return JSONResponse({"status": "ok", "key": key})


@app.post("/api/settings/providers")
async def api_settings_providers_post(request: Request):
    """Body: {"<env_name>": "<key>"} — set/clear per env. Empty string = remove."""
    if not HUB_PATH or not HUB_PATH.is_dir():
        raise HTTPException(500, "hub path not configured")
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON body")

    valid_envs = {p["env"] for p in PROVIDER_KEY_FIELDS}
    secrets = _load_secrets_dict()
    changed = []
    for k, v in body.items():
        if k not in valid_envs:
            continue
        if not isinstance(v, str):
            continue
        v = v.strip()
        if v == "":
            if k in secrets:
                del secrets[k]
                changed.append(k)
        else:
            if secrets.get(k) != v:
                secrets[k] = v
                changed.append(k)
    _save_secrets_dict(secrets)
    return JSONResponse({"status": "ok", "changed": changed})


# ============================================================
# Realtime voice call (Fase 11 RT)
# ============================================================

REALTIME_DEFAULT_MODEL = "gpt-4o-realtime-preview-2024-12-17"
REALTIME_DEFAULT_VOICE = "alloy"

# Cache dispatch_map per Realtime tool calling (chiave: cwd string, TTL 5 min)
_REALTIME_TOOLS_CACHE: dict = {}
_REALTIME_TOOLS_TTL = 300


async def _build_realtime_tools(cwd: Path, scope_kind: str = "hub",
                                target_name: Optional[str] = None) -> tuple:
    """Discover MCP tools nel formato Realtime API (flat, niente wrap `function`).

    Riusa _build_litellm_tools da llm_router (cache per cwd, TTL 5 min).
    Returns (tools_realtime_format, dispatch_map).
    """
    cache_key = f"{cwd}|{scope_kind}|{target_name or ''}"
    now = time.time()
    cached = _REALTIME_TOOLS_CACHE.get(cache_key)
    if cached and (now - cached["ts"]) < _REALTIME_TOOLS_TTL:
        return cached["tools"], cached["dispatch_map"]

    try:
        from llm_router import _build_litellm_tools, build_subprocess_env
        from mcp_scoper import scope_mcps as _scope_mcps
    except Exception as e:
        print(f"[realtime] _build_realtime_tools import fail: {e}")
        return [], {}

    # Scoping: usa Tier 0 + Tier 1 (no Tier 2 keyword routing per Realtime — il prompt
    # è iniziale, non turn-based)
    try:
        scoped_servers, _ = _scope_mcps(
            hub_path=HUB_PATH, scope_kind=scope_kind, target_name=target_name,
            cwd=cwd, user_prompt="", active_mcps=[], agent_config=None,
        )
        allowed_patterns = [f"mcp__{s}__*" for s in scoped_servers]
    except Exception:
        allowed_patterns = None

    env = build_subprocess_env(cwd)
    litellm_tools, dispatch_map = await _build_litellm_tools(cwd, env, allowed_patterns)

    # Convert OpenAI chat-completions format → Realtime flat format
    realtime_tools = []
    for t in litellm_tools:
        fn = t.get("function") or {}
        realtime_tools.append({
            "type": "function",
            "name": fn.get("name"),
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
        })

    _REALTIME_TOOLS_CACHE[cache_key] = {
        "ts": now, "tools": realtime_tools, "dispatch_map": dispatch_map, "env": env, "cwd": cwd,
    }
    return realtime_tools, dispatch_map


def _realtime_instructions(conv_id: Optional[str]) -> str:
    """Genera system prompt per la voice call.

    Include:
    - Identity Anja + USER profile + memory triade (via ContextComposer)
    - Recent conversation history se conv_id fornito (ultimi N messaggi)
    - Voice mode directives (risposte brevi, no markdown)
    """
    chat = _get_chat_module()
    if not chat or not HUB_PATH:
        return "You are Anja, a helpful AI assistant. Speak in Italian by default."
    projects = _build_projects_context()
    try:
        sys = chat.build_system_prompt(
            HUB_PATH, projects, user_prompt="",
            image_gen_enabled=False,
            user_name=_realtime_user_name(), timezone=""
        )
    except Exception as e:
        print(f"[realtime] build_system_prompt error: {e}")
        sys = "You are Anja, a helpful AI assistant. Speak in Italian by default."

    # Catalogo MCP runtime (realtime: nessun keyword filter, scoped_servers=None
    # quindi tutti i server installati appaiono come "attivi")
    try:
        cap_block = chat.mcp_capabilities_block(HUB_PATH, None)
        if cap_block:
            sys += cap_block
    except Exception as e:
        print(f"[realtime] mcp_capabilities_block error: {e}")

    # Recent conversation history (Fase 11 RT-resume)
    if conv_id:
        history_block = _realtime_history_block(conv_id, max_messages=12, max_chars=4000)
        if history_block:
            sys += "\n\n" + history_block

    # Voice mode directives
    sys += (
        "\n\n# Voice mode\n"
        "You are now in REAL-TIME VOICE CALL with the user. Keep responses concise "
        "(1-3 sentences typically). Avoid bullet points, code blocks, markdown — "
        "this is spoken aloud. Use natural conversational Italian unless the user "
        "speaks English. If the user references things from the recent conversation "
        "history above, acknowledge that you remember the context."
    )
    return sys


def _realtime_history_block(conv_id: str, max_messages: int = 12, max_chars: int = 4000) -> str:
    """Recupera storia della conversation e produce un block formattato per il system prompt."""
    chat = _get_chat_module()
    if not chat:
        return ""
    existing = chat.load_conversation(WEBAPP_DIR, conv_id) or {}
    msgs = existing.get("messages", [])
    if not msgs:
        return ""
    # Prendi gli ultimi N, ma se ci sono summary system, includili
    last = msgs[-max_messages:] if len(msgs) > max_messages else msgs
    lines: list[str] = []
    char_count = 0
    for m in last:
        role = m.get("role", "?")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        label = {"user": "USER", "claude": "ANJA", "assistant": "ANJA", "system": "SYSTEM"}.get(role, role.upper())
        # Tronca singolo messaggio se super lungo
        if len(content) > 1200:
            content = content[:1200] + " […]"
        line = f"[{label}] {content}"
        if char_count + len(line) > max_chars:
            lines.insert(0, "[...older messages truncated...]")
            break
        lines.append(line)
        char_count += len(line)
    if not lines:
        return ""
    omitted = max(0, len(msgs) - len(last))
    header = f"# Recent conversation history ({len(last)} of {len(msgs)} messages"
    if omitted > 0:
        header += f", {omitted} earlier omitted"
    header += ")"
    return header + "\n\n" + "\n\n".join(lines)


def _realtime_user_name() -> str:
    if not HUB_PATH:
        return "user"
    try:
        cfg = json.loads((HUB_PATH / "config.json").read_text(encoding="utf-8"))
        return cfg.get("default_user", "user") or "user"
    except Exception:
        return "user"


def _xai_realtime_model(model: Optional[str]) -> str:
    """Risolve un model name valido per xAI realtime (default flagship grok-voice)."""
    if model and "grok" in model:
        return model
    return "grok-voice-latest"


async def _xai_realtime_session(model, voice, instructions, tools, conv_id):
    """xAI Realtime: minta un ephemeral token (POST /v1/realtime/client_secrets) e
    ritorna al browser i dati per aprire il WebSocket `wss://api.x.ai/v1/realtime`.

    Diverso da OpenAI (WebRTC, sessione pre-configurata server-side): xAI usa
    WebSocket, quindi instructions/tools/voice li invia il client via `session.update`
    all'apertura. Auth browser: subprotocol `xai-client-secret.<token>` (i browser
    non possono settare header su WebSocket).
    """
    api_key = os.environ.get("XAI_API_KEY")
    if not api_key:
        raise HTTPException(400, "XAI_API_KEY not configured. Add it via Settings → Custom Secrets.")
    model = _xai_realtime_model(model)

    import urllib.request as _ur
    import urllib.error as _ue
    url = "https://api.x.ai/v1/realtime/client_secrets"
    data = json.dumps({"expires_after": {"seconds": 600}}).encode("utf-8")
    req = _ur.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    try:
        with _ur.urlopen(req, timeout=15) as resp:
            sess = json.loads(resp.read().decode("utf-8"))
    except _ue.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode("utf-8"))
        except Exception:
            err_body = {"error": str(e)}
        raise HTTPException(502, f"xAI Realtime token create failed: {err_body}")
    except Exception as e:
        raise HTTPException(502, f"xAI Realtime: {type(e).__name__}: {e}")

    cs = sess.get("client_secret")
    if isinstance(cs, dict):
        token = cs.get("value")
    else:
        token = cs or sess.get("value") or sess.get("secret") or sess.get("token")
    if not token:
        raise HTTPException(502, f"No ephemeral token in xAI response: {str(sess)[:200]}")

    return JSONResponse({
        "provider": "xai",
        "client_secret": token,
        "ws_url": f"wss://api.x.ai/v1/realtime?model={model}",
        "model": model,
        "voice": voice,
        "instructions": instructions,
        "tools": tools or [],
        "conversation_id": conv_id,
    })


@app.post("/api/realtime/session")
async def api_realtime_session(request: Request):
    """Crea una ephemeral session Realtime (OpenAI WebRTC o xAI WebSocket) per il browser.

    Body opzionale: {conversation_id, voice, model, provider}. Default: hub audio.realtime config.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    audio_cfg = _load_audio_config().get("realtime", {})
    provider = (body.get("provider") or audio_cfg.get("provider") or "openai").lower()
    model = body.get("model") or audio_cfg.get("model") or REALTIME_DEFAULT_MODEL
    voice = body.get("voice") or audio_cfg.get("voice") or REALTIME_DEFAULT_VOICE
    conv_id = body.get("conversation_id")

    instructions = _realtime_instructions(conv_id)
    # MCP tools (Fase 11 RT-tools): Tier 0 + Tier 1 funzioni esposte ad Anja via Realtime
    tools_realtime, _dm = await _build_realtime_tools(HUB_PATH, scope_kind="hub", target_name=None)

    # xAI Realtime → WebSocket + ephemeral token (schema/transport diverso da OpenAI)
    if "xai" in provider:
        return await _xai_realtime_session(model, voice, instructions, tools_realtime, conv_id)

    # OpenAI Realtime → WebRTC + ephemeral session
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(400, "OPENAI_API_KEY not configured. Add it via Settings → Custom Secrets.")

    payload = {
        "model": model,
        "voice": voice,
        "modalities": ["audio", "text"],
        "instructions": instructions,
        "input_audio_format": "pcm16",
        "output_audio_format": "pcm16",
        "input_audio_transcription": {"model": "whisper-1"},
        "turn_detection": {
            "type": "server_vad",
            "threshold": 0.5,
            "prefix_padding_ms": 300,
            "silence_duration_ms": 500,
        },
    }
    if tools_realtime:
        payload["tools"] = tools_realtime
        payload["tool_choice"] = "auto"

    import urllib.request as _ur
    import urllib.error as _ue
    url = "https://api.openai.com/v1/realtime/sessions"
    data = json.dumps(payload).encode("utf-8")
    req = _ur.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("OpenAI-Beta", "realtime=v1")
    try:
        with _ur.urlopen(req, timeout=15) as resp:
            session = json.loads(resp.read().decode("utf-8"))
    except _ue.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode("utf-8"))
        except Exception:
            err_body = {"error": str(e)}
        raise HTTPException(502, f"OpenAI Realtime session create failed: {err_body}")
    except Exception as e:
        raise HTTPException(502, f"OpenAI Realtime: {type(e).__name__}: {e}")

    client_secret = ((session.get("client_secret") or {}).get("value")
                     or session.get("client_secret"))
    if not client_secret:
        raise HTTPException(502, f"No client_secret in OpenAI response: {session}")

    return JSONResponse({
        "client_secret": client_secret,
        "expires_at": (session.get("client_secret") or {}).get("expires_at"),
        "model": model,
        "voice": voice,
        "session_id": session.get("id"),
        "conversation_id": conv_id,
    })


@app.post("/api/realtime/tool-call")
async def api_realtime_tool_call(request: Request):
    """Esegue un tool MCP per conto del client Realtime.

    Body: {name: 'server__tool', arguments: '{...json...}'}.
    Returns: {result: '<output_text>'} oppure {error: '...'}.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    name = body.get("name") or ""
    args_raw = body.get("arguments")
    if not name:
        raise HTTPException(400, "missing 'name'")

    # Parse arguments JSON if string
    args = {}
    if isinstance(args_raw, str):
        try:
            args = json.loads(args_raw) if args_raw.strip() else {}
        except Exception as e:
            return JSONResponse({"error": f"invalid arguments JSON: {e}"}, status_code=200)
    elif isinstance(args_raw, dict):
        args = args_raw

    # Find dispatch entry in cache (search through all cached scopes — usually 1)
    dispatch_entry = None
    env = None
    for v in _REALTIME_TOOLS_CACHE.values():
        if name in v["dispatch_map"]:
            dispatch_entry = v["dispatch_map"][name]
            env = v["env"]
            break

    if not dispatch_entry:
        # Cache miss → rebuild (rare race condition con cache expiry)
        if HUB_PATH:
            _, dispatch_map = await _build_realtime_tools(HUB_PATH, "hub", None)
            if name in dispatch_map:
                dispatch_entry = dispatch_map[name]
                from llm_router import build_subprocess_env
                env = build_subprocess_env(HUB_PATH)

    if not dispatch_entry:
        return JSONResponse({"error": f"unknown tool '{name}'"}, status_code=200)

    server_name, server_cfg, tool_name = dispatch_entry
    try:
        from llm_router import _mcp_call_tool
        result_text = await _mcp_call_tool(server_name, server_cfg, tool_name, args, env or {})
        return JSONResponse({"result": result_text or ""})
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=200)


@app.post("/api/realtime/transcript")
async def api_realtime_transcript(request: Request):
    """Salva il transcript di una call nella conversation (chiamato dal client a hang-up)."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    conv_id = body.get("conversation_id") or f"voice-{int(time.time())}"
    transcript_items = body.get("transcript") or []  # [{role: 'user'|'assistant', content: '...'}]
    duration_sec = body.get("duration_sec", 0)
    voice = body.get("voice", REALTIME_DEFAULT_VOICE)

    chat = _get_chat_module()
    if not chat:
        raise HTTPException(503, "chat module unavailable")

    existing = chat.load_conversation(WEBAPP_DIR, conv_id) or {
        "id": conv_id, "title": "Voice call", "scope": "hub", "messages": []
    }
    # Marker inizio call
    header = f"[📞 VOICE CALL · {datetime.now().strftime('%Y-%m-%d %H:%M')} · voice={voice} · {duration_sec}s]"
    new_messages = list(existing.get("messages", []))
    new_messages.append({"role": "system", "content": header})
    for item in transcript_items:
        role = "user" if item.get("role") == "user" else "claude"
        content = (item.get("content") or "").strip()
        if content:
            new_messages.append({"role": role, "content": content})
    chat.save_conversation(
        WEBAPP_DIR, conv_id, new_messages,
        title=existing.get("title") or "Voice call",
        scope=existing.get("scope", "hub"),
        provider=existing.get("provider", "openai"),
        model=existing.get("model", REALTIME_DEFAULT_MODEL),
        effort=existing.get("effort", ""),
    )
    # P1 — Mirror immediato post-realtime call (force)
    try:
        from session_mirror import mirror_from_file
        if HUB_PATH:
            mirror_from_file(conv_id, WEBAPP_DIR, HUB_PATH,
                            projects=_build_projects_context(), force=True)
    except Exception as e:
        print(f"[realtime] session_mirror error: {e}")
    return JSONResponse({"ok": True, "conversation_id": conv_id, "messages_added": len(transcript_items)})


# ============================================================
# Compact conversation (Fase 11)
# ============================================================

COMPACT_SYSTEM_PROMPT = """Sei un compattatore di conversation. Dato lo storico di una chat,
produci un riassunto strutturato (300-500 parole MAX) che preservi:

1. Decisioni prese
2. Fatti rilevanti emersi (nomi, numeri, scelte)
3. Stato corrente / cosa stiamo facendo
4. Pendenze / TODO espliciti

Tono: telegrafico, bullet points, no preamboli. Italiano.

Output: solo il riassunto, niente meta-commento."""


async def compact_conversation(conv_id: str, keep_last_n: int = 2) -> dict:
    """Compatta una conversation: riassume i messaggi precedenti, mantiene ultimi N turni
    intatti, resetta sdk_session_id (fa partire una nuova SDK session col summary come
    primo turno di context).
    """
    chat = _get_chat_module()
    if not chat:
        return {"ok": False, "error": "chat module unavailable"}
    existing = chat.load_conversation(WEBAPP_DIR, conv_id)
    if not existing:
        return {"ok": False, "error": f"conversation '{conv_id}' not found"}

    messages = existing.get("messages", [])
    # keep_last_n è in TURNI (1 turn = user + assistant = 2 messages)
    keep_msgs = keep_last_n * 2
    if len(messages) <= keep_msgs:
        return {"ok": False, "error": f"only {len(messages)} messages (need >{keep_msgs} for compact with keep_last_n={keep_last_n})"}

    cutoff = len(messages) - keep_msgs
    historical = messages[:cutoff]
    kept = messages[cutoff:]
    if not historical:
        return {"ok": False, "error": "no historical messages to summarize after applying keep_last_n"}

    transcript_lines = []
    for m in historical:
        role = m.get("role", "?")
        content = m.get("content", "")
        if not content:
            continue
        transcript_lines.append(f"[{role}]\n{content}\n")
    transcript = "\n---\n".join(transcript_lines)

    # Invoke Claude haiku per il riassunto (fast + cheap)
    summary_text = ""
    try:
        from claude_agent_sdk import ClaudeAgentOptions, query
        prompt = f"Storico conversation da riassumere:\n\n{transcript}"
        options = ClaudeAgentOptions(
            system_prompt=COMPACT_SYSTEM_PROMPT,
            model="haiku",
            permission_mode="bypassPermissions",
            allowed_tools=[],  # solo testo, no tool
        )
        async for message in query(prompt=prompt, options=options):
            content = getattr(message, "content", None)
            if content:
                if isinstance(content, str):
                    summary_text += content
                else:
                    for block in content:
                        text = getattr(block, "text", None)
                        if text:
                            summary_text += text
    except Exception as e:
        return {"ok": False, "error": f"summary generation failed: {type(e).__name__}: {e}"}

    summary_text = summary_text.strip()
    if not summary_text:
        return {"ok": False, "error": "empty summary"}

    # FIX 2026-05-11: il summary va come campo separato `compact_summary` che viene
    # iniettato nel system_prompt dei messaggi successivi (Claude SDK è stateful via
    # session_id, non via messages array — non leggerebbe mai il system msg qui).
    new_messages = list(kept)  # solo ultimi N turni come "real" history per UI

    payload = dict(existing)
    payload["messages"] = new_messages
    payload["sdk_session_id"] = ""  # reset → nuova SDK session
    payload["compact_summary"] = summary_text
    payload["compacted_at"] = time.time()
    payload["compacted_from_count"] = len(messages)

    conv_path = WEBAPP_DIR / "conversations" / f"{conv_id}.json"
    conv_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # P1 — Mirror immediato post-compact (force=True per bypass rate limit)
    try:
        from session_mirror import mirror_from_file
        if HUB_PATH:
            mirror_from_file(conv_id, WEBAPP_DIR, HUB_PATH,
                            projects=_build_projects_context(), force=True)
    except Exception as e:
        print(f"[compact] session_mirror error: {e}")

    # Fase 14 — Dialectic pass async fire-and-forget (post-compact hook)
    try:
        if HUB_PATH:
            scope = existing.get("scope", "hub")
            project_path = None
            if scope.startswith("project:"):
                pname = scope.split(":", 1)[1].strip()
                for p in _build_projects_context():
                    if p.get("name") == pname:
                        loc = p.get("location") or {}
                        if loc.get("kind") == "local" and loc.get("path"):
                            project_path = Path(loc["path"]).resolve()
                        break
            import dialectic_pass as _dp
            asyncio.create_task(_dp.run_dialectic_pass(
                conv_id=conv_id,
                scope=scope,
                conversations_dir=WEBAPP_DIR / "conversations",
                hub_path=HUB_PATH,
                project_path=project_path,
            ))
            print(f"[compact] dialectic pass scheduled (scope={scope})")
    except Exception as e:
        print(f"[compact] dialectic schedule error: {e}")

    return {
        "ok": True,
        "summary": summary_text,
        "messages_before": len(messages),
        "messages_after": len(new_messages),
        "kept_last": len(kept),
    }


@app.post("/api/conversations/{conv_id}/compact")
async def api_conversations_compact(conv_id: str, request: Request):
    keep_last = 2
    try:
        body = await request.json()
        keep_last = int(body.get("keep_last_n", 2))
    except Exception:
        pass
    result = await compact_conversation(conv_id, keep_last_n=keep_last)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "compact failed"))
    return JSONResponse(result)


# ============================================================
# Dialectic Memory endpoints (Fase 14)
# ============================================================

def _resolve_dialectic_paths(scope: str, slug: Optional[str] = None) -> dict:
    """Risolve dialectic_file + user_md_file per uno scope."""
    if not HUB_PATH:
        return {}
    if not slug:
        try:
            cfg = json.loads((HUB_PATH / "config.json").read_text(encoding="utf-8"))
            slug = cfg.get("default_user") or "user"
        except Exception:
            slug = "user"
    slug = _safe_user_slug(slug)   # path-safe: usato in <...>/users/<slug>[-dialectic].md
    if scope and scope.startswith("project:"):
        pname = scope.split(":", 1)[1].strip()
        for p in _build_projects_context():
            if p.get("name") == pname:
                loc = p.get("location") or {}
                if loc.get("kind") == "local" and loc.get("path"):
                    proot = Path(loc["path"]).resolve()
                    return {
                        "slug": slug,
                        "scope": scope,
                        "dialectic": proot / ".anjawiki" / "users" / f"{slug}-dialectic.md",
                        "user_md": proot / ".anjawiki" / "users" / f"{slug}.md",
                        "project_path": proot,
                    }
        return {}
    return {
        "slug": slug,
        "scope": "hub",
        "dialectic": HUB_PATH / "users" / f"{slug}-dialectic.md",
        "user_md": HUB_PATH / "users" / f"{slug}.md",
        "project_path": None,
    }


@app.get("/api/dialectic")
async def api_dialectic_read(scope: str = "hub", slug: str = ""):
    """Ritorna observations Active + Promoted + Decayed + NeverPromote per scope+slug."""
    try:
        import dialectic_io as dio
    except Exception:
        raise HTTPException(500, "dialectic_io not available")
    paths = _resolve_dialectic_paths(scope, slug or None)
    if not paths:
        raise HTTPException(404, "scope not resolved")
    dp = paths["dialectic"]
    if not dp.is_file():
        return JSONResponse({
            "slug": paths["slug"], "scope": paths["scope"],
            "active": [], "promoted": [], "decayed": [], "never_promote": [],
            "file": str(dp), "exists": False,
        })
    data = dio.read_dialectic(dp)
    data["file"] = str(dp)
    data["exists"] = True
    data["slug"] = paths["slug"]
    data["scope"] = paths["scope"]
    return JSONResponse(data)


@app.post("/api/dialectic/promote")
async def api_dialectic_promote(payload: dict = Body(...)):
    """Forza promozione di una observation singola."""
    text = (payload.get("text") or "").strip()
    scope = payload.get("scope") or "hub"
    slug = payload.get("slug") or ""
    if not text:
        raise HTTPException(400, "text required")
    paths = _resolve_dialectic_paths(scope, slug or None)
    if not paths:
        raise HTTPException(404, "scope not resolved")
    try:
        from promotion_distill import promote_observation
    except Exception:
        raise HTTPException(500, "promotion_distill not available")
    result = promote_observation(paths["dialectic"], paths["user_md"], text, slug=paths["slug"])
    return JSONResponse(result)


@app.post("/api/dialectic/revert")
async def api_dialectic_revert(payload: dict = Body(...)):
    """Soft delete: rimuove da USER.md + aggiunge a Never-promote."""
    text = (payload.get("text") or "").strip()
    scope = payload.get("scope") or "hub"
    slug = payload.get("slug") or ""
    if not text:
        raise HTTPException(400, "text required")
    paths = _resolve_dialectic_paths(scope, slug or None)
    if not paths:
        raise HTTPException(404, "scope not resolved")
    try:
        import dialectic_io as dio
    except Exception:
        raise HTTPException(500, "dialectic_io not available")
    ok = dio.revert_promoted(paths["dialectic"], text, user_md_path=paths["user_md"])
    return JSONResponse({"ok": ok, "text": text})


@app.post("/api/dialectic/never-promote")
async def api_dialectic_never(payload: dict = Body(...)):
    """Aggiunge text alla Never-promote list (anti-pattern)."""
    text = (payload.get("text") or "").strip()
    scope = payload.get("scope") or "hub"
    slug = payload.get("slug") or ""
    if not text:
        raise HTTPException(400, "text required")
    paths = _resolve_dialectic_paths(scope, slug or None)
    if not paths:
        raise HTTPException(404, "scope not resolved")
    try:
        import dialectic_io as dio
    except Exception:
        raise HTTPException(500, "dialectic_io not available")
    dio.add_to_never_promote(paths["dialectic"], text, slug=paths["slug"], scope=paths["scope"])
    return JSONResponse({"ok": True, "text": text})


@app.post("/api/dialectic/restore")
async def api_dialectic_restore(payload: dict = Body(...)):
    """Riporta una observation decayed in active."""
    text = (payload.get("text") or "").strip()
    scope = payload.get("scope") or "hub"
    slug = payload.get("slug") or ""
    if not text:
        raise HTTPException(400, "text required")
    paths = _resolve_dialectic_paths(scope, slug or None)
    if not paths:
        raise HTTPException(404, "scope not resolved")
    try:
        import dialectic_io as dio
    except Exception:
        raise HTTPException(500, "dialectic_io not available")
    ok = dio.restore_decayed(paths["dialectic"], text)
    return JSONResponse({"ok": ok, "text": text})


@app.post("/api/dialectic/run")
async def api_dialectic_run(payload: dict = Body(...)):
    """Trigger manuale dialectic pass su una conversation (debug/force)."""
    conv_id = (payload.get("conv_id") or "").strip()
    scope = (payload.get("scope") or "hub").strip()
    slug = payload.get("slug") or None
    if not conv_id:
        raise HTTPException(400, "conv_id required")
    paths = _resolve_dialectic_paths(scope, slug)
    try:
        import dialectic_pass as _dp
        report = await _dp.run_dialectic_pass(
            conv_id=conv_id,
            scope=scope,
            conversations_dir=WEBAPP_DIR / "conversations",
            hub_path=HUB_PATH,
            project_path=paths.get("project_path") if paths else None,
            user_slug=paths.get("slug") if paths else slug,
        )
        return JSONResponse(report)
    except Exception as e:
        raise HTTPException(500, f"dialectic run failed: {type(e).__name__}: {e}")


@app.post("/api/dialectic/distill")
async def api_dialectic_distill(payload: dict = Body(...)):
    """Distillation pass: promuove tutte le candidates che soddisfano i criteri."""
    scope = payload.get("scope") or "hub"
    slug = payload.get("slug") or ""
    use_llm = bool(payload.get("use_llm_judge", False))
    paths = _resolve_dialectic_paths(scope, slug or None)
    if not paths:
        raise HTTPException(404, "scope not resolved")
    try:
        from promotion_distill import distill_promotions
        report = await distill_promotions(
            paths["dialectic"], paths["user_md"],
            use_llm_judge=use_llm, slug=paths["slug"],
        )
        return JSONResponse(report)
    except Exception as e:
        raise HTTPException(500, f"distill failed: {type(e).__name__}: {e}")


# ============================================================
# Telegram inbound (Fase 11 M-Tg)
# ============================================================

# =================================================================
# F-GoalCodingWorker — coding run (MVP: ingresso chat/REST)
# Vedi anja-coding-worker-design.md. Riusa coding_worker (5 fasi) + checkpoint.
# =================================================================

CODING_RUNS: dict = {}  # run_id → result dict (running + done in-memory; persistito su disco)


def _coding_runs_dir() -> Path:
    d = HUB_PATH / "coding_runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _run_coding_bg(run_id: str, spec: dict):
    import coding_worker
    CODING_RUNS[run_id] = {"run_id": run_id, "workspace": spec.get("workspace"),
                           "status": "running", "task": spec.get("task", {}).get("title", "")}
    try:
        data = (await coding_worker.run(HUB_PATH, spec)).to_dict()
    except Exception as e:
        data = {"run_id": run_id, "workspace": spec.get("workspace"),
                "status": "engine-error", "error": f"{type(e).__name__}: {e}"}
    CODING_RUNS[run_id] = data
    try:
        (_coding_runs_dir() / f"{run_id}.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    try:  # notifica (always-approve): Activity/Bell con azione review
        import notification_bus as _nb
        st = data.get("status")
        cat = "success" if st == "verified" else ("warn" if st == "verify-failed" else "error")
        _nb.publish(HUB_PATH, source="coding-worker", category=cat,
                    title=f"Coding run {st}: {spec.get('workspace')}",
                    body=(data.get("summary") or data.get("error") or "")[:300],
                    action={"label": "Review diff", "url": f"/#coding/{run_id}", "type": "navigate"},
                    payload={"run_id": run_id, "workspace": spec.get("workspace")})
    except Exception:
        pass
    try:  # gate Telegram (always-approve): bottoni Approve/Reject → cact:<verdict>:<run_id>
        import telegram_action_notifier as _tan
        await _tan.notify_coding_run(HUB_PATH, data)
    except Exception:
        pass


@app.post("/api/coding/run")
async def api_coding_run(request: Request, payload: dict = Body(...)):
    import asyncio as _aio
    import coding_worker
    workspace = (payload.get("workspace") or "").strip()
    task = payload.get("task") or {}
    if not workspace or not task.get("title"):
        raise HTTPException(400, "workspace + task.title required")
    # workspace arriva dal BODY → il middleware membership non lo vede: gate esplicito.
    # PRIMA del check di esistenza → un member non-autorizzato riceve 403, non un 404
    # che rivelerebbe quali workspace esistono (existence oracle).
    _require_ws_access(request, workspace)
    if not coding_worker.resolve_workspace_dir(HUB_PATH, workspace):
        raise HTTPException(404, f"workspace not found: {workspace}")
    caps = payload.get("capabilities") or {"tools": ["Read", "Write", "Edit", "Bash"]}
    # backend 'local' = NESSUN container: `sandbox` abiliterebbe --dangerously-skip-permissions
    # sull'host. Mai fidarsi del client su questo → forzato off (lo skip è ammesso solo
    # nel backend containerizzato V2, dove l'isolamento è il container).
    if isinstance(caps, dict):
        caps.pop("sandbox", None)
    run_id = coding_worker._gen_run_id()
    spec = {
        "run_id": run_id, "workspace": workspace, "task": task,
        "engine": payload.get("engine", "claude"), "backend": "local",
        "capabilities": caps,
        "budget": payload.get("budget") or {"max_turns": 40, "timeout_sec": 1800},
        "policy": payload.get("policy", "always-approve"),
    }
    _aio.create_task(_run_coding_bg(run_id, spec))
    return JSONResponse({"run_id": run_id, "status": "started", "workspace": workspace})


@app.get("/api/coding/runs")
async def api_coding_runs(workspace: str = ""):
    runs = dict(CODING_RUNS)
    d = HUB_PATH / "coding_runs"
    if d.is_dir():
        for f in d.glob("*.json"):  # file = source of truth (sovrascrive l'in-memory stale)
            try:
                runs[f.stem] = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                pass
    out = [v for v in runs.values() if not workspace or v.get("workspace") == workspace]
    out.sort(key=lambda r: r.get("run_id", ""), reverse=True)
    return JSONResponse({"runs": out})


def _coding_resolve(run_id: str, verdict: str) -> JSONResponse:
    """Gate REST (approve|reject). coding_worker.resolve aggiorna il record JSON
    (source of truth); riallineiamo la cache in-memory CODING_RUNS."""
    import coding_worker
    result = coding_worker.resolve(HUB_PATH, run_id, verdict)
    if result.get("ok"):
        fresh = coding_worker.load_run(HUB_PATH, run_id)
        if fresh:
            CODING_RUNS[run_id] = fresh
    elif "non trovato" in (result.get("error") or ""):
        raise HTTPException(404, result["error"])
    return JSONResponse(result)


@app.post("/api/coding/runs/{run_id}/approve")
async def api_coding_approve(run_id: str):
    return _coding_resolve(run_id, "approve")


@app.post("/api/coding/runs/{run_id}/reject")
async def api_coding_reject(run_id: str):
    return _coding_resolve(run_id, "reject")


# =================================================================
# F-Webhook — trigger HTTP inbound (event-driven), bearer auth.
# Canale per eventi esterni: wake del proactive engine, dispatch a un agente (queue),
# signal generico (→ notification_bus / goal signals.jsonl). Ponte per la V2 (Swebify:
# container→hub via /hooks/signal). Token in <hub>/.secrets.env: ANJA_WEBHOOK_TOKEN
# (assente → endpoint 503, disabilitati by default).
# =================================================================

def _webhook_token() -> Optional[str]:
    return os.environ.get("ANJA_WEBHOOK_TOKEN") or None


def _check_webhook_auth(request: Request) -> None:
    import hmac
    token = _webhook_token()
    if not token:
        raise HTTPException(503, "webhook disabled: imposta ANJA_WEBHOOK_TOKEN in <hub>/.secrets.env")
    auth = request.headers.get("Authorization", "")
    presented = auth[7:].strip() if auth.startswith("Bearer ") else ""
    if not presented or not hmac.compare_digest(presented, token):
        raise HTTPException(401, "bearer token missing or invalid")


@app.post("/hooks/wake")
async def hooks_wake(request: Request, payload: dict = Body(default={})):
    """Forza un tick on-demand del proactive engine (invece di aspettare il cron)."""
    _check_webhook_auth(request)
    triggered = []
    if KANBAN_DISPATCHER:
        try:
            await KANBAN_DISPATCHER._tick()
            triggered.append("kanban_dispatcher")
        except Exception as e:
            print(f"[webhook] kanban tick: {e}", flush=True)
    try:
        from script_runtime import supervisor_tick
        await supervisor_tick(HUB_PATH)
        triggered.append("script_supervisor")
    except Exception as e:
        print(f"[webhook] supervisor tick: {e}", flush=True)
    try:
        import notification_bus as _nb
        _nb.publish(HUB_PATH, source="webhook", category="info",
                    title="Wake trigger", body=str(payload.get("reason", "external wake"))[:200])
    except Exception:
        pass
    return JSONResponse({"ok": True, "triggered": triggered})


@app.post("/hooks/agent")
async def hooks_agent(request: Request, payload: dict = Body(...)):
    """Dispatch event-driven a un agente: enqueue come task kanban `ready` (il dispatcher
    lo processa) + wake immediato. Queue, non sincrono → disaccoppiato e durevole."""
    _check_webhook_auth(request)
    prompt = (payload.get("prompt") or payload.get("text") or "").strip()
    if not prompt:
        raise HTTPException(400, "prompt required")
    kio = _kanban_io()
    if not kio:
        raise HTTPException(500, "kanban not available")
    task = kio.create_task(
        HUB_PATH, title=prompt[:120], body=prompt, status="ready",
        assignee=(payload.get("agent") or ""), scope=(payload.get("scope") or "hub"),
        metadata={"source": "webhook", "via": "hooks/agent"})
    if KANBAN_DISPATCHER:
        try:
            await KANBAN_DISPATCHER._tick()
        except Exception:
            pass
    return JSONResponse({"ok": True, "task_id": task.get("id"), "status": "queued"})


@app.post("/hooks/signal")
async def hooks_signal(request: Request, payload: dict = Body(...)):
    """Signal generico inbound → notification_bus (Activity/Bell) e, se `goal_id`, anche
    nella signals.jsonl del goal (canale V2: container→hub)."""
    _check_webhook_auth(request)
    category = payload.get("category", "info")
    if category not in ("info", "success", "warn", "error"):
        category = "info"
    published = False
    try:
        import notification_bus as _nb
        _nb.publish(HUB_PATH, source=(payload.get("source") or "webhook"), category=category,
                    title=(payload.get("title") or "signal")[:120],
                    body=str(payload.get("body") or "")[:500],
                    payload=payload.get("payload") or {})
        published = True
    except Exception as e:
        print(f"[webhook] signal publish: {e}", flush=True)
    goal_signaled = False
    goal_id = payload.get("goal_id")
    if goal_id:
        try:
            from datetime import datetime, timezone
            import script_runtime
            sf = script_runtime.signal_file_path(HUB_PATH, payload.get("scope", "hub"), goal_id)
            sf.parent.mkdir(parents=True, exist_ok=True)
            with sf.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"event_type": payload.get("event_type", "webhook"),
                                    "payload": payload.get("payload") or {},
                                    "ts": datetime.now(timezone.utc).isoformat()}) + "\n")
            goal_signaled = True
        except Exception as e:
            print(f"[webhook] goal signal: {e}", flush=True)
    return JSONResponse({"ok": True, "published": published, "goal_signaled": goal_signaled})


TELEGRAM_DAEMON = None  # type: ignore  # Optional[TelegramDaemon]

# Lock per chat_id (Fase 11 fix Bug B race condition concurrent dispatch)
_TELEGRAM_DISPATCH_LOCKS: dict = {}
# Lock per conv_id (F-TelegramAsyncNotify): serializza le scritture sullo STESSO
# thread, ma lascia liberi i thread diversi → abilita il dispatch async in background.
_TELEGRAM_CONV_LOCKS: dict = {}

def _telegram_chat_lock(chat_id: int):
    import asyncio as _asyncio
    if chat_id not in _TELEGRAM_DISPATCH_LOCKS:
        _TELEGRAM_DISPATCH_LOCKS[chat_id] = _asyncio.Lock()
    return _TELEGRAM_DISPATCH_LOCKS[chat_id]

def _tg_set_conv_asp_mode(conv_id: str, mode: str) -> None:
    """Persiste il permission mode sticky nella conversation (read-modify-write
    mirato: save_conversation ricostruisce il payload e perderebbe i metadata)."""
    path = WEBAPP_DIR / "conversations" / f"{conv_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() \
            else {"id": conv_id, "messages": []}
    except Exception:
        data = {"id": conv_id, "messages": []}
    data["asp_mode"] = mode
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8")


# F-TelegramModeDefault: /mode imposta anche il default del CANALE (per chat_id)
# — i nuovi thread lo ereditano, invece di ripartire ogni volta da "default".

def _tg_prefs_path() -> Path:
    return WEBAPP_DIR / "conversations" / ".telegram_prefs.json"


def _tg_default_asp_mode(chat_id: int) -> str:
    """Permission mode di default del canale Telegram ('' se mai impostato)."""
    try:
        prefs = json.loads(_tg_prefs_path().read_text(encoding="utf-8"))
        return (prefs.get(str(chat_id)) or {}).get("asp_mode", "")
    except Exception:
        return ""


def _tg_set_default_asp_mode(chat_id: int, mode: str) -> None:
    p = _tg_prefs_path()
    try:
        prefs = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except Exception:
        prefs = {}
    prefs.setdefault(str(chat_id), {})["asp_mode"] = mode
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8")


def _telegram_conv_lock(conv_id: str):
    import asyncio as _asyncio
    if conv_id not in _TELEGRAM_CONV_LOCKS:
        _TELEGRAM_CONV_LOCKS[conv_id] = _asyncio.Lock()
    return _TELEGRAM_CONV_LOCKS[conv_id]


# F-TelegramMultiSession — multi-thread per chat_id.
# Ogni thread è una conversation `telegram-{chat_id}-tN` (il legacy `telegram-{chat_id}`
# è il thread "main"). Lo stato "thread attivo" vive in conversations/.telegram_threads.json
# = {chat_id: conv_id}; chiave assente → main (backward compat totale).

def _tg_threads_state_path() -> Path:
    return WEBAPP_DIR / "conversations" / ".telegram_threads.json"


def _tg_load_threads_state() -> dict:
    p = _tg_threads_state_path()
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _tg_set_active_thread(chat_id: int, conv_id: str) -> None:
    state = _tg_load_threads_state()
    state[str(chat_id)] = conv_id
    p = _tg_threads_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _tg_active_conv(chat_id: int) -> str:
    return _tg_load_threads_state().get(str(chat_id)) or f"telegram-{chat_id}"


def _tg_list_threads(chat_id: int) -> list:
    """Thread esistenti di un chat_id, ordinati per attività recente.

    Regex esatta (no glob): chat_id negativi dei gruppi renderebbero ambiguo un prefix-match.
    suffix: "main" per il legacy telegram-{chat_id}, altrimenti "tN".
    """
    conv_dir = WEBAPP_DIR / "conversations"
    pat = re.compile(rf"^telegram-{re.escape(str(chat_id))}(-t\d+)?\.json$")
    out = []
    if conv_dir.is_dir():
        for f in conv_dir.iterdir():
            m = pat.match(f.name)
            if not m:
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            out.append({
                "conv_id": f.stem,
                "suffix": (m.group(1) or "").lstrip("-") or "main",
                "title": (data.get("title") or "").strip(),
                "scope_agent": data.get("scope_agent") or "",
                "n_msgs": len(data.get("messages", [])),
                "mtime": f.stat().st_mtime,
            })
    out.sort(key=lambda t: t["mtime"], reverse=True)
    return out


def _tg_new_thread_conv(chat_id: int) -> str:
    """Primo conv_id libero telegram-{chat_id}-tN (N≥2; t1 implicito = main legacy)."""
    taken = {t["suffix"] for t in _tg_list_threads(chat_id)}
    n = 2
    while f"t{n}" in taken:
        n += 1
    return f"telegram-{chat_id}-t{n}"


TELEGRAM_HELP_TEXT = """*Anja commands via Telegram*

`/help` — this message
`/status` — current model, provider, agent/project
`/model <name>` — change model (e.g. `/model opus`)
`/provider <name>` — change provider (claude/openai/xai/openrouter/...)
`/agent <name>` — switch to a specialized agent (e.g. `/agent trader`)
`/project <name>` — switch to a registered project's context
`/queue <when> <prompt>` — schedule a task (e.g. `/queue tomorrow 9am summarize yesterday's work`)
`/threads` — list this chat's threads, switch via buttons
`/newchat` — new thread (previous ones are kept)
`/async <msg>` — Anja works in the background and pings you when done (the chat stays free)
`/compact` — compact history into a summary, free up context
`/autocompact [on|off|<pct>]` — smart auto-compact when ctx ≥ N% (default 55%)
`/voice on|off|auto` — Anja also replies with a voice message (auto: only if you send audio)
`/retry` — retry the last interrupted or failed turn on this thread
`/stop` — interrupt the current turn (the conversation stays alive)
`/allow [always]` · `/deny` — answer a permission request (🔐) from the turn
`/approve` · `/replan <note>` — answer a proposed plan (plan mode)
`/merge` · `/discard` — close the git-session (📝): merge the diff into the branch or discard it
`/mode default|acceptEdits|plan|auto` — session permissions (auto = allow everything)

Audio: send a voice note → Anja transcribes it with Whisper and replies.
No command: Anja replies normally.
While Anja is working: a text message is injected into the current turn (steering).
"""


async def _telegram_handle_command(chat: Any, conv_id: str, chat_id: int,
                                    cmd: str, args: str, token: str):
    """Gestisce slash commands Telegram.

    Returns:
      True  → comando gestito, no Anja invoke
      str   → override del prompt (continua dispatch ad Anja col nuovo text)
      False → comando sconosciuto (al caller di rispondere)
    """
    from telegram_daemon import send_message as _tg_send

    existing = chat.load_conversation(WEBAPP_DIR, conv_id) or {}

    if cmd == "/stop":
        # F-AgentSessions Fase 1: interrupt della sessione ASP; fallback al
        # cancel duro del task registry per il path non-ASP.
        stopped = False
        if os.environ.get("ANJA_ASP_ENABLED") == "1":
            try:
                import claude_session
                stopped = await claude_session.pool.interrupt(conv_id)
            except Exception as e:
                print(f"[asp] tg /stop error: {e}")
        if not stopped:
            stopped = chat_streams.cancel(conv_id)
        await _tg_send(token, chat_id,
                       "⏹ _Turn interrupted._" if stopped
                       else "_No turn in progress on this thread._")
        return True

    if cmd in ("/merge", "/discard"):
        # F-AgentSessions Fase 4: chiusura git-sessione dal thread attivo
        if os.environ.get("ANJA_ASP_GIT") != "1":
            await _tg_send(token, chat_id, "_Git-session not active._")
            return True
        import asp_git as _ag
        ctx = _ag.get_ctx(conv_id)
        if ctx is None:
            await _tg_send(token, chat_id, "_No git-session on this thread._")
            return True
        if cmd == "/merge":
            res = await asyncio.to_thread(_ag.merge, ctx)
            msg = (f"✅ _Merged into_ `{res.get('into')}` (`{res.get('merged_commit')}`)"
                   if res.get("ok") else f"⚠ {res.get('error')}")
        else:
            res = await asyncio.to_thread(_ag.discard, ctx)
            msg = ("🗑 _Session branch discarded._" if res.get("ok")
                   else f"⚠ {res.get('error')}")
        import asp_permissions as _ap
        _ap.record_decision(tool="session.merge", target=ctx.get("branch", "?"),
                            decision=f"{cmd[1:]}:{'ok' if res.get('ok') else 'fail'}",
                            by=f"telegram:{chat_id}", scope="", conv_id=conv_id)
        await _tg_send(token, chat_id, msg)
        return True

    if cmd in ("/approve", "/replan"):
        # F-AgentSessions Fase 3: risposta a un piano proposto (plan mode)
        if os.environ.get("ANJA_ASP_PERMISSIONS") != "1":
            await _tg_send(token, chat_id, "_Control-plane not active._")
            return True
        import asp_permissions as _ap
        rid = _ap.pending.latest_for_conv(conv_id)
        if not rid:
            await _tg_send(token, chat_id, "_No plan pending._")
            return True
        decision = "approve" if cmd == "/approve" else "deny"
        meta = _ap.pending.resolve(rid, decision, message=args.strip(),
                                   by=f"telegram:{chat_id}")
        if meta is None:
            await _tg_send(token, chat_id, "_Already resolved._")
            return True
        await _tg_send(token, chat_id,
                       "✅ _Plan approved, proceeding._" if decision == "approve"
                       else "🔄 _Revising the plan with your feedback._")
        return True

    if cmd == "/mode":
        # F-AgentSessions: permission_mode STICKY per-conversazione (come la
        # UI): persiste su disco e vale a ogni (ri)creazione della sessione.
        # `auto` = bypassPermissions (chat TG in allowlist = canale fidato);
        # il passaggio da/verso bypass richiede il respawn (vincolo SDK) →
        # se il set live fallisce, il riciclo col resume avviene al turno dopo.
        mode = args.strip()
        if mode not in ("default", "acceptEdits", "plan", "auto"):
            await _tg_send(token, chat_id, "Usage: `/mode default|acceptEdits|plan|auto`")
            return True
        if os.environ.get("ANJA_ASP_PERMISSIONS") != "1":
            await _tg_send(token, chat_id, "_Control-plane not active._")
            return True
        if mode == "auto":
            import asp_permissions as _ap
            _ap.record_decision(tool="session.set", target=conv_id,
                                decision="mode-auto", by=f"telegram:{chat_id}",
                                scope="", conv_id=conv_id)
        _tg_set_conv_asp_mode(conv_id, mode)
        _tg_set_default_asp_mode(chat_id, mode)   # default canale: i nuovi thread lo ereditano
        import claude_session
        target = "bypassPermissions" if mode == "auto" else mode
        try:
            res = await claude_session.pool.set(conv_id, permission_mode=target)
        except Exception:
            res = {"ok": False}
        await _tg_send(token, chat_id,
                       (f"🔧 _Mode → `{mode}` — applied immediately._"
                        if res.get("ok")
                        else f"🔧 _Mode `{mode}` — takes effect from the next message (context preserved)._")
                       + "\n_Saved as Telegram default: applies to new threads too._")
        return True

    if cmd in ("/allow", "/deny"):
        # F-AgentSessions Fase 2: risolve la richiesta di permesso in attesa
        # sul thread attivo. La chat Telegram è in allowlist = canale fidato.
        if os.environ.get("ANJA_ASP_PERMISSIONS") != "1":
            await _tg_send(token, chat_id, "_Permissions control-plane not active._")
            return True
        import asp_permissions as _ap
        rid = _ap.pending.latest_for_conv(conv_id)
        if not rid:
            await _tg_send(token, chat_id, "_No permission request pending._")
            return True
        if cmd == "/allow":
            decision = ("always_allow"
                        if args.strip().lower().startswith("always") else "allow")
        else:
            decision = "deny"
        meta = _ap.pending.resolve(rid, decision, by=f"telegram:{chat_id}")
        if meta is None:
            await _tg_send(token, chat_id, "_Request already resolved._")
            return True
        lbl = {"allow": "✅ allowed", "always_allow": "✅ allowed (always)",
               "deny": "🚫 denied"}[decision]
        await _tg_send(token, chat_id, f"{lbl} — `{meta['tool']}`")
        return True

    if cmd == "/help":
        await _tg_send(token, chat_id, TELEGRAM_HELP_TEXT)
        return True

    if cmd == "/retry":
        prev = (existing.get("interrupted_prompt") or "").strip()
        if not prev:
            await _tg_send(token, chat_id, "Nothing to resume: no interrupted or failed turn on this thread.")
            return True
        await _tg_send(token, chat_id, f"🔁 Resuming: _{prev[:200]}_")
        return prev  # → il dispatch continua con questo testo

    if cmd == "/status":
        defaults = _load_hub_defaults()
        prov = existing.get("provider") or defaults["provider"]
        mod = existing.get("model") or defaults["model"]
        scope_p = existing.get("scope_project") or ""
        scope_a = existing.get("scope_agent") or ""
        if scope_p:
            scope_line = f"project: `{scope_p}`"
        elif scope_a:
            scope_line = f"agent: `{scope_a}`"
        else:
            scope_line = "Anja (default hub)"
        nmsg = len(existing.get("messages", []))
        sid = existing.get("sdk_session_id", "")
        auto_compact = existing.get("auto_compact", True)
        ac_pct = int(float(existing.get("auto_compact_pct", 0.55)) * 100)
        usage = existing.get("last_usage") or {}
        in_tok = int(usage.get("context_input_tokens") or usage.get("input_tokens", 0) or 0)
        ctx_win = int(usage.get("context_window", 0) or 0)
        ctx_line = ""
        if in_tok > 0 and ctx_win > 0:
            pct_now = int(in_tok / ctx_win * 100)
            ctx_line = f"\n• context: `{in_tok//1000}k/{ctx_win//1000}k` ({pct_now}%)"
        thread_suffix = conv_id.removeprefix(f"telegram-{chat_id}").lstrip("-") or "main"
        _pm = existing.get("asp_mode") or _tg_default_asp_mode(chat_id) or "default"
        _pm_def = _tg_default_asp_mode(chat_id)
        pm_line = f"• permissions: `{_pm}`" + (f" (Telegram default: `{_pm_def}`)" if _pm_def else "")
        msg_text = (
            f"*Chat status*\n"
            f"• provider: `{prov}`\n"
            f"• model: `{mod}`\n"
            f"• scope: {scope_line}\n"
            f"• thread: `{thread_suffix}` (`/threads` to switch)\n"
            f"{pm_line}\n"
            f"• messages: {nmsg}{ctx_line}\n"
            f"• auto-compact: {'on' if auto_compact else 'off'} ({ac_pct}% threshold)\n"
            f"• session_id: `{sid[:16] + '…' if sid else '(new)'}`"
        )
        await _tg_send(token, chat_id, msg_text)
        return True

    if cmd == "/model":
        if not args:
            # Inline keyboard con modelli del provider corrente
            defaults = _load_hub_defaults()
            cur_provider = existing.get("provider") or defaults["provider"]
            models = _get_provider_models_short(cur_provider)
            if not models:
                await _tg_send(token, chat_id, f"Usage: `/model <name>` (e.g. opus, sonnet, grok-4)")
                return True
            buttons = []
            row = []
            for m in models[:12]:  # max 12 per non saturare schermo
                row.append({"text": m, "callback_data": f"model:{m}"})
                if len(row) == 2:
                    buttons.append(row); row = []
            if row:
                buttons.append(row)
            markup = {"inline_keyboard": buttons}
            await _tg_send(token, chat_id, f"Which model? (provider: `{cur_provider}`)", reply_markup=markup)
            return True
        existing["model"] = args.strip()
        # Reset session_id perché alcuni provider non transferiscono context tra modelli
        existing["sdk_session_id"] = ""
        chat.save_conversation(WEBAPP_DIR, conv_id, existing.get("messages", []),
                               title=existing.get("title", ""), scope=existing.get("scope", "hub"),
                               provider=existing.get("provider", ""), model=args.strip(),
                               effort=existing.get("effort", ""))
        await _tg_send(token, chat_id, f"✓ model: `{args.strip()}` (session reset)")
        return True

    if cmd == "/provider":
        if not args:
            providers = ["claude", "openai", "xai", "openrouter", "gemini", "mistral", "groq"]
            # Fase 7v — aggiungi subscription/local se configurati
            try:
                from openai_oauth import is_openai_oauth_enabled
                if is_openai_oauth_enabled(HUB_PATH):
                    providers.insert(1, "openai_oauth")  # subito dopo claude
            except Exception:
                pass
            try:
                ollama_cfg_path = HUB_PATH / "config" / "ollama.json"
                if ollama_cfg_path.is_file():
                    ocfg = json.loads(ollama_cfg_path.read_text())
                    if ocfg.get("enabled"):
                        providers.append("ollama")
            except Exception:
                pass
            # Label friendly
            labels = {
                "claude": "Claude",
                "openai_oauth": "ChatGPT sub",
                "openai": "OpenAI",
                "xai": "xAI",
                "openrouter": "OpenRouter",
                "gemini": "Gemini",
                "mistral": "Mistral",
                "groq": "Groq",
                "ollama": "Ollama (local)",
            }
            buttons = []
            row = []
            for p in providers:
                row.append({"text": labels.get(p, p), "callback_data": f"provider:{p}"})
                if len(row) == 3:
                    buttons.append(row); row = []
            if row:
                buttons.append(row)
            markup = {"inline_keyboard": buttons}
            await _tg_send(token, chat_id, "Which provider?", reply_markup=markup)
            return True
        existing["provider"] = args.strip()
        existing["sdk_session_id"] = ""
        chat.save_conversation(WEBAPP_DIR, conv_id, existing.get("messages", []),
                               title=existing.get("title", ""), scope=existing.get("scope", "hub"),
                               provider=args.strip(), model=existing.get("model", ""),
                               effort=existing.get("effort", ""))
        await _tg_send(token, chat_id, f"✓ provider: `{args.strip()}` (session reset)")
        return True

    if cmd == "/agent":
        if not args:
            # Lista agent disponibili come inline buttons
            agents_dir = HUB_PATH / "agents"
            agent_names = []
            if agents_dir.is_dir():
                agent_names = [d.name for d in sorted(agents_dir.iterdir())
                              if d.is_dir() and (d / "config.json").is_file()]
            buttons = []
            row = []
            for a in agent_names[:12]:
                row.append({"text": f"🤖 {a}", "callback_data": f"agent:{a}"})
                if len(row) == 2:
                    buttons.append(row); row = []
            if row:
                buttons.append(row)
            buttons.append([{"text": "↩ reset (Anja)", "callback_data": "agent:reset"}])
            markup = {"inline_keyboard": buttons}
            cur = existing.get("scope_agent") or "Anja (default)"
            await _tg_send(token, chat_id, f"Which agent? (current: `{cur}`)", reply_markup=markup)
            return True
        if args.strip().lower() == "reset":
            existing["scope_agent"] = ""
            existing["sdk_session_id"] = ""
            chat.save_conversation(WEBAPP_DIR, conv_id, existing.get("messages", []),
                                   title=existing.get("title", ""), scope="hub",
                                   provider=existing.get("provider", ""), model=existing.get("model", ""),
                                   effort=existing.get("effort", ""))
            await _tg_send(token, chat_id, "✓ agent: Anja (default hub)")
            return True
        agent_name = args.strip()
        agent_dir = HUB_PATH / "agents" / agent_name
        if not agent_dir.is_dir():
            await _tg_send(token, chat_id, f"⚠ agent `{agent_name}` not found in {HUB_PATH}/agents/")
            return True
        existing["scope_agent"] = agent_name
        existing["sdk_session_id"] = ""
        chat.save_conversation(WEBAPP_DIR, conv_id, existing.get("messages", []),
                               title=existing.get("title", ""), scope=f"agent:{agent_name}",
                               provider=existing.get("provider", ""), model=existing.get("model", ""),
                               effort=existing.get("effort", ""))
        await _tg_send(token, chat_id, f"✓ agent: `{agent_name}`")
        return True

    if cmd == "/voice":
        arg = args.strip().lower()
        if arg not in ("on", "off", "auto"):
            cur = existing.get("voice_reply", "auto")
            await _tg_send(token, chat_id,
                f"Usage: `/voice on` (always voice) · `/voice off` (never voice) · `/voice auto` (voice only if you send audio)\n"
                f"Current state: *{cur}*")
            return True
        # Persist in conversation
        conv_path = WEBAPP_DIR / "conversations" / f"{conv_id}.json"
        if conv_path.is_file():
            payload = json.loads(conv_path.read_text(encoding="utf-8"))
        else:
            payload = {"id": conv_id, "title": "", "scope": "hub", "messages": []}
        payload["voice_reply"] = arg
        conv_path.parent.mkdir(parents=True, exist_ok=True)
        conv_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        labels = {"on": "always voice", "off": "never voice", "auto": "voice if voice input"}
        await _tg_send(token, chat_id, f"✓ voice: *{arg}* ({labels[arg]})")
        return True

    if cmd == "/autocompact":
        arg = args.strip().lower()
        cur_enabled = existing.get("auto_compact", True)
        cur_pct = int(float(existing.get("auto_compact_pct", 0.55)) * 100)
        # Forme accettate: "on", "off", "<pct>" (es. 60), "on 60", "off"
        if not arg:
            await _tg_send(token, chat_id,
                f"*Auto-compact*\n"
                f"• State: {'on' if cur_enabled else 'off'}\n"
                f"• Threshold: {cur_pct}% of context\n\n"
                f"Usage:\n"
                f"• `/autocompact on` — enable\n"
                f"• `/autocompact off` — disable\n"
                f"• `/autocompact 60` — set threshold to 60%\n"
                f"• `/autocompact on 50` — enable + 50% threshold")
            return True
        # Parse tokens
        parts = arg.split()
        new_enabled = None
        new_pct = None
        for p in parts:
            if p in ("on", "true", "1"):
                new_enabled = True
            elif p in ("off", "false", "0"):
                new_enabled = False
            else:
                try:
                    n = int(p.replace("%", ""))
                    if 20 <= n <= 95:
                        new_pct = n / 100.0
                except ValueError:
                    pass
        if new_enabled is None and new_pct is None:
            await _tg_send(token, chat_id, f"⚠ Unrecognized argument: `{arg}`. Use `/autocompact` for help.")
            return True
        # Patch conv file
        conv_path = WEBAPP_DIR / "conversations" / f"{conv_id}.json"
        if conv_path.is_file():
            payload = json.loads(conv_path.read_text(encoding="utf-8"))
        else:
            payload = {"id": conv_id, "messages": []}
        if new_enabled is not None:
            payload["auto_compact"] = new_enabled
        if new_pct is not None:
            payload["auto_compact_pct"] = new_pct
        conv_path.parent.mkdir(parents=True, exist_ok=True)
        conv_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        final_enabled = payload.get("auto_compact", cur_enabled)
        final_pct = int(float(payload.get("auto_compact_pct", 0.55)) * 100)
        await _tg_send(token, chat_id,
            f"✓ auto-compact: *{'on' if final_enabled else 'off'}* · threshold *{final_pct}%* of context")
        return True

    if cmd == "/queue":
        if not args:
            await _tg_send(token, chat_id,
                "Usage: `/queue <when> <what>`\n\n"
                "Examples:\n"
                "• `/queue in 30 min remind me to call Marco`\n"
                "• `/queue tomorrow at 9 give me a summary of yesterday's activity`\n"
                "• `/queue today at 5pm check Gmail email`")
            return True
        # Iniettiamo nel prompt una istruzione esplicita per Anja:
        # usa task.schedule_one_shot con notifica Telegram a questo chat_id.
        injected = (
            f"[QUEUE-COMMAND] Schedula questo task usando il tool `task.schedule_one_shot`. "
            f"NON chiedermi come notificare — passa output_actions con il chat_id Telegram fornito qui sotto.\n\n"
            f"Richiesta originale dell'utente: {args.strip()}\n\n"
            f"Parametri da usare:\n"
            f"- output_actions: [{{\"type\": \"telegram\", \"chat_id\": {chat_id}}}]\n"
            f"- Estrai `when` e `prompt` dalla richiesta originale.\n"
            f"- Conferma all'utente in italiano con quando partirà.\n"
        )
        # Fall-through: setta `text` (in scope esterno via return False non possibile;
        # invece processiamo qui inline modificando la flow). Solo via re-dispatch.
        # Trick: aggiungiamo a `existing` un flag che il dispatch principale leggerà.
        # Più semplice: ritorna False per indicare "non gestito", ma cambia text. Però
        # text è local del caller. Soluzione: facciamo dispatch direttamente qui.
        # In realtà più pulito: dispatch normale ma con text sostituito.
        # Ritorna il prompt override → caller continua dispatch con questo testo
        return injected

    if cmd == "/project":
        projects = _build_projects_context()
        if not args:
            buttons = []
            row = []
            for p in projects[:12]:
                row.append({"text": f"📁 {p.get('name','?')}", "callback_data": f"project:{p.get('name','')}"})
                if len(row) == 2:
                    buttons.append(row); row = []
            if row:
                buttons.append(row)
            buttons.append([{"text": "↩ reset (Anja hub)", "callback_data": "project:reset"}])
            markup = {"inline_keyboard": buttons}
            cur = existing.get("scope_project") or "Anja hub (default)"
            await _tg_send(token, chat_id, f"Which project? (current: `{cur}`)", reply_markup=markup)
            return True
        if args.strip().lower() == "reset":
            existing["scope_project"] = ""
            existing["scope_agent"] = ""
            existing["sdk_session_id"] = ""
            chat.save_conversation(WEBAPP_DIR, conv_id, existing.get("messages", []),
                                   title=existing.get("title", ""), scope="hub",
                                   provider=existing.get("provider", ""), model=existing.get("model", ""),
                                   effort=existing.get("effort", ""))
            await _tg_send(token, chat_id, "✓ scope: Anja hub (default)")
            return True
        proj_name = args.strip()
        # Verifica progetto registrato
        proj_match = [p for p in projects if p.get("name") == proj_name]
        if not proj_match:
            avail = ", ".join(p.get("name", "?") for p in projects) or "(none)"
            await _tg_send(token, chat_id, f"⚠ Project `{proj_name}` not registered.\nAvailable: {avail}")
            return True
        existing["scope_project"] = proj_name
        existing["scope_agent"] = ""  # mutuamente esclusivo con project
        existing["sdk_session_id"] = ""
        chat.save_conversation(WEBAPP_DIR, conv_id, existing.get("messages", []),
                               title=existing.get("title", ""), scope=f"project:{proj_name}",
                               provider=existing.get("provider", ""), model=existing.get("model", ""),
                               effort=existing.get("effort", ""))
        await _tg_send(token, chat_id, f"✓ scope: `project:{proj_name}` — Anja will reply in this project's context.")
        return True

    if cmd == "/threads":
        # F-TelegramMultiSession: lista thread come inline buttons (pattern /agent)
        threads = _tg_list_threads(chat_id)
        if not threads:
            await _tg_send(token, chat_id, "No threads yet — message me and I'll create one, or `/newchat`.")
            return True
        active = _tg_active_conv(chat_id)
        buttons = []
        for t in threads[:10]:
            label = t["title"][:28] or t["suffix"]
            mark = "▶ " if t["conv_id"] == active else ""
            agent_part = f" · 🤖{t['scope_agent']}" if t["scope_agent"] else ""
            buttons.append([{"text": f"{mark}{label}{agent_part} · {t['n_msgs']} msg",
                             "callback_data": f"thread:{t['suffix']}"}])
        buttons.append([{"text": "➕ new thread", "callback_data": "thread:new"}])
        await _tg_send(token, chat_id, "Threads in this chat (▶ = active):",
                       reply_markup={"inline_keyboard": buttons})
        return True

    if cmd == "/thread":
        # Target dei callback thread:<suffix> (il daemon li converte in "/thread <suffix>")
        arg = args.strip().lower()
        if not arg:
            return await _telegram_handle_command(chat, conv_id, chat_id, "/threads", "", token)
        if arg == "new":
            new_conv = _tg_new_thread_conv(chat_id)
            _tg_set_active_thread(chat_id, new_conv)
            await _tg_send(token, chat_id,
                f"✓ New thread `{new_conv.rsplit('-', 1)[-1]}` active — starting fresh.\n"
                f"Previous threads are kept: `/threads` to switch.")
            return True
        target = f"telegram-{chat_id}" if arg == "main" else f"telegram-{chat_id}-{arg}"
        if target not in {t["conv_id"] for t in _tg_list_threads(chat_id)} and arg != "main":
            await _tg_send(token, chat_id, f"⚠ Thread `{arg}` not found. `/threads` for the list.")
            return True
        _tg_set_active_thread(chat_id, target)
        data = chat.load_conversation(WEBAPP_DIR, target) or {}
        title = (data.get("title") or "").strip()[:40] or arg
        ag = data.get("scope_agent") or ""
        ag_part = f" (agent: `{ag}`)" if ag else ""
        await _tg_send(token, chat_id,
                       f"✓ Active thread: *{title}*{ag_part} — {len(data.get('messages', []))} msg.")
        return True

    if cmd == "/newchat":
        # F-TelegramMultiSession: crea un nuovo thread e lo rende attivo.
        # I precedenti restano intatti (prima cancellava la memoria).
        new_conv = _tg_new_thread_conv(chat_id)
        _tg_set_active_thread(chat_id, new_conv)
        # F-TelegramModeDefault: eredita il default canale + avviso di stato
        _def_mode = _tg_default_asp_mode(chat_id)
        mode_note = ""
        if _def_mode:
            _tg_set_conv_asp_mode(new_conv, _def_mode)
            _lbl = "auto (allow everything)" if _def_mode == "auto" else _def_mode
            mode_note = f"\n🔧 Permissions: `{_lbl}` — Telegram default (`/mode` to change)."
        await _tg_send(token, chat_id,
            "✓ New thread active — starting fresh.\nPrevious threads are kept: `/threads` to go back."
            + mode_note)
        return True

    if cmd in ("/async", "/bg"):
        # F-TelegramAsyncNotify: lancia il prompt in background sul thread attivo.
        # La chat resta libera per gli ALTRI thread (lock per-conv); la risposta
        # arriva quando è pronta, con la label del thread.
        prompt = args.strip()
        if not prompt:
            await _tg_send(token, chat_id,
                "Usage: `/async <message>` — Anja works in the background and pings you when done. "
                "Meanwhile switch threads (`/threads`) and keep chatting.")
            return True
        label = _tg_thread_label(existing, conv_id, chat_id)
        import asyncio as _aio
        _aio.create_task(_telegram_async_bg(chat_id, conv_id, prompt, label))
        await _tg_send(token, chat_id,
            f"⏳ Working in the background on *{label}* — I'll ping you as soon as it's ready. The chat stays free.")
        return True

    if cmd == "/compact":
        if not existing.get("messages"):
            await _tg_send(token, chat_id, "⚠ No messages to compact.")
            return True
        await _tg_send(token, chat_id, "⏳ Compacting the conversation…")
        # Telegram: keep_last_n=1 (2 msg) — Telegram chat sono brevi, compact aggressivo
        result = await compact_conversation(conv_id, keep_last_n=1)
        if not result.get("ok"):
            await _tg_send(token, chat_id, f"⚠ Compact failed: {result.get('error')}")
            return True
        msg_text = (
            f"✓ Compact OK: {result['messages_before']} → {result['messages_after']} msg "
            f"(last {result['kept_last']} kept).\n\n"
            f"*Summary:*\n{result['summary']}"
        )
        await _tg_send(token, chat_id, msg_text)
        return True

    # Fase 15 — Kanban commands
    if cmd == "/kanban" or cmd.startswith("/kanban "):
        try:
            kio = _kanban_io()
        except Exception:
            kio = None
        if not kio:
            await _tg_send(token, chat_id, "⚠ Kanban not available.")
            return True

        rest = cmd[len("/kanban"):].strip()
        # /kanban → lista active
        if not rest or rest == "list":
            tasks = kio.list_tasks(HUB_PATH, status="active", limit=20)
            if not tasks:
                await _tg_send(token, chat_id, "📋 *Kanban:* no active tasks.")
                return True
            lines = ["📋 *Kanban — active tasks:*\n"]
            by_status = {}
            for t in tasks:
                by_status.setdefault(t["status"], []).append(t)
            for status in ("running", "ready", "todo", "blocked", "triage"):
                items = by_status.get(status, [])
                if not items:
                    continue
                emoji = {"running": "⚙️", "ready": "▶️", "todo": "📝", "blocked": "🚫", "triage": "🤔"}.get(status, "•")
                lines.append(f"\n{emoji} *{status.upper()}* ({len(items)})")
                for t in items[:5]:
                    prio = "🔥" if t["priority"] >= 3 else ("⚡" if t["priority"] >= 2 else "")
                    assignee = f" @{t['assignee']}" if t.get("assignee") else ""
                    lines.append(f"  `#{t['id']}` {prio} {t['title']}{assignee}")
                if len(items) > 5:
                    lines.append(f"  _… +{len(items)-5} more_")
            await _tg_send(token, chat_id, "\n".join(lines))
            return True

        # /kanban add <title>
        if rest.startswith("add "):
            title = rest[4:].strip()
            if not title:
                await _tg_send(token, chat_id, "Usage: `/kanban add <title>`")
                return True
            # Resolve scope: se conv current è project, usa quello
            existing = chat.load_conversation(WEBAPP_DIR, conv_id) if conv_id else {}
            scope = existing.get("scope", "hub") if existing else "hub"
            # Default assignee: anja o responsabile workspace
            assignee = ""
            if scope.startswith("project:"):
                ws_name = scope.split(":", 1)[1]
                meta = _read_workspace_meta_yaml(ws_name)
                assignee = meta.get("responsabile") or "anja"
            else:
                try:
                    import json as _json
                    cfg = _json.loads((HUB_PATH / "config.json").read_text(encoding="utf-8"))
                    assignee = cfg.get("default_agent_name", "anja").lower()
                except Exception:
                    assignee = "anja"
            try:
                t = kio.create_task(HUB_PATH, title=title, scope=scope, assignee=assignee)
                await _tg_send(token, chat_id, f"➕ Task created `#{t['id']}`: *{title}*\nScope: `{scope}` · Assignee: `@{assignee}`")
            except Exception as e:
                await _tg_send(token, chat_id, f"⚠ Error: {e}")
            return True

        # /kanban done <id> [summary]
        if rest.startswith("done "):
            parts = rest[5:].strip().split(" ", 1)
            try:
                task_id = int(parts[0])
            except ValueError:
                await _tg_send(token, chat_id, "Usage: `/kanban done <id> [summary]`")
                return True
            summary = parts[1] if len(parts) > 1 else ""
            if summary:
                kio.add_comment(HUB_PATH, task_id, f"✓ Completed: {summary}", author="human:telegram")
            t = kio.update_status(HUB_PATH, task_id, "done")
            if not t:
                await _tg_send(token, chat_id, f"⚠ Task `#{task_id}` not found.")
                return True
            promoted = kio.auto_promote_ready(HUB_PATH)
            msg = f"✅ Task `#{task_id}` *done*: {t['title']}"
            if promoted:
                msg += f"\n↗ Auto-promoted: {', '.join(f'#{p}' for p in promoted)}"
            await _tg_send(token, chat_id, msg)
            return True

        # /kanban block <id> <reason>
        if rest.startswith("block "):
            parts = rest[6:].strip().split(" ", 1)
            try:
                task_id = int(parts[0])
                reason = parts[1] if len(parts) > 1 else "blocked via telegram"
            except (ValueError, IndexError):
                await _tg_send(token, chat_id, "Usage: `/kanban block <id> <reason>`")
                return True
            t = kio.update_status(HUB_PATH, task_id, "blocked", block_reason=reason)
            if t:
                await _tg_send(token, chat_id, f"🚫 Task `#{task_id}` blocked: {reason}")
            else:
                await _tg_send(token, chat_id, f"⚠ Task `#{task_id}` not found.")
            return True

        # /kanban show <id>
        if rest.startswith("show "):
            try:
                task_id = int(rest[5:].strip())
            except ValueError:
                await _tg_send(token, chat_id, "Usage: `/kanban show <id>`")
                return True
            t = kio.get_task(HUB_PATH, task_id)
            if not t:
                await _tg_send(token, chat_id, f"⚠ Task `#{task_id}` not found.")
                return True
            lines = [
                f"*#{t['id']}* — {t['title']}",
                f"Status: `{t['status']}` · Scope: `{t['scope']}`",
            ]
            if t.get("assignee"):
                lines.append(f"Assignee: `@{t['assignee']}`")
            if t.get("body"):
                lines.append(f"\n{t['body'][:500]}")
            if t.get("block_reason"):
                lines.append(f"\n🚫 _Blocked: {t['block_reason']}_")
            if t.get("comments"):
                lines.append(f"\n💬 {len(t['comments'])} comments")
            await _tg_send(token, chat_id, "\n".join(lines))
            return True

        # Help kanban
        await _tg_send(token, chat_id,
            "📋 *Kanban commands:*\n"
            "`/kanban` — list active tasks\n"
            "`/kanban add <title>` — create task\n"
            "`/kanban show <id>` — task detail\n"
            "`/kanban done <id> [summary]` — mark done\n"
            "`/kanban block <id> <reason>` — block")
        return True

    # Comando sconosciuto
    await _tg_send(token, chat_id, f"Unknown command `{cmd}`. Use `/help` for the list.")
    return True


def _tg_last_tool_label(state) -> str:
    """Etichetta compatta dell'ultimo tool usato nel turno, dal buffer dello stream.
    Sanitizzata dai caratteri markdown (l'heartbeat viaggia con parse_mode=Markdown)."""
    for ev in reversed(state.buffer):
        if ev.get("type") != "tool_use":
            continue
        name = ev.get("name", "?")
        if name.startswith("mcp__"):
            name = name.split("__")[-1]
        inp = ev.get("input") if isinstance(ev.get("input"), dict) else {}
        hint = (inp.get("description") or "").strip()
        if not hint:
            hint = str(inp.get("file_path") or inp.get("path") or inp.get("command") or "").strip()
            hint = hint.splitlines()[0] if hint else ""
            if "/" in hint and " " not in hint:
                hint = hint.rsplit("/", 1)[-1]
        hint = re.sub(r"[*_`\[\]]", "", hint)[:60]
        return f"{name} ({hint})" if hint else name
    return ""


async def _tg_heartbeat_loop(token: str, chat_id: int, state, hb: dict):
    """F-TurnHeartbeat: durante un turno lungo invia un messaggio di progresso e poi
    lo AGGIORNA in-place (editMessageText) invece di riempire la chat. Primo beat dopo
    una soglia di silenzio (i turni brevi non lo vedono mai), poi a intervallo fisso.
    Cancellato dal dispatcher a turno finito, che rimuove anche il messaggio."""
    from telegram_daemon import send_message as _send, edit_message_text as _edit
    first = int(os.environ.get("ANJA_TG_HEARTBEAT_FIRST", "90"))
    every = int(os.environ.get("ANJA_TG_HEARTBEAT_EVERY", "120"))
    await asyncio.sleep(first)
    while True:
        mins = max(1, int((time.time() - state.started_ts) // 60))
        label = _tg_last_tool_label(state)
        n = state.tool_iter_count
        # Fase 3 ASP — heartbeat todo-aware: se il turno ha un todo attivo,
        # mostra avanzamento e voce corrente dall'ultimo todo.updated nel log.
        todo_txt = ""
        try:
            for ev in reversed(state.buffer):
                if ev.get("type") == "todo.updated":
                    todos = ev.get("todos") or []
                    done_n = sum(1 for t in todos if t.get("status") == "completed")
                    cur = next((t["content"] for t in todos
                                if t.get("status") == "in_progress"), "")
                    if todos:
                        todo_txt = f"\n📋 {done_n}/{len(todos)}" + (f" — _{cur[:80]}_" if cur else "")
                    break
        except Exception:
            pass
        if n:
            txt = f"⏳ _Still working — {n} actions" + (f", last: {label}" if label else "") + f" · {mins} min_" + todo_txt
        else:
            txt = f"⏳ _Still working · {mins} min_" + todo_txt
        try:
            if hb.get("message_id"):
                await _edit(token, chat_id, hb["message_id"], txt)
            else:
                r = await _send(token, chat_id, txt)
                if r.get("ok"):
                    hb["message_id"] = (r.get("result") or {}).get("message_id")
        except Exception:
            pass
        await asyncio.sleep(every)


def _is_killed_turn_error(err_msg: str) -> bool:
    """True se l'errore del bridge indica un figlio `claude` terminato dall'esterno
    (SIGTERM → exit 143): il caso reale è il restart del server durante un deploy."""
    m = err_msg or ""
    return "exit code 143" in m or "SIGTERM" in m or "signal 15" in m


async def _telegram_dispatch(msg: dict):
    """Wrapper con lock per-conv_id (F-TelegramAsyncNotify): serializza le scritture
    sullo STESSO thread ma lascia liberi i thread diversi (era per-chat, Bug B 2026-05-11)."""
    conv_id = _tg_active_conv(msg.get("chat_id", 0))
    # F-AgentSessions Fase 1: messaggio di testo mentre il turno ASP è in corso
    # = steering (iniettato nel turno), non attesa in coda sul lock.
    _text = (msg.get("text") or "").strip()
    # Comandi di controllo vivo: DEVONO saltare il lock — il turno lo tiene
    # mentre aspetta proprio la risposta (/allow in coda dietro la propria
    # permission.requested = deadlock fino al timeout; trovato alla prima
    # validazione col bot reale, 2026-08-09).
    _ASP_LIVE_CMDS = {"/stop", "/allow", "/deny", "/approve", "/replan",
                      "/mode", "/merge", "/discard"}
    if (_text.startswith("/")
            and _text.split(maxsplit=1)[0].lower() in _ASP_LIVE_CMDS
            and os.environ.get("ANJA_ASP_ENABLED") == "1"):
        chat = _get_chat_module()
        token = TELEGRAM_DAEMON.token if TELEGRAM_DAEMON else None
        if chat and token:
            parts = _text.split(maxsplit=1)
            try:
                await _telegram_handle_command(
                    chat, conv_id, msg["chat_id"], parts[0].lower(),
                    parts[1] if len(parts) > 1 else "", token)
            except Exception as e:
                print(f"[asp] tg live-command error: {type(e).__name__}: {e}")
                try:
                    from telegram_daemon import send_message as _tg_send
                    await _tg_send(token, msg["chat_id"], f"⚠ Command error: {e}")
                except Exception:
                    pass
            return
    if (_text and not _text.startswith("/")
            and os.environ.get("ANJA_ASP_ENABLED") == "1"):
        try:
            import claude_session
            if await claude_session.pool.steer(conv_id, _text):
                token = TELEGRAM_DAEMON.token if TELEGRAM_DAEMON else None
                if token:
                    from telegram_daemon import send_message as _tg_send
                    await _tg_send(token, msg["chat_id"],
                                   "⤳ _injected into the current turn_")
                return
        except Exception as e:
            print(f"[asp] tg steer error: {e}")
    _lock = _telegram_conv_lock(conv_id)
    if (_lock.locked() and _text and not _text.startswith("/")):
        # Turno in corso ma steering non agganciabile (sessione in boot, o
        # path legacy): rendi visibile la coda invece del silenzio.
        try:
            token = TELEGRAM_DAEMON.token if TELEGRAM_DAEMON else None
            if token:
                from telegram_daemon import send_message as _tg_send
                await _tg_send(token, msg["chat_id"],
                               "🕐 _queued: I'll get to it as soon as the current turn finishes_")
        except Exception:
            pass
    async with _lock:
        await _telegram_dispatch_locked(msg)


def _tg_thread_label(conv_data: dict, conv_id: str, chat_id: int) -> str:
    """Label leggibile di un thread per le notifiche async: titolo, o suffix·agent."""
    title = (conv_data.get("title") or "").strip()
    if title:
        return title[:30]
    suffix = conv_id.removeprefix(f"telegram-{chat_id}").lstrip("-") or "main"
    ag = conv_data.get("scope_agent") or ""
    return f"{suffix}·{ag}" if ag else suffix


async def _telegram_async_bg(chat_id: int, conv_id: str, text: str, label: str):
    """F-TelegramAsyncNotify: esegue un dispatch in background sul thread FISSATO
    `conv_id` (non l'attivo, che l'utente può cambiare nel frattempo) e notifica la
    risposta con la label del thread. Prende il lock del conv → si serializza con i
    messaggi diretti sullo stesso thread, ma non blocca gli altri."""
    msg = {"chat_id": chat_id, "text": text, "from_async": True}
    try:
        async with _telegram_conv_lock(conv_id):
            await _telegram_dispatch_locked(msg, conv_id_override=conv_id,
                                            reply_prefix=f"✅ *{label}*\n\n")
    except Exception as e:
        token = TELEGRAM_DAEMON.token if TELEGRAM_DAEMON else None
        if token:
            try:
                from telegram_daemon import send_message as _tg_send
                await _tg_send(token, chat_id,
                               f"⚠ Async task *{label}* failed: {type(e).__name__}: {e}")
            except Exception:
                pass


async def _telegram_dispatch_locked(msg: dict, conv_id_override: str = None, reply_prefix: str = ""):
    """Inbound Telegram message → invoke default hub agent (Anja) → send reply.

    Persiste la conversation nel thread ATTIVO del chat_id (F-TelegramMultiSession):
    default `telegram-{chat_id}` (main), oppure `telegram-{chat_id}-tN` se switchato
    via /threads. Multi-turn via SDK resume per-thread.

    `conv_id_override` fissa il thread (F-TelegramAsyncNotify: l'async non deve seguire
    l'attivo, che può cambiare mentre lavora); `reply_prefix` antepone la label alla risposta.
    """
    global TELEGRAM_DAEMON
    chat_id = msg["chat_id"]
    text = msg["text"]
    conv_id = conv_id_override or _tg_active_conv(chat_id)

    chat = _get_chat_module()
    if not chat:
        print("[telegram] chat module unavailable")
        return

    token = TELEGRAM_DAEMON.token if TELEGRAM_DAEMON else None
    if not token:
        return

    # Slash command dispatch
    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        cmd_args = parts[1] if len(parts) > 1 else ""
        try:
            result = await _telegram_handle_command(chat, conv_id, chat_id, cmd, cmd_args, token)
            if isinstance(result, str):
                # Comando ha prodotto un override del prompt → continua dispatch con questo testo
                text = result
            elif result:
                return
        except Exception as e:
            print(f"[telegram] command error: {type(e).__name__}: {e}")
            from telegram_daemon import send_message as _tg_send
            await _tg_send(token, chat_id, f"⚠ Command error: {e}")
            return

    # Typing indicator (best-effort)
    try:
        from telegram_daemon import send_typing, send_message as _tg_send
        await send_typing(token, chat_id)
    except Exception:
        return

    # Carica conversation esistente per resume sdk_session_id e provider/model
    existing = chat.load_conversation(WEBAPP_DIR, conv_id) or {}
    sdk_session_id = existing.get("sdk_session_id")
    # Fase 13 — applica project prefs se conv è scope=project (anche per Telegram via /project)
    _conv_scope = existing.get("scope") or ("project:" + existing.get("scope_project", "") if existing.get("scope_project") else "hub")
    defaults = _resolve_defaults_for_scope(_conv_scope)
    provider = existing.get("provider") or defaults["provider"]
    model = existing.get("model") or defaults["model"]
    effort_str = existing.get("effort") or defaults["effort"]
    effort = effort_str if effort_str and effort_str != "off" else None

    # Resolve scope: project > agent > hub (mutuamente esclusivi, project ha priorità)
    projects = _build_projects_context()
    scope_project = existing.get("scope_project") or ""
    scope_agent = existing.get("scope_agent") or ""
    if scope_project:
        scope_str = f"project:{scope_project}"
        scope_kind_str = "project"
        scope_target = scope_project
    elif scope_agent:
        scope_str = f"agent:{scope_agent}"
        scope_kind_str = "agent"
        scope_target = scope_agent
    else:
        scope_str = "hub"
        scope_kind_str = "hub"
        scope_target = None
    cwd, _kt = chat.resolve_chat_cwd(HUB_PATH, scope_str, projects)

    # User identity dal hub config
    user_name = "user"
    timezone = ""
    try:
        cfg = json.loads((HUB_PATH / "config.json").read_text(encoding="utf-8"))
        default_user_slug = cfg.get("default_user", "")
        if default_user_slug:
            user_name = default_user_slug
    except Exception:
        pass

    # Build system prompt project vs agent vs hub
    agent_cfg_for_scope = None
    try:
        if scope_kind_str == "agent":
            agent_cfg_for_scope = chat.load_agent_config(HUB_PATH, scope_target)
            system_prompt = chat.build_agent_system_prompt(
                HUB_PATH, scope_target, cwd, agent_cfg_for_scope,
                user_prompt=text, image_gen_enabled=False,
                user_name=user_name, timezone=timezone,
            )
            base_tools = agent_cfg_for_scope.get("allowed_tools") or chat.PROJECT_TOOLS_FULL
        elif scope_kind_str == "project":
            system_prompt = chat.build_project_system_prompt(
                scope_target, cwd, user_prompt=text, image_gen_enabled=False,
                hub_name=HUB_PATH.name, user_name=user_name, timezone=timezone,
                hub_path=HUB_PATH,
            )
            base_tools = chat.PROJECT_TOOLS_FULL
        else:
            system_prompt = chat.build_system_prompt(
                HUB_PATH, projects, user_prompt=text,
                image_gen_enabled=False,
                user_name=user_name, timezone=timezone,
            )
            base_tools = chat.HUB_TOOLS_READONLY
    except Exception as e:
        # Mai fallire muto: l'utente riceverebbe il nulla (successo il 2026-07-26
        # con una graffa non-escapata nel template → KeyError → silenzio totale).
        print(f"[telegram] build_system_prompt error: {type(e).__name__}: {e}")
        try:
            await _tg_send(token, chat_id,
                           f"⚠ Internal error while preparing context ({type(e).__name__}). "
                           "Try again; if it persists it's a server bug, not your message.")
        except Exception:
            pass
        return

    # F-CLI-Media: da Telegram la generazione media è sempre disponibile (via
    # giv, Bash); i path citati nella risposta vengono allegati a fine turno.
    system_prompt = system_prompt + _giv_media_hint(telegram=True)

    # FIX 2026-05-11 Bug A: inietta compact_summary nel system_prompt se presente.
    # Il SDK è stateful via session_id, quindi non legge il summary dai messages —
    # va passato qui ogni volta finché la conv resta short.
    compact_summary = existing.get("compact_summary")
    if compact_summary:
        # Mostra ultimi 4-6 messaggi reali come "recent turns" + summary del pregresso
        recent_lines = []
        for m in existing.get("messages", [])[-6:]:
            role_lbl = {"user": "USER", "claude": "ANJA", "assistant": "ANJA"}.get(m.get("role"), m.get("role", "?").upper())
            content = (m.get("content") or "").strip()
            if not content:
                continue
            if len(content) > 800:
                content = content[:800] + " […]"
            recent_lines.append(f"[{role_lbl}] {content}")
        recent_block = "\n\n".join(recent_lines) if recent_lines else ""
        injection = (
            "\n\n# Conversation context (compacted history)\n\n"
            "## Summary of earlier turns\n\n" + compact_summary +
            ("\n\n## Most recent turns\n\n" + recent_block if recent_block else "")
        )
        system_prompt = system_prompt + injection

    # MCP scoping (Tier 0 + Tier 1 agent + keyword routing)
    try:
        from mcp_scoper import scope_mcps as _scope_mcps
        scoped_servers, _ = _scope_mcps(
            hub_path=HUB_PATH, scope_kind=scope_kind_str, target_name=scope_target,
            cwd=cwd, user_prompt=text, active_mcps=[], agent_config=agent_cfg_for_scope,
        )
    except Exception:
        scoped_servers = None

    allowed_tools = chat.augment_with_mcp(
        base_tools, cwd, provider=provider, scoped_servers=scoped_servers
    )
    allowed_tools = [t for t in allowed_tools if not t.startswith("mcp__anja_images__")]

    cap_block = chat.mcp_capabilities_block(cwd, scoped_servers)
    if cap_block:
        system_prompt = (system_prompt or "") + cap_block

    # Stream response, accumula text + cattura sdk_session_id + usage tokens.
    # Il turno è registrato in chat_stream_registry (F-GracefulRestart: visibile in
    # /api/activity/summary + conteggiato allo shutdown) e affiancato dall'heartbeat.
    full_response = ""
    new_sdk_session_id = None
    last_usage = None  # Fase 11 smart-compact: cattura input_tokens + context_window
    turn_errored = False
    turn_interrupted = False
    stream_state = chat_streams.register(
        conv_id, scope=scope_str, model=model, provider=provider,
        user_msg=text, title=(existing.get("title") or f"telegram:{chat_id}"),
    )
    hb = {"message_id": None}
    hb_task = asyncio.create_task(_tg_heartbeat_loop(token, chat_id, stream_state, hb))
    try:
        # F-AgentSessions ponte TG→ASP: stesso selettore del drainer WS.
        # Senza, i turni Telegram giravano legacy: pool vuoto → steer/stop/🔐
        # /mode/todo-heartbeat morti da TG (trovato alla prima validazione col
        # bot reale, 2026-08-09).
        _use_asp = (os.environ.get("ANJA_ASP_ENABLED") == "1"
                    and provider == "claude")
        if _use_asp:
            import claude_session
            _gen = claude_session.stream_turn(
                conv_id=conv_id,
                user_prompt=text,
                system_prompt=system_prompt,
                cwd=cwd,
                model=model,
                allowed_tools=allowed_tools,
                effort=effort,
                resume_session_id=sdk_session_id,
                scoped_servers=scoped_servers,
                image_attachments=None,
                permission_mode=(existing.get("asp_mode")
                                 or _tg_default_asp_mode(chat_id) or None),
            )
        else:
            _gen = chat.stream_response(
                user_prompt=text,
                system_prompt=system_prompt,
                cwd=cwd,
                model=model,
                allowed_tools=allowed_tools,
                effort=effort,
                provider=provider,
                resume_session_id=sdk_session_id,
                scoped_servers=scoped_servers,
            )
        async for event in _gen:
            stream_state.append(event)
            etype = event.get("type")
            if etype == "text":
                full_response += event.get("content", "")
            elif etype == "session_id" and event.get("session_id"):
                # Bug fix 2026-05-11: il claude_chat emette "session_id" non "system".
                new_sdk_session_id = event["session_id"]
            elif etype == "usage":
                last_usage = {
                    # input_tokens è CUMULATIVO sui round-tool (per costo); il riempimento
                    # reale della finestra è context_input_tokens (picco per-chiamata) —
                    # senza, l'auto-compact leggeva "567% ctx" su turni tool-heavy.
                    "input_tokens": int(event.get("input_tokens", 0) or 0),
                    "context_input_tokens": int(event.get("context_input_tokens", 0) or 0),
                    "output_tokens": int(event.get("output_tokens", 0) or 0),
                    "context_window": int(event.get("context_window", 0) or 0),
                    "ts": time.time(),
                }
                cost_store.record_usage_event(HUB_PATH, event, feature="chat", scope="telegram")
            elif etype == "notice" and "interrot" in (event.get("message") or ""):
                turn_interrupted = True
            elif etype == "error":
                turn_errored = True
                err_msg = event.get("message", "unknown")
                if _is_killed_turn_error(err_msg):
                    print(f"[telegram] turno {conv_id} UCCISO da SIGTERM (restart?) "
                          f"dopo {stream_state.tool_iter_count} azioni")
                    full_response = ("🔄 The server restarted while I was working (likely a "
                                     "deploy/update) and the turn was interrupted.\n"
                                     "Send /retry to have me start over.")
                else:
                    full_response = f"⚠ Error: {err_msg}\nSend /retry to try again."
                break
    except Exception as e:
        turn_errored = True
        full_response = f"⚠ Internal exception: {type(e).__name__}: {e}\nSend /retry to try again."
    finally:
        hb_task.cancel()
        stream_state.completed = True
        if turn_errored:
            stream_state.error = full_response[:200]
        if hb.get("message_id"):
            try:
                from telegram_daemon import delete_message as _tg_delete
                await _tg_delete(token, chat_id, hb["message_id"])
            except Exception:
                pass

    if not full_response.strip():
        full_response = "(no response generated)"
    elif turn_interrupted:
        full_response = ("⏹ _Turn interrupted — here's what I had "
                         "so far:_\n\n" + full_response)

    # Voice-loop logic (Fase 11 TTS 3): decide se rispondere anche con voice
    voice_reply_mode = existing.get("voice_reply", "auto")  # 'auto' | 'on' | 'off'
    from_voice = bool(msg.get("from_voice"))
    should_voice = (
        voice_reply_mode == "on" or
        (voice_reply_mode == "auto" and from_voice)
    )

    # Send testo back to Telegram con quick-reply keyboard persistente
    try:
        from telegram_daemon import QUICK_REPLY_KEYBOARD
        await _tg_send(token, chat_id, reply_prefix + full_response, reply_markup=QUICK_REPLY_KEYBOARD)
    except Exception as e:
        print(f"[telegram] send_message error: {e}")

    # F-CLI-Media: allega come foto/video i media generati citati nella risposta
    # (path sotto <hub>/raw/{images,videos}/ — vedi hint giv nel system prompt)
    if not turn_errored:
        try:
            from telegram_daemon import send_media as _tg_send_media
            _mpat = re.compile(
                re.escape(str(HUB_PATH))
                + r"/raw/(?:images|videos)/\d{4}-\d{2}-\d{2}/"
                + r"[\w.\-]+?\.(?:png|jpe?g|webp|gif|mp4|mov|webm)")
            sent_media = []
            for mp in _mpat.findall(full_response):
                if mp in sent_media or len(sent_media) >= 5:
                    continue
                sent_media.append(mp)
                resp = await _tg_send_media(token, chat_id, mp)
                if not resp.get("ok"):
                    print(f"[telegram] send_media KO {mp}: {resp.get('description')}")
        except Exception as e:
            print(f"[telegram] media attach error: {e}")

    # Send voice reply (TTS) se previsto
    if should_voice and full_response.strip() and not turn_errored:
        try:
            from telegram_daemon import synthesize_speech, send_voice, send_audio
            # Strip markdown per TTS (asterischi, backtick rendono male in voce)
            clean = re.sub(r"[*_`#]", "", full_response)
            clean = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", clean)
            clean = clean.strip()
            if clean:
                audio_bytes, model_used, codec = await synthesize_speech(clean, hub_path=HUB_PATH)
                # opus → sendVoice (voice message), altri → sendAudio (audio file)
                if codec == "opus":
                    resp = await send_voice(token, chat_id, audio_bytes)
                else:
                    resp = await send_audio(token, chat_id, audio_bytes, title="Anja", ext=codec)
                if resp.get("ok"):
                    print(f"[telegram] audio reply sent via {model_used} ({len(audio_bytes)} bytes, {codec})")
                else:
                    print(f"[telegram] send audio failed: {resp}")
                    await _tg_send(token, chat_id, f"⚠ Audio send failed: `{resp.get('description', 'unknown')}`")
        except Exception as e:
            err_msg = str(e)
            print(f"[telegram] TTS error: {err_msg}")
            await _tg_send(token, chat_id, f"⚠ TTS failed: `{err_msg[:200]}`")

    # Smart auto-compact su soglia % token (Fase 11 fix 2026-05-11)
    # Default: compact quando input_tokens >= 55% del context_window del modello.
    # Fallback per provider/turni senza usage data: trigger a 50 msg.
    try:
        auto_compact_enabled = existing.get("auto_compact", True)
        if auto_compact_enabled:
            updated = chat.load_conversation(WEBAPP_DIR, conv_id) or {}
            recently_compacted = (time.time() - (updated.get("compacted_at") or 0)) < 60
            if not recently_compacted:
                pct_threshold = float(updated.get("auto_compact_pct", 0.55))
                usage = updated.get("last_usage") or {}
                # picco finestra, NON il cumulativo dei round-tool (come il path WS a
                # _persist: il cumulativo su turni tool-heavy supera il 100% di suo)
                input_tok = int(usage.get("context_input_tokens") or usage.get("input_tokens", 0) or 0)
                ctx_win = int(usage.get("context_window", 0) or 0)
                pct_used = (input_tok / ctx_win) if ctx_win > 0 else 0
                msgs = updated.get("messages", [])
                fallback_msg_trigger = (input_tok == 0 or ctx_win == 0) and len(msgs) >= 50
                if (pct_used >= pct_threshold) or fallback_msg_trigger:
                    if fallback_msg_trigger:
                        print(f"[telegram] auto-compact (fallback msgs): {len(msgs)} msgs (no usage data)")
                    else:
                        print(f"[telegram] auto-compact (token): {input_tok}/{ctx_win} = {pct_used:.1%} ≥ {pct_threshold:.0%}")
                    result = await compact_conversation(conv_id, keep_last_n=3)
                    if result.get("ok"):
                        if not fallback_msg_trigger:
                            reason = f"{int(pct_used*100)}% ctx ({input_tok//1000}k/{ctx_win//1000}k)"
                        else:
                            reason = f"{len(msgs)} msg"
                        await _tg_send(token, chat_id,
                                       f"🗜 _Auto-compact ({reason}): {result['messages_before']} → {result['messages_after']} msg_")
    except Exception as e:
        print(f"[telegram] auto-compact error: {e}")

    # Persist conversation
    try:
        new_messages = list(existing.get("messages", []))
        new_messages.append({"role": "user", "content": text})
        new_messages.append({"role": "claude", "content": full_response})
        chat.save_conversation(
            WEBAPP_DIR, conv_id, new_messages,
            title=existing.get("title") or text[:60],
            scope="hub",
            provider=provider, model=model, effort=effort or "",
        )
        # Salva sdk_session_id + last_usage (espandiamo a-mano oltre save_conversation)
        # + interrupted_prompt: su turno fallito salva il testo per /retry, su successo pulisci.
        if new_sdk_session_id or last_usage or turn_errored or existing.get("interrupted_prompt"):
            conv_path = WEBAPP_DIR / "conversations" / f"{conv_id}.json"
            if conv_path.is_file():
                payload = json.loads(conv_path.read_text(encoding="utf-8"))
                if new_sdk_session_id:
                    payload["sdk_session_id"] = new_sdk_session_id
                if last_usage:
                    payload["last_usage"] = last_usage
                if turn_errored:
                    payload["interrupted_prompt"] = text
                else:
                    payload.pop("interrupted_prompt", None)
                payload["from"] = "telegram"
                payload["chat_id"] = chat_id
                conv_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        # P1 — Session mirror auto (rate-limited 30s)
        try:
            from session_mirror import mirror_from_file
            mirror_from_file(conv_id, WEBAPP_DIR, HUB_PATH, projects=projects)
        except Exception as e:
            print(f"[telegram] session_mirror error: {e}")
    except Exception as e:
        print(f"[telegram] persist conversation error: {e}")


@app.on_event("startup")
async def _startup_telegram():
    global TELEGRAM_DAEMON
    if not HUB_PATH:
        return
    try:
        from telegram_daemon import TelegramDaemon
        TELEGRAM_DAEMON = TelegramDaemon(HUB_PATH, on_message=_telegram_dispatch)
        await TELEGRAM_DAEMON.start()
    except Exception as e:
        print(f"[telegram] startup error: {e}")


# F24.b — Auto-cleanup uploads vecchi (>30gg) async task
UPLOADS_CLEANUP_TASK: Optional[asyncio.Task] = None


async def _uploads_cleanup_loop():
    """Run cleanup at startup + once every 24h."""
    try:
        import chat_attachments as _ca
        while True:
            try:
                res = _ca.cleanup_old_uploads(WEBAPP_DIR, max_age_days=30)
                if res.get("removed_files", 0) > 0:
                    print(f"[uploads-cleanup] removed {res['removed_files']} files, {res['removed_dirs']} dirs, freed {res['freed_bytes']//1024}KB", flush=True)
            except Exception as e:
                print(f"[uploads-cleanup] error: {e}", flush=True)
            await asyncio.sleep(86400)  # 24h
    except asyncio.CancelledError:
        return


@app.on_event("startup")
async def _startup_uploads_cleanup():
    global UPLOADS_CLEANUP_TASK
    UPLOADS_CLEANUP_TASK = asyncio.create_task(_uploads_cleanup_loop())
    print("[uploads-cleanup] task started (interval: 24h, retention: 30d)", flush=True)


@app.on_event("shutdown")
async def _shutdown_uploads_cleanup():
    global UPLOADS_CLEANUP_TASK
    if UPLOADS_CLEANUP_TASK:
        UPLOADS_CLEANUP_TASK.cancel()
        try:
            await asyncio.gather(UPLOADS_CLEANUP_TASK, return_exceptions=True)
        except Exception:
            pass


# D2 — Script runtime supervisor (monitor scripts alive)
SCRIPT_SUPERVISOR_TASK: Optional[asyncio.Task] = None


@app.on_event("startup")
async def _startup_script_supervisor():
    global SCRIPT_SUPERVISOR_TASK
    if not HUB_PATH:
        return
    try:
        from script_runtime import supervisor_loop
        SCRIPT_SUPERVISOR_TASK = asyncio.create_task(supervisor_loop(HUB_PATH, tick_sec=30))
        print("[script_runtime] supervisor task started", flush=True)
    except Exception as e:
        print(f"[script_runtime] startup error: {e}", flush=True)


@app.on_event("shutdown")
async def _shutdown_script_supervisor():
    global SCRIPT_SUPERVISOR_TASK
    if SCRIPT_SUPERVISOR_TASK:
        SCRIPT_SUPERVISOR_TASK.cancel()
        try:
            await asyncio.gather(SCRIPT_SUPERVISOR_TASK, return_exceptions=True)
        except Exception:
            pass


# Fase 18.B — Goal scheduler async task
GOAL_SCHEDULER_TASK: Optional[asyncio.Task] = None


@app.on_event("startup")
async def _startup_goal_scheduler():
    global GOAL_SCHEDULER_TASK
    if not HUB_PATH:
        return
    try:
        from goal_scheduler import goal_scheduler_loop
        GOAL_SCHEDULER_TASK = asyncio.create_task(goal_scheduler_loop(HUB_PATH))
        print("[goal_scheduler] task started", flush=True)
    except Exception as e:
        print(f"[goal_scheduler] startup error: {e}", flush=True)


@app.on_event("shutdown")
async def _shutdown_goal_scheduler():
    global GOAL_SCHEDULER_TASK
    if GOAL_SCHEDULER_TASK:
        try:
            GOAL_SCHEDULER_TASK.cancel()
            await asyncio.gather(GOAL_SCHEDULER_TASK, return_exceptions=True)
        except Exception:
            pass


@app.on_event("shutdown")
async def _shutdown_telegram():
    global TELEGRAM_DAEMON
    # F-GracefulRestart: rendi visibile nel journal COSA sta per essere interrotto —
    # un restart durante un turno attivo è lavoro perso lato utente.
    try:
        active = chat_streams.list_active()
        if active:
            detail = ", ".join(f"{s['conv_id']} ({s['tool_iter_count']} azioni)" for s in active)
            print(f"[shutdown] ATTENZIONE: {len(active)} turno/i chat attivi interrotti dal restart: {detail}")
    except Exception:
        pass
    if TELEGRAM_DAEMON:
        try:
            await TELEGRAM_DAEMON.stop()
        except Exception:
            pass


@app.get("/api/telegram/status")
async def api_telegram_status():
    if not TELEGRAM_DAEMON:
        return JSONResponse({"running": False, "enabled": False, "has_token": False, "_note": "daemon not initialized"})
    return JSONResponse(TELEGRAM_DAEMON.status())


@app.post("/api/telegram/reload")
async def api_telegram_reload(request: Request):
    """Re-read config + secrets, restart daemon."""
    global TELEGRAM_DAEMON
    _require_admin(request)
    if not TELEGRAM_DAEMON or not HUB_PATH:
        raise HTTPException(503, "daemon not initialized")
    await TELEGRAM_DAEMON.stop()
    TELEGRAM_DAEMON.reload_config()
    await TELEGRAM_DAEMON.start()
    return JSONResponse(TELEGRAM_DAEMON.status())


@app.post("/api/telegram/start")
async def api_telegram_start(request: Request):
    _require_admin(request)
    if not TELEGRAM_DAEMON:
        raise HTTPException(503, "daemon not initialized")
    await TELEGRAM_DAEMON.start()
    return JSONResponse(TELEGRAM_DAEMON.status())


@app.post("/api/telegram/stop")
async def api_telegram_stop(request: Request):
    _require_admin(request)
    if not TELEGRAM_DAEMON:
        raise HTTPException(503, "daemon not initialized")
    await TELEGRAM_DAEMON.stop()
    return JSONResponse(TELEGRAM_DAEMON.status())


@app.post("/api/telegram/config")
async def api_telegram_config(request: Request):
    """Persisti telegram block in <hub>/config.json + restart daemon."""
    global TELEGRAM_DAEMON
    _require_admin(request)   # riscrive allowed_chat_ids → chi pilota il bot
    if not HUB_PATH:
        raise HTTPException(503, "no hub")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    cfg_path = HUB_PATH / "config.json"
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}
    except Exception:
        cfg = {}
    cfg["telegram"] = {
        "enabled": bool(body.get("enabled", False)),
        "allowed_chat_ids": [int(x) for x in (body.get("allowed_chat_ids") or [])],
        "poll_interval_sec": int(body.get("poll_interval_sec", 2)),
    }
    cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # Restart daemon con nuova config
    if TELEGRAM_DAEMON:
        try:
            await TELEGRAM_DAEMON.stop()
        except Exception:
            pass
        TELEGRAM_DAEMON.reload_config()
        if cfg["telegram"]["enabled"]:
            await TELEGRAM_DAEMON.start()
    return JSONResponse(TELEGRAM_DAEMON.status() if TELEGRAM_DAEMON else {"ok": True})


# ============================================================
# Fase P-CLI — Printing Press CLI integration (cli-architect)
# ============================================================

@app.get("/api/pp/doctor")
async def api_pp_doctor():
    """Diagnose Printing Press install state."""
    try:
        from pp_integration import doctor as _pp_doctor
        return JSONResponse(_pp_doctor())
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=500)


@app.post("/api/pp/ensure")
async def api_pp_ensure():
    """Idempotent install Go + printing-press."""
    try:
        from pp_integration import ensure_installed as _pp_ensure
        result = await asyncio.get_event_loop().run_in_executor(None, _pp_ensure)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=500)


@app.get("/api/pp/list")
async def api_pp_list():
    """Lista PP CLI in library + dove installate."""
    if not HUB_PATH:
        raise HTTPException(503, "no hub")
    try:
        from pp_integration import list_installed_pp as _pp_list
        return JSONResponse(_pp_list(HUB_PATH))
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=500)


@app.post("/api/pp/generate")
async def api_pp_generate(request: Request):
    """Generate new PP CLI. Body: {name, source, source_type}."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    name = (body.get("name") or "").strip()
    source = body.get("source") or "catalog"
    source_type = body.get("source_type") or "auto"
    if not name or not name.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(400, "invalid name (alphanumeric + dash/underscore only)")
    try:
        from pp_integration import generate_cli as _pp_gen
        result = await asyncio.get_event_loop().run_in_executor(
            None, _pp_gen, name, source, source_type, 900
        )
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=500)


@app.post("/api/pp/install")
async def api_pp_install(request: Request):
    """Install PP CLI in hub or workspace."""
    if not HUB_PATH:
        raise HTTPException(503, "no hub")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    name = (body.get("name") or "").strip()
    scope = body.get("scope") or "hub"
    workspace = body.get("workspace")
    env = body.get("env") or {}
    if not name:
        raise HTTPException(400, "name required")
    if scope == "workspace" and not workspace:
        raise HTTPException(400, "workspace name required when scope=workspace")
    try:
        from pp_integration import install_pp_cli as _pp_install
        result = _pp_install(HUB_PATH, name, scope, workspace, env)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=500)


@app.post("/api/pp/uninstall")
async def api_pp_uninstall(request: Request):
    """Uninstall PP CLI from hub/workspace."""
    if not HUB_PATH:
        raise HTTPException(503, "no hub")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    name = (body.get("name") or "").strip()
    scope = body.get("scope") or "hub"
    workspace = body.get("workspace")
    delete_library = bool(body.get("delete_library", False))
    if not name:
        raise HTTPException(400, "name required")
    try:
        from pp_integration import uninstall_pp_cli as _pp_uninstall
        result = _pp_uninstall(HUB_PATH, name, scope, workspace, delete_library)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=500)


@app.post("/api/pp/catalog/search")
async def api_pp_catalog_search(request: Request):
    """Search PP catalog."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    query = (body.get("query") or "").strip()
    if not query:
        return JSONResponse({"items": []})
    try:
        from pp_integration import pp_binary
        pp = pp_binary()
        if not pp:
            return JSONResponse({"items": [], "error": "pp not installed"})
        proc = await asyncio.create_subprocess_exec(
            str(pp), "catalog", "search", query,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, _err = await asyncio.wait_for(proc.communicate(), timeout=10)
        text = out.decode("utf-8", errors="replace")
        items = []
        for ln in text.splitlines():
            ln = ln.strip()
            if not ln or "No entries" in ln or ln.startswith("="):
                continue
            if ":" in ln:
                n, desc = ln.split(":", 1)
                items.append({"name": n.strip(), "description": desc.strip()})
            else:
                items.append({"name": ln, "description": ""})
        return JSONResponse({"items": items, "raw": text[:1000]})
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=500)


# ============================================================
# main
# ============================================================

def _sync_hub_api_port(hub: Path, port: int) -> None:
    """Allinea le base-URL nel <hub>/.mcp.json alla porta REALE del server, così i tool
    hub_api/anja_hub_ops chiamano l'API giusta — non il default 8765, che può essere
    occupato da un altro servizio (es. un'altra app). Idempotente, best-effort."""
    mcp = hub / ".mcp.json"
    if not mcp.is_file():
        return
    try:
        cfg = json.loads(mcp.read_text(encoding="utf-8"))
    except Exception:
        return
    base = f"http://127.0.0.1:{port}"
    changed = False
    for name, key in (("hub_api", "ANJA_API_BASE"), ("anja_hub_ops", "ANJA_WEBAPP_URL")):
        env = (cfg.get("mcpServers", {}).get(name) or {}).get("env")
        if isinstance(env, dict) and env.get(key) and env[key] != base:
            env[key] = base
            changed = True
    if changed:
        try:
            mcp.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
            print(f"  [mcp] base-URL hub_api/hub_ops allineate a {base}")
        except Exception:
            pass


def main():
    global HUB_PATH

    parser = argparse.ArgumentParser(description="anja Mission Control server")
    parser.add_argument("--hub", required=True, help="path to anja hub directory")
    parser.add_argument("--port", type=int, default=8765, help="server port (default 8765)")
    parser.add_argument("--host", default="127.0.0.1", help="server host (default 127.0.0.1)")
    args = parser.parse_args()

    hub = Path(args.hub).resolve()
    if not hub.is_dir():
        sys.exit(f"ERROR: hub not found: {hub}")
    if not (hub / "config" / "projects.json").is_file():
        sys.exit(f"ERROR: not a anja hub (no config/projects.json): {hub}")

    HUB_PATH = hub
    os.environ["ANJA_HUB"] = str(hub)
    os.environ["ANJA_WEBAPP_PORT"] = str(args.port)  # il manifesto hub_api usa la porta reale (no più 8765 hardcoded)
    _sync_hub_api_port(hub, args.port)  # auto-allinea ANJA_API_BASE/ANJA_WEBAPP_URL nel .mcp.json alla porta reale

    # F-HubKnowledge — garantisce il knowledge layer proprio dell'hub (<hub>/.anjawiki/).
    # Idempotente: migra trasparentemente gli hub creati prima di questa feature.
    try:
        scripts_dir = str(ANJA_HUB_DIR / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from init_hub import ensure_hub_wiki
        if ensure_hub_wiki(hub):
            print(f"[anja] hub knowledge layer creato: {hub}/.anjawiki/")
    except Exception as e:
        print(f"[anja] ensure_hub_wiki skipped: {e}")

    # Load custom secrets into os.environ so Claude SDK MCP children inherit them.
    # Il file <hub>/.secrets.env è la source-of-truth: override esplicito così un
    # valore stale già presente nell'environment (es. ANJA_WEBHOOK_TOKEN) non vince.
    try:
        for k, v in _load_secrets_dict().items():
            os.environ[k] = v
    except Exception as e:
        print(f"[anja] failed to load secrets at boot: {e}")

    print(f"anja Mission Control")
    print(f"  hub:    {hub}")
    print(f"  serve:  http://{args.host}:{args.port}")
    print(f"  static: {STATIC_DIR}")
    print()

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


# =================================================================
# F-RawUI — Sources & Ingest API
# =================================================================

import urllib.parse  # noqa: E402 — used below for url decoding

_SOURCE_SAFE_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
_SOURCE_PREVIEWABLE_EXT = {
    "md": "text/markdown", "txt": "text/plain", "rst": "text/plain", "log": "text/plain",
    "json": "application/json", "yaml": "text/yaml", "yml": "text/yaml",
    "html": "text/html", "htm": "text/html",
    "pdf": "application/pdf",
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
    "webp": "image/webp", "svg": "image/svg+xml",
}


def _resolve_scope_root(scope: str, target: str = "") -> tuple[Optional[Path], str]:
    """Risolve la root del scope sources (project ext o workspace internal).
    Returns (path, error_msg)."""
    if not HUB_PATH:
        return None, "hub not configured"
    if scope == "hub":
        return HUB_PATH, ""  # knowledge layer proprio dell'hub: <hub>/.anjawiki/
    if scope == "project" and target:
        p = resolve_project_path(target, HUB_PATH)
        return (p, "") if p else (None, f"project '{target}' not found")
    if scope == "workspace" and target:
        wsp = HUB_PATH / "workspaces" / target
        if not wsp.is_dir():
            return None, f"workspace '{target}' not found"
        return (wsp.resolve() if wsp.is_symlink() else wsp), ""
    return None, f"invalid scope/target: scope={scope!r} target={target!r}"


def _sources_root(scope: str, target: str) -> tuple[Optional[Path], str]:
    """Return <root>/.anjawiki/raw/ for the scope. Crea se manca."""
    root, err = _resolve_scope_root(scope, target)
    if err:
        return None, err
    raw = root / ".anjawiki" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    return raw, ""


def _validate_safe_name(name: str, label: str) -> None:
    if not _SOURCE_SAFE_NAME.match(name):
        raise HTTPException(400, f"{label} '{name}' not safe (use [a-zA-Z0-9._-], no leading dot)")


@app.get("/api/sources/list")
async def api_sources_list(request: Request, scope: str = "project", target: str = ""):
    """Lista topic + file in `<scope-root>/.anjawiki/raw/`.

    scope: 'project' (external registered) o 'workspace' (internal).
    target: nome del project/workspace.

    Response:
    {topics: [{name: str, count: int, total_size: int,
               files: [{name, size, mtime, ext, mime, previewable: bool}]}]}
    """
    _require_target_access(request, scope, target)
    raw, err = _sources_root(scope, target)
    if err:
        raise HTTPException(404, err)
    topics = []
    for entry in sorted(raw.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        files = []
        for f in sorted(entry.rglob("*")):
            if not f.is_file() or f.name.startswith("."):
                continue
            try:
                st = f.stat()
                rel_to_topic = f.relative_to(entry)
                ext = f.suffix.lstrip(".").lower()
                files.append({
                    "name": str(rel_to_topic),
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                    "ext": ext,
                    "mime": _SOURCE_PREVIEWABLE_EXT.get(ext, "application/octet-stream"),
                    "previewable": ext in _SOURCE_PREVIEWABLE_EXT,
                })
            except Exception:
                continue
        topics.append({
            "name": entry.name,
            "count": len(files),
            "total_size": sum(f["size"] for f in files),
            "files": files,
        })
    return JSONResponse({"scope": scope, "target": target, "topics": topics})


@app.post("/api/sources/add")
async def api_sources_add(request: Request):
    """Aggiunge una fonte. Body JSON:
      {scope, target, topic, mode: 'url'|'inline', url?, filename?, content_b64?, content_text?}

    mode='url': scarica via httpx, deduce filename da Content-Disposition o URL path.
    mode='inline': filename + content_b64 (base64) o content_text (plain) richiesti.

    Salva in `<raw>/<topic>/<filename>`. Crea topic dir se manca. Sanitize name.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    scope = body.get("scope") or "project"
    target = body.get("target") or ""
    _require_target_access(request, scope, target)
    topic = (body.get("topic") or "misc").strip()
    mode = body.get("mode") or "url"

    raw, err = _sources_root(scope, target)
    if err:
        raise HTTPException(404, err)
    _validate_safe_name(topic, "topic")
    topic_dir = raw / topic
    topic_dir.mkdir(parents=True, exist_ok=True)

    if mode == "url":
        url = (body.get("url") or "").strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            raise HTTPException(400, "url must start with http(s)://")
        # SSRF guard: valida+pinna l'IP e ri-controlla OGNI redirect a mano
        # (follow_redirects=False) — un URL pubblico può rimbalzare su un IP interno.
        ok, err = _ssrf_check(url)
        if err:
            raise HTTPException(400, f"URL rejected: {err}")
        try:
            import httpx
            with httpx.Client(follow_redirects=False, timeout=60) as cli:
                cur = url
                for _ in range(5):
                    host, ip = ok
                    with _pin_dns(host, ip):   # connette all'IP validato (anti-rebinding)
                        r = cli.get(cur, headers={"User-Agent": "AnjaHub/1.0 (+sources-ui)"})
                    if r.is_redirect and r.headers.get("location"):
                        cur = str(r.next_request.url) if r.next_request else r.headers["location"]
                        ok, err = _ssrf_check(cur)
                        if err:
                            raise HTTPException(400, f"redirect rejected: {err}")
                        continue
                    break
                r.raise_for_status()
                content = r.content
                ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"fetch failed: {type(e).__name__}: {e}")
        # Filename derivation
        fname = body.get("filename") or ""
        if not fname:
            cd = r.headers.get("content-disposition", "")
            m = re.search(r'filename\*?=["\']?(?:UTF-8\'\')?([^;"\']+)', cd, re.I)
            if m:
                fname = urllib.parse.unquote(m.group(1).strip())
            else:
                from urllib.parse import urlparse
                path = urlparse(url).path
                fname = path.rsplit("/", 1)[-1] or "index"
                if "." not in fname:
                    # guess ext from content-type
                    ext_map = {"text/html": ".html", "application/pdf": ".pdf",
                               "text/plain": ".txt", "application/json": ".json",
                               "text/markdown": ".md", "image/png": ".png",
                               "image/jpeg": ".jpg", "image/gif": ".gif"}
                    fname += ext_map.get(ctype, ".bin")
        fname = re.sub(r"[^a-zA-Z0-9._-]", "_", fname)[:200]
        target_file = topic_dir / fname
        # Evita overwrite silente: aggiungi suffix se esiste
        if target_file.exists():
            stem = target_file.stem; ext = target_file.suffix; i = 1
            while (topic_dir / f"{stem}-{i}{ext}").exists():
                i += 1
            target_file = topic_dir / f"{stem}-{i}{ext}"
        target_file.write_bytes(content)
        return JSONResponse({
            "status": "saved", "scope": scope, "target": target, "topic": topic,
            "filename": target_file.name, "size": len(content), "source_url": url,
        })

    if mode == "inline":
        fname = (body.get("filename") or "").strip()
        if not fname:
            raise HTTPException(400, "inline mode requires 'filename'")
        fname = re.sub(r"[^a-zA-Z0-9._-]", "_", fname)[:200]
        target_file = topic_dir / fname
        if target_file.exists():
            stem = target_file.stem; ext = target_file.suffix; i = 1
            while (topic_dir / f"{stem}-{i}{ext}").exists():
                i += 1
            target_file = topic_dir / f"{stem}-{i}{ext}"
        if "content_b64" in body:
            import base64 as _b64
            try:
                target_file.write_bytes(_b64.b64decode(body["content_b64"]))
            except Exception as e:
                raise HTTPException(400, f"invalid base64: {e}")
        elif "content_text" in body:
            target_file.write_text(body["content_text"], encoding="utf-8")
        else:
            raise HTTPException(400, "inline mode requires content_b64 or content_text")
        return JSONResponse({
            "status": "saved", "scope": scope, "target": target, "topic": topic,
            "filename": target_file.name, "size": target_file.stat().st_size,
        })

    raise HTTPException(400, f"unknown mode '{mode}' (expected 'url' or 'inline')")


@app.delete("/api/sources/file")
async def api_sources_delete(scope: str, target: str, topic: str, filename: str, request: Request):
    """Elimina un file raw. Query: scope, target, topic, filename."""
    _require_target_access(request, scope, target)
    raw, err = _sources_root(scope, target)
    if err:
        raise HTTPException(404, err)
    _validate_safe_name(topic, "topic")
    # filename può contenere `/` per subdir, ma niente `..`
    if ".." in filename or filename.startswith("/"):
        raise HTTPException(400, "filename traversal denied")
    f = (raw / topic / filename).resolve()
    try:
        f.relative_to((raw / topic).resolve())
    except ValueError:
        raise HTTPException(400, "filename traversal denied")
    if not f.is_file():
        raise HTTPException(404, f"file not found: {topic}/{filename}")
    f.unlink()
    # Rimuovi topic dir se vuoto
    try:
        (raw / topic).rmdir()
    except OSError:
        pass
    return JSONResponse({"status": "deleted", "scope": scope, "target": target, "topic": topic, "filename": filename})


@app.get("/api/sources/file")
async def api_sources_file(scope: str, target: str, topic: str, filename: str,
                           request: Request, download: bool = False):
    """Serve file raw (per preview iframe / download). Query: scope, target, topic, filename."""
    _require_target_access(request, scope, target)
    raw, err = _sources_root(scope, target)
    if err:
        raise HTTPException(404, err)
    if ".." in filename or filename.startswith("/"):
        raise HTTPException(400, "filename traversal denied")
    f = (raw / topic / filename).resolve()
    try:
        f.relative_to((raw / topic).resolve())
    except ValueError:
        raise HTTPException(400, "filename traversal denied")
    if not f.is_file():
        raise HTTPException(404, f"file not found: {topic}/{filename}")
    ext = f.suffix.lstrip(".").lower()
    mime = _SOURCE_PREVIEWABLE_EXT.get(ext, "application/octet-stream")
    # Anti stored-XSS: contenuto utente (fetch url / inline) non deve MAI essere servito
    # come tipo attivo same-origin — un .html/.svg eseguirebbe JS nell'origine dell'app.
    if ext in ("html", "htm", "svg", "xml", "xhtml"):
        mime = "text/plain; charset=utf-8"
    headers = {"X-Content-Type-Options": "nosniff"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{f.name}"'
        mime = "application/octet-stream"
    return FileResponse(str(f), media_type=mime, headers=headers)


@app.post("/api/sources/ingest-now")
async def api_sources_ingest_now(request: Request):
    """Avvia l'ingest reale della fonte in background: spawna `ingest_source_bg.py`
    detached, che sintetizza via LLM una source page nel wiki + aggiorna index/log.
    Body: {scope, target, topic, filename}. Ritorna subito (status: started);
    la UI segue via GET /api/sources/ingest-status."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    scope = body.get("scope") or "project"
    target = body.get("target") or ""
    _require_target_access(request, scope, target)
    topic = (body.get("topic") or "").strip()
    filename = (body.get("filename") or "").strip()
    scope_root, err = _resolve_scope_root(scope, target)
    if err:
        raise HTTPException(404, err)
    _validate_safe_name(topic, "topic")
    raw_file = (scope_root / ".anjawiki" / "raw" / topic / filename).resolve()
    try:
        raw_file.relative_to((scope_root / ".anjawiki" / "raw" / topic).resolve())
    except ValueError:
        raise HTTPException(400, "filename traversal denied")
    if not raw_file.is_file():
        raise HTTPException(404, f"file not found: {topic}/{filename}")

    script = ANJA_HUB_DIR / "scripts" / "ingest_source_bg.py"
    if not script.is_file():
        raise HTTPException(500, "ingest_source_bg.py not found")
    try:
        subprocess.Popen(
            [sys.executable, str(script), "--scope-root", str(scope_root),
             "--topic", topic, "--filename", filename],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=os.environ.copy(), start_new_session=True,
        )
    except Exception as e:
        raise HTTPException(500, f"failed to spawn ingest: {e}")
    return JSONResponse({"status": "started", "scope": scope, "target": target,
                         "topic": topic, "filename": filename})


@app.get("/api/wiki/pages")
async def api_wiki_pages(scope: str = "project", target: str = ""):
    """Lista le pagine generate del wiki (`<scope-root>/.anjawiki/wiki/<kind>/*.md`).
    Usato per mostrare la conoscenza prodotta dall'ingest (sources/entities/concepts)."""
    scope_root, err = _resolve_scope_root(scope, target)
    if err:
        raise HTTPException(404, err)
    wiki = scope_root / ".anjawiki" / "wiki"
    title_re = re.compile(r'^title:\s*"?([^"\n]+?)"?\s*$', re.M)
    out = []
    for kind in ("sources", "entities", "concepts", "analysis"):
        d = wiki / kind
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            title = f.stem
            try:
                head = f.read_text(encoding="utf-8")[:500]
                m = title_re.search(head)
                if m:
                    title = m.group(1)
            except Exception:
                pass
            try:
                mtime = f.stat().st_mtime
            except Exception:
                mtime = 0
            out.append({"kind": kind, "slug": f.stem, "path": f"{kind}/{f.name}",
                        "title": title, "mtime": mtime})
    out.sort(key=lambda p: p["mtime"], reverse=True)
    return JSONResponse({"pages": out})


@app.get("/api/wiki/page")
async def api_wiki_page(scope: str = "project", target: str = "", path: str = ""):
    """Contenuto markdown di una pagina wiki. `path` relativo a `wiki/` (es. sources/x.md)."""
    scope_root, err = _resolve_scope_root(scope, target)
    if err:
        raise HTTPException(404, err)
    wiki = (scope_root / ".anjawiki" / "wiki").resolve()
    f = (wiki / path).resolve()
    try:
        f.relative_to(wiki)
    except ValueError:
        raise HTTPException(400, "path traversal denied")
    if not f.is_file() or f.suffix != ".md":
        raise HTTPException(404, "page not found")
    return PlainTextResponse(f.read_text(encoding="utf-8"))


@app.get("/api/sources/ingest-status")
async def api_sources_ingest_status(request: Request, scope: str = "project", target: str = ""):
    """Stato ingest per scope (mappa topic/filename → {status, source, error}).
    Letto da `<scope-root>/.anjawiki/_ingest_status.json`."""
    _require_target_access(request, scope, target)
    scope_root, err = _resolve_scope_root(scope, target)
    if err:
        raise HTTPException(404, err)
    f = scope_root / ".anjawiki" / "_ingest_status.json"
    if not f.is_file():
        return JSONResponse({})
    try:
        return JSONResponse(json.loads(f.read_text(encoding="utf-8")))
    except Exception:
        return JSONResponse({})


@app.post("/api/sources/add-crawl")
async def api_sources_add_crawl(request: Request):
    """Crawl shallow di una documentazione multi-pagina: scarica la seed + le
    sotto-pagine interne (stesso path-prefix, fino a max_pages) come file raw,
    opzionalmente le ingerisce. Per doc senza sitemap (Sphinx/MkDocs/RTD).
    Body: {scope, target, topic, url, max_pages?, ingest?}. Spawna in background;
    segui via GET /api/sources/crawl-status."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")
    scope = body.get("scope") or "project"
    target = body.get("target") or ""
    _require_target_access(request, scope, target)
    topic = (body.get("topic") or "").strip()
    url = (body.get("url") or "").strip()
    max_pages = max(1, min(int(body.get("max_pages", 25)), 100))
    ingest = bool(body.get("ingest", False))
    scope_root, err = _resolve_scope_root(scope, target)
    if err:
        raise HTTPException(404, err)
    _validate_safe_name(topic, "topic")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(400, "url must start with http(s)://")
    script = ANJA_HUB_DIR / "scripts" / "crawl_docs_bg.py"
    if not script.is_file():
        raise HTTPException(500, "crawl_docs_bg.py not found")
    cmd = [sys.executable, str(script), "--scope-root", str(scope_root),
           "--topic", topic, "--seed-url", url, "--max-pages", str(max_pages)]
    if ingest:
        cmd.append("--ingest")
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env=os.environ.copy(), start_new_session=True)
    except Exception as e:
        raise HTTPException(500, f"failed to spawn crawl: {e}")
    return JSONResponse({"status": "started", "scope": scope, "target": target,
                         "topic": topic, "url": url, "max_pages": max_pages, "ingest": ingest})


@app.get("/api/sources/crawl-status")
async def api_sources_crawl_status(request: Request, scope: str = "project", target: str = ""):
    """Stato del crawl: {status, seed, total, fetched, ingested, error}.
    Letto da `<scope-root>/.anjawiki/_crawl_status.json`."""
    _require_target_access(request, scope, target)
    scope_root, err = _resolve_scope_root(scope, target)
    if err:
        raise HTTPException(404, err)
    f = scope_root / ".anjawiki" / "_crawl_status.json"
    if not f.is_file():
        return JSONResponse({})
    try:
        return JSONResponse(json.loads(f.read_text(encoding="utf-8")))
    except Exception:
        return JSONResponse({})


@app.get("/api/sources/pending")
async def api_sources_pending(request: Request, scope: str = "project", target: str = ""):
    """Pending queue del auto_ingest_daemon (file rilevati ma non ancora ingeriti).
    Legge `<scope-root>/.anjawiki/_pending_ingest.json` se presente.
    """
    _require_target_access(request, scope, target)
    root, err = _resolve_scope_root(scope, target)
    if err:
        raise HTTPException(404, err)
    f = root / ".anjawiki" / "_pending_ingest.json"
    if not f.is_file():
        return JSONResponse({"files": [], "last_updated": 0})
    try:
        return JSONResponse(json.loads(f.read_text(encoding="utf-8")))
    except Exception as e:
        raise HTTPException(500, f"failed to read pending queue: {e}")


if __name__ == "__main__":
    main()
