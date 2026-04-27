"""`canivete cron` — schedule prompts for yourself.

The bot daemon polls `/workspace/.cron.jsonl` (override with `CRON_LOG`)
and, when a job is due, drops the prompt into your input queue as if it
were a message from the user. Use it to remember to do something later.

Storage is an append-only JSONL log; the current state is the replay of
events (`add` / `fired` / `remove`). Side-effect free reads.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import uuid
from pathlib import Path

import typer
from rich.table import Table

from canivete.ui import console, err_console

app = typer.Typer(
    name="cron",
    help="⏰ [yellow]Schedule prompts for yourself to run later.[/]",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


LOG = Path(os.environ.get("CRON_LOG", "/workspace/.cron.jsonl"))


# ─── Time helpers ────────────────────────────────────────────────────

def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(
        timespec="seconds")


def _parse_in(spec: str) -> dt.datetime:
    """Parse a relative duration like '30m', '2h', '1d', '90s'."""
    m = re.fullmatch(r"(\d+)\s*([smhd])", spec.strip().lower())
    if not m:
        raise typer.BadParameter(
            f"--in: invalid format {spec!r}. "
            "Use '30m', '2h', '1d', or '90s'.")
    n, unit = int(m.group(1)), m.group(2)
    delta = {
        "s": dt.timedelta(seconds=n),
        "m": dt.timedelta(minutes=n),
        "h": dt.timedelta(hours=n),
        "d": dt.timedelta(days=n),
    }[unit]
    return dt.datetime.now(dt.timezone.utc).astimezone() + delta


def _parse_at(spec: str) -> dt.datetime:
    """Parse an ISO 8601 datetime. Naive datetimes assume local tz."""
    try:
        d = dt.datetime.fromisoformat(spec)
    except ValueError as e:
        raise typer.BadParameter(f"--at: not ISO 8601: {spec!r}") from e
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.datetime.now().astimezone().tzinfo)
    return d


# ─── Storage ─────────────────────────────────────────────────────────

def _append(event: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _replay() -> dict:
    """Replay the JSONL log into a dict of {id: state}."""
    state: dict = {}
    if not LOG.exists():
        return state
    for line in LOG.read_text().splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        action = ev.get("action")
        jid = ev.get("id")
        if not jid:
            continue
        if action == "add":
            state[jid] = {**ev, "fired": False, "removed": False}
        elif action == "fired" and jid in state:
            state[jid]["fired"] = True
            state[jid]["fired_at"] = ev.get("at")
        elif action == "remove" and jid in state:
            state[jid]["removed"] = True
    return state


# ─── Commands ────────────────────────────────────────────────────────

@app.command("add", help="Schedule a job. Use --at OR --in (one of them).")
def cron_add(
    prompt: str = typer.Argument(
        ..., help="Prompt that will be delivered when the job fires."),
    at: str | None = typer.Option(
        None, "--at",
        help="Absolute time, ISO 8601 (e.g. 2026-04-27T09:00:00-03:00)."),
    in_: str | None = typer.Option(
        None, "--in",
        help="Relative duration (e.g. 30m, 2h, 1d, 90s)."),
):
    if (at and in_) or not (at or in_):
        err_console.print("[red]Use --at OR --in (exactly one).[/]")
        raise typer.Exit(2)
    when = _parse_at(at) if at else _parse_in(in_)
    jid = "j_" + uuid.uuid4().hex[:8]
    _append({
        "action": "add",
        "id": jid,
        "at": when.isoformat(timespec="seconds"),
        "prompt": prompt,
        "created": _now_iso(),
    })
    console.print(
        f"[green]✓[/] [yellow]{jid}[/] → "
        f"[cyan]{when.isoformat(timespec='seconds')}[/]")


@app.command("list", help="List pending jobs.")
def cron_list():
    state = _replay()
    pending = sorted(
        (j for j in state.values() if not j["fired"] and not j["removed"]),
        key=lambda j: j.get("at") or "")
    if not pending:
        console.print("[dim](no pending jobs)[/]")
        raise typer.Exit(0)
    table = Table(show_header=True, header_style="bold yellow",
                  border_style="dim")
    table.add_column("ID", style="yellow", no_wrap=True)
    table.add_column("WHEN", style="cyan", no_wrap=True)
    table.add_column("PROMPT")
    for j in pending:
        prompt = j.get("prompt") or ""
        if len(prompt) > 70:
            prompt = prompt[:67] + "…"
        table.add_row(j["id"], j.get("at", ""), prompt)
    console.print(table)


@app.command("rm", help="Cancel a pending job.")
def cron_rm(
    job_id: str = typer.Argument(
        ..., help="Job ID (see `canivete cron list`)."),
):
    state = _replay()
    if job_id not in state:
        err_console.print(f"[red]Unknown ID:[/] {job_id}")
        raise typer.Exit(1)
    if state[job_id]["removed"]:
        console.print(f"[dim]{job_id} was already removed.[/]")
        raise typer.Exit(0)
    _append({"action": "remove", "id": job_id, "at": _now_iso()})
    console.print(f"[green]✓[/] removed: [yellow]{job_id}[/]")
