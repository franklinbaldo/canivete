from pathlib import Path
import os
import urllib.request
import json
import httpx
from canivete.tg import _api_url

def download_telegram_file(file_id: str) -> Path:
    """Download a file from Telegram and return the local path."""
    url = _api_url("getFile")
    req = urllib.request.Request(f"{url}?file_id={file_id}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    
    if not data.get("ok"):
        raise RuntimeError(f"Telegram getFile failed: {data}")
    
    file_path = data["result"]["file_path"]
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    download_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    
    ext = os.path.splitext(file_path)[1]
    
    with urllib.request.urlopen(download_url, timeout=60) as resp:
        return persist_to_inbound(resp.read(), ext)

def persist_to_inbound(data: bytes, ext: str) -> Path:
    """Save raw data to the inbound media directory."""
    agent_root = Path(os.environ.get("AGENT_ROOT", "."))
    inbound_dir = agent_root / "media" / "inbound"
    inbound_dir.mkdir(parents=True, exist_ok=True)
    
    import datetime
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    import uuid
    fname = f"{ts}_{uuid.uuid4().hex[:6]}{ext}"
    path = inbound_dir / fname
    path.write_bytes(data)
    return path

def transcribe_audio(path: Path) -> str:
    """Transcribe an audio file using the configured Whisper service."""
    whisper_url = os.environ.get("WHISPER_URL")
    if not whisper_url:
        return "[Whisper service not configured]"
    
    model = os.environ.get("WHISPER_MODEL", "small")
    
    # We use httpx for easier multipart upload
    try:
        with open(path, "rb") as f:
            files = {"file": (path.name, f)}
            data = {"model": model, "response_format": "text"}
            # The faster-whisper-server matches OpenAI API
            resp = httpx.post(f"{whisper_url}/v1/audio/transcriptions", 
                              data=data, files=files, timeout=120.0)
            resp.raise_for_status()
            return resp.text.strip()
    except Exception as e:
        return f"[Transcription failed: {e}]"
