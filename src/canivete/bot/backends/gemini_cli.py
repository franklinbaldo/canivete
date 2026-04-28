import asyncio
import json
import os
import re
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

from pydantic import ValidationError

from canivete.bot.backends.base import (
    BackendEvent,
    DoneEvent,
    ErrorEvent,
    SpawnResult,
    StatsEvent,
    TextEvent,
    ThoughtEvent,
    ToolCallEvent,
    ToolResultEvent,
)


class GeminiCliBackend:
    name: str = "gemini-cli"

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self._session_id: str | None = None

    def spawn(
        self,
        prompt: str,
        *,
        session_id: str | None,
        attachments: list[Path],
        system_prompt: str | None = None,
    ) -> SpawnResult:
        if system_prompt:
            gemini_md = Path(os.environ.get("WORKSPACE", ".")) / "GEMINI.md"
            gemini_md.write_text(system_prompt, encoding="utf-8")

        cmd = ["gemini"]
        if session_id:
            cmd.extend(["--resume", session_id])
        cmd.extend(["--yolo", "--skip-trust", "--output-format", "stream-json", "-p", prompt])
        for a in attachments:
            cmd.append(f"@{a}")

        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._session_id = None

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

            if "/.gemini/tmp/" in line and "/chats/" in line:
                match = re.search(r"/\.gemini/tmp/[^/]+/chats/([^.]+)\.json", line)
                if match:
                    self._session_id = match.group(1)
                continue

            try:
                data = json.loads(line)
            except ValueError:
                continue

            kind = data.get("kind")
            try:
                if kind == "text":
                    yield TextEvent(**data)
                elif kind == "tool_call":
                    yield ToolCallEvent(**data)
                elif kind == "tool_result":
                    yield ToolResultEvent(**data)
                elif kind == "thought":
                    yield ThoughtEvent(**data)
                elif kind == "error":
                    yield ErrorEvent(**data)
                elif kind == "stats":
                    yield StatsEvent(**data)
                elif kind == "done":
                    if data.get("session_id"):
                        self._session_id = data["session_id"]
                    yield DoneEvent(**data)
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
