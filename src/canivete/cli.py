"""Top-level Typer app — wires the subcommands together and renders the
overview banner when invoked without arguments."""

from __future__ import annotations

import typer

from canivete import __version__
from canivete.cron import app as cron_app
from canivete.tg import app as tg_app
from canivete.ui import show_overview

app = typer.Typer(
    name="canivete",
    help="🇨🇭🔪 canivete — swiss-army CLI for AI agents on Telegram.",
    no_args_is_help=False,
    rich_markup_mode="rich",
    add_completion=False,
)


app.add_typer(tg_app, name="tg")
app.add_typer(cron_app, name="cron")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"canivete {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """When called without a subcommand, render the overview tree banner."""
    if ctx.invoked_subcommand is None:
        show_overview()
        raise typer.Exit(0)
