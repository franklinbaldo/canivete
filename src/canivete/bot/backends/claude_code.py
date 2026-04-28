import asyncio
import json
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

from pydantic import ValidationError

from canivete.bot.backends.base import (
    BackendEvent,
    DoneEvent,
    ErrorEvent,
    SpawnResult,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
)


class ClaudeCodeBackend:
    name: str = "claude-code"

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self._session_id: str | None = None

    def spawn(
        self,
        prompt: str,
        *,
        session_id: str | None,
        attachments: list[Path],
    ) -> SpawnResult:
        cmd = [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
        ]
        if session_id:
            cmd.append("--continue")

        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._session_id = session_id

        return SpawnResult(events=self._stream())

    async def _stream(self) -> AsyncIterator[BackendEvent]:
        if not self.proc or not self.proc.stdout:
            return

        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, self.proc.stdout.readline)
            if not line:
                break

            line = line.strip()
            if not line:
                continue

            try:
                data = json.loads(line)
            except ValueError:
                continue

            kind = data.get("type") or data.get("kind")

            try:
                if kind in ("text", "message_start", "content_block_delta"):
                    text = data.get("text") or data.get("delta", {}).get("text")
                    if text:
                        yield TextEvent(text=text)
                elif kind == "tool_use":
                    yield ToolCallEvent(
                        tool=data.get("name", "tool"),
                        args=data.get("input", {}),
                        call_id=data.get("id"),
                    )
                elif kind == "tool_result":
                    yield ToolResultEvent(
                        call_id=data.get("tool_use_id"),
                        ok=not data.get("is_error", False),
                        output=data.get("content", ""),
                    )
                elif kind == "error":
                    yield ErrorEvent(message=data.get("error", {}).get("message", "Unknown error"))
                elif kind in ("message_stop", "done"):
                    yield DoneEvent(session_id=self._session_id)
            except ValidationError:
                pass

        self.proc.wait()

    def kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
