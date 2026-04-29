import os

import typer

app = typer.Typer(
    help="⚙️ run the meta-harness daemon",
    no_args_is_help=False,
    invoke_without_command=True,
)


@app.callback(invoke_without_command=True)
def bot(
    backend: str = typer.Option(
        None,
        "--backend",
        help="Backend to use (gemini-cli or claude-code). Defaults to CANIVETE_BOT_BACKEND or AGENT_BACKEND env var, or gemini-cli.",
    ),
):
    try:
        import jinja2  # noqa: F401
        import telegramify_markdown  # noqa: F401
    except ImportError:
        typer.echo("Missing dependencies for bot daemon.")
        typer.echo("Please install with: uv pip install 'canivete[bot]'")
        raise typer.Exit(1)

    from canivete.bot.daemon import run_daemon

    backend_name = (
        backend
        or os.environ.get("CANIVETE_BOT_BACKEND")
        or os.environ.get("AGENT_BACKEND")
        or "gemini-cli"
    )
    run_daemon(backend_name)
