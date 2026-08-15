#!/usr/bin/env python3
"""crawl_docs_bg.py — crawl shallow di una documentazione multi-pagina nel wiki.

Spawnato detached da `POST /api/sources/add-crawl`. Scarica la pagina seed,
estrae i link interni (stesso dominio, sotto il path-prefix del seed), scarica
fino a `max_pages` sotto-pagine salvandole come file raw nel topic. Con `--ingest`
ingerisce ciascuna (spawna `ingest_source_bg.py` in sequenza).

Pensato per doc Sphinx/MkDocs/ReadTheDocs senza sitemap (es. Incus docs).

Usage:
  python3 crawl_docs_bg.py --scope-root <dir> --topic <t> --seed-url <url>
      [--max-pages 25] [--ingest] [--depth 1]

Stato/progresso in `<scope-root>/.anjawiki/_crawl_status.json`.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

EXCLUDE_SUFFIX = (".css", ".js", ".png", ".jpg", ".jpeg", ".svg", ".ico",
                  ".woff", ".woff2", ".zip", ".pdf.txt", "/_sources", "/_static")
EXCLUDE_NAMES = ("genindex", "search", "py-modindex", "_sources", "_static")


def _log(msg: str, log_path: Path) -> None:
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}\n")
    except Exception:
        pass


def _slugify(s: str) -> str:
    s = s.strip("/").lower()
    s = re.sub(r"[^\w/.-]", "-", s)
    s = s.replace("/", "-")
    s = re.sub(r"-+", "-", s)
    return s.strip("-") or "page"


def _set_status(scope_root: Path, **fields) -> None:
    f = scope_root / ".anjawiki" / "_crawl_status.json"
    data = {}
    if f.is_file():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data.update({"updated": datetime.now().isoformat(timespec="seconds"), **fields})
    try:
        f.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception:
        pass


def _extract_links(html: str, base_url: str, prefix: str) -> list[str]:
    """Link interni risolti, sotto il path-prefix del seed, dedup, ordinati."""
    out, seen = [], set()
    for m in re.finditer(r'href=["\']([^"\']+)["\']', html, re.I):
        href = m.group(1).split("#")[0].strip()
        if not href or href.startswith(("mailto:", "javascript:", "data:")):
            continue
        absu = urljoin(base_url, href)
        p = urlparse(absu)
        if p.scheme not in ("http", "https"):
            continue
        path = p.path
        if not path.startswith(prefix):
            continue
        if any(x in absu for x in EXCLUDE_SUFFIX):
            continue
        if any(n in path for n in EXCLUDE_NAMES):
            continue
        absu = f"{p.scheme}://{p.netloc}{path}"  # drop query/fragment
        if absu not in seen:
            seen.add(absu)
            out.append(absu)
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--scope-root", required=True)
    p.add_argument("--topic", required=True)
    p.add_argument("--seed-url", required=True)
    p.add_argument("--max-pages", type=int, default=25)
    p.add_argument("--ingest", action="store_true")
    p.add_argument("--model", default=os.environ.get("ANJA_INGEST_MODEL", "haiku"))
    args = p.parse_args()

    scope_root = Path(args.scope_root).resolve()
    aw = scope_root / ".anjawiki"
    raw_topic = aw / "raw" / args.topic
    log_path = aw / ".bg-crawl.log"
    seed = args.seed_url
    pp = urlparse(seed)
    # prefix = directory del seed (path fino all'ultimo '/')
    prefix = pp.path if pp.path.endswith("/") else pp.path.rsplit("/", 1)[0] + "/"

    _log(f"STARTED crawl {seed} (topic={args.topic}, max={args.max_pages}, ingest={args.ingest})", log_path)
    _set_status(scope_root, status="crawling", seed=seed, topic=args.topic,
                total=0, fetched=0, ingested=0, error=None)

    try:
        import httpx
        raw_topic.mkdir(parents=True, exist_ok=True)
        headers = {"User-Agent": "AnjaHub/1.0 (+docs-crawl)"}
        with httpx.Client(follow_redirects=True, timeout=30, headers=headers) as cli:
            r = cli.get(seed)
            r.raise_for_status()
            seed_html = r.text
            urls = [f"{pp.scheme}://{pp.netloc}{prefix}"] + _extract_links(seed_html, seed, prefix)
            # dedup preservando ordine
            seen, ordered = set(), []
            for u in urls:
                if u not in seen:
                    seen.add(u); ordered.append(u)
            ordered = ordered[: args.max_pages]
            _set_status(scope_root, total=len(ordered))
            _log(f"found {len(ordered)} pages (cap {args.max_pages})", log_path)

            saved = []
            for i, u in enumerate(ordered):
                try:
                    resp = cli.get(u)
                    resp.raise_for_status()
                    rel = urlparse(u).path[len(prefix):] or "index"
                    fname = _slugify(rel) + ".html"
                    (raw_topic / fname).write_bytes(resp.content)
                    saved.append(fname)
                    _set_status(scope_root, fetched=len(saved))
                except Exception as e:
                    _log(f"skip {u}: {e}", log_path)

            _log(f"fetched {len(saved)} pages → raw/{args.topic}/", log_path)

            if args.ingest and saved:
                ingest_script = Path(__file__).resolve().parent / "ingest_source_bg.py"
                ingested = 0
                for fname in saved:
                    try:
                        subprocess.run(
                            [sys.executable, str(ingest_script), "--scope-root", str(scope_root),
                             "--topic", args.topic, "--filename", fname, "--model", args.model],
                            timeout=300, capture_output=True,
                        )
                        ingested += 1
                        _set_status(scope_root, ingested=ingested)
                    except Exception as e:
                        _log(f"ingest fail {fname}: {e}", log_path)
                _log(f"ingested {ingested}/{len(saved)}", log_path)

            _set_status(scope_root, status="done", fetched=len(saved))
            _log(f"OK crawl done ({len(saved)} pages)", log_path)
            sys.exit(0)
    except Exception as e:
        _set_status(scope_root, status="error", error=str(e)[:300])
        _log(f"FATAL {e}\n{traceback.format_exc()}", log_path)
        sys.exit(99)


if __name__ == "__main__":
    main()
