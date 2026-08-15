#!/usr/bin/env python3
"""test_ingest_pipeline.py — test di integrazione della pipeline ingest hub.

Esercita la sequenza hub_api REALE end-to-end, senza internet né LLM:
  1. mini HTTP server locale che serve una doc fake multi-pagina
  2. webapp server su hub temp
  3. POST /api/sources/add-crawl  (crawl multi-pagina, ingest=false)
  4. poll GET /api/sources/crawl-status → done
  5. GET /api/sources/list?scope=hub → verifica che TUTTE le pagine siano scaricate
  6. POST /api/sources/add (singolo) + verifica scope=hub

Deterministico e veloce (ingest LLM escluso: testa fetch/crawl/sequenza REST).
L'ingest LLM vero è coperto manualmente (richiede `claude` + costo).

Usage: python3 anja-hub/tests/test_ingest_pipeline.py
Exit 0 = tutti i check verdi.
"""

import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PYBIN = sys.executable

# --- doc fake multi-pagina servita dal mini server -----------------------
INDEX_HTML = """<!DOCTYPE html><html><body>
<h1>Fake Docs</h1>
<a href="getting-started/">Getting started</a>
<a href="config/">Config</a>
<a href="howto/networking/">Networking</a>
<a href="_static/style.css">style</a>   <!-- escluso -->
<a href="https://external.example.com/x">external</a> <!-- escluso -->
<a href="genindex/">index</a>           <!-- escluso -->
</body></html>"""
PAGE = "<!DOCTYPE html><html><body><h1>{}</h1><p>Content of {}.</p></body></html>"
PAGES = {
    "/": INDEX_HTML,
    "/getting-started/": PAGE.format("Getting started", "getting-started"),
    "/config/": PAGE.format("Config", "config"),
    "/howto/networking/": PAGE.format("Networking", "howto/networking"),
}


class DocHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGES.get(self.path)
        if body is None:
            self.send_response(404); self.end_headers(); return
        b = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read().decode())


def _post(url, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def main() -> int:
    failures = []

    def check(cond, msg):
        print(("  ✓ " if cond else "  ✗ ") + msg)
        if not cond:
            failures.append(msg)

    doc_port = _free_port()
    web_port = _free_port()
    doc_srv = HTTPServer(("127.0.0.1", doc_port), DocHandler)
    threading.Thread(target=doc_srv.serve_forever, daemon=True).start()
    seed = f"http://127.0.0.1:{doc_port}/"

    tmp = Path(tempfile.mkdtemp()) / "hub"
    subprocess.run([PYBIN, str(REPO / "anja-hub/scripts/init_hub.py"), "--target", str(tmp)],
                   capture_output=True)
    subprocess.run([PYBIN, str(REPO / "anja-hub/scripts/users_init.py"), "--hub", str(tmp),
                    "--name", "Test", "--default", "--force"], capture_output=True)

    web = subprocess.Popen([PYBIN, str(REPO / "anja-hub/webapp/server.py"), "--hub", str(tmp),
                            "--port", str(web_port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{web_port}"
    try:
        # attendi boot
        up = False
        for _ in range(40):
            try:
                _get(f"{base}/api/registry"); up = True; break
            except Exception:
                time.sleep(0.5)
        check(up, "webapp server up")
        if not up:
            return 1

        print("\n[crawl] add-crawl multi-pagina (scope=hub, ingest=false)")
        r = _post(f"{base}/api/sources/add-crawl",
                  {"scope": "hub", "topic": "fakedocs", "url": seed, "max_pages": 10, "ingest": False})
        check(r.get("status") == "started", "add-crawl ritorna started")

        st = {}
        for _ in range(30):
            st = _get(f"{base}/api/sources/crawl-status?scope=hub")
            if st.get("status") in ("done", "error"):
                break
            time.sleep(1)
        check(st.get("status") == "done", f"crawl status done (got {st.get('status')})")
        # index + 3 sotto-pagine = 4; gli esclusi (_static/external/genindex) non contano
        check(st.get("fetched") == 4, f"4 pagine scaricate (got {st.get('fetched')})")

        print("\n[list] verifica file raw nel wiki hub")
        lst = _get(f"{base}/api/sources/list?scope=hub")
        topics = {t["name"]: t for t in lst.get("topics", [])}
        check("fakedocs" in topics, "topic 'fakedocs' presente")
        n = topics.get("fakedocs", {}).get("count", 0)
        check(n == 4, f"4 file raw nel topic (got {n})")

        print("\n[add] singola fonte inline (scope=hub)")
        a = _post(f"{base}/api/sources/add",
                  {"scope": "hub", "topic": "notes", "mode": "inline",
                   "filename": "n.md", "content_text": "# Nota"})
        check(a.get("status") == "saved", "add inline saved")

        # verifica fisica: file nel posto giusto (no doppio .anjawiki)
        raw = tmp / ".anjawiki" / "raw"
        check((raw / "fakedocs").is_dir() and len(list((raw / "fakedocs").glob("*.html"))) == 4,
              "4 .html in <hub>/.anjawiki/raw/fakedocs/")
        check(not (tmp / ".anjawiki" / ".anjawiki").exists(), "nessun doppio .anjawiki")
    finally:
        web.terminate()
        doc_srv.shutdown()
        import shutil
        shutil.rmtree(tmp.parent, ignore_errors=True)

    print()
    if failures:
        print(f"❌ FAILED: {len(failures)} check falliti")
        return 1
    print("✅ OK: pipeline ingest hub (crawl multi-pagina + add) verde")
    return 0


if __name__ == "__main__":
    sys.exit(main())
