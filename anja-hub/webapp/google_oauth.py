"""google_oauth.py — flow OAuth Google in-app (collega GSC/GA dall'hub).

"Collega Google" → consenso browser → il token (con refresh_token) viene salvato
in <scope>/.anjawiki/google-token.json, pronto per google_collect. Serve un OAuth
client (Desktop o Web) in <hub>/.anjawiki/google-oauth-client.json (lo carica una
volta l'admin dell'hub dalla propria Google Cloud Console).

Lo state OAuth (CSRF) mappa in memoria → la dir dove salvare il token (single
process; se serve multi-worker, va su store condiviso).
"""

from __future__ import annotations

import os
from pathlib import Path

# Con include_granted_scopes Google può restituire più scope di quelli chiesti:
# senza questo flag oauthlib tratta il mismatch come errore nel fetch_token.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

SCOPES = [
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/content",
]
CLIENT_NAME = "google-oauth-client.json"
TOKEN_NAME = "google-token.json"

_PENDING: dict[str, tuple[str, str]] = {}   # state → (token dir, code_verifier PKCE)


def client_file(hub_dir: Path) -> Path | None:
    p = Path(hub_dir) / CLIENT_NAME
    return p if p.is_file() else None


def _flow(client: Path, redirect_uri: str):
    from google_auth_oauthlib.flow import Flow
    return Flow.from_client_secrets_file(str(client), scopes=SCOPES, redirect_uri=redirect_uri)


def start(hub_dir: Path, redirect_uri: str, token_dir: Path) -> str:
    """Genera l'auth_url e registra state→token_dir. '' se manca l'OAuth client."""
    client = client_file(hub_dir)
    if not client:
        return ""
    flow = _flow(client, redirect_uri)
    url, state = flow.authorization_url(access_type="offline", prompt="consent",
                                        include_granted_scopes="true")
    # PKCE: le google-auth-oauthlib recenti generano un code_verifier nell'auth
    # request — il callback ricostruisce il flow e DEVE riusare lo stesso
    # verifier, o Google risponde invalid_grant "Missing code verifier".
    _PENDING[state] = (str(token_dir), getattr(flow, "code_verifier", "") or "")
    return url


def callback(hub_dir: Path, redirect_uri: str, code: str, state: str) -> dict:
    """Scambia il code per il token e lo salva in <token_dir>/google-token.json (0600)."""
    pending = _PENDING.pop(state, None)
    if not pending:
        return {"ok": False, "error": "state non valido o scaduto"}
    token_dir, code_verifier = pending
    client = client_file(hub_dir)
    if not client:
        return {"ok": False, "error": "OAuth client non configurato"}
    try:
        flow = _flow(client, redirect_uri)
        if code_verifier:
            flow.code_verifier = code_verifier
        flow.fetch_token(code=code)
        creds = flow.credentials
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"scambio token fallito: {e}"}
    dest = Path(token_dir) / TOKEN_NAME
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")   # 0600 atomico (tmp + replace)
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    os.replace(tmp, dest)
    try:
        os.chmod(dest, 0o600)
    except OSError:
        pass
    return {"ok": True}


def status(token_dir: Path, hub_dir: Path) -> dict:
    """Stato collegamento: client OAuth configurato? token presente (ws o hub)?"""
    import google_collect
    token = google_collect.find_token(token_dir, Path(hub_dir))
    where = ""
    if token:
        where = "workspace" if Path(token).parent == Path(token_dir) else "hub"
    return {"client_configured": client_file(hub_dir) is not None,
            "connected": bool(token), "token_scope": where}
