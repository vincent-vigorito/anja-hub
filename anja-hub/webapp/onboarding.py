"""onboarding.py — F-MarketingVertical — popola i "fatti" del brand dal sito live.

Lo scaffold crea solo placeholder; questo step riempie il workspace con la conoscenza
su cui lavora il pod:
  - `data/catalogo/{pagine,articoli}.md` — indici GENERATI (deterministico, no LLM)
  - `data/ESPERTO.md` — ruolo + dominio sintetizzato dal sito (1 chiamata LLM, no tool)
  - `data/BRAND.md` — stub identità visiva (da rifinire a mano)

I file catalogo sono rigenerabili (idealmente una routine periodica, Fase 4).
Stdlib + marketing.wp_client + pod_orchestrator. Vedi anja-marketing-workspace-design.md §2.1.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "anja-hub" / "scripts"))
from marketing.wp_client import WordPressClient  # noqa: E402

import pod_orchestrator  # noqa: E402


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _vault(hub_path: Path, ws_slug: str) -> dict:
    f = Path(hub_path) / "workspaces" / ws_slug / ".anjawiki" / ".secrets.env"
    return {k: (v or "").strip() for k, v in dotenv_values(f).items()} if f.is_file() else {}


def _wp(hub_path: Path, ws_slug: str):
    v = _vault(hub_path, ws_slug)
    if not v.get("WP_BASE_URL"):
        return None
    return WordPressClient(v["WP_BASE_URL"].rstrip("/"), v.get("WP_USERNAME", ""), v.get("WP_APP_PASSWORD", ""))


def _title(item: dict) -> str:
    t = item.get("title")
    return (t.get("rendered") if isinstance(t, dict) else t) or ""


def _data_dir(hub_path: Path, ws_slug: str) -> Path:
    d = Path(hub_path) / "workspaces" / ws_slug / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _strip_html(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _strip_fence(text: str) -> str:
    """Rimuove un eventuale wrapper ```markdown ... ``` attorno all'output LLM."""
    t = (text or "").strip()
    if t.startswith("```"):
        lines = t.split("\n")
        lines = lines[1:]  # drop ```lang
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines)
    return t.strip()


# ----------------------------------------------------------------------
# 1) Catalogo (deterministico)
# ----------------------------------------------------------------------

async def gen_catalogo(hub_path: Path, ws_slug: str) -> dict:
    wp = _wp(hub_path, ws_slug)
    if wp is None:
        return {"ok": False, "error": "WP non configurato nel vault del brand"}
    try:
        pages = await wp.list_content("pages", per_page=100, status="publish,draft")
        posts = await wp.list_content("posts", per_page=100, status="publish,draft")
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        await wp.close()

    cat = _data_dir(hub_path, ws_slug) / "catalogo"
    cat.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    def _table(items):
        head = "| ID | Titolo | Slug | Stato | URL |\n|---|---|---|---|---|\n"
        rows = "\n".join(
            f"| {i.get('id')} | {_title(i)} | `{i.get('slug')}` | {i.get('status')} | {i.get('link')} |"
            for i in items
        )
        return head + rows + "\n"

    (cat / "pagine.md").write_text(
        f"# Catalogo pagine — {ws_slug}\n\n_Generato {stamp}. RIGENERABILE, non editare a mano._\n\n{_table(pages)}",
        encoding="utf-8")
    (cat / "articoli.md").write_text(
        f"# Catalogo articoli — {ws_slug}\n\n_Generato {stamp}. RIGENERABILE, non editare a mano._\n\n{_table(posts)}",
        encoding="utf-8")
    return {"ok": True, "pages": len(pages), "posts": len(posts), "dir": str(cat)}


# ----------------------------------------------------------------------
# 2) ESPERTO (agentico — 1 chiamata, no tool)
# ----------------------------------------------------------------------

ESPERTO_PROMPT = """Sei l'esperto di dominio incaricato di scrivere il file `ESPERTO.md` per un brand,\
 basandoti ESCLUSIVAMENTE sui dati forniti qui sotto (NON inventare nulla; ciò che non puoi\
 dedurre marcalo con `⬜ TODO`). Output: SOLO il markdown del file, struttura:

# ESPERTO — {name}

> **Ruolo**: quando lavori su questo brand sei [definisci il ruolo/consulente adatto al settore].

## L'azienda
(chi è, cosa fa, proposta di valore / USP — dedotti dai contenuti)

## Prodotti / Servizi
(mappa sintetica per categoria, dai titoli/pagine)

## I clienti tipo
(profili dedotti)

## Il linguaggio
(come comunica il brand: tono, parole ricorrenti)

## Vincoli e compliance
(⬜ TODO se non deducibili)

## Fonti di verità
- `data/catalogo/` (indici pagine/articoli)
- contenuti sul sito via i tool del server `anja_marketing`

=== DATI DEL SITO ===
Nome: {name}
Descrizione: {desc}

Pagine pubblicate:
{pages}

Estratto homepage:
{home}
"""


async def _fetch_context(hub_path: Path, ws_slug: str):
    wp = _wp(hub_path, ws_slug)
    if wp is None:
        return None
    try:
        info = await wp.site_info()
        pages = await wp.list_content("pages", per_page=50, status="publish")
        home = None
        for cand in ("home", "homepage", "front-page", "index"):
            match = [p for p in pages if p.get("slug") == cand]
            if match:
                home = match[0]
                break
        if home is None and pages:
            home = pages[0]
        home_text = ""
        if home:
            full = await wp.get_content("pages", home["id"])
            c = full.get("content")
            home_text = _strip_html((c.get("rendered") if isinstance(c, dict) else c) or "")
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    finally:
        await wp.close()
    return {"info": info, "pages": pages, "home_text": home_text[:6000]}


async def gen_esperto(hub_path: Path, ws_slug: str, lead: str | None = None) -> dict:
    ctx = await _fetch_context(hub_path, ws_slug)
    if ctx is None:
        return {"ok": False, "error": "WP non configurato nel vault del brand"}
    if ctx.get("error"):
        return {"ok": False, "error": ctx["error"]}

    lead = lead or pod_orchestrator._resolve_lead(Path(hub_path), ws_slug)
    pages_list = "\n".join(f"- {_title(p)} (/{p.get('slug')}/)" for p in ctx["pages"]) or "(nessuna)"
    prompt = ESPERTO_PROMPT.format(
        name=ctx["info"].get("name", ws_slug),
        desc=ctx["info"].get("description", ""),
        pages=pages_list,
        home=ctx["home_text"] or "(vuoto)",
    )
    res = await pod_orchestrator.run_specialist(hub_path, ws_slug, lead, prompt, tools=False, persist=False)
    if not res.get("ok") or not res.get("summary"):
        return {"ok": False, "error": res.get("error") or "sintesi vuota"}
    fp = _data_dir(hub_path, ws_slug) / "ESPERTO.md"
    fp.write_text(_strip_fence(res["summary"]) + "\n", encoding="utf-8")
    return {"ok": True, "file": str(fp), "usage": res.get("usage")}


# ----------------------------------------------------------------------
# 3) BRAND stub + bundle
# ----------------------------------------------------------------------

BRAND_TEMPLATE = """# BRAND — {ws} (identità visiva, LOCALE)

> Riferimento per i kit social. Da rifinire a mano (l'estrazione automatica da CSS/logo
> è imprecisa). Il social agent legge questo file.

## Palette
- Primario: ⬜ TODO (hex)
- Secondario: ⬜ TODO
- Accento: ⬜ TODO

## Tipografia / stile card
- Font: ⬜ TODO
- Stile: ⬜ TODO (es. flat, gradiente, fotografico)

## Regole
- Packshot reali (mai packaging inventato)
- ⬜ TODO altre regole brand
"""


async def gen_brand_stub(hub_path: Path, ws_slug: str) -> dict:
    fp = _data_dir(hub_path, ws_slug) / "BRAND.md"
    if fp.exists():
        return {"ok": True, "skipped": "esiste già"}
    fp.write_text(BRAND_TEMPLATE.format(ws=ws_slug), encoding="utf-8")
    return {"ok": True, "file": str(fp)}


async def onboard_brand(hub_path: Path, ws_slug: str, esperto: bool = True) -> dict:
    """Onboarding completo: catalogo (deterministico) + ESPERTO (LLM) + BRAND stub."""
    out = {"workspace": ws_slug}
    out["catalogo"] = await gen_catalogo(hub_path, ws_slug)
    out["brand"] = await gen_brand_stub(hub_path, ws_slug)
    out["esperto"] = await gen_esperto(hub_path, ws_slug) if esperto else {"skipped": True}
    out["ok"] = bool(out["catalogo"].get("ok"))
    return out


# CLI: python onboarding.py <catalogo|esperto|all> <hub> <ws>
if __name__ == "__main__":
    import asyncio
    import json
    mode, hub, ws = sys.argv[1], sys.argv[2], sys.argv[3]
    fn = {"catalogo": gen_catalogo, "esperto": gen_esperto, "all": onboard_brand}[mode]
    print(json.dumps(asyncio.run(fn(Path(hub), ws)), indent=2, ensure_ascii=False))
