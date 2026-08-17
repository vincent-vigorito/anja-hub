"""connectors_io.py — Settings/Connettori del workspace (F1a).

UI facile per configurare i backend esterni di un workspace marketing
(WordPress / Meta / Google), mappati sul `.secrets.env` per-workspace già
esistente (`<ws>/.anjawiki/.secrets.env`, auto-caricato dal server MCP,
gitignored, owner-only) — stesso vocabolario di chiavi del prototipo
anja-marketer.

NON è il vault cifrato (Fernet) — quello è F2. Qui i valori stanno in chiaro
nel `.secrets.env` (file privato 0600), coerente con lo store hub esistente.

Regole di sicurezza:
  - i valori dei campi `secret` NON escono mai dal backend (read_status li omette,
    espone solo `set: true/false`);
  - in salvataggio, un campo secret con input vuoto = "lascia invariato"
    (non azzera il segreto esistente);
  - le chiavi NON gestite nel file (es. OPENROUTER_API_KEY) sono preservate.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import vault_io

# Schema connettori — stesse chiavi env del marketer (models.py ENV_MAP).
CONNECTORS = [
    {
        "key": "wordpress", "label": "WordPress", "icon": "globe",
        "fields": [
            {"key": "WP_BASE_URL", "label": "Base URL", "secret": False, "placeholder": "https://esempio.it"},
            {"key": "WP_USERNAME", "label": "Username", "secret": False, "placeholder": ""},
            {"key": "WP_APP_PASSWORD", "label": "Application Password", "secret": True, "placeholder": "xxxx xxxx xxxx"},
        ],
    },
    {
        "key": "swerpicommerce", "label": "SwerpiCommerce", "icon": "shopping-cart",
        "fields": [
            {"key": "SWERPICOMMERCE_BASE_URL", "label": "Base URL", "secret": False, "placeholder": "https://<tenant>/api/v2"},
            {"key": "SWERPICOMMERCE_API_ID", "label": "API ID", "secret": False, "placeholder": "from the tenant panel → API keys"},
            {"key": "SWERPICOMMERCE_API_SECRET", "label": "API secret", "secret": True, "placeholder": "from the tenant panel → API keys"},
            {"key": "SWERPICOMMERCE_BEARER_AUTH", "label": "Bearer token", "secret": True, "optional": True,
             "placeholder": "(optional — the agent derives it from API ID+secret)"},
        ],
    },
    {
        "key": "meta", "label": "Meta (Facebook / Instagram)", "icon": "facebook",
        "fields": [
            {"key": "META_ACCESS_TOKEN", "label": "Access token", "secret": True, "placeholder": "EAAB…"},
            {"key": "META_PAGE_ID", "label": "Page ID", "secret": False, "placeholder": ""},
            {"key": "META_IG_USER_ID", "label": "IG User ID", "secret": False, "placeholder": ""},
            {"key": "META_ADS_TOKEN", "label": "Ads token", "secret": True, "optional": True, "placeholder": "(only if you run ads)"},
            {"key": "META_AD_ACCOUNT_ID", "label": "Ad account ID", "secret": False, "optional": True, "placeholder": "(only if you run ads)"},
        ],
    },
    {
        "key": "google", "label": "Google (GA4 / GSC / Ads)", "icon": "line-chart",
        "fields": [
            {"key": "GA4_PROPERTY_ID", "label": "GA4 Property ID", "secret": False, "placeholder": ""},
            {"key": "GSC_SITE", "label": "GSC site", "secret": False, "placeholder": "sc-domain:esempio.it"},
            {"key": "GOOGLE_ADS_CUSTOMER_ID", "label": "Google Ads customer ID", "secret": False, "optional": True, "placeholder": "(only if you run ads)"},
            {"key": "MERCHANT_ACCOUNT_ID", "label": "Merchant Center account ID", "secret": False, "optional": True, "placeholder": "(e-commerce with Google Shopping only)"},
        ],
    },
    {
        # Chiavi dei generatori immagini: risorsa HUB (Settings → Integrations),
        # condivisa da tutti i workspace — la vista workspace la esclude (shared).
        "key": "ai_images", "label": "Image generation (direct APIs)",
        "icon": "sparkles", "shared": True,
        "fields": [
            {"key": "OPENAI_API_KEY", "label": "OpenAI — GPT Image / DALL·E", "secret": True, "optional": True, "placeholder": "sk-…"},
            {"key": "GEMINI_API_KEY", "label": "Google AI Studio — Nano Banana / Imagen", "secret": True, "optional": True, "placeholder": "AIza… (from aistudio.google.com)"},
            {"key": "XAI_API_KEY", "label": "xAI — Grok Imagine", "secret": True, "optional": True, "placeholder": "xai-…"},
            {"key": "OPENROUTER_API_KEY", "label": "OpenRouter — Seedream / Seedance + video", "secret": True, "optional": True, "placeholder": "sk-or-…"},
            {"key": "STABILITY_API_KEY", "label": "Stability — SD 3.5", "secret": True, "optional": True, "placeholder": "sk-…"},
            {"key": "DASHSCOPE_API_KEY", "label": "Alibaba DashScope — Qwen Image", "secret": True, "optional": True, "placeholder": ""},
            {"key": "IMAGE_DEFAULT_MODEL", "label": "Default model for agents", "secret": False, "optional": True, "placeholder": "e.g. nano-banana-3, gpt-image-1, grok-image"},
        ],
    },
]

_MANAGED = {f["key"]: f for con in CONNECTORS for f in con["fields"]}
_KEY_RE = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")

# Card CMS mostrate solo se il backend del workspace corrisponde (meta.yaml
# in secrets_dir). Backend assente/sconosciuto → si mostrano tutte (hub,
# workspace pre-blueprint).
_CMS_GROUPS = {"wordpress": {"wp", "woo"}, "swerpicommerce": {"swerpi"}}
_BACKEND_RE = re.compile(r"^backend:\s*(\S+)", re.M)


def _workspace_backend(secrets_dir: Path) -> str:
    meta = Path(secrets_dir) / "meta.yaml"
    if not meta.is_file():
        return ""
    m = _BACKEND_RE.search(meta.read_text(encoding="utf-8", errors="replace"))
    return m.group(1).strip() if m else ""


def _env_values(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        return {}
    vals = {}
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        vals[k.strip()] = v.strip()
    return vals


def _vault_path(secrets_dir: Path) -> Path:
    return Path(secrets_dir) / ".secrets.vault"


def _env_path(secrets_dir: Path) -> Path:
    return Path(secrets_dir) / ".secrets.env"


def _load_values(hub_path: Path, secrets_dir: Path) -> dict:
    """Valori gestiti dal vault cifrato. Se il vault non esiste ancora, MIGRA i
    valori gestiti dal `.secrets.env` esistente (one-shot) → il vault diventa
    canonico, il runtime continua a funzionare."""
    vp = _vault_path(secrets_dir)
    if vp.is_file():
        return vault_io.load(hub_path, vp)
    env_vals = _env_values(_env_path(secrets_dir))
    migrated = {k: env_vals[k] for k in _MANAGED if env_vals.get(k)}
    if migrated:
        vault_io.store(hub_path, vp, migrated)
    return migrated


def hub_secrets_dir(hub_path: Path) -> Path:
    """Dir dei segreti a livello hub (vault condiviso): `<hub>/.anjawiki`."""
    return Path(hub_path) / ".anjawiki"


def resolve_values(hub_path: Path, secrets_dir: Path) -> dict:
    """Credenziali EFFETTIVE per uno scope: i valori del workspace, con FALLBACK al
    vault hub per le chiavi non impostate localmente. Così una risorsa condivisa
    (es. le key dei modelli immagine) si configura una volta a livello hub e i singoli
    workspace possono fare override. Se `secrets_dir` è già l'hub, niente fallback."""
    ws = _load_values(hub_path, secrets_dir)
    hub_dir = hub_secrets_dir(hub_path)
    if Path(secrets_dir).resolve() == hub_dir.resolve():
        return ws
    merged = dict(_load_values(hub_path, hub_dir))
    merged.update({k: v for k, v in ws.items() if v})   # il workspace vince
    return merged


def _is_materialized(secrets_dir: Path) -> bool:
    """True se i segreti gestiti sono attualmente in chiaro nel `.secrets.env`
    (cioè disponibili al runtime MCP)."""
    env_vals = _env_values(_env_path(secrets_dir))
    return any(env_vals.get(k) for k in _MANAGED)


def _build_groups(values: dict, backend: str = "") -> list[dict]:
    groups = []
    for con in CONNECTORS:
        allowed = _CMS_GROUPS.get(con["key"])
        if allowed is not None and backend and backend not in allowed:
            continue
        fields, n_set, req_total, req_set = [], 0, 0, 0
        for f in con["fields"]:
            v = (values.get(f["key"], "") or "").strip()
            is_set = bool(v)
            if is_set:
                n_set += 1
            if not f.get("optional"):
                req_total += 1
                if is_set:
                    req_set += 1
            fields.append({
                "key": f["key"], "label": f["label"], "secret": f["secret"],
                "placeholder": f.get("placeholder", ""),
                "value": "" if f["secret"] else v,     # mai esporre il valore segreto
                "set": is_set,
            })
        total = len(con["fields"])
        if req_total == 0:   # tutti opzionali (es. key immagini): stato = quante configurate
            status = "connected" if n_set == total else ("partial" if n_set else "missing")
        else:
            status = "connected" if req_set == req_total else ("partial" if n_set else "missing")
        groups.append({
            "key": con["key"], "label": con["label"], "icon": con["icon"],
            "fields": fields, "status": status, "set_count": n_set, "total": total,
            "all_optional": req_total == 0,
            "shared": con.get("shared", False),
        })
    return groups


def read_status(hub_path: Path, secrets_dir: Path) -> dict:
    """Stato dei connettori dal vault cifrato. Plain inclusi, segreti SOLO come
    flag `set`. `materialized` = segreti in chiaro nel .secrets.env per il runtime."""
    values = _load_values(hub_path, secrets_dir)
    groups = _build_groups(values, _workspace_backend(secrets_dir))
    # Vista workspace: i connettori shared si gestiscono SOLO a livello
    # hub (Settings → Integrazioni).
    if Path(secrets_dir).resolve() != hub_secrets_dir(hub_path).resolve():
        groups = [g for g in groups if not g.get("shared")]
    return {
        "connectors": groups,
        "encrypted": True,
        "materialized": _is_materialized(secrets_dir),
    }


def _write_secure(path: Path, text: str) -> None:
    """Scrive il file owner-only (0600), troncando."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _write_env_merge(env_path: Path, managed: dict) -> None:
    """Riscrive il `.secrets.env` con i valori gestiti `managed` (solo non vuoti),
    preservando le chiavi NON gestite e i commenti. Le chiavi gestite assenti da
    `managed` vengono rimosse (smaterializzazione)."""
    path = Path(env_path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.is_file() else []
    out, seen = [], set()
    for ln in lines:
        m = _KEY_RE.match(ln)
        if m and m.group(1) in _MANAGED:
            k = m.group(1)
            if k not in seen:
                if managed.get(k):
                    out.append(f"{k}={managed[k]}")
                seen.add(k)
        else:
            out.append(ln)
    for k in _MANAGED:
        if k not in seen and managed.get(k):
            out.append(f"{k}={managed[k]}")
            seen.add(k)
    text = "\n".join(out).rstrip("\n") + "\n" if out else ""
    _write_secure(path, text)


def save(hub_path: Path, secrets_dir: Path, values: dict) -> dict:
    """Aggiorna i connettori nel VAULT cifrato. Secret con input vuoto = invariato.
    Se i segreti sono attualmente materializzati, ri-materializza per tenere il
    runtime in sync. Ritorna read_status()."""
    values = values or {}
    existing = _load_values(hub_path, secrets_dir)

    final = {}
    for k, f in _MANAGED.items():
        if k in values:
            newv = (str(values[k]) if values[k] is not None else "").strip()
            # anti-injection: un value con newline/CR/NUL inietterebbe righe nel
            # .secrets.env in fase di materializzazione.
            if any(ch in newv for ch in ("\n", "\r", "\x00")):
                raise ValueError(f"valore non valido per {k}: caratteri di controllo non ammessi")
            final[k] = existing.get(k, "") if (f["secret"] and newv == "") else newv
        else:
            final[k] = existing.get(k, "")
    final = {k: v for k, v in final.items() if v}

    vault_io.store(hub_path, _vault_path(secrets_dir), final)
    if _is_materialized(secrets_dir):
        _write_env_merge(_env_path(secrets_dir), final)
    return read_status(hub_path, secrets_dir)


def materialize(hub_path: Path, secrets_dir: Path) -> dict:
    """Scrive i segreti del vault in chiaro sul `.secrets.env` (0600) → il runtime
    MCP li vede. Preserva le chiavi non gestite."""
    _write_env_merge(_env_path(secrets_dir), _load_values(hub_path, secrets_dir))
    return read_status(hub_path, secrets_dir)


def dematerialize(hub_path: Path, secrets_dir: Path) -> dict:
    """Rimuove i segreti gestiti dal `.secrets.env` (restano nel vault cifrato).
    Le altre chiavi sono preservate."""
    _write_env_merge(_env_path(secrets_dir), {})
    return read_status(hub_path, secrets_dir)


# --- CLI media (giv): materializzazione chiavi per gli agent ---------------------

# Chiavi lette dalla CLI `giv` (env di processo → ./credentials.env → ./.env).
GIV_KEYS = ("GEMINI_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "OPENROUTER_API_KEY")


def write_media_credentials(hub_path: Path) -> list[str]:
    """Materializza le sole chiavi media dal vault in `credentials.env` (0600):
    alla root dell'hub e in ogni workspace (con gli override del workspace).
    È il file che la CLI `giv` legge dalla cwd degli agent. Nessuna chiave →
    file rimosso. Ritorna i path scritti."""
    hub_path = Path(hub_path)
    targets = [(hub_path, hub_secrets_dir(hub_path))]
    ws_root = hub_path / "workspaces"
    if ws_root.is_dir():
        for ws in sorted(ws_root.iterdir()):
            if ws.is_dir() and (ws / ".anjawiki").is_dir():
                targets.append((ws, ws / ".anjawiki"))
    written = []
    for root, secrets_dir in targets:
        try:
            vals = resolve_values(hub_path, secrets_dir)
            lines = [f"{k}={(vals.get(k) or '').strip()}"
                     for k in GIV_KEYS if (vals.get(k) or "").strip()]
            dest = root / "credentials.env"
            if not lines:
                dest.unlink(missing_ok=True)
                continue
            _write_secure(dest, "# generato dall'hub (Settings → Integrations → "
                                "Generazione immagini) — chiavi per la CLI giv\n"
                          + "\n".join(lines) + "\n")
            written.append(str(dest))
        except Exception as e:  # noqa: BLE001 — un workspace rotto non blocca gli altri
            print(f"[connectors] write_media_credentials {root}: {e}", flush=True)
    return written
