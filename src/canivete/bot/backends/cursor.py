"""Backend for Cursor Agent CLI.

Spawns `cursor-agent -p <prompt> --output-format stream-json --force ...`
and parses the streaming JSON output into BackendEvents.

System prompt is delivered by writing it to CURSOR.md in the workspace.

Auth: caller must set OPENAI_API_KEY / ANTHROPIC_API_KEY (or another provider key
matching the chosen --model) in the env. The backend doesn't inject credentials.
"""
import asyncio
import json
import os
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


class CursorBackend:
    name: str = "cursor"

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self._session_id: str | None = None

    def generate_session_id(self) -> str | None:
        # Cursor manages session ids internally
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
            (workspace / "CURSOR.md").write_text(system_prompt, encoding="utf-8")

        cmd = [
            "cursor-agent",
            "-p", prompt,
            "--output-format", "stream-json",
            "--force",
        ]
        model = os.environ.get("CURSOR_MODEL")
        if model:
            cmd.extend(["-m", model])

        # TODO: Investigate --resume and -c to resume sessions in cursor-agent
        # For now, cursor-agent handles it or we just start fresh.

        for a in attachments:
            cmd.append(f"@{a}")

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

    async def _stream(self) -> AsyncIterator[BackendEvent]:  # noqa: PLR0915
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

            try:
                data = json.loads(line)
            except ValueError:
                continue

            ev_type = data.get("type") or data.get("kind")
            try:
                if ev_type in ("init", "session", "session_start"):
                    sid = data.get("session_id") or data.get("session", {}).get("id")
                    if sid:
                        self._session_id = sid
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

                if ev_type in ("done", "stop", "session_end"):
                    ev = _flush_text()
                    if ev:
                        yield ev
                    sid = data.get("session_id") or data.get("session", {}).get("id")
                    if sid:
                        self._session_id = sid
                    yield DoneEvent(session_id=self._session_id)
                    continue
            except ValidationError:
                pass

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
