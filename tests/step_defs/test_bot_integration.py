import asyncio
import json
import time
from unittest.mock import MagicMock, patch

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from canivete.bot.daemon import Daemon

pytestmark = pytest.mark.integration

scenarios("../features/bot_integration.feature")


@pytest.fixture
def context() -> dict:
    return {"sent_messages": []}


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test_token")


@pytest.fixture(autouse=True)
def mock_urlopen_for_daemon(monkeypatch, context):
    mock = MagicMock()
    mock.return_value.__enter__.return_value.read.return_value = (
        b'{"ok": true, "result": {"message_id": 42}}'
    )

    def side_effect(req, timeout=None):
        payload = json.loads(req.data.decode())
        context["sent_messages"].append(payload)
        return mock.return_value

    mock.side_effect = side_effect
    monkeypatch.setattr("urllib.request.urlopen", mock)
    return mock


@given("a mock Telegram API")
def mock_telegram_api(context):
    pass


@given("a mock claude subprocess returning text events")
def mock_claude_subprocess_text(context):
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.stderr = MagicMock()
    mock_proc.stderr.readline.side_effect = [""]

    events = [
        json.dumps({"type": "text", "text": "Hello "}),
        json.dumps({"type": "text", "text": "world!"}),
        json.dumps({"type": "done"}),
    ]

    def mock_stdout_readline():
        return (events.pop(0) + "\n") if events else ""

    mock_proc.stdout = MagicMock()
    mock_proc.stdout.readline.side_effect = mock_stdout_readline

    context["popen_patcher"] = patch("subprocess.Popen", return_value=mock_proc)
    context["mock_popen"] = context["popen_patcher"].start()


@when(parsers.parse("the daemon receives a message from chat {chat_id}"))
def daemon_receives_message(context, chat_id):
    daemon = Daemon(backend_name="claude-code")
    context["daemon"] = daemon
    worker = daemon.get_worker(int(chat_id))

    async def wait_worker():
        worker.handle_text("Hi Claude!")
        # Give enough time for the stream to process and edit_message to be called
        # edit_message only triggers when now - last_edit_time > 1.0 or at the end
        # Since it reaches "done" event, the finally block will call edit_message regardless of time!
        for _ in range(40):
            if not worker.is_running:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.1)  # wait for the final message to be sent

    asyncio.run(wait_worker())
    context["worker"] = worker


@then("the daemon spawns claude with the message as prompt")
def check_claude_spawn(context):
    mock_popen = context["mock_popen"]
    mock_popen.assert_called_once()
    args = mock_popen.call_args[0][0]
    assert "claude" in args
    assert "-p" in args
    assert "Hi Claude!" in args


@then("the daemon sends the response text back via Telegram")
def check_telegram_response(context):
    messages = context["sent_messages"]
    assert any("Hello\nworld\\!" in msg.get("text", "") for msg in messages)


@given(parsers.parse('a chat with active session_id "{session_id}"'))
def mock_chat_with_session_id(context, session_id):
    pass


@when("user sends /new")
def user_sends_new(context):
    daemon = Daemon(backend_name="claude-code")
    context["daemon"] = daemon
    worker = daemon.get_worker(123)
    worker.session_id = "abc-123"

    async def run_cmd():
        worker.handle_text("/new")
        await asyncio.sleep(0.1)

    asyncio.run(run_cmd())
    context["worker"] = worker


@then("worker.session_id is None")
def check_worker_session_id(context):
    assert context["worker"].session_id is None


@then("the response message confirms")
def check_response_message(context):
    messages = context["sent_messages"]
    assert any("abc-123" in msg.get("text", "") for msg in messages)


@given('a mock claude subprocess emitting "429 Too Many Requests" on stderr')
def mock_claude_429(context):
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.stdout = MagicMock()
    mock_proc.stdout.readline.side_effect = lambda: time.sleep(1) or ""

    # We must yield lines sequentially in a way thread can consume it
    stderr_lines = ["429 Too Many Requests: RESOURCE_EXHAUSTED\n", ""]
    mock_proc.stderr = MagicMock()
    mock_proc.stderr.readline.side_effect = lambda: stderr_lines.pop(0) if stderr_lines else ""

    context["popen_patcher"] = patch("subprocess.Popen", return_value=mock_proc)
    context["mock_popen"] = context["popen_patcher"].start()
    context["mock_proc"] = mock_proc


@when("the daemon spawns it")
def daemon_spawns_it(context):
    daemon = Daemon(backend_name="claude-code")
    context["daemon"] = daemon
    worker = daemon.get_worker(123)

    async def run_spawn():
        worker.handle_text("Hi")
        # Thread will read stderr, find match, call backend.kill()
        for _ in range(40):
            if worker.fatal_error_matched:
                break
            await asyncio.sleep(0.05)
        # Give a small moment for async task handle_fatal_exit to run
        await asyncio.sleep(0.2)

    asyncio.run(run_spawn())
    context["worker"] = worker


@then("the daemon kills it within 1 second")
def check_daemon_kills(context):
    mock_proc = context["mock_proc"]
    assert mock_proc.terminate.called or mock_proc.kill.called


@then("the daemon sends a fatal error message to Telegram")
def check_fatal_message(context):
    messages = context["sent_messages"]
    assert any("RESOURCE_EXHAUSTED" in msg.get("text", "") for msg in messages)
    assert any("provider's quota" in msg.get("text", "") for msg in messages)


@pytest.fixture(autouse=True)
def cleanup(context):
    yield
    if "popen_patcher" in context:
        context["popen_patcher"].stop()
