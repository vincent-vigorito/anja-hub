"""
google_chat.py — invio messaggio Google Chat via incoming webhook.

Config:
    type: google_chat
    webhook_url: "{{GCHAT_WEBHOOK_URL}}"
    title: optional title
"""

import json
import urllib.request
from pathlib import Path


GCHAT_TEXT_LIMIT = 4000  # Google Chat hard limit per text message


def send_gchat(cfg: dict, body: str, hub: Path) -> dict:
    url = cfg.get("webhook_url")
    if not url or url.startswith("{{"):
        return {"status": "failed", "details": "missing webhook_url (or unexpanded secret)"}

    title = cfg.get("title")
    text = f"*{title}*\n{body}" if title else body
    if len(text) > GCHAT_TEXT_LIMIT:
        text = text[: GCHAT_TEXT_LIMIT - 80] + "\n\n…(truncated)"

    payload = {"text": text}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=UTF-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            code = resp.getcode()
            if 200 <= code < 300:
                return {"status": "success", "details": f"http {code}"}
            return {"status": "failed", "details": f"http {code}"}
    except Exception as e:
        return {"status": "failed", "details": f"{type(e).__name__}: {e}"}
