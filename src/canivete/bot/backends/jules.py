import asyncio
import time
import urllib.error
from collections.abc import AsyncIterator
from pathlib import Path

from canivete.bot.backends.base import (
    BackendEvent,
    DoneEvent,
    ErrorEvent,
    SpawnResult,
    TextEvent,
    ThoughtEvent,
)
from canivete.jules import JulesClient


class JulesBackend:
    name: str = "jules"

    def __init__(self) -> None:
        self._session_id: str | None = None
        self._killed: bool = False

    def generate_session_id(self) -> str | None:
        return None  # Let the API generate the ID when creating a session

    def spawn(
        self,
        prompt: str,
        *,
        session_id: str | None,
        attachments: list[Path],
        system_prompt: str | None = None,
        is_new_session: bool = False,
    ) -> SpawnResult:
        self._killed = False
        self._session_id = session_id

        return SpawnResult(events=self._stream(prompt, is_new_session, system_prompt))

    async def _stream(self, prompt: str, is_new_session: bool, system_prompt: str | None) -> AsyncIterator[BackendEvent]:  # noqa: PLR0915
        loop = asyncio.get_running_loop()

        try:
            with JulesClient() as client:
                if is_new_session:
                    # Append system prompt to the main prompt if it exists
                    final_prompt = prompt
                    if system_prompt:
                        final_prompt = f"{system_prompt}\n\n{prompt}"

                    session_data = await loop.run_in_executor(
                        None,
                        lambda: client.create_session(
                            title="Bot Session",
                            prompt=final_prompt,
                        ),
                    )
                    self._session_id = session_data.get("name", "").split("/")[-1]
                else:
                    if not self._session_id:
                        yield ErrorEvent(message="No session ID provided for existing session.")
                        return

                    await loop.run_in_executor(
                        None, lambda: client.send_message(self._session_id, prompt)
                    )

                yield TextEvent(text=f"🔗 **Jules Session:** https://jules.google.com/session/{self._session_id}")

                last_state = None
                seen_activities: set[str] = set()
                last_heartbeat = time.time()

                while not self._killed:
                    if not self._session_id:
                        break

                    session_data = await loop.run_in_executor(
                        None, lambda: client.get_session(self._session_id)
                    )
                    state = session_data.get("state", "UNKNOWN")

                    now = time.time()
                    if state != last_state:
                        yield ThoughtEvent(
                            subject="State Update", description=f"Jules state: {state}"
                        )
                        last_state = state
                        last_heartbeat = now
                    elif state in ("IN_PROGRESS", "RUNNING") and (now - last_heartbeat) > 30:
                        # Yield keepalive to prevent Canivete from timing out due to inactivity
                        yield ThoughtEvent(
                            subject="Keepalive", description=f"Jules is still working... (state: {state})"
                        )
                        last_heartbeat = now

                    # Fetch activities to emulate text/tool streaming
                    try:
                        activities_data = await loop.run_in_executor(
                            None,
                            lambda: client._request(  # noqa: SLF001
                                "GET", f"/sessions/{self._session_id}/activities"
                            ),
                        )
                        activities = activities_data.get("activities", [])

                        # Iterate over activities (API returns newest first or oldest first? usually newest first)
                        # So reverse them to replay chronologically
                        for act in reversed(activities):
                            act_name = act.get("name", "")
                            if not act_name or act_name in seen_activities:
                                continue

                            seen_activities.add(act_name)

                            if "message" in act:
                                msg = act["message"]
                                yield TextEvent(text=msg)
                            elif "toolCall" in act:
                                tool_call = act["toolCall"]
                                yield ThoughtEvent(
                                    subject="Tool Execution",
                                    description=str(tool_call.get("tool", "unknown tool")),
                                )

                    except urllib.error.HTTPError as e:
                        # Ignore intermittent 404s/500s on activities if session is still valid
                        yield ThoughtEvent(
                            subject="Activities fetch warning",
                            description=f"HTTP {e.code}",
                        )
                    except Exception as e:
                        yield ThoughtEvent(
                            subject="Activities fetch warning",
                            description=str(e),
                        )

                    if state in ("COMPLETED", "FAILED", "AWAITING_PLAN_APPROVAL"):
                        if state == "AWAITING_PLAN_APPROVAL":
                            yield ThoughtEvent(
                                subject="Action Required",
                                description="Jules is awaiting plan approval. You can approve it externally.",
                            )
                        elif state == "FAILED":
                            yield ErrorEvent(message="Jules session encountered a failure.")
                        break

                    await asyncio.sleep(2.5)

                yield DoneEvent(session_id=self._session_id)

        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode(errors="replace")
                yield ErrorEvent(message=f"Jules API Error {e.code}: {err_body}")
            except Exception:
                yield ErrorEvent(message=f"Jules API Error {e.code}")
        except Exception as e:
            yield ErrorEvent(message=f"Unexpected Error: {e}")

    def kill(self) -> None:
        self._killed = True
