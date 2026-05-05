import asyncio
import json
import os
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import uuid_utils
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

    def generate_session_id(self) -> str | None:
        return str(uuid_utils.uuid7())

    def spawn(
        self,
        prompt: str,
        *,
        session_id: str | None,
        attachments: list[Path],
        system_prompt: str | None = None,
        is_new_session: bool = False,
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
        model = os.environ.get("CLAUDE_CODE_MODEL")
        if model:
            cmd.extend(["--model", model])
        effort = os.environ.get("CLAUDE_CODE_EFFORT")
        if effort:
            cmd.extend(["--effort", effort])
        if system_prompt:
            cmd.extend(["--append-system-prompt", system_prompt])
        if session_id and is_new_session:
            cmd.extend(["--session-id", session_id])
        elif session_id:
            cmd.extend(["--resume", session_id])

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
                if kind == "assistant":
                    blocks = (data.get("message") or {}).get("content") or []
                    for block in blocks:
                        btype = block.get("type")
                        if btype == "text":
                            text = block.get("text")
                            if text:
                                yield TextEvent(text=text)
                        elif btype == "tool_use":
                            yield ToolCallEvent(
                                tool=block.get("name", "tool"),
                                args=block.get("input", {}),
                                call_id=block.get("id"),
                            )
                elif kind == "user":
                    blocks = (data.get("message") or {}).get("content") or []
                    for block in blocks:
                        if block.get("type") == "tool_result":
                            yield ToolResultEvent(
                                call_id=block.get("tool_use_id"),
                                ok=not block.get("is_error", False),
                                output=block.get("content", ""),
                            )
                elif kind == "result":
                    sid = data.get("session_id") or self._session_id
                    self._session_id = sid
                    if data.get("is_error"):
                        yield ErrorEvent(message=str(data.get("result") or "Unknown error"))
                    yield DoneEvent(session_id=sid)
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
