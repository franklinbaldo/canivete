"""`canivete profile` — self-configuration for the Telegram bot.

Exposes endpoints to set the bot's name, description, short description,
and profile photo.
"""

from __future__ import annotations

import urllib.error
from pathlib import Path

import typer

from canivete.tg import _api_url, _post_form, _post_multipart
from canivete.ui import console, err_console

app = typer.Typer(
    name="profile",
    help="📸 [magenta]Configure the bot's Telegram identity.[/]",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _send_profile(method: str, fields: dict, files: dict | None = None) -> dict:
    url = _api_url(method)
    result = _post_multipart(url, fields, files) if files else _post_form(url, fields)
    if not result.get("ok"):
        err_console.print(f"[red]Telegram returned not-ok:[/] {result}")
        raise typer.Exit(1)
    return result


@app.command("photo", help="Set the bot's profile photo (setMyProfilePhoto).")
def set_photo(
    path: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
        help="Path to image file.",
    ),
):
    _send_profile("setMyProfilePhoto", {}, files={"photo": str(path)})
    console.print("[green]✓[/] profile photo updated")


@app.command("name", help="Set the bot's name (setMyName, max 64 chars).")
def set_name(
    name: str = typer.Argument(..., help="New name."),
    language_code: str | None = typer.Option(
        None, "--language-code", help="Two-letter ISO 639-1 language code."
    ),
):
    fields = {"name": name}
    if language_code:
        fields["language_code"] = language_code
    _send_profile("setMyName", fields)
    console.print("[green]✓[/] name updated")


@app.command(
    "description", help="Set the bot's long description (setMyDescription, max 512 chars)."
)
def set_description(
    description: str = typer.Argument(..., help="Long bio shown on profile."),
    language_code: str | None = typer.Option(
        None, "--language-code", help="Two-letter ISO 639-1 language code."
    ),
):
    fields = {"description": description}
    if language_code:
        fields["language_code"] = language_code
    _send_profile("setMyDescription", fields)
    console.print("[green]✓[/] description updated")


@app.command(
    "short", help="Set the bot's short description (setMyShortDescription, max 120 chars)."
)
def set_short_description(
    short_description: str = typer.Argument(..., help="One-liner shown above the message field."),
    language_code: str | None = typer.Option(
        None, "--language-code", help="Two-letter ISO 639-1 language code."
    ),
):
    fields = {"short_description": short_description}
    if language_code:
        fields["language_code"] = language_code
    _send_profile("setMyShortDescription", fields)
    console.print("[green]✓[/] short description updated")


@app.command(
    "show",
    help="Show current name and descriptions. (Note: getMyProfilePhoto is not provided by the Telegram API).",
)
def show_profile(
    language_code: str | None = typer.Option(
        None, "--language-code", help="Two-letter ISO 639-1 language code."
    ),
):
    fields = {}
    if language_code:
        fields["language_code"] = language_code

    name_res = _send_profile("getMyName", fields)
    desc_res = _send_profile("getMyDescription", fields)
    short_res = _send_profile("getMyShortDescription", fields)

    name = name_res.get("result", {}).get("name", "")
    desc = desc_res.get("result", {}).get("description", "")
    short = short_res.get("result", {}).get("short_description", "")

    console.print(f"[bold cyan]Name:[/] {name}")
    console.print(f"[bold cyan]Short Description:[/] {short}")
    console.print(f"[bold cyan]Description:[/] {desc}")
