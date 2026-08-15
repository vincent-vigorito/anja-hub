#!/usr/bin/env python3
"""test_embeddings.py — F-Mem2-DialecticDedup + Brain semantic search.

Verifica la LOGICA semantica (cosine/RRF, ricerca ibrida Brain, fuzzy dedup dialectic)
con un FAKE embedder deterministico → nessuna key reale richiesta. Copre anche il
fallback graceful (senza embedder / embedding fallito → comportamento lessicale/esatto).

Run: /opt/homebrew/opt/python@3.12/bin/python3.12 anja-hub/tests/test_embeddings.py
Exit 0 = OK, 1 = regressione.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))
import embeddings              # noqa: E402
import brain_io                # noqa: E402
import dialectic_io as dio     # noqa: E402

RESULTS: list[bool] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    RESULTS.append(bool(cond))
    print(f"  {'✓' if cond else '❌'} {name}" + (f" — {detail}" if detail and not cond else ""))
    return cond


def fake_embedder(concept_map: dict):
    """Embedder deterministico: mappa un testo al vettore del primo 'concetto' (substring)
    che contiene, altrimenti un vettore ortogonale. Simula la similarità semantica."""
    def embed(texts):
        out = []
        for t in (texts or []):
            tl = str(t).lower()
            vec = next((v for sub, v in concept_map.items() if sub in tl), [0.0, 0.0, 1.0])
            out.append(vec)
        return out
    return embed


def test_cosine_rrf():
    print("cosine + RRF:")
    check("cosine identici = 1", abs(embeddings.cosine([1, 0, 0], [1, 0, 0]) - 1.0) < 1e-9)
    check("cosine ortogonali = 0", abs(embeddings.cosine([1, 0, 0], [0, 1, 0])) < 1e-9)
    check("cosine degenere = 0", embeddings.cosine([], [1]) == 0.0)
    # RRF: 'b' è alto in entrambi i ranking → primo
    merged = embeddings.rrf_merge(["a", "b", "c"], ["b", "c", "a"])
    check("RRF fonde i ranking (b primo)", merged[0] == "b", str(merged))


def test_get_embedder_no_key():
    print("get_embedder senza key → None:")
    keys = ("ANJA_EMBED_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY",
            "ANJA_EMBED_PROVIDER", "ANJA_EMBED_MODEL")
    saved = {k: os.environ.pop(k, None) for k in keys}
    try:
        tmp = Path(tempfile.mkdtemp())   # hub senza secrets
        check("nessuna key configurata → None (graceful)", embeddings.get_embedder(tmp) is None)
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_brain_semantic():
    print("Brain search ibrida (semantica senza keyword-match):")
    d = Path(tempfile.mkdtemp())
    brain_io.save_note(d, "felini", "Felini", "i gatti dormono molto durante il giorno")
    brain_io.save_note(d, "cucina", "Cucina", "ricette di pasta al forno")
    emb = fake_embedder({"gatt": [1, 0, 0], "animali domestici": [1, 0, 0], "pasta": [0, 1, 0]})

    # query senza sovrapposizione lessicale con le note
    lex = brain_io.search_notes(d, "animali domestici")           # solo lessicale
    check("lessicale: query senza keyword-match → 0 risultati", lex == [])
    hyb = brain_io.search_notes(d, "animali domestici", embedder=emb)   # ibrida
    slugs = [n["slug"] for n in hyb]
    check("semantica: trova 'felini' via significato", slugs == ["felini"], str(slugs))
    check("semantica: esclude 'cucina' (sotto soglia)", "cucina" not in slugs)


def test_dialectic_fuzzy_dedup():
    print("Dialectic fuzzy dedup (riformulazione → reinforced):")
    d = Path(tempfile.mkdtemp())
    dpath = d / "mem-dialectic.md"
    dio.add_observation(dpath, "vincent preferisce risposte brevi", slug="mem")
    emb = fake_embedder({"brevi": [1, 0, 0], "concise": [1, 0, 0]})

    # stessa preferenza, parole diverse → NIENTE match esatto
    r_exact = dio.add_observation(dpath, "a vincent piacciono risposte concise", slug="mem")
    check("senza embedder: parole diverse → NEW (duplicato)", r_exact.get("op") == "new")

    # reset e riprova con embedder → fuzzy match rinforza
    dpath2 = d / "mem2-dialectic.md"
    dio.add_observation(dpath2, "vincent preferisce risposte brevi", slug="mem2")
    r_fuzzy = dio.add_observation(dpath2, "a vincent piacciono risposte concise", slug="mem2", embedder=emb)
    check("con embedder: riformulazione affine → REINFORCED", r_fuzzy.get("op") == "reinforced")
    data = dio.read_dialectic(dpath2)
    check("una sola observation attiva (deduplicata)", len(data["active"]) == 1, str(len(data["active"])))
    check("sightings incrementato a 2", data["active"][0]["sightings"] == 2)

    # graceful: embedder che fallisce (ritorna []) → NEW, nessun crash
    dpath3 = d / "mem3-dialectic.md"
    dio.add_observation(dpath3, "vincent preferisce risposte brevi", slug="mem3")
    r_dead = dio.add_observation(dpath3, "a vincent piacciono risposte concise", slug="mem3",
                                 embedder=lambda texts: [])
    check("embedder morto → fallback NEW (graceful)", r_dead.get("op") == "new")


def main() -> int:
    test_cosine_rrf()
    test_get_embedder_no_key()
    test_brain_semantic()
    test_dialectic_fuzzy_dedup()
    passed, total = sum(RESULTS), len(RESULTS)
    print(f"\n{'✅' if passed == total else '❌'} {passed}/{total} check superati")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
