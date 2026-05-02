"""`canivete tg` — send messages and files via the Telegram bot.

Reads `TELEGRAM_BOT_TOKEN` from the environment (already injected by
docker-compose in the Funes setup). Default destination is the chat id
in `CRON_CHAT_ID`; override with `--chat-id`.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
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
            "[red]No CRON_CHAT_ID or TELEGRAM_ALLOWED_USERS set; pass --chat-id explicitly.[/]"
        )
        raise typer.Exit(1)
    return first


def _api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{_token()}/{method}"


# ─── HTTP plumbing ───────────────────────────────────────────────────


def _post_form(url: str, fields: dict) -> dict:
    payload = {k: v for k, v in fields.items() if v is not None}
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def _post_multipart(url: str, fields: dict, files: dict) -> dict:
    boundary = f"----canivete-{uuid.uuid4().hex}"
    body = bytearray()
    for k, v in fields.items():
        if v is None:
            continue
        body += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'
        ).encode()
    for k, path in files.items():
        fname = os.path.basename(path)
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            content = f.read()
        body += (
            f"--{boundary}\r\nContent-Disposition: form-data; "
            f'name="{k}"; filename="{fname}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode()
        body += content
        body += b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        url, data=bytes(body), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read())


def _send(method: str, fields: dict, files: dict | None = None) -> None:
    url = _api_url(method)
    result = _post_multipart(url, fields, files) if files else _post_form(url, fields)
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
        None, "--chat-id", help="Destination. Default: CRON_CHAT_ID."
    ),
    reply_to: int | None = typer.Option(None, "--reply-to", help="message_id to reply to."),
):
    _send(
        "sendMessage",
        {
            "chat_id": chat_id or _default_chat(),
            "text": text,
            "reply_to_message_id": reply_to,
        },
    )


def _make_captioned(method: str, file_param: str):
    """Factory for media commands that accept --caption (photo, document,
    video, audio)."""

    def cmd(
        path: Path = typer.Argument(
            ...,
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="Path to local file.",
        ),
        caption: str | None = typer.Option(None, "--caption", help="Caption text (optional)."),
        chat_id: str | None = typer.Option(
            None, "--chat-id", help="Destination. Default: CRON_CHAT_ID."
        ),
        reply_to: int | None = typer.Option(None, "--reply-to", help="message_id to reply to."),
    ):
        fields = {"chat_id": chat_id or _default_chat(), "reply_to_message_id": reply_to}
        if caption:
            fields["caption"] = caption
        _send(method, fields, files={file_param: str(path)})

    return cmd


def _make_uncaptioned(method: str, file_param: str):
    """Factory for media commands without caption support (voice)."""

    def cmd(
        path: Path = typer.Argument(
            ...,
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="Path to local file.",
        ),
        chat_id: str | None = typer.Option(
            None, "--chat-id", help="Destination. Default: CRON_CHAT_ID."
        ),
        reply_to: int | None = typer.Option(None, "--reply-to", help="message_id to reply to."),
    ):
        fields = {"chat_id": chat_id or _default_chat(), "reply_to_message_id": reply_to}
        _send(method, fields, files={file_param: str(path)})

    return cmd


app.command("photo", help="Send an image (jpg/png/webp).")(_make_captioned("sendPhoto", "photo"))
app.command("document", help="Send any file (pdf, zip, txt, …).")(
    _make_captioned("sendDocument", "document")
)
app.command("voice", help="Send a voice note (.ogg/opus, no caption).")(
    _make_uncaptioned("sendVoice", "voice")
)
app.command("video", help="Send a video (.mp4).")(_make_captioned("sendVideo", "video"))
app.command("audio", help="Send an audio file (.mp3/.m4a/…) — not voice.")(
    _make_captioned("sendAudio", "audio")
)


@app.command("buttons", help="Send a message with an inline keyboard.")
def send_buttons(  # noqa: C901, PLR0912
    text: str | None = typer.Argument(None, help="Message text."),
    row: list[str] | None = typer.Option(
        None,
        "--row",
        help="Pairs of LABEL:CALLBACK_DATA. Separate pairs with spaces or commas. Stack multiple --row flags.",
    ),
    json_data: str | None = typer.Option(
        None, "--json", help="Inline JSON representation of the payload."
    ),
    json_file: Path | None = typer.Option(
        None, "--json-file", help="Path to JSON file with the payload."
    ),
    chat_id: str | None = typer.Option(
        None, "--chat-id", help="Destination. Default: CRON_CHAT_ID."
    ),
    reply_to: int | None = typer.Option(None, "--reply-to", help="message_id to reply to."),
):
    opts = sum(1 for x in [text, json_data, json_file] if x)
    if opts > 1:
        err_console.print(
            "[red]Validation error:[/] Provide either TEXT+--row, --json, or --json-file (mutually-exclusive)."
        )
        raise typer.Exit(1)

    if json_file:
        payload = json.loads(json_file.read_text())
    elif json_data:
        payload = json.loads(json_data)
    elif text is not None:
        if not row:
            err_console.print(
                "[red]Validation error:[/] Must provide at least one --row when using text argument."
            )
            raise typer.Exit(1)

        rows = []
        for r in row:
            pairs = [p for p in re.split(r"[,\s]+", r) if p]
            parsed_row = []
            for pair in pairs:
                if ":" not in pair:
                    err_console.print(
                        f"[red]Validation error:[/] Invalid button pair '{pair}'. Must be LABEL:CALLBACK_DATA."
                    )
                    raise typer.Exit(1)
                label, data = pair.split(":", 1)
                parsed_row.append({"label": label, "data": data})
            rows.append(parsed_row)
        payload = {"text": text, "rows": rows}
    else:
        err_console.print("[red]Validation error:[/] Must provide TEXT, --json, or --json-file.")
        raise typer.Exit(1)

    payload_text = payload.get("text")
    payload_rows = payload.get("rows", [])

    if not payload_text:
        err_console.print("[red]Validation error:[/] Message text cannot be empty.")
        raise typer.Exit(1)

    if not payload_rows:
        err_console.print(
            "[red]Validation error:[/] The message must have at least one row of buttons."
        )
        raise typer.Exit(1)

    inline_keyboard = []
    for i, r in enumerate(payload_rows):
        if not r:
            err_console.print(f"[red]Validation error:[/] Row {i + 1} has zero buttons.")
            raise typer.Exit(1)
        kb_row = []
        for btn in r:
            label = btn.get("label", "")
            data = btn.get("data", "")
            if not label or not data:
                err_console.print(
                    "[red]Validation error:[/] Each button must have 'label' and 'data'."
                )
                raise typer.Exit(1)
            if len(data.encode("utf-8")) > 64:
                err_console.print(
                    f"[red]Validation error:[/] callback_data '{data}' exceeds 64 bytes."
                )
                raise typer.Exit(1)
            kb_row.append({"text": label, "callback_data": data})
        inline_keyboard.append(kb_row)

    # Note: Telegram API requires reply_markup to be a JSON string when sent via form-urlencoded
    _send(
        "sendMessage",
        {
            "chat_id": chat_id or _default_chat(),
            "text": payload_text,
            "reply_to_message_id": reply_to,
            "reply_markup": json.dumps({"inline_keyboard": inline_keyboard}),
        },
    )


# ─── commands subgroup (chat-scoped slash menu) ──────────────────────


commands_app = typer.Typer(
    name="commands",
    help="📋 [yellow]Manage chat-scoped slash command menu.[/]",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app.add_typer(commands_app, name="commands")


_COMMAND_NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")


def _chat_scope(chat_id: str) -> str:
    """JSON-encoded scope object for setMyCommands / deleteMyCommands /
    getMyCommands. Telegram expects scope as a string inside the form body."""
    return json.dumps({"type": "chat", "chat_id": int(chat_id)})


def _parse_command_pair(raw: str) -> dict:
    """Parse a 'COMMAND:DESCRIPTION' string. Telegram limits: command is
    1-32 chars of [a-z0-9_], description max 256 chars."""
    if ":" not in raw:
        raise typer.BadParameter(
            f"Invalid command spec {raw!r}: missing ':'. Expected 'name:description'."
        )
    name, desc = raw.split(":", 1)
    name = name.strip()
    desc = desc.strip()
    if not _COMMAND_NAME_RE.fullmatch(name):
        err_console.print(
            f"[red]Invalid command name {name!r}: must be lowercase a-z, 0-9, _; 1-32 chars.[/]"
        )
        raise typer.Exit(1)
    if len(desc) > 256:
        err_console.print(f"[red]Description for {name!r} too long ({len(desc)} > 256 chars).[/]")
        raise typer.Exit(1)
    return {"command": name, "description": desc}


def _call_telegram(method: str, fields: dict) -> dict:
    """Like `_send` but without message_id extraction. Returns the
    parsed response so the caller can inspect `result` (which may be
    a bool, list, or object depending on the method)."""
    url = _api_url(method)
    return _post_form(url, fields)


@commands_app.command("set", help="Publish chat-scoped slash commands.")
def commands_set(
    pairs: list[str] = typer.Argument(..., help="One or more COMMAND:DESCRIPTION pairs."),
    chat_id: str | None = typer.Option(
        None, "--chat-id", help="Destination chat. Default: CRON_CHAT_ID."
    ),
):
    cid = chat_id or _default_chat()
    payload = [_parse_command_pair(p) for p in pairs]
    res = _call_telegram(
        "setMyCommands",
        {"commands": json.dumps(payload), "scope": _chat_scope(cid)},
    )
    if not res.get("ok"):
        err_console.print(f"[red]Telegram returned not-ok:[/] {res}")
        raise typer.Exit(1)
    console.print(f"[green]✓[/] published {len(payload)} command(s) to chat [cyan]{cid}[/]")


@commands_app.command("clear", help="Delete chat-scoped slash commands (back to global).")
def commands_clear(
    chat_id: str | None = typer.Option(
        None, "--chat-id", help="Destination chat. Default: CRON_CHAT_ID."
    ),
):
    cid = chat_id or _default_chat()
    res = _call_telegram("deleteMyCommands", {"scope": _chat_scope(cid)})
    if not res.get("ok"):
        err_console.print(f"[red]Telegram returned not-ok:[/] {res}")
        raise typer.Exit(1)
    console.print(f"[green]✓[/] cleared chat-scoped commands for chat [cyan]{cid}[/]")


@commands_app.command("list", help="List currently published chat-scoped commands.")
def commands_list(
    chat_id: str | None = typer.Option(
        None, "--chat-id", help="Destination chat. Default: CRON_CHAT_ID."
    ),
):
    cid = chat_id or _default_chat()
    res = _call_telegram("getMyCommands", {"scope": _chat_scope(cid)})
    if not res.get("ok"):
        err_console.print(f"[red]Telegram returned not-ok:[/] {res}")
        raise typer.Exit(1)
    cmds = res.get("result", []) or []
    if not cmds:
        console.print("[dim](no chat-scoped commands)[/]")
        return
    for c in cmds:
        console.print(f"  /{c.get('command', '?')}  {c.get('description', '')}")
