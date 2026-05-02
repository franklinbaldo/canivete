"""`canivete miniapp` — generate Telegram WebApp on the fly via Intuit."""

from __future__ import annotations

import base64
import json
import re
import subprocess
import tempfile
import urllib.error
from pathlib import Path

import typer
from rich.panel import Panel
from rich.text import Text

from canivete.tg import _api_url, _default_chat, _post_form
from canivete.ui import console, err_console

app = typer.Typer(
    name="miniapp",
    help="🪟 [green]Spin up a Telegram Web App from raw HTML.[/]\n\n"
    "By default, this sends small payloads as an inline base64 string directly in the URL "
    "and falls back to creating a public GitHub Gist if the URL is too long.\n\n"
    "The Intuit base URL is https://franklinbaldo.github.io/intuit/",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _create_gist(filepath: Path) -> str:
    """Use `gh` to create a public gist and return the gist ID."""
    result = subprocess.run(
        ["gh", "gist", "create", "--public", str(filepath)],
        capture_output=True,
        text=True,
        check=True,
    )

    url = result.stdout.strip()
    # Extract ID from gist URL: e.g. https://gist.github.com/franklinbaldo/1234abcd
    m = re.search(r"/([a-f0-9]+)$", url)
    if not m:
        err_console.print(f"[red]Failed to extract Gist ID from URL:[/] {url}")
        raise typer.Exit(1)

    return m.group(1)


@app.command("send", help="Send a Telegram message with an inline Web App button.")
def miniapp_send(  # noqa: C901, PLR0912, PLR0915
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
    inline: bool = typer.Option(
        False, "--inline", help="Force inline base64 URL. Fails if too long."
    ),
    gist: bool = typer.Option(
        False, "--gist", help="Force Gist creation even if inline would fit."
    ),
    chat_id: str | None = typer.Option(
        None, "--chat-id", help="Destination. Default: CRON_CHAT_ID."
    ),
    reply_to: int | None = typer.Option(None, "--reply-to", help="message_id to reply to."),
):
    provided = sum(1 for x in [html_file, html, gist_id] if x is not None)
    if provided != 1:
        err_console.print(
            "[red]You must provide exactly one of --html-file, --html, or --gist-id.[/]"
        )
        raise typer.Exit(2)

    final_url = ""
    path_taken = ""
    reason = ""
    url_length = 0

    if gist_id:
        final_url = f"https://franklinbaldo.github.io/intuit/?gist={gist_id}"
        path_taken = "gist-id"
        reason = "reused existing gist"
    else:
        html_content = ""
        if html_file:
            html_content = html_file.read_text(encoding="utf-8")
        elif html:
            html_content = html

        encoded = base64.b64encode(html_content.encode("utf-8")).decode("ascii")
        candidate_url = f"https://franklinbaldo.github.io/intuit/?b64={encoded}"
        url_length = len(candidate_url.encode("utf-8"))

        def do_gist(reason_text: str):
            if html_file:
                actual_gist_id = _create_gist(html_file)
            else:
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_path = Path(temp_dir) / "index.html"
                    temp_path.write_text(html_content, encoding="utf-8")
                    actual_gist_id = _create_gist(temp_path)
            return f"https://franklinbaldo.github.io/intuit/?gist={actual_gist_id}", reason_text

        if gist:
            final_url, reason = do_gist("forced via --gist")
            path_taken = "gist"
        elif inline:
            if url_length > 4096:
                err_console.print(
                    f"[red]HTML too large for inline ({url_length} > 4096 bytes) and --inline was passed.[/]"
                )
                raise typer.Exit(1)
            final_url = candidate_url
            path_taken = "inline (?b64=)"
            reason = f"forced via --inline ({url_length} of 4096 bytes used)"
        elif url_length <= 4096:
            final_url = candidate_url
            path_taken = "inline (?b64=)"
            reason = f"HTML fits in URL ({url_length} of 4096 bytes used)"
        else:
            final_url, reason = do_gist(f"HTML too large for inline ({url_length} > 4096 bytes)")
            path_taken = "gist"

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
                            "web_app": {"url": final_url},
                        }
                    ]
                ]
            }
        ),
    }
    if reply_to is not None:
        payload["reply_to_message_id"] = reply_to

    url = _api_url("sendMessage")
    result = _post_form(url, payload)

    if not result.get("ok"):
        err_console.print(f"[red]Telegram returned not-ok:[/] {result}")
        raise typer.Exit(1)

    mid = result.get("result", {}).get("message_id", "?")
    console.print(f"[green]✓[/] sent Web App — message_id [cyan]{mid}[/]")

    panel_text = Text()
    panel_text.append(" • path:  ", style="bold")
    panel_text.append(f"{path_taken}".ljust(15))
    panel_text.append(f" ← {reason}\n")
    panel_text.append(" • intuit base: ", style="bold")
    panel_text.append("https://franklinbaldo.github.io/intuit/\n\n")
    panel_text.append("Consequences:\n", style="bold")
    if "inline" in path_taken:
        panel_text.append(" • No Gist was created — nothing public on GitHub.\n")
        panel_text.append(" • To force a Gist instead (stable link, public), pass --gist.")
    elif path_taken == "gist":
        panel_text.append(" • Created a new public GitHub Gist.\n")
        panel_text.append(" • Anyone with the URL can view the source.")
    elif path_taken == "gist-id":
        panel_text.append(" • Reused existing public GitHub Gist.\n")

    console.print()
    console.print(
        Panel(panel_text, title="⚠️ Defaults applied", title_align="left", border_style="yellow")
    )
