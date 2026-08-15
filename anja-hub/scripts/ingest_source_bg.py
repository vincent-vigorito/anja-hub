#!/usr/bin/env python3
"""ingest_source_bg.py — ingest in background di una fonte raw nel wiki.

Spawnato detached da `POST /api/sources/ingest-now`. NON blocca la request.
Legge il file raw, spawna `claude -p` per sintetizzare una **source page**
markdown, la scrive in `<wiki>/sources/<slug>.md`, aggiorna `index.md` +
`log.md`, e registra lo stato in `<scope-root>/.anjawiki/_ingest_status.json`
(per il polling della UI).

Usage:
  python3 ingest_source_bg.py --scope-root <dir> --topic <t> --filename <f> [--model haiku]

Env opzionale:
  ANJA_CLAUDE_BIN     — path al binario claude (default: risolto)
  ANJA_INGEST_MODEL   — haiku|sonnet|opus (default 'haiku')
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import injection_guard
except ImportError:
    injection_guard = None

MAX_CONTENT_CHARS = 40000


def _resolve_claude_bin(explicit: str | None = None) -> str | None:
    """Path assoluto di `claude`. Gli hook/spawn detached hanno PATH minimale
    senza ~/.local/bin → risolviamo esplicitamente."""
    if explicit and explicit != "claude":
        return explicit
    found = shutil.which("claude")
    if found:
        return found
    for c in (Path.home() / ".local/bin/claude", Path("/usr/local/bin/claude"),
              Path("/opt/homebrew/bin/claude"), Path.home() / ".claude/local/claude",
              Path("/usr/bin/claude")):
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


def _strip_html(text: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-z]+;", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def _slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    return s.strip("-") or "source"


def _log(msg: str, log_path: Path | None) -> None:
    if not log_path:
        return
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}\n")
    except Exception:
        pass


def _set_status(scope_root: Path, filename: str, topic: str, **fields) -> None:
    """Upsert dello stato ingest in <scope-root>/.anjawiki/_ingest_status.json."""
    f = scope_root / ".anjawiki" / "_ingest_status.json"
    data = {}
    if f.is_file():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    key = f"{topic}/{filename}"
    entry = data.get(key, {})
    entry.update({"topic": topic, "filename": filename,
                  "updated": datetime.now().isoformat(timespec="seconds"), **fields})
    data[key] = entry
    try:
        f.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception:
        pass


def _append_index_source(wiki: Path, slug: str, summary: str) -> None:
    idx = wiki / "index.md"
    line = f"- [[{slug}]] — {summary}"
    if not idx.is_file():
        idx.write_text(f"# Index\n\n## Sources\n\n{line}\n", encoding="utf-8")
        return
    text = idx.read_text(encoding="utf-8")
    if f"[[{slug}]]" in text:
        return  # già presente
    if "## Sources" in text:
        text = re.sub(r"(## Sources[ \t]*\n)", r"\1" + line + "\n", text, count=1)
    else:
        text = text.rstrip() + "\n\n## Sources\n\n" + line + "\n"
    idx.write_text(text, encoding="utf-8")


def _append_log(wiki: Path, title: str) -> None:
    log = wiki / "log.md"
    entry = f"\n## [{date.today().isoformat()}] ingest | {title}\n"
    if log.is_file():
        log.write_text(log.read_text(encoding="utf-8").rstrip() + "\n" + entry, encoding="utf-8")
    else:
        log.write_text(f"# Log\n{entry}", encoding="utf-8")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--scope-root", required=True, help="root dello scope (hub o workspace/project dir)")
    p.add_argument("--topic", required=True)
    p.add_argument("--filename", required=True)
    p.add_argument("--model", default=os.environ.get("ANJA_INGEST_MODEL", "haiku"))
    p.add_argument("--claude-bin", default=os.environ.get("ANJA_CLAUDE_BIN", "claude"))
    args = p.parse_args()

    scope_root = Path(args.scope_root).resolve()
    aw = scope_root / ".anjawiki"
    wiki = aw / "wiki"
    raw_file = aw / "raw" / args.topic / args.filename
    log_path = aw / ".bg-ingest.log"
    topic, filename = args.topic, args.filename

    _log(f"STARTED {topic}/{filename} (pid={os.getpid()})", log_path)
    _set_status(scope_root, filename, topic, status="ingesting", source=None, error=None)

    try:
        if not raw_file.is_file():
            _set_status(scope_root, filename, topic, status="error", error="raw file not found")
            _log(f"ERROR raw not found: {raw_file}", log_path)
            sys.exit(2)

        claude = _resolve_claude_bin(args.claude_bin)
        if not claude:
            _set_status(scope_root, filename, topic, status="error", error="claude binary not found")
            _log("ERROR claude bin not found", log_path)
            sys.exit(3)

        raw = raw_file.read_text(encoding="utf-8", errors="replace")
        ext = raw_file.suffix.lower()
        content = _strip_html(raw) if ext in (".html", ".htm") else raw
        truncated = len(content) > MAX_CONTENT_CHARS
        content = content[:MAX_CONTENT_CHARS]

        # F-Security-Injection: la fonte è contenuto esterno NON fidato. Neutralizza
        # caratteri nascosti, logga eventuali pattern di prompt-injection e racchiudi
        # in un blocco sentinella con nudge anti-injection prima di passarlo all'LLM.
        if injection_guard is not None:
            source_block, guard = injection_guard.guard_untrusted(content, f"{topic}/{filename}")
            if guard["findings"]:
                labels = [f["label"] for f in guard["findings"]]
                _set_status(scope_root, filename, topic, injection_flags=labels)
                _log(f"INJECTION-GUARD {topic}/{filename}: high={guard['high']} "
                     f"medium={guard['medium']} low={guard['low']} "
                     f"invisible_removed={guard['invisible_removed']} labels={labels}", log_path)
        else:
            source_block = f"---\n{content}\n---"

        prompt = (
            "Sei un knowledge ingestor per un wiki personale. Leggi la FONTE e produci "
            "il CORPO markdown di una source page (NIENTE frontmatter, NIENTE preambolo, "
            "NIENTE ```). Struttura esatta in italiano:\n\n"
            "> TL;DR in 3-5 righe.\n\n"
            "## Punti chiave\n- bullet concreti e fedeli alla fonte\n\n"
            "## Entità e concetti\n- lista di nomi candidati come `[[nome-in-kebab-case]]` "
            "(tecnologie, comandi, concetti chiave menzionati)\n\n"
            f"FONTE ({topic}/{filename}):\n{source_block}"
        )

        try:
            result = subprocess.run(
                [claude, "-p", prompt, "--model", args.model],
                capture_output=True, timeout=240, text=True,
            )
        except subprocess.TimeoutExpired:
            _set_status(scope_root, filename, topic, status="error", error="claude timeout 240s")
            _log("ERROR claude timeout", log_path)
            sys.exit(4)

        if result.returncode != 0:
            err = (result.stderr or "")[:300]
            _set_status(scope_root, filename, topic, status="error", error=f"claude rc={result.returncode}: {err}")
            _log(f"ERROR claude rc={result.returncode} {err}", log_path)
            sys.exit(result.returncode)

        body = result.stdout.strip()
        if not body:
            _set_status(scope_root, filename, topic, status="error", error="empty LLM output")
            _log("ERROR empty output", log_path)
            sys.exit(5)

        title = f"{topic}: {filename}"
        slug = f"{date.today().isoformat()}-{_slugify(topic)}-{_slugify(raw_file.stem)}"
        # TL;DR per l'index: prima riga di contenuto reale (salta label "TL;DR" e heading)
        def _is_filler(line: str) -> bool:
            c = line.lstrip("> #").strip().lower()
            return (not c) or c in ("tl;dr", "tldr") or line.lstrip().startswith("#")
        first = next((l.lstrip("> ").strip() for l in body.splitlines() if not _is_filler(l)), title)
        summary = first[:120]

        note = "\n\n> _(fonte troncata per lunghezza durante l'ingest)_" if truncated else ""
        page = (
            f"---\ntitle: \"{title}\"\ntype: source\ncreated: \"{date.today().isoformat()}\"\n"
            f"updated: \"{date.today().isoformat()}\"\ntags: [{_slugify(topic)}]\n"
            f"source_path: ../../raw/{topic}/{filename}\n---\n\n"
            f"# {title}\n\n{body}{note}\n"
        )
        sources_dir = wiki / "sources"
        sources_dir.mkdir(parents=True, exist_ok=True)
        (sources_dir / f"{slug}.md").write_text(page, encoding="utf-8")

        _append_index_source(wiki, slug, summary)
        _append_log(wiki, title)
        _set_status(scope_root, filename, topic, status="done", source=slug, error=None)
        _log(f"OK ingested → sources/{slug}.md ({len(body)} chars, model={args.model})", log_path)
        sys.exit(0)
    except Exception as e:
        _set_status(scope_root, filename, topic, status="error", error=str(e)[:300])
        _log(f"FATAL {e}\n{traceback.format_exc()}", log_path)
        sys.exit(99)


if __name__ == "__main__":
    main()
