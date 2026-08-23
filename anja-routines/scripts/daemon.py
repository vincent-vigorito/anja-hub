#!/usr/bin/env python3
"""
daemon.py — always-on loop che esegue routine anja schedulate.

Polling ogni POLL_INTERVAL (30s default). Per ogni routine enabled,
calcola se "should fire" usando croniter contro `last_fire` (in routines.json),
e se sì spawna `runner.py --name <name>` come subprocess separato.

Limite concorrenza: MAX_CONCURRENT (5 default). I run in eccesso vengono saltati
con warning (la prossima iter li ricontrolla).

Usage:
    python3 daemon.py [--hub /path] [--once] [--interval 30] [--max-concurrent 5]

Env:
    ANJA_HUB   override hub path
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from routine_registry import (
    find_hub_root,
    list_routines,
    load_state,
    save_state,
    runs_dir,
)


POLL_INTERVAL_DEFAULT = 30
MAX_CONCURRENT_DEFAULT = 5

# track running subprocesses {name: Popen}
_RUNNING: dict = {}
_STOP = False


def _log(msg: str) -> None:
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}", flush=True)


def _handle_signal(signum, frame):
    global _STOP
    _log(f"signal {signum} received, stopping after current cycle")
    _STOP = True


def _should_fire(routine: dict, now: datetime, last_fire_iso: str = "") -> bool:
    """Ritorna True se la routine deve partire ora.
    Usa croniter se disponibile; fallback grezzo per sicurezza (no fire)."""
    yaml_obj = routine["yaml"] or {}
    if not routine.get("valid"):
        return False
    schedule = yaml_obj.get("schedule")
    if not schedule:
        return False
    if not routine["state"].get("enabled", True):
        return False

    try:
        from croniter import croniter
    except ImportError:
        # senza croniter non possiamo schedulare in modo serio.
        # avvisiamo una sola volta in routines.json
        return False

    # cron schedule è interpretato in LOCAL time (utente scrive "19:36" pensando local).
    # Convertiamo now e base in local-naive per allineare con croniter.
    now_local = now.astimezone().replace(tzinfo=None) if now.tzinfo else now

    if last_fire_iso:
        try:
            base_dt = datetime.fromisoformat(last_fire_iso.replace("Z", "+00:00"))
            base_local = base_dt.astimezone().replace(tzinfo=None)
        except Exception:
            base_local = now_local
    else:
        # mai eseguita → base = creation_time (mtime file yaml) in local-naive
        try:
            yaml_path = routine.get("file") or routine.get("path") or routine.get("yaml_path")
            if yaml_path:
                mtime = Path(yaml_path).stat().st_mtime
                base_local = datetime.fromtimestamp(mtime)  # naive local
            else:
                base_local = now_local
        except Exception:
            base_local = now_local

    try:
        itr = croniter(schedule, base_local)
        next_fire = itr.get_next(datetime)
        # croniter ritorna naive local quando base è naive
        if next_fire.tzinfo is not None:
            next_fire = next_fire.astimezone().replace(tzinfo=None)
        return next_fire <= now_local
    except Exception:
        return False


def _reap_finished() -> None:
    """Rimuove dalla mappa _RUNNING i subprocess terminati."""
    done = []
    for name, proc in _RUNNING.items():
        if proc.poll() is not None:
            done.append(name)
    for name in done:
        proc = _RUNNING.pop(name)
        rc = proc.returncode
        _log(f"  ◀ '{name}' finished (rc={rc})")


def _spawn(routine_name: str, hub: Path, extra_args: list | None = None) -> bool:
    """Spawna runner.py --name <routine_name> come subprocess detached."""
    runner = Path(__file__).parent / "runner.py"
    py = sys.executable

    rd = runs_dir(hub)
    rd.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    stdout_path = rd / f"{routine_name}-{ts}.stdout.log"

    f = open(stdout_path, "wb")
    env = os.environ.copy()
    env["ANJA_HUB"] = str(hub)
    proc = subprocess.Popen(
        [py, str(runner), "--name", routine_name] + (extra_args or []),
        stdout=f,
        stderr=subprocess.STDOUT,
        env=env,
        cwd=str(hub),
    )
    _RUNNING[routine_name] = proc
    _log(f"  ▶ spawned '{routine_name}' pid={proc.pid} → {stdout_path.name}")
    return True


def _fire_events(hub: Path, routines: list, max_concurrent: int = MAX_CONCURRENT_DEFAULT) -> int:
    """F-EventTriggers: consuma i fire-file scritti da POST /hooks/<name> (webapp).

    `<hub>/routines/.fire/<name>-<ts>-<hash>.json` → rename a `.run` (anti double-fire)
    → spawn runner --event-file. Un solo run per routine alla volta (come i cron);
    i file in eccesso aspettano il ciclo dopo. Stale `.run` (>24h, runner morto
    prima di cancellarli) vengono ripuliti."""
    fire_dir = hub / "routines" / ".fire"
    if not fire_dir.is_dir():
        return 0
    known = {r["name"] for r in routines
             if r.get("valid") and r.get("state", {}).get("enabled", True)}
    fired = 0
    now = time.time()
    for fp in sorted(fire_dir.glob("*.json.run")):
        if now - fp.stat().st_mtime > 86400:
            fp.unlink(missing_ok=True)
            _log(f"  ✗ stale event file removed: {fp.name}")
    for fp in sorted(fire_dir.glob("*.json")):
        try:
            name = json.loads(fp.read_text(encoding="utf-8")).get("routine", "")
        except Exception:
            fp.unlink(missing_ok=True)
            continue
        if name not in known:
            fp.unlink(missing_ok=True)
            _log(f"  ✗ event for unknown routine '{name}' dropped")
            continue
        if name in _RUNNING or len(_RUNNING) >= max_concurrent:
            continue
        run_path = fp.with_suffix(fp.suffix + ".run")
        try:
            fp.rename(run_path)
        except OSError:
            continue
        _spawn(name, hub, extra_args=["--event-file", str(run_path)])
        fired += 1
    return fired


def loop(hub: Path, interval: int, max_concurrent: int, once: bool = False) -> None:
    _log(f"daemon started — hub={hub} interval={interval}s max_concurrent={max_concurrent}")

    while not _STOP:
        cycle_start = time.time()
        _reap_finished()

        try:
            routines = list_routines(hub)
        except Exception as e:
            _log(f"ERROR loading routines: {e}")
            routines = []

        state = load_state(hub)
        now = datetime.now(timezone.utc)
        fired = 0

        for r in routines:
            name = r["name"]
            if name in _RUNNING:
                continue
            if len(_RUNNING) >= max_concurrent:
                _log(f"  ⚠ max_concurrent reached ({max_concurrent}), skipping further fires this cycle")
                break
            last_fire = state.get(name, {}).get("last_fire", "")
            if _should_fire(r, now, last_fire):
                _spawn(name, hub)
                # update last_fire immediately to avoid re-firing
                entry = state.get(name, {"enabled": True})
                entry["last_fire"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                state[name] = entry
                fired += 1

        if fired:
            save_state(state, hub)

        _fire_events(hub, routines, max_concurrent)

        if once:
            _log("--once mode → exiting after one cycle")
            break

        elapsed = time.time() - cycle_start
        remaining = max(1, interval - elapsed)
        time.sleep(remaining)

    # cleanup: aspetta i subprocess (max 30s)
    if _RUNNING:
        _log(f"waiting for {len(_RUNNING)} running routines to finish (max 30s)…")
        deadline = time.time() + 30
        while _RUNNING and time.time() < deadline:
            _reap_finished()
            time.sleep(1)
    _log("daemon stopped")


def _ensure_user_path() -> None:
    """Sotto systemd (user) il PATH è /usr/local/sbin:...:/usr/bin — senza ~/.local/bin,
    dove vivono le CLI usate dalle routine (anja-cli, giv, grok). Prepend se manca."""
    local_bin = Path.home() / ".local" / "bin"
    parts = os.environ.get("PATH", "").split(":")
    if str(local_bin) not in parts and local_bin.is_dir():
        os.environ["PATH"] = f"{local_bin}:{os.environ.get('PATH', '')}"


def main():
    p = argparse.ArgumentParser(description="anja routines daemon")
    p.add_argument("--hub", help="hub path (else ANJA_HUB env or auto)")
    p.add_argument("--interval", type=int, default=POLL_INTERVAL_DEFAULT,
                   help=f"poll interval seconds (default {POLL_INTERVAL_DEFAULT})")
    p.add_argument("--max-concurrent", type=int, default=MAX_CONCURRENT_DEFAULT,
                   help=f"max concurrent runs (default {MAX_CONCURRENT_DEFAULT})")
    p.add_argument("--once", action="store_true", help="run a single cycle then exit")
    args = p.parse_args()

    hub = Path(args.hub).expanduser().resolve() if args.hub else find_hub_root()
    _ensure_user_path()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    loop(hub, args.interval, args.max_concurrent, once=args.once)


if __name__ == "__main__":
    main()
