from pathlib import Path


def download_telegram_file(file_id: str) -> Path:
    raise NotImplementedError("Stub for downloading telegram file.")

def persist_to_inbound(data: bytes, ext: str) -> Path:
    raise NotImplementedError("Stub for persisting to inbound.")

def transcribe_audio(path: Path) -> str:
    raise NotImplementedError("Stub for transcribing audio.")
