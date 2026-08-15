"""
email.py — invio email via SMTP.

Config attesa nel yaml (3 modi):

1) **Inline SMTP per-routine** (raccomandato — un agent può avere il suo account):
    type: email
    to: you@example.com
    subject: "[anja] Daily — {date}"
    smtp:
      host: smtp.gmail.com
      port: 587
      user: news-agent@example.com
      password: "{{SMTP_PASS_NEWS_AGENT}}"   # secret reference
      from: "anja News <news-agent@example.com>"
      tls: true

2) **SMTP da .secrets.env del hub** (fallback ai env globali):
    SMTP_HOST=...
    SMTP_PORT=587
    SMTP_USER=...
    SMTP_PASS=...
    SMTP_FROM=...
    SMTP_TLS=true

3) Mix: campi inline, gli altri da env.
"""

import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path


def _resolve_smtp(cfg_smtp: dict = None) -> dict:
    """Cerca SMTP creds. Priorità: cfg_smtp (inline yaml) → env (.secrets.env / SMTP_*).
    cfg_smtp è già stato expandato dei {{SECRETS}} dal runner."""
    cfg_smtp = cfg_smtp or {}

    def _v(key_cfg, key_env, default=None):
        v = cfg_smtp.get(key_cfg)
        if v not in (None, "", "{{" + key_env + "}}"):
            return v
        return os.environ.get(key_env, default)

    port = _v("port", "SMTP_PORT", "587")
    try:
        port = int(port) if port is not None else 587
    except (ValueError, TypeError):
        port = 587

    tls_raw = _v("tls", "SMTP_TLS", "true")
    use_tls = str(tls_raw).lower() not in ("false", "0", "no")

    user = _v("user", "SMTP_USER")
    sender = _v("from", "SMTP_FROM") or user

    return {
        "host": _v("host", "SMTP_HOST"),
        "port": port,
        "user": user,
        "password": _v("password", "SMTP_PASS"),
        "sender": sender,
        "use_tls": use_tls,
    }


def _interpolate_subject(subject: str) -> str:
    return subject.replace("{date}", datetime.now().strftime("%Y-%m-%d"))


def send_email(cfg: dict, body: str, hub: Path) -> dict:
    cfg_smtp = cfg.get("smtp") if isinstance(cfg.get("smtp"), dict) else {}

    smtp = _resolve_smtp(cfg_smtp)
    if not smtp["host"] or not smtp["user"] or not smtp["password"]:
        # prova a caricare da .secrets.env (fallback se non nell'env)
        from routine_registry import secrets_path  # type: ignore
        sp = secrets_path(hub)
        if sp.is_file():
            for line in sp.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() not in os.environ:
                    os.environ[k.strip()] = v.strip().strip('"').strip("'")
            smtp = _resolve_smtp(cfg_smtp)

    if not smtp["host"] or not smtp["user"] or not smtp["password"]:
        return {
            "status": "failed",
            "details": "SMTP creds missing — fornisci 'smtp:' inline nel yaml o configura SMTP_* in .secrets.env",
        }

    to = cfg.get("to")
    if not to:
        return {"status": "failed", "details": "missing 'to'"}
    if isinstance(to, str):
        to = [to]

    subject = _interpolate_subject(cfg.get("subject", "[anja] routine output"))
    sender = cfg.get("from") or smtp["sender"]
    cc = cfg.get("cc") or []
    if isinstance(cc, str):
        cc = [cc]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)

    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        if smtp["port"] == 465:
            server = smtplib.SMTP_SSL(smtp["host"], smtp["port"], timeout=30)
        else:
            server = smtplib.SMTP(smtp["host"], smtp["port"], timeout=30)
            if smtp["use_tls"]:
                server.starttls()
        server.login(smtp["user"], smtp["password"])
        server.sendmail(sender, to + cc, msg.as_string())
        server.quit()
    except Exception as e:
        return {"status": "failed", "details": f"{type(e).__name__}: {e}"}

    return {"status": "success", "details": f"sent to {to}"}
