"""`canivete jules` — manage Jules coding sessions.

Reads `JULES_API_KEY` from the environment.
"""

from __future__ import annotations

import json
import os
import pathlib
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import typer
from rich.panel import Panel
from rich.table import Table
from typing_extensions import Self

from canivete.ui import console, err_console

API_BASE_URL = "https://jules.googleapis.com/v1alpha"


def require_api_key() -> str:
    key = os.environ.get("JULES_API_KEY")
    if not key:
        err_console.print("[red]JULES_API_KEY is not set.[/red]")
        raise typer.Exit(1)
    return key


class JulesClient:
    """Thin wrapper around urllib for Jules API calls."""

    def __init__(self, api_key: str | None = None, base_url: str = API_BASE_URL) -> None:
        self.api_key = api_key or require_api_key()
        self.base_url = base_url.rstrip("/")

    def _request(
        self, method: str, path: str, params: dict | None = None, body: dict | None = None
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if params:
            query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            url = f"{url}?{query}"

        headers = {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
        }

        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30.0) as resp:
                content = resp.read()
                return json.loads(content) if content else {}
        except urllib.error.HTTPError as e:
            err_body = e.read().decode(errors='replace')
            try:
                err_data = json.loads(err_body)
                msg = err_data.get("error", {}).get("message", str(e))
                err_console.print(f"[red]Jules API Error ({e.code}):[/red] {msg}")
            except Exception:  # noqa: BLE001  # noqa: BLE001
                err_console.print(
                    f"[red]Jules API Error ({e.code}):[/red] {err_body}"
                )
            raise typer.Exit(1) from e
        except urllib.error.URLError as e:
            err_console.print(f"[red]Network Error:[/red] {e}")
            raise typer.Exit(1) from e

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    # ── Sessions ──────────────────────────────────────────────

    def list_sessions(self, page_size: int = 20, page_token: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"pageSize": page_size}
        if page_token:
            params["pageToken"] = page_token
        return self._request("GET", "/sessions", params=params)

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self._request("GET", f"/sessions/{session_id}")

    def create_session(
        self,
        title: str,
        source_name: str | None = None,
        prompt: str | None = None,
        branch: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"title": title}
        if prompt:
            body["prompt"] = prompt
        if source_name:
            source_context: dict[str, Any] = {"source": source_name}
            # Bug fix: always pass startingBranch
            source_context["githubRepoContext"] = {"startingBranch": branch or "main"}
            body["sourceContext"] = source_context

        return self._request("POST", "/sessions", body=body)

    def send_message(self, session_id: str, message: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/sessions/{session_id}:sendMessage",
            body={"prompt": message},
        )

    def archive_session(self, session_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/sessions/{session_id}:archive",
            body={},
        )

    # ── Sources ───────────────────────────────────────────────

    def list_sources(self, page_size: int = 20, page_token: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"pageSize": page_size}
        if page_token:
            params["pageToken"] = page_token
        return self._request("GET", "/sources", params=params)


app = typer.Typer(
    name="jules",
    help="🤖 [red]Manage Jules coding sessions.[/]",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
sessions_app = typer.Typer(name="sessions", help="Manage coding sessions.")
sources_app = typer.Typer(name="sources", help="Manage source repositories.")

app.add_typer(sessions_app)
app.add_typer(sources_app)


def _resolve_source_name(source: str) -> str:
    if not source:
        return source
    if "sources/github/" in source:
        return source
    if "/" in source:
        return f"sources/github/{source}"
    return f"sources/github/franklinbaldo/{source}"


@sessions_app.command("list")
def list_sessions(
    page_size: int = typer.Option(20, help="Number of sessions per page."),
    page_token: str | None = typer.Option(None, help="Pagination token."),
    json_flag: bool = typer.Option(False, "--json", help="Output JSON."),
) -> None:
    """List active sessions."""
    with JulesClient() as client:
        data = client.list_sessions(page_size=page_size, page_token=page_token)

    if json_flag:
        console.print(json.dumps(data, indent=2))
        return

    sessions = data.get("sessions", [])
    if not sessions:
        console.print("[dim]No sessions found.[/]")
        return

    table = Table(title="Jules Sessions")
    table.add_column("ID", style="cyan")
    table.add_column("Title")
    table.add_column("State", style="magenta")

    for s in sessions:
        sid = s.get("name", "").split("/")[-1]
        table.add_row(sid, s.get("title", ""), s.get("state", ""))

    console.print(table)


@sessions_app.command("new")
def new_session(
    title: str = typer.Argument(..., help="Session title."),
    source: str | None = typer.Option(
        None, help="Source resource name or short id (e.g. canivete)."
    ),
    prompt: str | None = typer.Option(None, "--prompt", help="Long-form task description."),
    prompt_file: str | None = typer.Option(
        None,
        "--prompt-file",
        help="Path to markdown file containing task description. Mutually exclusive with --prompt.",
    ),
    branch: str = typer.Option("main", "--branch", help="Starting branch for the session."),
) -> None:
    """Create a new coding session."""

    if prompt and prompt_file:
        err_console.print("[red]Error: --prompt and --prompt-file are mutually exclusive.[/red]")
        raise typer.Exit(1)

    final_prompt = prompt
    if prompt_file:
        try:
            final_prompt = pathlib.Path(prompt_file).read_text(encoding="utf-8")
        except Exception as e:
            err_console.print(f"[red]Error reading prompt file:[/red] {e}")
            raise typer.Exit(1) from e

    resolved_source = _resolve_source_name(source) if source else None

    with JulesClient() as client:
        session = client.create_session(
            title=title, source_name=resolved_source, prompt=final_prompt, branch=branch
        )

    sid = session.get("name", "").split("/")[-1]
    url = f"https://jules.app/session/{sid}"

    console.print(f"[green]Session created:[/green] {sid}")
    console.print(f"  URL:   [blue underline]{url}[/]")
    console.print(f"  Title: {session.get('title', '')}")
    console.print(f"  State: {session.get('state', '')}")


@sessions_app.command("show")
def show_session(
    session_id: str = typer.Argument(..., help="Session ID."),
) -> None:
    """Show session details."""
    with JulesClient() as client:
        session = client.get_session(session_id)

    state = session.get("state", "UNKNOWN")
    console.print(
        Panel(
            f"[bold]{session.get('title', 'Untitled')}[/bold]\n"
            f"State: {state}\n"
            f"Created: {session.get('createTime', 'N/A')[:19]}",
            title=f"Session {session_id}",
        )
    )


@sessions_app.command("send")
def send_message(
    session_id: str = typer.Argument(..., help="Session ID."),
    message: str = typer.Argument(..., help="Message to send."),
) -> None:
    """Send a follow-up message to a session."""
    with JulesClient() as client:
        client.send_message(session_id, message)
    console.print("[green]Message sent.[/green]")


@sessions_app.command("archive")
def archive_session(
    session_id: str = typer.Argument(..., help="Session ID."),
) -> None:
    """Archive a session."""
    with JulesClient() as client:
        client.archive_session(session_id)
    console.print("[green]Session archived.[/green]")


@sources_app.command("list")
def list_sources(
    page_size: int = typer.Option(20, help="Number of sources per page."),
    page_token: str | None = typer.Option(None, help="Pagination token."),
    filter_substr: str | None = typer.Option(None, "--filter", help="Filter by id substring."),
    json_flag: bool = typer.Option(False, "--json", help="Output JSON."),
) -> None:
    """List source repositories."""
    with JulesClient() as client:
        data = client.list_sources(page_size=page_size, page_token=page_token)

    sources = data.get("sources", [])

    if filter_substr:
        sources = [s for s in sources if filter_substr.lower() in s.get("name", "").lower()]

    if json_flag:
        # Re-pack filtered sources into response-like shape
        out_data = {"sources": sources}
        if "nextPageToken" in data:
            out_data["nextPageToken"] = data["nextPageToken"]
        console.print(json.dumps(out_data, indent=2))
        return

    if not sources:
        console.print("[dim]No sources found.[/]")
        return

    table = Table(title="Jules Sources")
    table.add_column("Resource Name", style="cyan")
    table.add_column("Default Branch")

    for s in sources:
        name = s.get("name", "")
        # Try to find default branch info
        repo = s.get("gitHubRepository") or s.get("repository") or s.get("githubRepo") or {}
        branch = repo.get("defaultBranch", "main")
        if isinstance(branch, dict):
            branch = branch.get("displayName", "main")
        table.add_row(name, branch)

    console.print(table)
