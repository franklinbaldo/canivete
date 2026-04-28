"""`canivete miniapp` — generate Telegram WebApp on the fly via Intuit."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import urllib.error
from pathlib import Path

import typer

from canivete.tg import _api_url, _default_chat, _post_form
from canivete.ui import console, err_console

app = typer.Typer(
    name="miniapp",
    help="🪟 [green]Spin up a Telegram Web App from raw HTML.[/]\n\n"
    "This command publishes your HTML to a public Gist. The URL is shareable.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _create_gist(filepath: Path) -> str:
    """Use `gh` to create a public gist and return the gist ID."""
    try:
        result = subprocess.run(
            ["gh", "gist", "create", "--public", str(filepath)],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        err_console.print(f"[red]Failed to create Gist using `gh` CLI:[/] {e.stderr}")
        raise typer.Exit(1) from e

    url = result.stdout.strip()
    # Extract ID from gist URL: e.g. https://gist.github.com/franklinbaldo/1234abcd
    m = re.search(r"/([a-f0-9]+)$", url)
    if not m:
        err_console.print(f"[red]Failed to extract Gist ID from URL:[/] {url}")
        raise typer.Exit(1)

    return m.group(1)


@app.command("send", help="Send a Telegram message with an inline Web App button.")
def miniapp_send(
    label: str = typer.Argument(..., help="Button label shown in Telegram."),
    html_file: Path | None = typer.Option(
        None,
        "--html-file",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Path to HTML file to publish.",
    ),
    html: str | None = typer.Option(None, "--html", help="Inline HTML string to publish."),
    gist_id: str | None = typer.Option(None, "--gist-id", help="Reuse an existing Gist ID."),
    text: str = typer.Option(".", "--text", help="Message body that accompanies the button."),
    chat_id: str | None = typer.Option(
        None, "--chat-id", help="Destination. Default: CRON_CHAT_ID."
    ),
):
    provided = sum(1 for x in [html_file, html, gist_id] if x is not None)
    if provided != 1:
        err_console.print(
            "[red]You must provide exactly one of --html-file, --html, or --gist-id.[/]"
        )
        raise typer.Exit(2)

    actual_gist_id = gist_id

    if html_file:
        actual_gist_id = _create_gist(html_file)
    elif html:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / "index.html"
            temp_path.write_text(html, encoding="utf-8")
            actual_gist_id = _create_gist(temp_path)

    # Now we have the gist_id, prepare the payload to Telegram
    payload = {
        "chat_id": chat_id or _default_chat(),
        "text": text,
        "reply_markup": json.dumps(
            {
                "inline_keyboard": [
                    [
                        {
                            "text": label,
                            "web_app": {
                                "url": f"https://franklinbaldo.github.io/intuit/?gist={actual_gist_id}"
                            },
                        }
                    ]
                ]
            }
        ),
    }

    url = _api_url("sendMessage")
    try:
        result = _post_form(url, payload)
    except urllib.error.HTTPError as e:
        err_console.print(f"[red]HTTP {e.code}:[/] {e.read().decode(errors='replace')}")
        raise typer.Exit(1) from e
    except (urllib.error.URLError, OSError) as e:
        err_console.print(f"[red]Network error:[/] {e}")
        raise typer.Exit(1) from e

    if not result.get("ok"):
        err_console.print(f"[red]Telegram returned not-ok:[/] {result}")
        raise typer.Exit(1)

    mid = result.get("result", {}).get("message_id", "?")
    console.print(f"[green]✓[/] sent Web App — message_id [cyan]{mid}[/]")
