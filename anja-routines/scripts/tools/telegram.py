"""
telegram.py — invio messaggio Telegram via Bot API.

Config:
    type: telegram
    chat_id: 123456789                       # optional, default = hub config telegram.allowed_chat_ids[0]
    token: "{{TELEGRAM_BOT_TOKEN}}"          # optional, default = <hub>/.secrets.env TELEGRAM_BOT_TOKEN
    title: "Market Briefing"                 # optional, bold prefix
    parse_mode: "Markdown"                   # optional, default Markdown; fallback to plain on error
"""

import json
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from typing import Optional


TELEGRAM_API_BASE = "https://api.telegram.org"
MAX_LEN = 4000


def _load_token_from_secrets(hub: Path) -> Optional[str]:
    env_file = hub / ".secrets.env"
    if not env_file.is_file():
        return None
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or not line.startswith("TELEGRAM_BOT_TOKEN"):
                continue
            _, _, val = line.partition("=")
            return val.strip().strip('"').strip("'")
    except Exception:
        return None
    return None


def _bot_id_from_token(token: str) -> Optional[int]:
    """Telegram bot token format: `<bot_id>:<secret>`."""
    if not token or ":" not in token:
        return None
    try:
        return int(token.split(":", 1)[0])
    except ValueError:
        return None


def _load_default_chat_id(hub: Path, bot_id: Optional[int] = None) -> Optional[int]:
    """Pick primo chat_id user da `<hub>/config.json::telegram.allowed_chat_ids`,
    saltando il bot_id stesso (che spesso è nella lista perché il daemon
    la popola via getUpdates → autores)."""
    cfg_path = hub / "config.json"
    if not cfg_path.is_file():
        return None
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        ids = (data.get("telegram") or {}).get("allowed_chat_ids") or []
        for raw in ids:
            try:
                cid = int(raw)
            except (TypeError, ValueError):
                continue
            if bot_id is not None and cid == bot_id:
                continue
            return cid
        return None
    except Exception:
        return None


def _post(url: str, data: dict, timeout: int = 15) -> dict:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "error_code": e.code, "description": str(e)}
    except Exception as e:
        return {"ok": False, "description": f"{type(e).__name__}: {e}"}


def _chunks(text: str) -> list[str]:
    if len(text) <= MAX_LEN:
        return [text]
    out, cur = [], text
    while len(cur) > MAX_LEN:
        cut = cur.rfind("\n", 0, MAX_LEN)
        if cut < MAX_LEN // 2:
            cut = MAX_LEN
        out.append(cur[:cut])
        cur = cur[cut:].lstrip("\n")
    out.append(cur)
    return out


def send_telegram(cfg: dict, body: str, hub: Path) -> dict:
    token = cfg.get("token")
    if not token or (isinstance(token, str) and token.startswith("{{")):
        token = _load_token_from_secrets(hub)
    if not token:
        return {"status": "failed", "details": "missing TELEGRAM_BOT_TOKEN (cfg.token or hub .secrets.env)"}

    chat_id = cfg.get("chat_id")
    if isinstance(chat_id, str) and chat_id.startswith("{{"):
        chat_id = None  # unexpanded secret → treat as missing
    if chat_id is None:
        chat_id = _load_default_chat_id(hub, bot_id=_bot_id_from_token(token))
    if chat_id is None:
        return {"status": "failed", "details": "missing chat_id (cfg.chat_id or hub config.json telegram.allowed_chat_ids)"}
    try:
        chat_id = int(chat_id)
    except (TypeError, ValueError):
        return {"status": "failed", "details": f"chat_id must be int, got {chat_id!r}"}

    title = cfg.get("title")
    text = f"*{title}*\n{body}" if title else body
    parse_mode = cfg.get("parse_mode", "Markdown")
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"

    parts = _chunks(text)
    last = {}
    for i, ch in enumerate(parts):
        payload = {"chat_id": str(chat_id), "text": ch, "parse_mode": parse_mode}
        last = _post(url, payload)
        if not last.get("ok"):
            # fallback senza parse_mode (Telegram rejects on Markdown parse error)
            last = _post(url, {"chat_id": str(chat_id), "text": ch})
            if not last.get("ok"):
                desc = last.get("description") or last.get("error_code") or "unknown"
                return {"status": "failed", "details": f"chunk {i+1}/{len(parts)}: {desc}"}
    return {"status": "success", "details": f"sent {len(parts)} chunk(s) to chat {chat_id}"}
