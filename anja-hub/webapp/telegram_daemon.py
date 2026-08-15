"""telegram_daemon.py — inbound Telegram bot polling daemon (Fase 11 M-Tg).

Pattern: getUpdates long-polling, no public URL needed. Gira come asyncio
background task dentro server.py uvicorn.

Sicurezza:
- Allow-list di chat_id obbligatoria (config.telegram.allowed_chat_ids).
- Se chat_id sconosciuto invia messaggio: replica con istruzioni di onboarding
  e logga il chat_id per copia-incolla in config.

Conversation: ogni chat_id Telegram ha N thread in <webapp>/conversations/
(stesso schema della webapp chat): `telegram-{chat_id}` (main) + `telegram-{chat_id}-tN`.
Il thread attivo è in conversations/.telegram_threads.json; switch via /threads
(F-TelegramMultiSession — la logica vive in server.py, qui solo transport).

Stdlib only (urllib.request per HTTP, json, asyncio).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Callable, Awaitable, Any

TELEGRAM_API_BASE = "https://api.telegram.org"
DEFAULT_POLL_INTERVAL_SEC = 2
DEFAULT_TIMEOUT_SEC = 25  # long-polling
OFFSET_FILENAME = ".telegram_offset.json"


# =================================================================
# Config + secrets
# =================================================================

def load_config(hub_path: Path) -> dict:
    """Read telegram block dal hub config.json. Default: disabled."""
    cfg = hub_path / "config.json"
    if not cfg.is_file():
        return {"enabled": False}
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
        tg = data.get("telegram") or {}
        return {
            "enabled": bool(tg.get("enabled", False)),
            "allowed_chat_ids": [int(x) for x in tg.get("allowed_chat_ids", [])],
            "poll_interval_sec": int(tg.get("poll_interval_sec", DEFAULT_POLL_INTERVAL_SEC)),
            "default_agent": tg.get("default_agent", ""),  # "" = hub default (Anja)
        }
    except Exception as e:
        print(f"[telegram] config parse error: {e}")
        return {"enabled": False}


def load_token(hub_path: Path) -> Optional[str]:
    """Read TELEGRAM_BOT_TOKEN da `<hub>/.secrets.env`."""
    env_file = hub_path / ".secrets.env"
    if not env_file.is_file():
        return os.environ.get("TELEGRAM_BOT_TOKEN")
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("TELEGRAM_BOT_TOKEN"):
                _, _, val = line.partition("=")
                return val.strip().strip('"').strip("'")
    except Exception:
        pass
    return os.environ.get("TELEGRAM_BOT_TOKEN")


def _offset_path(hub_path: Path) -> Path:
    return hub_path / OFFSET_FILENAME


def load_offset(hub_path: Path) -> int:
    f = _offset_path(hub_path)
    if not f.is_file():
        return 0
    try:
        return int(json.loads(f.read_text(encoding="utf-8")).get("offset", 0))
    except Exception:
        return 0


def save_offset(hub_path: Path, offset: int) -> None:
    try:
        _offset_path(hub_path).write_text(
            json.dumps({"offset": offset, "updated": time.time()}),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[telegram] save_offset error: {e}")


# =================================================================
# Telegram REST helpers (stdlib urllib)
# =================================================================

async def _http_get(url: str, timeout: int = 30) -> dict:
    """Async wrapper attorno urllib.request (run in thread executor)."""
    loop = asyncio.get_running_loop()
    def _blocking():
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    return await loop.run_in_executor(None, _blocking)


async def _http_post(url: str, data: dict, timeout: int = 15) -> dict:
    loop = asyncio.get_running_loop()
    def _blocking():
        body = urllib.parse.urlencode(data).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                err_body = json.loads(e.read().decode("utf-8"))
            except Exception:
                err_body = {"error_code": e.code, "description": str(e)}
            return err_body
    return await loop.run_in_executor(None, _blocking)


async def get_updates(token: str, offset: int, timeout: int = DEFAULT_TIMEOUT_SEC) -> dict:
    """Telegram long-polling getUpdates."""
    url = f"{TELEGRAM_API_BASE}/bot{token}/getUpdates?offset={offset}&timeout={timeout}"
    return await _http_get(url, timeout=timeout + 5)


async def send_message(token: str, chat_id: int, text: str,
                      parse_mode: str = "Markdown",
                      reply_markup: Optional[dict] = None) -> dict:
    """Telegram sendMessage. Markdown by default; fallback to plain on error.

    `reply_markup`: dict con `keyboard` (reply keyboard) o `inline_keyboard` (callback).
    """
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    MAX = 4000

    def _payload(text_chunk, with_markup=False):
        d = {"chat_id": str(chat_id), "text": text_chunk, "parse_mode": parse_mode}
        if with_markup and reply_markup is not None:
            d["reply_markup"] = json.dumps(reply_markup)
        return d

    if len(text) <= MAX:
        resp = await _http_post(url, _payload(text, with_markup=True))
        if not resp.get("ok"):
            # Markdown malformato (```code```, backtick, _ , | …) → Telegram 400 "can't parse
            # entities". Fallback: reinvia in plain text senza parse_mode (consegna garantita).
            d = {"chat_id": str(chat_id), "text": text}
            if reply_markup is not None:
                d["reply_markup"] = json.dumps(reply_markup)
            resp = await _http_post(url, d)
        return resp

    chunks = []
    cur = text
    while len(cur) > MAX:
        cut = cur.rfind("\n", 0, MAX)
        if cut < MAX // 2:
            cut = MAX
        chunks.append(cur[:cut])
        cur = cur[cut:].lstrip("\n")
    chunks.append(cur)
    last_resp = {}
    for i, ch in enumerate(chunks):
        # markup solo nell'ultimo chunk
        is_last = (i == len(chunks) - 1)
        last_resp = await _http_post(url, _payload(ch, with_markup=is_last))
        if not last_resp.get("ok"):
            last_resp = await _http_post(url, {"chat_id": str(chat_id), "text": ch})
    return last_resp


async def set_my_commands(token: str, commands: list[dict]) -> dict:
    """Registra il menu '/' del bot.
    `commands` = [{"command": "help", "description": "..."}].
    """
    url = f"{TELEGRAM_API_BASE}/bot{token}/setMyCommands"
    return await _http_post(url, {"commands": json.dumps(commands)})


async def answer_callback_query(token: str, callback_query_id: str, text: str = "") -> dict:
    """Acknowledge inline button click (rimuove spinner). Opzionale text mostra toast."""
    url = f"{TELEGRAM_API_BASE}/bot{token}/answerCallbackQuery"
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    return await _http_post(url, payload)


async def delete_message(token: str, chat_id: int, message_id: int) -> dict:
    """Cancella un messaggio del bot (usato per rimuovere l'heartbeat a turno finito)."""
    url = f"{TELEGRAM_API_BASE}/bot{token}/deleteMessage"
    return await _http_post(url, {"chat_id": str(chat_id), "message_id": message_id})


async def edit_message_text(token: str, chat_id: int, message_id: int, text: str,
                            parse_mode: str = "Markdown",
                            reply_markup: Optional[dict] = None) -> dict:
    """Modifica un messaggio esistente (usato dopo callback per aggiornare inline keyboard)."""
    url = f"{TELEGRAM_API_BASE}/bot{token}/editMessageText"
    payload = {"chat_id": str(chat_id), "message_id": message_id, "text": text, "parse_mode": parse_mode}
    if reply_markup is not None:
        payload["reply_markup"] = json.dumps(reply_markup)
    return await _http_post(url, payload)


# Reply keyboard persistente con azioni quick (mostrata dopo ogni risposta Anja)
QUICK_REPLY_KEYBOARD = {
    "keyboard": [
        [{"text": "📊 Status"}, {"text": "🔄 New"}],
        [{"text": "📦 Compact"}, {"text": "❓ Help"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
    "input_field_placeholder": "Scrivi ad Anja, o tap un'azione…",
}

# Map dei testi keyboard ai comandi slash equivalenti
QUICK_REPLY_TO_COMMAND = {
    "📊 Status": "/status",
    "🔄 New": "/newchat",
    "📦 Compact": "/compact",
    "❓ Help": "/help",
}

# =================================================================
# Voice/audio transcription
# =================================================================

# Default chain: prova in ordine, primo che ha key disponibile
TRANSCRIPTION_FALLBACK_CHAIN = [
    "whisper-1",                          # OpenAI (paid, accurate)
    "groq/whisper-large-v3-turbo",        # Groq (free, fast)
]


async def get_file_path(token: str, file_id: str) -> Optional[str]:
    """Telegram getFile → file_path relativo per download."""
    url = f"{TELEGRAM_API_BASE}/bot{token}/getFile"
    r = await _http_post(url, {"file_id": file_id})
    if not r.get("ok"):
        return None
    return r.get("result", {}).get("file_path")


async def download_telegram_file(token: str, file_path: str, dest: Path) -> bool:
    """Scarica un file dal Telegram CDN. Stdlib only."""
    url = f"{TELEGRAM_API_BASE}/file/bot{token}/{file_path}"
    loop = asyncio.get_running_loop()
    def _blocking():
        try:
            urllib.request.urlretrieve(url, str(dest))
            return True
        except Exception as e:
            print(f"[telegram] download error: {e}")
            return False
    return await loop.run_in_executor(None, _blocking)


def _load_audio_stt_model(hub_path: Path) -> Optional[str]:
    """Read audio.stt config dal hub config.json. Returns model_id LiteLLM-compatible.
    Returns None se config assente → caller usa fallback chain.
    """
    cfg = hub_path / "config.json"
    if not cfg.is_file():
        return None
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
        stt = (data.get("audio") or {}).get("stt") or {}
        model = stt.get("model", "").strip()
        provider = stt.get("provider", "").strip()
        if not model:
            return None
        # Se model non ha prefix provider e provider richiesto → aggiungi
        if provider and "/" not in model and provider != "openai":
            return f"{provider}/{model}"
        return model
    except Exception:
        return None


def _load_audio_tts_config(hub_path: Path) -> dict:
    """Read audio.tts dict from hub config.json. Returns dict with provider/model/voice."""
    cfg = hub_path / "config.json"
    if not cfg.is_file():
        return {"provider": "openai", "model": "tts-1", "voice": "nova"}
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
        tts = (data.get("audio") or {}).get("tts") or {}
        return {
            "provider": tts.get("provider", "openai"),
            "model": tts.get("model", "tts-1"),
            "voice": tts.get("voice", "nova"),
        }
    except Exception:
        return {"provider": "openai", "model": "tts-1", "voice": "nova"}


def _ffmpeg_available() -> bool:
    """Check ffmpeg installed (cached after first call)."""
    if not hasattr(_ffmpeg_available, "_cached"):
        import shutil
        _ffmpeg_available._cached = shutil.which("ffmpeg") is not None  # type: ignore
    return _ffmpeg_available._cached  # type: ignore


async def _convert_to_opus(audio_bytes: bytes, input_codec: str = "mp3") -> Optional[bytes]:
    """Convert audio bytes (mp3/wav/etc) to opus/ogg via ffmpeg subprocess.
    Returns None se ffmpeg non disponibile o errore."""
    if not _ffmpeg_available():
        return None
    import tempfile
    import subprocess
    loop = asyncio.get_running_loop()
    def _blocking():
        with tempfile.NamedTemporaryFile(suffix=f".{input_codec}", delete=False) as inp:
            inp.write(audio_bytes)
            inp_path = inp.name
        out_path = inp_path.replace(f".{input_codec}", ".ogg")
        try:
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", inp_path,
                 "-c:a", "libopus", "-b:a", "48k", "-vn",
                 out_path],
                capture_output=True, timeout=30,
            )
            if r.returncode != 0:
                print(f"[telegram] ffmpeg opus conversion failed: {r.stderr.decode()[:200]}")
                return None
            with open(out_path, "rb") as f:
                return f.read()
        finally:
            for p in (inp_path, out_path):
                try:
                    Path(p).unlink(missing_ok=True)
                except Exception:
                    pass
    return await loop.run_in_executor(None, _blocking)


async def _xai_tts_direct(text: str, voice: str, language: str = "en") -> tuple[bytes, str]:
    """xAI TTS batch via REST `https://api.x.ai/v1/tts`. Schema diverso da OpenAI:
    - field: `text` (non `input`)
    - field: `voice_id` (non `voice`)
    - field: `language` richiesto
    - codec options: mp3/wav/pcm/mulaw/alaw (NO opus)
    - no `model` field (modello fisso)

    Returns (audio_bytes, codec). Codec usato: mp3.
    """
    api_key = os.environ.get("XAI_API_KEY")
    if not api_key:
        raise RuntimeError("XAI_API_KEY not set in env")
    url = "https://api.x.ai/v1/tts"
    payload = json.dumps({
        "text": text[:14000],  # xAI cap 15k char
        "voice_id": voice,
        "language": language,
        "output_format": {"codec": "mp3", "sample_rate": 24000, "bit_rate": 128000},
    }).encode("utf-8")
    loop = asyncio.get_running_loop()
    def _blocking():
        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "audio/*")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            try:
                err = e.read().decode("utf-8")
            except Exception:
                err = str(e)
            raise RuntimeError(f"xAI TTS HTTP {e.code}: {err}")
    audio = await loop.run_in_executor(None, _blocking)
    return audio, "mp3"


async def synthesize_speech(text: str, hub_path: Optional[Path] = None,
                            target_format: str = "opus") -> tuple[bytes, str, str]:
    """Sintetizza testo → audio bytes.

    Returns (audio_bytes, model_used, codec). Codec: 'opus' (sendVoice) o 'mp3' (sendAudio).
    Routing:
    - xai → direct HTTP a api.x.ai/v1/tts, restituisce mp3
    - openai/groq/elevenlabs → via LiteLLM speech() con opus per Telegram
    """
    cfg = _load_audio_tts_config(hub_path) if hub_path else {
        "provider": "openai", "model": "tts-1", "voice": "nova"
    }
    provider = cfg["provider"]
    model = cfg["model"]
    voice = cfg["voice"]

    # xAI: direct HTTP (LiteLLM non supporta /v1/tts xAI)
    if provider == "xai":
        try:
            audio_bytes, codec = await _xai_tts_direct(text, voice, language="it")
        except Exception as e:
            raise RuntimeError(f"xAI TTS failed (voice={voice}): {e}")
        # Best-effort: converti mp3 → opus per Telegram voice message nativo
        if codec == "mp3" and target_format == "opus":
            opus_bytes = await _convert_to_opus(audio_bytes, input_codec="mp3")
            if opus_bytes:
                return opus_bytes, f"xai/voice:{voice}", "opus"
        return audio_bytes, f"xai/voice:{voice}", codec

    # Provider standard via LiteLLM
    try:
        import litellm
        litellm.suppress_debug_info = True
    except ImportError:
        raise RuntimeError("litellm not installed")

    if provider and provider != "openai" and "/" not in model:
        model_id = f"{provider}/{model}"
    else:
        model_id = model

    try:
        loop = asyncio.get_running_loop()
        def _blocking_tts():
            return litellm.speech(
                model=model_id,
                voice=voice,
                input=text[:4096],
                response_format=target_format,
            )
        resp = await loop.run_in_executor(None, _blocking_tts)
        audio_bytes = getattr(resp, "content", None)
        if audio_bytes is None and hasattr(resp, "read"):
            audio_bytes = resp.read()
        if not audio_bytes:
            raise RuntimeError("empty audio response")
        return audio_bytes, model_id, target_format
    except Exception as e:
        raise RuntimeError(f"TTS via {model_id} failed: {type(e).__name__}: {e}")


async def send_audio(token: str, chat_id: int, audio_bytes: bytes,
                    title: str = "Anja", ext: str = "mp3") -> dict:
    """Telegram sendAudio (per mp3/wav/m4a — formati non opus).
    Si visualizza come audio file con controlli playback.
    """
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendAudio"
    fields = {"chat_id": str(chat_id), "title": title}
    mime = {"mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4"}.get(ext, "audio/mpeg")
    files = {"audio": (f"anja.{ext}", audio_bytes, mime)}
    body, content_type = _multipart_encode(fields, files)
    loop = asyncio.get_running_loop()
    def _blocking():
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8"))
            except Exception:
                return {"ok": False, "error_code": e.code, "description": str(e)}
    return await loop.run_in_executor(None, _blocking)


def _multipart_encode(fields: dict, files: dict) -> tuple[bytes, str]:
    """Build multipart/form-data body. files = {field_name: (filename, bytes, content_type)}.
    Returns (body_bytes, content_type_with_boundary).
    """
    import secrets as _secrets
    boundary = "----anja" + _secrets.token_hex(16)
    lines = []
    for key, value in fields.items():
        lines.append(f"--{boundary}".encode())
        lines.append(f'Content-Disposition: form-data; name="{key}"'.encode())
        lines.append(b"")
        lines.append(str(value).encode("utf-8"))
    for field_name, (filename, content, ctype) in files.items():
        lines.append(f"--{boundary}".encode())
        lines.append(
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"'.encode()
        )
        lines.append(f"Content-Type: {ctype}".encode())
        lines.append(b"")
        lines.append(content)
    lines.append(f"--{boundary}--".encode())
    lines.append(b"")
    body = b"\r\n".join(lines)
    return body, f"multipart/form-data; boundary={boundary}"


async def send_voice(token: str, chat_id: int, audio_bytes: bytes,
                    caption: Optional[str] = None) -> dict:
    """Telegram sendVoice. Audio deve essere .ogg con Opus codec.
    Limite: 1MB voice messages, 50MB audio files.
    """
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendVoice"
    fields = {"chat_id": str(chat_id)}
    if caption:
        fields["caption"] = caption[:1024]
        fields["parse_mode"] = "Markdown"
    files = {"voice": ("anja.ogg", audio_bytes, "audio/ogg")}
    body, content_type = _multipart_encode(fields, files)
    loop = asyncio.get_running_loop()
    def _blocking():
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8"))
            except Exception:
                return {"ok": False, "error_code": e.code, "description": str(e)}
    return await loop.run_in_executor(None, _blocking)


_MEDIA_SEND = {
    ".png": ("sendPhoto", "photo", "image/png"),
    ".jpg": ("sendPhoto", "photo", "image/jpeg"),
    ".jpeg": ("sendPhoto", "photo", "image/jpeg"),
    ".webp": ("sendPhoto", "photo", "image/webp"),
    ".gif": ("sendDocument", "document", "image/gif"),
    ".mp4": ("sendVideo", "video", "video/mp4"),
    ".mov": ("sendVideo", "video", "video/quicktime"),
    ".webm": ("sendDocument", "document", "video/webm"),
}


async def send_media(token: str, chat_id: int, file_path: str,
                     caption: str = "") -> dict:
    """Invia un file media generato (foto/video) nella chat. Fallback a
    sendDocument se sendPhoto/sendVideo rifiuta (es. PNG oltre i limiti photo)."""
    p = Path(file_path)
    if not p.is_file():
        return {"ok": False, "description": f"file not found: {file_path}"}
    method, field, mime = _MEDIA_SEND.get(p.suffix.lower(),
                                          ("sendDocument", "document", "application/octet-stream"))
    data = p.read_bytes()
    loop = asyncio.get_running_loop()

    def _post(m: str, f: str):
        fields = {"chat_id": str(chat_id)}
        if caption:
            fields["caption"] = caption[:1024]
        body, content_type = _multipart_encode(fields, {f: (p.name, data, mime)})
        req = urllib.request.Request(f"{TELEGRAM_API_BASE}/bot{token}/{m}",
                                     data=body, method="POST")
        req.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8"))
            except Exception:
                return {"ok": False, "error_code": e.code, "description": str(e)}

    resp = await loop.run_in_executor(None, lambda: _post(method, field))
    if not resp.get("ok") and method != "sendDocument":
        resp = await loop.run_in_executor(None, lambda: _post("sendDocument", "document"))
    return resp


def _resolve_user_language(hub_path: Optional[Path]) -> Optional[str]:
    """Best-effort: ricava lingua preferita dell'utente da `users/<default_user>.md` frontmatter."""
    if not hub_path:
        return None
    try:
        cfg = json.loads((hub_path / "config.json").read_text(encoding="utf-8"))
        slug = cfg.get("default_user")
        if not slug:
            return None
        user_file = hub_path / "users" / f"{slug}.md"
        if not user_file.is_file():
            return None
        text = user_file.read_text(encoding="utf-8")
        # frontmatter: languages: [it] or languages: [it, en]
        import re as _re
        m = _re.search(r"^languages:\s*\[([^\]]+)\]", text, _re.M)
        if m:
            langs = [l.strip().strip('"\'') for l in m.group(1).split(",")]
            return langs[0] if langs else None
    except Exception:
        pass
    return None


async def _xai_stt_direct(file_path: Path, language: Optional[str] = None) -> str:
    """xAI Speech-to-Text diretto via REST `POST https://api.x.ai/v1/stt`.

    litellm non supporta `xai` per la transcription, quindi — come per il TTS
    (`_xai_tts_direct`) — chiamiamo l'endpoint a mano. Multipart: `model=grok-stt`,
    `format`, `language`, e `file` PER ULTIMO (richiesto dall'API xAI). Ritorna il testo.
    """
    api_key = os.environ.get("XAI_API_KEY")
    if not api_key:
        raise RuntimeError("XAI_API_KEY not set in env")
    url = "https://api.x.ai/v1/stt"
    fields = {"model": "grok-stt", "format": "true"}
    if language:
        fields["language"] = language
    with open(file_path, "rb") as f:
        audio_bytes = f.read()
    ext = file_path.suffix.lower().lstrip(".")
    mime = {"mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg",
            "oga": "audio/ogg", "m4a": "audio/mp4", "mp4": "audio/mp4",
            "webm": "audio/webm", "flac": "audio/flac"}.get(ext, "application/octet-stream")
    # `file` deve essere l'ultimo campo del multipart (requisito xAI STT) → _multipart_encode
    # serializza i `fields` prima dei `files`, quindi l'ordine è corretto.
    files = {"file": (file_path.name, audio_bytes, mime)}
    body, content_type = _multipart_encode(fields, files)
    loop = asyncio.get_running_loop()

    def _blocking():
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                err = e.read().decode("utf-8")
            except Exception:
                err = str(e)
            raise RuntimeError(f"xAI STT HTTP {e.code}: {err}")

    data = await loop.run_in_executor(None, _blocking)
    if isinstance(data, str):
        return data.strip()
    text = data.get("text") or data.get("transcript") or data.get("transcription") or ""
    if not text and isinstance(data.get("results"), list):
        text = " ".join(r.get("text", "") for r in data["results"] if isinstance(r, dict))
    return (text or "").strip()


async def transcribe_audio(file_path: Path, model_chain: Optional[list] = None,
                          hub_path: Optional[Path] = None,
                          language: Optional[str] = None) -> tuple[str, str]:
    """Trascrive audio file via LiteLLM.

    Returns (text, model_used). Raises se tutti i fallback falliscono.

    Se `language` fornito (es. 'it') viene passato a Whisper per disambiguare
    (evita auto-detect su accenti dialettali che porta a portoghese/spagnolo).
    Se assente, prova a leggere `languages` dal user profile HOT.
    """
    try:
        import litellm
        litellm.suppress_debug_info = True
    except ImportError:
        raise RuntimeError("litellm not installed")

    if language is None and hub_path:
        language = _resolve_user_language(hub_path)

    chain: list = []
    if hub_path:
        configured = _load_audio_stt_model(hub_path)
        if configured:
            chain.append(configured)
    chain += [m for m in (model_chain or TRANSCRIPTION_FALLBACK_CHAIN) if m not in chain]

    last_error = None
    for model in chain:
        try:
            # xAI STT: litellm non supporta xai transcription → handler diretto /v1/stt
            if model.split("/", 1)[0] == "xai":
                text = (await _xai_stt_direct(file_path, language=language)).strip()
                if text:
                    return text, "xai/grok-stt"
                continue
            kwargs = {"model": model}
            if language:
                kwargs["language"] = language
            with open(file_path, "rb") as f:
                kwargs["file"] = f
                resp = await litellm.atranscription(**kwargs)
            text = (getattr(resp, "text", None) or
                    (resp.get("text") if isinstance(resp, dict) else None) or "").strip()
            if text:
                return text, model
        except Exception as e:
            last_error = f"{model}: {type(e).__name__}: {e}"
            print(f"[telegram] transcription via {model} failed: {e}")
            continue
    raise RuntimeError(f"All transcription backends failed. Last error: {last_error}")


# Comandi che Telegram mostra nel menu "/"
BOT_COMMANDS = [
    {"command": "help", "description": "Mostra elenco comandi"},
    {"command": "status", "description": "Provider, model, agent corrente"},
    {"command": "model", "description": "Cambia model (es. /model opus)"},
    {"command": "provider", "description": "Cambia provider (claude, xai, openai...)"},
    {"command": "agent", "description": "Switch ad agent specializzato (es. /agent trader)"},
    {"command": "project", "description": "Switch al context di un progetto registrato"},
    {"command": "queue", "description": "Schedula task per più tardi (es. /queue domani 9am ...)"},
    {"command": "threads", "description": "Lista/switch thread di conversazione"},
    {"command": "newchat", "description": "Nuovo thread (i precedenti restano)"},
    {"command": "compact", "description": "Compatta storia chat in summary"},
    {"command": "autocompact", "description": "Auto-compact su soglia % token (es. /autocompact 60)"},
    {"command": "voice", "description": "Risposta vocale: on|off|auto (default auto se gli mandi audio)"},
    {"command": "kanban", "description": "Kanban tasks: list/add/done/block/show"},
    {"command": "retry", "description": "Riprova l'ultimo turno interrotto/fallito"},
    {"command": "stop", "description": "Interrompi il turno in corso"},
    {"command": "mode", "description": "Permessi del thread: default|acceptEdits|plan|auto"},
    {"command": "allow", "description": "Consenti il permesso richiesto (always = impara)"},
    {"command": "deny", "description": "Nega il permesso richiesto"},
    {"command": "approve", "description": "Approva il piano proposto"},
    {"command": "replan", "description": "Chiedi una revisione del piano"},
    {"command": "merge", "description": "Applica le modifiche della git-sessione"},
    {"command": "discard", "description": "Scarta le modifiche della git-sessione"},
]


async def send_typing(token: str, chat_id: int) -> None:
    """Mostra 'typing...' al user mentre Anja pensa."""
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendChatAction"
    try:
        await _http_post(url, {"chat_id": str(chat_id), "action": "typing"})
    except Exception:
        pass


# =================================================================
# Onboarding helper: chat_id sconosciuto
# =================================================================

ONBOARDING_TEMPLATE = """\
Ciao! Sono Anja, il tuo personal AI assistant.

Per parlare con me devi essere autorizzato. Aggiungi questo chat_id alla allow-list:

  chat_id: `{chat_id}`

Modifica `<hub>/config.json` aggiungendo:
```
"telegram": {{
  "enabled": true,
  "allowed_chat_ids": [{chat_id}]
}}
```

Poi riavvia il server o chiama l'endpoint /api/telegram/reload.
"""


# =================================================================
# Main daemon loop
# =================================================================

class TelegramDaemon:
    """Polling daemon stateful. Riavviabile a runtime via stop()/start()."""

    def __init__(self, hub_path: Path, on_message: Callable[[dict], Awaitable[None]]):
        self.hub_path = hub_path
        self.on_message = on_message
        self.config: dict = load_config(hub_path)
        self.token: Optional[str] = load_token(hub_path)
        self.running = False
        self.task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self.last_error: Optional[str] = None
        self.unknown_chat_ids: list[int] = []  # log per onboarding UI
        self.message_count = 0
        self.last_message_at: Optional[float] = None

    def reload_config(self) -> dict:
        """Re-read config.json + .secrets.env (per cambi runtime)."""
        self.config = load_config(self.hub_path)
        self.token = load_token(self.hub_path)
        return {"enabled": self.config["enabled"], "has_token": bool(self.token)}

    def status(self) -> dict:
        return {
            "running": self.running,
            "enabled": self.config.get("enabled", False),
            "has_token": bool(self.token),
            "allowed_chat_ids": self.config.get("allowed_chat_ids", []),
            "default_agent": self.config.get("default_agent", ""),
            "poll_interval_sec": self.config.get("poll_interval_sec", DEFAULT_POLL_INTERVAL_SEC),
            "last_error": self.last_error,
            "unknown_chat_ids_seen": self.unknown_chat_ids[-10:],
            "message_count": self.message_count,
            "last_message_at": self.last_message_at,
        }

    async def start(self):
        if self.running:
            return
        if not self.config.get("enabled"):
            print("[telegram] daemon NOT started: telegram.enabled=false")
            return
        if not self.token:
            print("[telegram] daemon NOT started: TELEGRAM_BOT_TOKEN missing in .secrets.env")
            return
        self._stop_event.clear()
        self.running = True
        self.task = asyncio.create_task(self._run_loop(), name="telegram-daemon")
        # Registra menu "/" del bot (best-effort, non blocca avvio)
        try:
            r = await set_my_commands(self.token, BOT_COMMANDS)
            if r.get("ok"):
                print(f"[telegram] setMyCommands OK ({len(BOT_COMMANDS)} comandi)")
            else:
                print(f"[telegram] setMyCommands warn: {r}")
        except Exception as e:
            print(f"[telegram] setMyCommands error: {e}")
        print(f"[telegram] daemon STARTED. allow-list: {self.config['allowed_chat_ids']}")

    async def stop(self):
        if not self.running:
            return
        self._stop_event.set()
        if self.task:
            try:
                await asyncio.wait_for(self.task, timeout=5)
            except asyncio.TimeoutError:
                self.task.cancel()
        self.running = False
        print("[telegram] daemon STOPPED")

    async def _run_loop(self):
        offset = load_offset(self.hub_path)
        while not self._stop_event.is_set():
            try:
                data = await get_updates(self.token, offset)
            except Exception as e:
                self.last_error = f"getUpdates: {type(e).__name__}: {e}"
                print(f"[telegram] {self.last_error}")
                await asyncio.sleep(5)
                continue

            if not data.get("ok"):
                self.last_error = f"getUpdates !ok: {data}"
                print(f"[telegram] {self.last_error}")
                await asyncio.sleep(10)
                continue

            self.last_error = None
            updates = data.get("result", [])
            for upd in updates:
                offset = max(offset, int(upd.get("update_id", 0)) + 1)
                try:
                    await self._handle_update(upd)
                except Exception as e:
                    print(f"[telegram] handler error: {type(e).__name__}: {e}")

            save_offset(self.hub_path, offset)
            await asyncio.sleep(self.config.get("poll_interval_sec", DEFAULT_POLL_INTERVAL_SEC))

    async def _handle_update(self, upd: dict):
        # Inline button callback: arriva come callback_query
        cbq = upd.get("callback_query")
        if cbq:
            await self._handle_callback_query(cbq)
            return

        msg = upd.get("message") or upd.get("edited_message")
        if not msg:
            return
        chat = msg.get("chat") or {}
        chat_id = int(chat.get("id", 0))
        text = (msg.get("text") or "").strip()

        # Voice / audio: trascrivi via Whisper, sostituisci `text`
        voice = msg.get("voice") or msg.get("audio")
        is_voice = bool(voice)
        if is_voice and not chat_id:
            return
        if not text and not is_voice:
            return

        allowed = self.config.get("allowed_chat_ids") or []
        if chat_id not in allowed:
            if chat_id not in self.unknown_chat_ids:
                self.unknown_chat_ids.append(chat_id)
                try:
                    import notification_bus as _nb
                    _nb.publish(
                        self.hub_path, source="telegram", category="warn",
                        title="Unknown Telegram chat_id",
                        body=f"chat_id={chat_id} username='{chat.get('username','?')}' — onboarding inviato",
                        action={"label": "Add to allow-list", "url": "/#settings/telegram", "type": "navigate"},
                        payload={"chat_id": chat_id, "username": chat.get("username")},
                    )
                except Exception:
                    pass
            print(f"[telegram] unknown chat_id {chat_id} from '{chat.get('username','?')}'")
            try:
                await send_message(self.token, chat_id, ONBOARDING_TEMPLATE.format(chat_id=chat_id))
            except Exception as e:
                print(f"[telegram] onboarding reply error: {e}")
            return

        # Trascrivi audio se presente
        if is_voice:
            try:
                await send_typing(self.token, chat_id)
                file_id = voice.get("file_id")
                fp = await get_file_path(self.token, file_id)
                if not fp:
                    await send_message(self.token, chat_id, "⚠ Non sono riuscito a scaricare l'audio.")
                    return
                # Download in tempfile (suffix da mime_type, fallback .oga)
                import tempfile
                mime = voice.get("mime_type", "")
                suffix = ".oga"
                if "mpeg" in mime: suffix = ".mp3"
                elif "wav" in mime: suffix = ".wav"
                elif "m4a" in mime or "mp4" in mime: suffix = ".m4a"
                tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
                tmp.close()
                ok = await download_telegram_file(self.token, fp, Path(tmp.name))
                if not ok:
                    await send_message(self.token, chat_id, "⚠ Download audio fallito.")
                    return
                # Transcribe (config-driven: prima prova audio.stt model from hub config.json)
                text, model_used = await transcribe_audio(Path(tmp.name), hub_path=self.hub_path)
                # Cleanup tmp file
                try:
                    Path(tmp.name).unlink(missing_ok=True)
                except Exception:
                    pass
                # Preview al user
                await send_message(self.token, chat_id, f"🎤 Ho capito: _{text}_")
            except Exception as e:
                print(f"[telegram] transcription error: {type(e).__name__}: {e}")
                await send_message(self.token, chat_id, f"⚠ Trascrizione fallita: {e}")
                return
            if not text.strip():
                await send_message(self.token, chat_id, "⚠ Audio trascritto ma vuoto.")
                return

        # Translate quick-reply keyboard text → slash command
        if text in QUICK_REPLY_TO_COMMAND:
            text = QUICK_REPLY_TO_COMMAND[text]

        self.message_count += 1
        self.last_message_at = time.time()
        # F-AgentSessions: dispatch come task, NON await inline — un turno dura
        # minuti e bloccava il loop getUpdates: /allow, /stop e lo steering non
        # venivano nemmeno letti finché il turno non chiudeva (trovato alla
        # validazione col bot reale, 2026-08-09). La serializzazione per-thread
        # resta al conv-lock in _telegram_dispatch.
        asyncio.create_task(self._dispatch_safe({
            "chat_id": chat_id,
            "text": text,
            "username": chat.get("username", ""),
            "from_name": (msg.get("from") or {}).get("first_name", ""),
            "message_id": msg.get("message_id"),
            "raw": upd,
            "from_voice": is_voice,  # Fase 11 — voice-loop trigger
        }))

    async def _dispatch_safe(self, payload: dict):
        try:
            await self.on_message(payload)
        except Exception as e:
            print(f"[telegram] dispatch error: {type(e).__name__}: {e}", flush=True)
            try:
                await send_message(self.token, payload["chat_id"],
                                   f"⚠ Errore interno: {type(e).__name__}: {e}")
            except Exception:
                pass

    async def _handle_callback_query(self, cbq: dict):
        """Inline button click. Data format: 'cmd:arg' (es. 'model:opus', 'agent:trader')."""
        cb_id = cbq.get("id", "")
        data = cbq.get("data", "")
        chat = (cbq.get("message") or {}).get("chat") or {}
        chat_id = int(chat.get("id", 0))
        from_id = int((cbq.get("from") or {}).get("id", 0))
        message_id = (cbq.get("message") or {}).get("message_id", 0)
        allowed = self.config.get("allowed_chat_ids") or []
        # Autorizza chi preme il bottone (from.id), non la chat: in un gruppo
        # chat.id è il gruppo mentre from.id è l'utente reale che clicca.
        if from_id not in allowed:
            await answer_callback_query(self.token, cb_id, "❌ Non autorizzato")
            return
        # Phase B — Intercept goal action callbacks (act:approve|reject|hold:...)
        if data.startswith("act:"):
            try:
                from telegram_action_notifier import handle_action_callback
                handled = await handle_action_callback(self.hub_path, cbq)
                if handled:
                    return
            except Exception as e:
                print(f"[telegram] action callback error: {e}", flush=True)
                await answer_callback_query(self.token, cb_id, f"❌ {type(e).__name__}")
                return
        # F-GoalCodingWorker — coding run gate (cact:approve|reject:<run_id>)
        if data.startswith("cact:"):
            try:
                from telegram_action_notifier import handle_coding_callback
                handled = await handle_coding_callback(self.hub_path, cbq)
                if handled:
                    return
            except Exception as e:
                print(f"[telegram] coding callback error: {e}", flush=True)
                await answer_callback_query(self.token, cb_id, f"❌ {type(e).__name__}")
                return
        # Acknowledge subito (rimuove spinner)
        await answer_callback_query(self.token, cb_id, "")
        # Parse cmd:arg
        if ":" in data:
            cmd, arg = data.split(":", 1)
            text = f"/{cmd} {arg}"
        else:
            text = f"/{data}"
        # Dispatch come messaggio normale
        await self.on_message({
            "chat_id": chat_id,
            "text": text,
            "username": "",
            "from_name": "",
            "message_id": message_id,
            "raw": cbq,
            "from_callback": True,
        })


# =================================================================
# Standalone debug CLI
# =================================================================

async def _debug_main():
    import argparse
    ap = argparse.ArgumentParser(description="Telegram daemon debug runner")
    ap.add_argument("--hub", required=True)
    args = ap.parse_args()
    hub = Path(args.hub).expanduser().resolve()

    async def echo_handler(msg: dict):
        print(f"[debug] inbound from chat_id={msg['chat_id']}: '{msg['text']}'")
        token = load_token(hub)
        if token:
            await send_message(token, msg["chat_id"], f"🔁 echo: {msg['text']}")

    daemon = TelegramDaemon(hub, on_message=echo_handler)
    await daemon.start()
    if not daemon.running:
        print("[debug] daemon failed to start. Check config + token.")
        return
    print("[debug] daemon running. Send a message to your bot. Ctrl+C to stop.")
    try:
        while daemon.running:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        await daemon.stop()


if __name__ == "__main__":
    asyncio.run(_debug_main())
