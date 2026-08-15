"""dialectic_pass.py — post-conversation dialectic analysis (Fase 14).

Triggered da:
  - compact_conversation() in server.py (post-compact hook)
  - session_mirror save + quiet detection (>5min idle)
  - manual /api/dialectic/run endpoint

Workflow:
  1. Load conversation (last N turns)
  2. Resolve dialectic file path (hub o project per scope)
  3. Read current dialectic Active + Never-promote + USER.md HOT
  4. Haiku LLM call con system prompt anti-rumore
  5. Parse JSON {new: [...], reinforced: [...], retired: [...]}
  6. add_observation() per ogni new/reinforced (upsert sighting)

Async fire-and-forget: non blocca caller.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Optional

import dialectic_io as dio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
try:
    import injection_guard
except ImportError:
    injection_guard = None


DIALECTIC_SYSTEM_PROMPT = """Sei un osservatore silenzioso che impara dal modo in cui l'utente conversa con un AI.

Il tuo lavoro: estrai SOLO insight NUOVI e non già dichiarati esplicitamente. Devi essere severo: meglio nessuna osservazione che rumore.

CRITERI:
- Estrai SOLO pattern di preferenza, stile, anti-pattern, contesto stabile
- IGNORA mood transienti ("oggi è stanco"), one-off context ("sta debuggando X")
- IGNORA cose già dichiarate in USER profile (te lo passo nel context)
- IGNORA cose nella lista "Never promote" (te la passo)
- IGNORA observations già presenti in Active (te le passo, con sightings)
- Se vedi pattern già in Active: ritornalo in "reinforced" (incrementa sighting)
- Massimo 3 new + 3 reinforced per pass. Sii selettivo.

OUTPUT: SOLO JSON valido, niente markdown wrapper, niente testo prima/dopo.

Schema:
{
  "new": [
    {"text": "<una frase, max 12 parole>", "tag": "<style|ux|project|tooling|personal|...>", "confidence": 1-3, "reason": "<frase che giustifica>"}
  ],
  "reinforced": [
    {"text": "<text identico a quello in Active>", "reason": "<perchè ripetuto>"}
  ],
  "retired": [
    {"text": "<text in Active che ora sembra non più vero>", "reason": "<perchè>"}
  ]
}

Esempio buone observations:
- "preferisce risposte schematiche su file lunghi" (style)
- "non ama modal di conferma su workflow ripetitivi" (ux)
- "valuta tradeoff sempre prima di implementare" (workflow)

Esempio CATTIVE observations (NON estrarle):
- "sta debuggando la chat" (transient context)
- "ha problemi con Monaco" (one-off)
- "è gentile" (troppo generico)
- "vuole completare la Fase 14" (transient goal)
"""


async def _haiku_call(prompt: str, system_prompt: str = DIALECTIC_SYSTEM_PROMPT,
                     max_retries: int = 1, hub_path=None, feature: str = "dialectic") -> str:
    """Esegue chiamata haiku via claude-agent-sdk. Ritorna text raw.
    Se hub_path è dato, registra il costo (M-CostObservability) col `feature` indicato."""
    try:
        from claude_agent_sdk import ClaudeAgentOptions, query
    except ImportError:
        print("[dialectic] claude-agent-sdk not available")
        return ""

    full_text = ""
    try:
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            model="haiku",
            permission_mode="bypassPermissions",
            allowed_tools=[],
        )
        async for message in query(prompt=prompt, options=options):
            if hub_path is not None and type(message).__name__ == "ResultMessage":
                u = getattr(message, "usage", None)
                if isinstance(u, dict):
                    try:
                        import cost_store
                        cost_store.record(
                            hub_path, provider="anthropic", model="claude-haiku", feature=feature,
                            input_tokens=(u.get("input_tokens", 0) or 0) + (u.get("cache_creation_input_tokens", 0) or 0) + (u.get("cache_read_input_tokens", 0) or 0),
                            output_tokens=u.get("output_tokens", 0) or 0)
                    except Exception:
                        pass
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
    except Exception as e:
        print(f"[dialectic] haiku call error: {type(e).__name__}: {e}")
        return ""
    return full_text.strip()


def _strip_json_wrappers(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    return text.strip()


def _parse_dialectic_output(raw: str) -> dict:
    if not raw:
        return {}
    cleaned = _strip_json_wrappers(raw)
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except Exception as e:
        print(f"[dialectic] JSON parse error: {e}. Raw: {cleaned[:200]}")
    return {}


def _load_conversation_text(conversations_dir: Path, conv_id: str, last_n: int = 20) -> str:
    """Carica ultimi N messaggi da conversation JSON in formato leggibile."""
    p = conversations_dir / f"{conv_id}.json"
    if not p.is_file():
        return ""
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return ""
    msgs = data.get("messages") or []
    tail = msgs[-last_n:]
    out = []
    for m in tail:
        role = m.get("role", "?")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(str(c) for c in content)
        # Trim molto lunghi
        if len(content) > 800:
            content = content[:800] + "…[truncated]"
        out.append(f"### {role}\n{content}")
    return "\n\n".join(out)


def _read_user_hot_text(user_md_path: Optional[Path]) -> str:
    if not user_md_path or not user_md_path.is_file():
        return "(no USER profile yet)"
    try:
        raw = user_md_path.read_text(encoding="utf-8")
        # Strip frontmatter
        if raw.startswith("---"):
            end = raw.find("\n---", 3)
            if end >= 0:
                raw = raw[end + 4:].lstrip()
        return raw.strip()[:2000]
    except Exception:
        return "(read error)"


def _format_dialectic_for_prompt(dialectic_data: dict) -> str:
    """Formatta Active observations + Never-promote per il prompt LLM."""
    active = dialectic_data.get("active") or []
    never = dialectic_data.get("never_promote") or []
    parts = []
    if active:
        parts.append("Current Active observations (do NOT duplicate, but you may reinforce):")
        for o in active[:20]:
            parts.append(f"- {o.get('text', '')} [{o.get('sightings', 1)} sightings, tag: {o.get('tag', '')}]")
    else:
        parts.append("(no active observations yet)")
    parts.append("")
    if never:
        parts.append("Never promote (anti-patterns — do NOT extract these):")
        for n in never:
            parts.append(f"- {n}")
    else:
        parts.append("(no never-promote anti-patterns)")
    return "\n".join(parts)


async def run_dialectic_pass(
    conv_id: str,
    scope: str,
    conversations_dir: Path,
    hub_path: Path,
    project_path: Optional[Path] = None,
    user_slug: Optional[str] = None,
) -> dict:
    """Esegue un dialectic pass su una conversazione. Ritorna report dict.

    Args:
        conv_id: id conversation persistita
        scope: 'hub' | 'project:<name>' | 'agent:<name>'
        conversations_dir: dir dove vivono i file <conv_id>.json
        hub_path: root del hub
        project_path: root del project se scope contiene project
        user_slug: slug utente; se None lo risolve da hub config.json
    """
    report = {"conv_id": conv_id, "scope": scope, "new": [], "reinforced": [], "retired": [], "rejected": [], "errors": []}

    # Resolve user slug
    if not user_slug:
        try:
            cfg = json.loads((hub_path / "config.json").read_text(encoding="utf-8"))
            user_slug = cfg.get("default_user") or "user"
        except Exception:
            user_slug = "user"

    # Determine dialectic file path (project se in project scope, altrimenti hub)
    is_project_scope = scope.startswith("project:") and project_path is not None
    if is_project_scope:
        dialectic_file = project_path / ".anjawiki" / "users" / f"{user_slug}-dialectic.md"
        user_md_path = project_path / ".anjawiki" / "users" / f"{user_slug}.md"
        dialectic_scope = f"project:{project_path.name}"
    else:
        dialectic_file = hub_path / "users" / f"{user_slug}-dialectic.md"
        user_md_path = hub_path / "users" / f"{user_slug}.md"
        dialectic_scope = "hub"

    # Load conversation
    conv_text = _load_conversation_text(conversations_dir, conv_id, last_n=20)
    if not conv_text:
        report["errors"].append("empty or missing conversation")
        return report

    # Load current dialectic + USER HOT
    if dialectic_file.is_file():
        current = dio.read_dialectic(dialectic_file)
    else:
        current = dio.empty_dialectic(slug=user_slug, scope=dialectic_scope)
    user_hot_text = _read_user_hot_text(user_md_path if user_md_path else None)
    dialectic_text = _format_dialectic_for_prompt(current)

    prompt = (
        f"# Conversation (last turns)\n\n{conv_text}\n\n"
        f"---\n\n"
        f"# Current USER profile (HOT)\n\n{user_hot_text}\n\n"
        f"---\n\n"
        f"# Current dialectic state ({dialectic_scope})\n\n{dialectic_text}\n\n"
        f"---\n\n"
        f"Now extract NEW or REINFORCED observations. Output JSON only."
    )

    print(f"[dialectic] running pass: conv={conv_id} scope={dialectic_scope} slug={user_slug}")
    raw = await _haiku_call(prompt, hub_path=hub_path, feature="dialectic")
    parsed = _parse_dialectic_output(raw)
    if not parsed:
        report["errors"].append(f"LLM output parse failed. Raw: {raw[:200]}")
        return report

    # Fuzzy dedup semantico (opt-in): una riformulazione dello stesso concetto rinforza
    # l'observation esistente invece di duplicarla. Default OFF (aggiunge un embed al
    # write-path); si attiva con ANJA_DIALECTIC_FUZZY_DEDUP=1 + una key embedding.
    import os
    _emb = None
    if os.environ.get("ANJA_DIALECTIC_FUZZY_DEDUP") == "1":
        try:
            import embeddings
            _emb = embeddings.get_embedder(hub_path)
        except Exception:
            _emb = None

    # Apply
    for obs in parsed.get("new", [])[:3]:  # safety cap
        text = (obs.get("text") or "").strip()
        tag = (obs.get("tag") or "").strip()
        if not text:
            continue
        # F-Security-Injection: un'observation viene persistita e ri-iniettata nel
        # system prompt di ogni chat futura. Scarta quelle che sono istruzioni mascherate.
        if injection_guard is not None:
            safe, bad = injection_guard.is_safe_observation(text)
            if not safe:
                report["rejected"].append({"text": text, "labels": [b["label"] for b in bad]})
                print(f"[dialectic] ⚠ rejected observation (injection): {text[:80]}")
                continue
        result = dio.add_observation(
            dialectic_file, text=text, tag=tag, session_id=conv_id,
            slug=user_slug, scope=dialectic_scope, embedder=_emb,
        )
        if result.get("op"):
            report["new"].append({"text": text, "tag": tag, "op": result.get("op")})

    for obs in parsed.get("reinforced", [])[:3]:
        text = (obs.get("text") or "").strip()
        if not text:
            continue
        if injection_guard is not None:
            safe, bad = injection_guard.is_safe_observation(text)
            if not safe:
                report["rejected"].append({"text": text, "labels": [b["label"] for b in bad]})
                print(f"[dialectic] ⚠ rejected observation (injection): {text[:80]}")
                continue
        result = dio.add_observation(
            dialectic_file, text=text, tag="", session_id=conv_id,
            slug=user_slug, scope=dialectic_scope, embedder=_emb,
        )
        report["reinforced"].append({"text": text, "op": result.get("op")})

    # Retired: move to Decayed manually
    retired_items = parsed.get("retired", [])[:3]
    if retired_items:
        data = dio.read_dialectic(dialectic_file)
        active = data.get("active") or []
        decayed = data.get("decayed") or []
        from datetime import datetime as _dt
        today = _dt.now().strftime("%Y-%m-%d")
        moved_texts = set()
        for r in retired_items:
            rt = (r.get("text") or "").strip().lower()
            if not rt:
                continue
            for o in active:
                if o.get("text", "").strip().lower() == rt:
                    decayed.append({
                        "date": today,
                        "text": o["text"],
                        "reason": r.get("reason") or "LLM judged retired",
                    })
                    moved_texts.add(rt)
                    report["retired"].append({"text": o["text"]})
                    break
        if moved_texts:
            data["active"] = [o for o in active if o.get("text", "").strip().lower() not in moved_texts]
            data["decayed"] = decayed
            dio.write_dialectic(dialectic_file, data)

    print(f"[dialectic] pass done: new={len(report['new'])} reinforced={len(report['reinforced'])} retired={len(report['retired'])}")
    return report


def schedule_dialectic_pass(*args, **kwargs):
    """Fire-and-forget wrapper. Crea task asyncio senza await."""
    try:
        loop = asyncio.get_event_loop()
        task = loop.create_task(run_dialectic_pass(*args, **kwargs))
        return task
    except RuntimeError:
        # Fallback se nessun event loop attivo
        return asyncio.run(run_dialectic_pass(*args, **kwargs))
