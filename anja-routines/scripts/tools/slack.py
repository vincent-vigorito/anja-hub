"""
slack.py — invio messaggio Slack via incoming webhook.

Config:
    type: slack
    webhook_url: "{{SLACK_WEBHOOK_URL}}"     # già expanded dal runner
    title: optional title (will be bold prefix)
"""

import json
import urllib.request
from pathlib import Path


def send_slack(cfg: dict, body: str, hub: Path) -> dict:
    url = cfg.get("webhook_url")
    if not url or url.startswith("{{"):
        return {"status": "failed", "details": "missing webhook_url (or unexpanded secret)"}

    title = cfg.get("title")
    text = f"*{title}*\n{body}" if title else body
    # Slack limita a ~40k chars; tronca per sicurezza
    if len(text) > 30000:
        text = text[:30000] + "\n\n…(truncated)"

    payload = {"text": text}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            code = resp.getcode()
            if 200 <= code < 300:
                return {"status": "success", "details": f"http {code}"}
            return {"status": "failed", "details": f"http {code}"}
    except Exception as e:
        return {"status": "failed", "details": f"{type(e).__name__}: {e}"}
