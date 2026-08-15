"""dreaming.py — F-Dreaming: consolidamento memoria notturno.

Il "sogno" dell'hub: una passata periodica che consolida la memoria dialectic
(working memory post-compact) senza intervento umano, riusando la machinery già
esistente (`promotion_distill`) e aggiungendo la DECADENZA delle observation stantie.

Deterministico di default (no LLM):
  1. PROMUOVE le observation mature (sightings/sessions oltre soglia) → USER.md HOT
  2. FA DECADERE le observation vecchie mai riviste (Active → Decayed, non cancellate)
  3. DISTILLA i pattern cross-workspace → hub dialectic

Non distruttivo: le promozioni lasciano un marker in USER.md, le decadute finiscono
nella sezione `## Decayed` (recuperabili). Scope: dialectic dell'utente di default
dell'hub. Schedulato da server.py (`_dreaming_loop`) + trigger manuale via endpoint.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import dialectic_io as dio
import promotion_distill as pd

DECAY_DAYS = int(os.environ.get("ANJA_DREAMING_DECAY_DAYS", "21"))


def _dialectic_path(hub_path: Path, slug: str) -> Path:
    return Path(hub_path) / "users" / f"{slug}-dialectic.md"


def _user_md_path(hub_path: Path, slug: str) -> Path:
    return Path(hub_path) / "users" / f"{slug}.md"


def decay_stale(dialectic_path: Path, decay_days: int = DECAY_DAYS, today: str | None = None) -> list[str]:
    """Sposta Active → Decayed le observation con `last_seen` più vecchio di `decay_days`
    e non ancora candidate a promozione (sightings < MIN_SIGHTINGS): sono transienti mai
    consolidate. Le mature restano (le promuove la distillation); le prive di last_seen
    restano (safety). Ritorna i testi decaduti."""
    dialectic_path = Path(dialectic_path)
    if not dialectic_path.is_file():
        return []
    today = today or datetime.now().strftime("%Y-%m-%d")
    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=decay_days)).strftime("%Y-%m-%d")
    data = dio.read_dialectic(dialectic_path)
    kept, gone = [], []
    for o in data.get("active", []):
        last = (o.get("last_seen") or "").strip()
        sightings = int(o.get("sightings", 1))
        if sightings >= pd.MIN_SIGHTINGS or not last or last >= cutoff:
            kept.append(o)      # matura o recente o senza data → non decade
        else:
            gone.append(o)
    if not gone:
        return []
    data["active"] = kept
    dec = data.get("decayed") or []
    for o in gone:
        # niente parentesi nella reason: _fmt_dated la avvolge già in (...) e il parser
        # non gestisce parentesi annidate (come la reason di promozione)
        dec.append({"date": today, "text": o.get("text", ""),
                    "reason": f"stantia dal {o.get('last_seen', '?')}, {o.get('sightings', 1)} sightings"})
    data["decayed"] = dec
    dio.write_dialectic(dialectic_path, data)
    return [o.get("text", "") for o in gone]


async def consolidate(hub_path: Path, slug: str, *, use_llm_judge: bool = False,
                      cross_workspace: bool = True, projects: list | None = None) -> dict:
    """Un ciclo di consolidamento per l'utente `slug`. Best-effort per fase (una fase
    che fallisce non blocca le altre). Ritorna {slug, promoted, decayed, cross, ...errori}."""
    hub_path = Path(hub_path)
    dpath = _dialectic_path(hub_path, slug)
    upath = _user_md_path(hub_path, slug)
    report: dict = {"slug": slug, "promoted": [], "decayed": [], "cross": []}

    # 1) promozione mature → USER.md (riusa la machinery esistente)
    try:
        pr = await pd.distill_promotions(dpath, upath, use_llm_judge=use_llm_judge, slug=slug)
        report["promoted"] = [p.get("text", "") for p in pr.get("promoted", [])]
    except Exception as e:
        report["error_promote"] = f"{type(e).__name__}: {e}"

    # 2) decadenza delle stantie (dopo la promozione: le mature sono già uscite da Active)
    try:
        report["decayed"] = decay_stale(dpath)
    except Exception as e:
        report["error_decay"] = f"{type(e).__name__}: {e}"

    # 3) distillazione cross-workspace → hub dialectic
    if cross_workspace and projects:
        try:
            cr = pd.cross_workspace_distill(hub_path, projects, slug)
            report["cross"] = [d.get("text", "") for d in cr.get("distilled", [])]
        except Exception as e:
            report["error_cross"] = f"{type(e).__name__}: {e}"

    report["changed"] = bool(report["promoted"] or report["decayed"] or report["cross"])
    return report
