import asyncio
import json
import os
import re
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

from pydantic import ValidationError
from rich.console import Console

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
        self.console = Console()

    def generate_session_id(self) -> str | None:
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
        if system_prompt:
            gemini_md = Path(os.environ.get("WORKSPACE", ".")) / "GEMINI.md"
            gemini_md.write_text(system_prompt, encoding="utf-8")

        cmd = ["gemini"]
        res_id = session_id or "latest"
        if res_id:
            cmd.extend(["--resume", res_id])
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

    async def _stream(self) -> AsyncIterator[BackendEvent]:  # noqa: PLR0915
        if not self.proc or not self.proc.stdout:
            return

        loop = asyncio.get_running_loop()
        # gemini emits assistant text in many small {"delta": true} chunks.
        # Buffer them so the daemon edits the Telegram message with whole
        # paragraphs, not with one '\n'-glued event per token.
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

            # Log raw lines for debugging
            self.console.print(f"[dim]Raw gemini output: {line[:120]}[/]")

            # Pre-stream-json gemini sometimes prints chat path as plain text.
            if "/.gemini/tmp/" in line and "/chats/" in line:
                match = re.search(r"/\.gemini/tmp/[^/]+/chats/([^.]+)\.json", line)
                if match:
                    self._session_id = match.group(1)
                continue

            try:
                data = json.loads(line)
            except ValueError:
                continue

            # gemini-cli stream-json schema uses "type", not "kind".
            ev_type = data.get("type") or data.get("kind")
            try:
                if ev_type == "init":
                    if data.get("session_id"):
                        self._session_id = data["session_id"]
                    continue

                if ev_type == "message":
                    role = data.get("role")
                    content = data.get("content") or ""
                    if role != "assistant" or not content:
                        continue
                    if data.get("delta"):
                        text_buffer += content
                        continue
                    text_buffer += content
                    ev = _flush_text()
                    if ev:
                        yield ev
                    continue

                if ev_type in ("tool_use", "tool_call"):
                    ev = _flush_text()
                    if ev:
                        yield ev
                    yield ToolCallEvent(
                        tool=data.get("tool_name") or data.get("tool") or "tool",
                        args=data.get("parameters") or data.get("args") or {},
                        call_id=data.get("tool_id") or data.get("call_id"),
                    )
                    continue

                if ev_type == "tool_result":
                    ev = _flush_text()
                    if ev:
                        yield ev
                    output = data.get("output")
                    if output is None:
                        output = ""
                    elif not isinstance(output, str):
                        output = json.dumps(output)
                    yield ToolResultEvent(
                        call_id=data.get("tool_id") or data.get("call_id"),
                        ok=data.get("status", "success") == "success"
                        and not data.get("is_error", False),
                        output=output,
                    )
                    continue

                if ev_type == "thought":
                    yield ThoughtEvent(
                        subject=data.get("subject"),
                        description=data.get("description"),
                    )
                    continue

                if ev_type == "error":
                    msg = data.get("message") or data.get("error") or "Unknown error"
                    if isinstance(msg, dict):
                        msg = msg.get("message", "Unknown error")
                    yield ErrorEvent(message=str(msg))
                    continue

                if ev_type == "stats":
                    yield StatsEvent(
                        duration_ms=data.get("duration_ms"),
                        tokens_in=data.get("tokens_in") or data.get("input_tokens"),
                        tokens_out=data.get("tokens_out") or data.get("output_tokens"),
                        cached=data.get("cached"),
                        model=data.get("model"),
                    )
                    continue

                if ev_type in ("done", "stop"):
                    ev = _flush_text()
                    if ev:
                        yield ev
                    if data.get("session_id"):
                        self._session_id = data["session_id"]
                    yield DoneEvent(session_id=self._session_id)
                    continue
            except ValidationError:
                pass

        # EOF without explicit done: flush any pending assistant text.
        ev = _flush_text()
        if ev:
            yield ev

        self.proc.wait()

    def kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
