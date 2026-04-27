"""`canivete tg` — send messages and files via the Telegram bot.

Reads `TELEGRAM_BOT_TOKEN` from the environment (already injected by
docker-compose in the Funes setup). Default destination is the chat id
in `CRON_CHAT_ID`; override with `--chat-id`.
"""

from __future__ import annotations

import json
import mimetypes
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import typer

from canivete.ui import console, err_console

app = typer.Typer(
    name="tg",
    help="📨 [blue]Send messages and files via Telegram.[/]",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


# ─── Config ──────────────────────────────────────────────────────────

def _token() -> str:
    t = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not t:
        err_console.print("[red]TELEGRAM_BOT_TOKEN is not set.[/]")
        raise typer.Exit(1)
    return t


def _default_chat() -> str:
    """Default chat id is `CRON_CHAT_ID`; falls back to the first allowed
    user. If neither is set, abort and ask for `--chat-id`."""
    cid = os.environ.get("CRON_CHAT_ID")
    if cid:
        return cid
    allowed = os.environ.get("TELEGRAM_ALLOWED_USERS", "")
    first = next((x for x in allowed.split(",") if x.strip()), None)
    if not first:
        err_console.print(
            "[red]No CRON_CHAT_ID or TELEGRAM_ALLOWED_USERS set; "
            "pass --chat-id explicitly.[/]")
        raise typer.Exit(1)
    return first


def _api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{_token()}/{method}"


# ─── HTTP plumbing ───────────────────────────────────────────────────

def _post_form(url: str, fields: dict) -> dict:
    payload = {k: v for k, v in fields.items() if v is not None}
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def _post_multipart(url: str, fields: dict, files: dict) -> dict:
    boundary = f"----canivete-{uuid.uuid4().hex}"
    body = bytearray()
    for k, v in fields.items():
        if v is None:
            continue
        body += (
            f"--{boundary}\r\nContent-Disposition: form-data; "
            f"name=\"{k}\"\r\n\r\n{v}\r\n").encode()
    for k, path in files.items():
        fname = os.path.basename(path)
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            content = f.read()
        body += (
            f"--{boundary}\r\nContent-Disposition: form-data; "
            f"name=\"{k}\"; filename=\"{fname}\"\r\n"
            f"Content-Type: {mime}\r\n\r\n").encode()
        body += content
        body += b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        url, data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read())


def _send(method: str, fields: dict, files: dict | None = None) -> None:
    url = _api_url(method)
    try:
        result = (_post_multipart(url, fields, files)
                  if files else _post_form(url, fields))
    except urllib.error.HTTPError as e:
        err_console.print(
            f"[red]HTTP {e.code}:[/] {e.read().decode(errors='replace')}")
        raise typer.Exit(1) from e
    except (urllib.error.URLError, OSError) as e:
        err_console.print(f"[red]Network error:[/] {e}")
        raise typer.Exit(1) from e
    if not result.get("ok"):
        err_console.print(f"[red]Telegram returned not-ok:[/] {result}")
        raise typer.Exit(1)
    mid = result.get("result", {}).get("message_id", "?")
    console.print(f"[green]✓[/] sent — message_id [cyan]{mid}[/]")


# ─── Commands ────────────────────────────────────────────────────────

@app.command("text", help="Send plain text.")
def send_text(
    text: str = typer.Argument(..., help="Message text."),
    chat_id: str | None = typer.Option(
        None, "--chat-id", help="Destination. Default: CRON_CHAT_ID."),
    reply_to: int | None = typer.Option(
        None, "--reply-to", help="message_id to reply to."),
):
    _send("sendMessage", {
        "chat_id": chat_id or _default_chat(),
        "text": text,
        "reply_to_message_id": reply_to,
    })


def _make_captioned(method: str, file_param: str):
    """Factory for media commands that accept --caption (photo, document,
    video, audio)."""
    def cmd(
        path: Path = typer.Argument(
            ..., exists=True, file_okay=True, dir_okay=False,
            readable=True, resolve_path=True,
            help="Path to local file."),
        caption: str | None = typer.Option(
            None, "--caption", help="Caption text (optional)."),
        chat_id: str | None = typer.Option(
            None, "--chat-id", help="Destination. Default: CRON_CHAT_ID."),
        reply_to: int | None = typer.Option(
            None, "--reply-to", help="message_id to reply to."),
    ):
        fields = {"chat_id": chat_id or _default_chat(),
                  "reply_to_message_id": reply_to}
        if caption:
            fields["caption"] = caption
        _send(method, fields, files={file_param: str(path)})
    return cmd


def _make_uncaptioned(method: str, file_param: str):
    """Factory for media commands without caption support (voice)."""
    def cmd(
        path: Path = typer.Argument(
            ..., exists=True, file_okay=True, dir_okay=False,
            readable=True, resolve_path=True,
            help="Path to local file."),
        chat_id: str | None = typer.Option(
            None, "--chat-id", help="Destination. Default: CRON_CHAT_ID."),
        reply_to: int | None = typer.Option(
            None, "--reply-to", help="message_id to reply to."),
    ):
        fields = {"chat_id": chat_id or _default_chat(),
                  "reply_to_message_id": reply_to}
        _send(method, fields, files={file_param: str(path)})
    return cmd


app.command("photo",    help="Send an image (jpg/png/webp).")(
    _make_captioned("sendPhoto",    "photo"))
app.command("document", help="Send any file (pdf, zip, txt, …).")(
    _make_captioned("sendDocument", "document"))
app.command("voice",    help="Send a voice note (.ogg/opus, no caption).")(
    _make_uncaptioned("sendVoice",  "voice"))
app.command("video",    help="Send a video (.mp4).")(
    _make_captioned("sendVideo",    "video"))
app.command("audio",    help="Send an audio file (.mp3/.m4a/…) — not voice.")(
    _make_captioned("sendAudio",    "audio"))
