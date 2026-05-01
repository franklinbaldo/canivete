"""Backend for Cline CLI.

Spawns `cline -y <prompt>`
and parses the streaming prose output into BackendEvents.

System prompt is delivered by writing it to .clinerules in the workspace.

Auth: caller must set ANTHROPIC_API_KEY (or another provider key) in the env.
The backend doesn't inject credentials.
"""
import asyncio
import os
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

from pydantic import ValidationError

from canivete.bot.backends.base import (
    BackendEvent,
    DoneEvent,
    SpawnResult,
    TextEvent,
)


class ClineBackend:
    name: str = "cline"

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self._session_id: str | None = None

    def generate_session_id(self) -> str | None:
        # Cline manages session ids internally
        return None

    def spawn(
        self,
        prompt: str,
        *,
        session_id: str | None,
        attachments: list[Path],
        system_prompt: str | None = None,
        is_new_session: bool = False,
    ) -> SpawnResult:
        workspace = Path(os.environ.get("WORKSPACE", "."))
        if system_prompt:
            (workspace / ".clinerules").write_text(system_prompt, encoding="utf-8")

        # Cline CLI handles models via config, but we pass whatever we can.
        # No attachments explicit flag documented for cline -y, so we prepend them.
        attachments_text = ""
        if attachments:
            attachments_text = "\n\nAttachments:\n" + "\n".join(
                f"- {a.name}: <{a.read_text(encoding='utf-8')[:500]}...>"
                if a.exists() else f"- {a.name} (not found)"
                for a in attachments
            )

        cmd = [
            "cline",
            "-y", prompt + attachments_text,
        ]

        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=workspace,
        )
        self._session_id = None

        return SpawnResult(events=self._stream())

    async def _stream(self) -> AsyncIterator[BackendEvent]:
        if not self.proc or not self.proc.stdout:
            return

        loop = asyncio.get_running_loop()
        text_buffer = ""

        def _flush_text():
            nonlocal text_buffer
            chunk = text_buffer
            text_buffer = ""
            if chunk:
                try:
                    return TextEvent(text=chunk)
                except ValidationError:
                    return None
            return None

        while True:
            line = await loop.run_in_executor(None, self.proc.stdout.readline)
            if not line:
                break

            line = line.strip()
            if not line:
                continue

            # Fallback for Cline: everything is text event, line by line.
            # Look for markers like [tool: ...] if needed.
            text_buffer += line + "\n"
            ev = _flush_text()
            if ev:
                yield ev

        ev = _flush_text()
        if ev:
            yield ev

        yield DoneEvent(session_id=self._session_id)
        self.proc.wait()

    def kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
