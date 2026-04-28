from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, Field


class TextEvent(BaseModel):
    kind: Literal["text"] = "text"
    text: str


class ToolCallEvent(BaseModel):
    kind: Literal["tool_call"] = "tool_call"
    tool: str
    args: dict
    call_id: str | None = None


class ToolResultEvent(BaseModel):
    kind: Literal["tool_result"] = "tool_result"
    call_id: str | None = None
    ok: bool
    output: str | None = None


class ThoughtEvent(BaseModel):
    kind: Literal["thought"] = "thought"
    subject: str | None = None
    description: str | None = None


class ErrorEvent(BaseModel):
    kind: Literal["error"] = "error"
    message: str
    fatal: bool = False


class StatsEvent(BaseModel):
    kind: Literal["stats"] = "stats"
    duration_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cached: int | None = None
    model: str | None = None


class DoneEvent(BaseModel):
    kind: Literal["done"] = "done"
    session_id: str | None = None


BackendEvent = Annotated[
    TextEvent
    | ToolCallEvent
    | ToolResultEvent
    | ThoughtEvent
    | ErrorEvent
    | StatsEvent
    | DoneEvent,
    Field(discriminator="kind"),
]


class SpawnResult(BaseModel):
    events: AsyncIterator[BackendEvent]
    session_id: str | None = None
    exit_code: int | None = None
    model_config = {"arbitrary_types_allowed": True}


class Backend(Protocol):
    name: str

    def spawn(
        self,
        prompt: str,
        *,
        session_id: str | None,
        attachments: list[Path],
    ) -> SpawnResult: ...

    def kill(self) -> None: ...
