from __future__ import annotations

import datetime
import json
import mimetypes
import os
import shutil
import urllib.request
import uuid
from pathlib import Path

from canivete.tg import _api_url, _token

_MIME_EXT_OVERRIDE = {
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/webm": ".webm",
}

_AUDIO_EXTENSIONS = {
    ".aac", ".aiff", ".amr", ".flac", ".m4a", ".mp3", ".oga", ".ogg",
    ".opus", ".wav", ".weba", ".webm", ".wma",
}


def mime_to_ext(mime: str | None, fallback: str = ".bin") -> str:
    if not mime:
        return fallback
    return _MIME_EXT_OVERRIDE.get(mime) or mimetypes.guess_extension(mime) or fallback


def is_audio_document(document: dict) -> bool:
    mime = document.get("mime_type") or ""
    if mime.startswith("audio/"):
        return True
    name = document.get("file_name") or ""
    return Path(name).suffix.lower() in _AUDIO_EXTENSIONS


def download_telegram_file(file_id: str, suffix: str = ".bin") -> Path | None:
    req = urllib.request.Request(_api_url("getFile"), data=json.dumps({"file_id": file_id}).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        info = json.loads(resp.read())
    if not info.get("ok"):
        return None
    file_path = info["result"]["file_path"]
    local_path = Path(os.environ.get("WORKSPACE", ".")) / "tmp" / "canivete-bot" / f"{uuid.uuid4().hex[:12]}{suffix}"
    local_path.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(
        f"https://api.telegram.org/file/bot{_token()}/{file_path}",
        local_path,
    )
    return local_path


def persist_to_inbound(tmp_path: Path, suffix: str) -> Path:
    inbound = Path(os.environ.get("WORKSPACE", ".")) / "media" / "inbound"
    inbound.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H%M%S")
    dst = inbound / f"{ts}---{uuid.uuid4().hex[:8]}{suffix}"
    shutil.move(str(tmp_path), dst)
    return dst


def _whisper_request(url: str, model: str, audio_bytes: bytes, filename: str, mime: str, language: str | None, prompt: str | None, timeout: int) -> str | None:
    boundary = f"----canivete-whisper-{uuid.uuid4().hex}"

    def field(name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode()

    body = bytearray()
    body += field("model", model)
    if language:
        body += field("language", language)
    if prompt:
        body += field("prompt", prompt)
    body += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode()
    body += audio_bytes
    body += f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{url.rstrip('/')}/v1/audio/transcriptions",
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return (json.loads(resp.read()).get("text") or "").strip() or None


def transcribe_audio(path: Path) -> str | None:
    whisper_url = os.environ.get("WHISPER_URL")
    if not whisper_url:
        return None
    audio_bytes = path.read_bytes()
    mime = mimetypes.guess_type(path)[0] or "audio/ogg"
    return _whisper_request(
        whisper_url,
        os.environ.get("WHISPER_MODEL", "whisper-1"),
        audio_bytes,
        path.name,
        mime,
        os.environ.get("WHISPER_LANGUAGE", "pt"),
        os.environ.get("WHISPER_PROMPT", "Voice message from a Telegram conversation."),
        int(os.environ.get("WHISPER_TIMEOUT", "120")),
    )
