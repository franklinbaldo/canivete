import asyncio
from unittest.mock import MagicMock, patch

from pytest_bdd import given, scenarios, then, when
from typer.testing import CliRunner

from canivete.bot.backends.base import DoneEvent, TextEvent
from canivete.bot.daemon import ChatWorker
from canivete.cli import app

scenarios("../features/bot.feature")
runner = CliRunner()


import pytest


@pytest.fixture
def test_context():
    return {}


@when('I run "canivete bot --help"')
def run_bot_help(test_context):
    result = runner.invoke(app, ["bot", "--help"])
    test_context["result"] = result


@then("the exit code is 0")
def check_exit_code_0(test_context):
    assert test_context["result"].exit_code == 0, test_context["result"].stdout


@then('the output contains "Backend to use"')
def check_output_contains_backend(test_context):
    assert "Backend to use" in test_context["result"].output


@when('I run the bot daemon with backend "unknown-backend"')
def run_unknown_backend(test_context):
    worker = ChatWorker(chat_id=123, backend_name="unknown-backend")
    with patch("canivete.bot.daemon.err_console.print") as mock_print:
        worker.spawn_backend("test")
        test_context["mock_print"] = mock_print


@then('the daemon prints "Unknown backend: unknown-backend"')
def check_unknown_backend_print(test_context):
    test_context["mock_print"].assert_called_with("[red]Unknown backend:[/] unknown-backend")


@when('a user sends "Hello agent"')
def user_sends_hello(test_context):
    test_context["prompt"] = "Hello agent"


@then('the bot daemon should spawn the "gemini-cli" backend with prompt "Hello agent"')
def daemon_spawns_gemini(test_context):
    worker = ChatWorker(chat_id=123, backend_name="gemini-cli")
    with (
        patch("subprocess.Popen") as mock_popen,
        patch("canivete.bot.daemon.Thread"),
        patch("canivete.bot.daemon.asyncio.create_task"),
    ):
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc
        worker.spawn_backend(test_context["prompt"])
        mock_popen.assert_called_once()
        args = mock_popen.call_args[0][0]
        assert "gemini" in args
        assert "-p" in args
        assert test_context["prompt"] in args


@given('the backend emits "text" and "done" events')
def backend_emits_text_done(test_context):
    async def mock_events():
        yield TextEvent(text="Hello")
        yield DoneEvent(session_id="123")

    test_context["mock_events"] = mock_events()


@when("a user sends a message")
def user_sends_msg(test_context):
    pass


@then("the daemon calls editMessageText with the rendered events")
def daemon_calls_edit(test_context):
    worker = ChatWorker(chat_id=123, backend_name="gemini-cli")

    with (
        patch("canivete.bot.daemon.send_message", return_value=456),
        patch("canivete.bot.daemon.edit_message") as mock_edit,
    ):
        from canivete.bot.backends.base import SpawnResult

        spawn_res = SpawnResult(events=test_context["mock_events"], session_id="123")

        asyncio.run(worker._consume_events(spawn_res))

        # It should edit message at least once at the end
        mock_edit.assert_called()


@given("the backend emits no renderable events")
def backend_emits_no_events(test_context):
    async def mock_events():
        if False:
            yield  # async generator that yields nothing

    test_context["empty_events"] = mock_events()


@then(
    'the daemon edits the placeholder with a "Backend exited without producing any output" fallback'
)
def daemon_edits_with_fallback(test_context):
    worker = ChatWorker(chat_id=123, backend_name="gemini-cli")

    with (
        patch("canivete.bot.daemon.send_message", return_value=789),
        patch("canivete.bot.daemon.edit_message") as mock_edit,
    ):
        from canivete.bot.backends.base import SpawnResult

        spawn_res = SpawnResult(events=test_context["empty_events"], session_id=None)
        asyncio.run(worker._consume_events(spawn_res))

        mock_edit.assert_called_once()
        args = mock_edit.call_args[0]
        assert "Backend exited without producing any output" in args[2]


@given("urlopen raises TimeoutError")
def urlopen_raises_timeout(test_context):
    test_context["timeout_exc"] = TimeoutError("The read operation timed out")


@when("the daemon polls Telegram")
def daemon_polls_telegram(test_context):
    pass


@then("_post_json returns None and logs the error")
def post_json_handles_timeout(test_context):
    from canivete.bot.daemon import _post_json

    with (
        patch(
            "canivete.bot.daemon.urllib.request.urlopen",
            side_effect=test_context["timeout_exc"],
        ),
        patch("canivete.bot.daemon.err_console.print") as mock_print,
    ):
        result = _post_json("http://example.invalid/x", {"a": 1})

    assert result is None
    mock_print.assert_called_once()
    printed = mock_print.call_args[0][0]
    assert "Telegram API Error" in printed


@given('the backend stderr emits "RESOURCE_EXHAUSTED"')
def backend_stderr_exhausted(test_context):
    pass


@then("the daemon immediately kills the subprocess")
def daemon_kills_subprocess(test_context):
    worker = ChatWorker(chat_id=123, backend_name="gemini-cli")

    mock_stderr = MagicMock()
    mock_stderr.readline.side_effect = ["RESOURCE_EXHAUSTED details\n", ""]

    worker.backend = MagicMock()

    # Run the watcher synchronously for test
    worker._watch_stderr(mock_stderr)

    worker.backend.kill.assert_called_once()
    test_context["worker"] = worker


@then('the daemon posts an error message with suggestion for "rate_limit"')
def check_error_message(test_context):
    worker = test_context["worker"]
    with (
        patch("canivete.bot.daemon.asyncio.create_task") as mock_task,
        patch("canivete.bot.daemon.asyncio.to_thread") as mock_thread,
        patch("canivete.bot.daemon.send_message") as mock_send,
    ):
        worker._handle_fatal_exit()
        mock_task.assert_called_once()
        args = mock_thread.call_args[0]
        assert "Quota / rate limit hit" in args[2]


@given('the backend process hangs for "AGENT_TIMEOUT"')
def backend_hangs(test_context):
    pass


@then("the daemon kills the subprocess")
def daemon_timeout_kills(test_context):
    worker = ChatWorker(chat_id=123, backend_name="gemini-cli")
    worker.is_running = True
    worker.timeout = 0  # trigger immediately
    worker.start_time = 0
    worker.backend = MagicMock()

    # Run synchronously
    worker._watch_timeout()

    worker.backend.kill.assert_called_once()
    test_context["worker"] = worker


@then("the daemon posts a timeout error message")
def daemon_posts_timeout(test_context):
    worker = test_context["worker"]
    with (
        patch("canivete.bot.daemon.asyncio.create_task") as mock_task,
        patch("canivete.bot.daemon.asyncio.to_thread") as mock_thread,
        patch("canivete.bot.daemon.send_message") as mock_send,
    ):
        worker._handle_fatal_exit()
        mock_task.assert_called_once()
        args = mock_thread.call_args[0]
        assert "Subprocess hit AGENT_TIMEOUT" in args[2]


@when('a user clicks an inline button with data "vote_yes"')
def click_inline_btn(test_context):
    import os

    from canivete.bot.callback import handle_callback_query

    os.environ["TELEGRAM_BOT_TOKEN"] = "dummy"

    cb = {
        "id": "query_123",
        "data": "vote_yes",
        "from": {"first_name": "TestUser"},
        "message": {"message_id": 999, "chat": {"id": 123}},
    }

    with patch("canivete.bot.callback.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value.__enter__.return_value.read.return_value = b'{"ok": true}'

        res = handle_callback_query(cb)
        test_context["mock_urlopen"] = mock_urlopen
        test_context["cb_res"] = res


@then("the daemon calls answerCallbackQuery")
def check_answer_cb(test_context):
    calls = test_context["mock_urlopen"].call_args_list
    # Note: Request object's url might be inside the args
    assert any("answerCallbackQuery" in c[0][0].full_url for c in calls)


@then('the daemon injects a pseudo-message containing "vote_yes" into the chat worker')
def check_pseudo_msg(test_context):
    assert "vote_yes" in test_context["cb_res"]
    assert "[TestUser clicked" in test_context["cb_res"]


@when('a user sends the dynamic command "/pick_2"')
def dynamic_cmd(test_context):
    from canivete.bot.commands import handle_dynamic_command

    test_context["dyn_res"] = handle_dynamic_command("/pick_2", "Frank")


@then('the daemon injects a pseudo-message containing "invoked /pick_2" into the chat worker')
def check_dyn_msg(test_context):
    assert "invoked /pick_2" in test_context["dyn_res"]


@when('a user sends the static command "/cancel"')
def static_cmd(test_context):
    from canivete.bot.commands import handle_dynamic_command

    test_context["stat_res"] = handle_dynamic_command("/cancel", "Frank")


@then("the daemon does not inject a pseudo-message")
def check_no_msg(test_context):
    assert test_context["stat_res"] is None


@when("the daemon spawns a Claude backend for a new chat")
def spawns_claude_new_chat(test_context):
    worker = ChatWorker(chat_id=123, backend_name="claude-code")
    with (
        patch("subprocess.Popen") as mock_popen,
        patch("canivete.bot.daemon.Thread"),
        patch("canivete.bot.daemon.asyncio.create_task"),
    ):
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc
        worker.spawn_backend("Hello")
        test_context["claude_popen_args"] = mock_popen.call_args[0][0]


@then("it passes --session-id with a valid UUIDv7")
def check_claude_session_id(test_context):
    import uuid_utils

    args = test_context["claude_popen_args"]
    assert "--session-id" in args
    idx = args.index("--session-id")
    session_id = args[idx + 1]

    # Verify it is a valid UUIDv7
    u = uuid_utils.UUID(session_id)
    assert u.version == 7


@given('a chat has an active session_id "0190d5f1-4c00-7f38-b7d8-1a4c6c8e3a2d"')
def chat_has_active_session_id(test_context):
    test_context["active_session_id"] = "0190d5f1-4c00-7f38-b7d8-1a4c6c8e3a2d"


@when("the daemon spawns Claude for the same chat")
def daemon_spawns_claude_same_chat(test_context):
    worker = ChatWorker(chat_id=123, backend_name="claude-code")
    worker.session_id = test_context["active_session_id"]
    worker.is_new_session = False

    with (
        patch("subprocess.Popen") as mock_popen,
        patch("canivete.bot.daemon.Thread"),
        patch("canivete.bot.daemon.asyncio.create_task"),
    ):
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc
        worker.spawn_backend("Hello again")
        test_context["claude_popen_args_resume"] = mock_popen.call_args[0][0]


@then("it passes --resume 0190d5f1-4c00-7f38-b7d8-1a4c6c8e3a2d")
def check_claude_resume(test_context):
    args = test_context["claude_popen_args_resume"]
    assert "--resume" in args
    idx = args.index("--resume")
    assert args[idx + 1] == "0190d5f1-4c00-7f38-b7d8-1a4c6c8e3a2d"


@when("the daemon asks Gemini backend for a new session_id")
def daemon_asks_gemini_session_id(test_context):
    from canivete.bot.backends import REGISTRY

    backend_cls = REGISTRY.get("gemini-cli")
    backend = backend_cls()
    test_context["gemini_generated_id"] = backend.generate_session_id()


@when("the daemon asks Cursor backend for a new session_id")
def daemon_asks_cursor_session_id(test_context):
    from canivete.bot.backends import REGISTRY

    backend_cls = REGISTRY.get("cursor")
    backend = backend_cls()
    test_context["cursor_generated_id"] = backend.generate_session_id()


@when("the daemon asks Cline backend for a new session_id")
def daemon_asks_cline_session_id(test_context):
    from canivete.bot.backends import REGISTRY

    backend_cls = REGISTRY.get("cline")
    backend = backend_cls()
    test_context["cline_generated_id"] = backend.generate_session_id()


@when("the daemon asks OpenCode backend for a new session_id")
def daemon_asks_opencode_session_id(test_context):
    from canivete.bot.backends import REGISTRY

    backend_cls = REGISTRY.get("opencode")
    backend = backend_cls()
    test_context["opencode_generated_id"] = backend.generate_session_id()


@then("it returns None")
def check_returns_none(test_context):
    val = test_context.get(
        "gemini_generated_id",
        test_context.get(
            "kilo_generated_id",
            test_context.get(
                "cursor_generated_id",
                test_context.get(
                    "cline_generated_id",
                    test_context.get("opencode_generated_id"),
                ),
            ),
        ),
    )
    assert val is None


# ──────── Cursor backend step defs ────────────────────────────────────────


@when('I spawn CursorBackend with a system prompt "I am Cursor"')
def spawn_cursor_with_prompt(test_context, monkeypatch, tmp_path):
    from canivete.bot.backends.cursor import CursorBackend

    workspace = tmp_path / "cursor-workspace"
    workspace.mkdir()
    monkeypatch.setenv("WORKSPACE", str(workspace))
    test_context["cursor_workspace"] = workspace

    backend = CursorBackend()
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock()
        backend.spawn(
            "hello",
            session_id=None,
            attachments=[],
            system_prompt="I am Cursor",
        )


@then('it writes "I am Cursor" to CURSOR.md in the workspace')
def check_cursor_md_written(test_context):
    workspace = test_context["cursor_workspace"]
    cursor_md = workspace / "CURSOR.md"
    assert cursor_md.exists()
    assert cursor_md.read_text(encoding="utf-8") == "I am Cursor"


@when('I spawn CursorBackend with prompt "Hello"')
def spawn_cursor_simple(test_context, tmp_path, monkeypatch):
    from canivete.bot.backends.cursor import CursorBackend

    monkeypatch.setenv("WORKSPACE", str(tmp_path))
    backend = CursorBackend()
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock()
        backend.spawn(
            "Hello",
            session_id=None,
            attachments=[],
        )
        test_context["cursor_simple_args"] = mock_popen.call_args[0][0]


@then(
    'the cursor command includes "cursor-agent", "-p", "Hello", "--output-format", "stream-json", "--force"'
)
def check_cursor_command_args(test_context):
    args = test_context["cursor_simple_args"]
    assert "cursor-agent" in args
    assert "-p" in args
    assert "Hello" in args
    assert "--output-format" in args
    assert "stream-json" in args
    assert "--force" in args


# ──────── Cline backend step defs ─────────────────────────────────────────


@when('I spawn ClineBackend with a system prompt "I am Cline"')
def spawn_cline_with_prompt(test_context, monkeypatch, tmp_path):
    from canivete.bot.backends.cline import ClineBackend

    workspace = tmp_path / "cline-workspace"
    workspace.mkdir()
    monkeypatch.setenv("WORKSPACE", str(workspace))
    test_context["cline_workspace"] = workspace

    backend = ClineBackend()
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock()
        backend.spawn(
            "hello",
            session_id=None,
            attachments=[],
            system_prompt="I am Cline",
        )


@then('it writes "I am Cline" to .clinerules in the workspace')
def check_clinerules_written(test_context):
    workspace = test_context["cline_workspace"]
    clinerules = workspace / ".clinerules"
    assert clinerules.exists()
    assert clinerules.read_text(encoding="utf-8") == "I am Cline"


@when('I spawn ClineBackend with prompt "Hello"')
def spawn_cline_simple(test_context, tmp_path, monkeypatch):
    from canivete.bot.backends.cline import ClineBackend

    monkeypatch.setenv("WORKSPACE", str(tmp_path))
    backend = ClineBackend()
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock()
        backend.spawn(
            "Hello",
            session_id=None,
            attachments=[],
        )
        test_context["cline_simple_args"] = mock_popen.call_args[0][0]


@then('the cline command includes "cline", "-y", "Hello"')
def check_cline_command_args(test_context):
    args = test_context["cline_simple_args"]
    assert "cline" in args
    assert "-y" in args
    assert "Hello" in args


# ──────── OpenCode backend step defs ──────────────────────────────────────


@when('I spawn OpenCodeBackend with a system prompt "I am OpenCode"')
def spawn_opencode_with_prompt(test_context, monkeypatch, tmp_path):
    from canivete.bot.backends.opencode import OpenCodeBackend

    workspace = tmp_path / "opencode-workspace"
    workspace.mkdir()
    monkeypatch.setenv("WORKSPACE", str(workspace))
    test_context["opencode_workspace"] = workspace

    backend = OpenCodeBackend()
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock()
        backend.spawn(
            "hello",
            session_id=None,
            attachments=[],
            system_prompt="I am OpenCode",
        )


@then('it writes "I am OpenCode" to OPENCODE.md in the workspace')
def check_opencode_md_written(test_context):
    workspace = test_context["opencode_workspace"]
    opencode_md = workspace / "OPENCODE.md"
    assert opencode_md.exists()
    assert opencode_md.read_text(encoding="utf-8") == "I am OpenCode"


@when('I spawn OpenCodeBackend with prompt "Hello"')
def spawn_opencode_simple(test_context, tmp_path, monkeypatch):
    from canivete.bot.backends.opencode import OpenCodeBackend

    monkeypatch.setenv("WORKSPACE", str(tmp_path))
    backend = OpenCodeBackend()
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock()
        backend.spawn(
            "Hello",
            session_id=None,
            attachments=[],
        )
        test_context["opencode_simple_args"] = mock_popen.call_args[0][0]


@then('the opencode command includes "opencode", "run", "Hello"')
def check_opencode_command_args(test_context):
    args = test_context["opencode_simple_args"]
    assert "opencode" in args
    assert "run" in args
    assert "Hello" in args


@given("a chat has session_id S1")
def chat_has_session_id_s1(test_context):
    worker = ChatWorker(chat_id=123, backend_name="claude-code")
    worker.session_id = "S1"
    worker.is_new_session = False
    test_context["worker_s1"] = worker


@when("user sends /new")
def user_sends_new(test_context):
    worker = test_context["worker_s1"]
    with (
        patch("canivete.bot.daemon.asyncio.create_task") as mock_task,
        patch("canivete.bot.daemon.asyncio.to_thread") as mock_thread,
        patch("canivete.bot.daemon.send_message") as mock_send,
    ):
        worker.handle_text("/new")
        test_context["mock_thread"] = mock_thread


@then("worker.session_id is None")
def check_worker_session_id_is_none(test_context):
    worker = test_context["worker_s1"]
    assert worker.session_id is None


@then("worker.is_new_session is True")
def check_worker_is_new_session(test_context):
    worker = test_context["worker_s1"]
    assert worker.is_new_session is True


@then('the message confirms "Anterior preservada: S1"')
def check_message_confirms_anterior(test_context):
    args = test_context["mock_thread"].call_args[0]
    # args[2] is the text message passed to send_message
    assert "Anterior preservada: `S1`" in args[2]


@given('we simulate both "gemini-cli" and "claude-code"')
def smoke_simulate_both(test_context):
    pass


@then("both backends should handle basic message flow")
def smoke_test_both(test_context):
    for b in ["gemini-cli", "claude-code"]:
        worker = ChatWorker(chat_id=123, backend_name=b)
        with (
            patch("subprocess.Popen"),
            patch("canivete.bot.daemon.Thread"),
            patch("canivete.bot.daemon.asyncio.create_task"),
        ):
            worker.spawn_backend("Hi")


from canivete.bot.daemon import build_system_prompt


@pytest.fixture
def agent_root(tmp_path):
    root = tmp_path / "agent_root"
    root.mkdir()
    return root


@given("an agent root with SOUL.md, TOOLS.md, CLAUDE.md, and README.md")
def agent_root_with_soul_tools_claude_readme(agent_root):
    (agent_root / "SOUL.md").write_text("I am Soul.", encoding="utf-8")
    (agent_root / "TOOLS.md").write_text("I have tools.", encoding="utf-8")
    (agent_root / "CLAUDE.md").write_text("Claude config", encoding="utf-8")
    (agent_root / "README.md").write_text("Readme info", encoding="utf-8")
    return agent_root


@when("I build the system prompt")
def run_build_system_prompt(test_context, agent_root):
    test_context["system_prompt"] = build_system_prompt(agent_root)


@then("it returns a string with SOUL.md and TOOLS.md concatenated")
def check_soul_tools_concat(test_context):
    sp = test_context["system_prompt"]
    assert "I am Soul." in sp
    assert "I have tools." in sp


@then("each file is prefixed with a FILE: <fullpath> header, SOUL.md first")
def check_soul_tools_headers(test_context, agent_root):
    sp = test_context["system_prompt"]
    # Cada arquivo é prefixado por uma linha "FILE: <fullpath>" entre regras "===".
    assert f"FILE: {agent_root / 'SOUL.md'}" in sp
    assert f"FILE: {agent_root / 'TOOLS.md'}" in sp
    # SOUL aparece sempre antes do resto, mesmo que ordem alfabética colocaria TOOLS depois.
    assert sp.find(f"FILE: {agent_root / 'SOUL.md'}") < sp.find(f"FILE: {agent_root / 'TOOLS.md'}")
    # Sanity: o cabeçalho "===" envolve a linha FILE.
    assert "================================================================\nFILE:" in sp


@given("an agent root with SOUL.md, CLAUDE.md, GEMINI.md, README.md, and SYSTEM.md")
def agent_root_with_skips(agent_root):
    (agent_root / "SOUL.md").write_text("I am Soul.", encoding="utf-8")
    (agent_root / "CLAUDE.md").write_text("Claude config", encoding="utf-8")
    (agent_root / "GEMINI.md").write_text("Gemini config", encoding="utf-8")
    (agent_root / "README.md").write_text("Readme info", encoding="utf-8")
    (agent_root / "SYSTEM.md").write_text("System generated", encoding="utf-8")
    return agent_root


@then("it returns a string with SOUL.md only")
def check_soul_only(test_context):
    sp = test_context["system_prompt"]
    assert "I am Soul." in sp


@then("it does not contain CLAUDE.md, GEMINI.md, README.md, or SYSTEM.md")
def check_no_skips(test_context):
    sp = test_context["system_prompt"]
    assert "Claude config" not in sp
    assert "Gemini config" not in sp
    assert "Readme info" not in sp
    assert "System generated" not in sp


@given("an agent root with SOUL.md and notes.md")
def agent_root_with_notes(agent_root):
    (agent_root / "SOUL.md").write_text("I am Soul.", encoding="utf-8")
    (agent_root / "notes.md").write_text("Just some notes.", encoding="utf-8")
    return agent_root


@then("it does not contain notes.md")
def check_no_notes(test_context):
    sp = test_context["system_prompt"]
    assert "Just some notes." not in sp


@given("an agent root with no all-caps md files")
def agent_root_empty(agent_root):
    (agent_root / "README.md").write_text("Just readme", encoding="utf-8")
    return agent_root


@then("it returns an empty string")
def check_empty_string(test_context):
    sp = test_context["system_prompt"]
    assert sp == ""


@when('I spawn ClaudeCodeBackend with a system prompt "I am Claudio"')
def spawn_claude_backend(test_context):
    from canivete.bot.backends.claude_code import ClaudeCodeBackend

    backend = ClaudeCodeBackend()
    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc
        backend.spawn("hello", session_id=None, attachments=[], system_prompt="I am Claudio")
        test_context["mock_popen"] = mock_popen


@then('the claude command includes "--append-system-prompt" and "I am Claudio"')
def check_claude_command(test_context):
    mock_popen = test_context["mock_popen"]
    mock_popen.assert_called_once()
    args = mock_popen.call_args[0][0]
    assert "--append-system-prompt" in args
    idx = args.index("--append-system-prompt")
    assert args[idx + 1] == "I am Claudio"


@when('I spawn GeminiCliBackend with a system prompt "I am Aparicio"')
def spawn_gemini_backend(test_context, monkeypatch, tmp_path):
    from canivete.bot.backends.gemini_cli import GeminiCliBackend

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("WORKSPACE", str(workspace))
    test_context["workspace"] = workspace

    backend = GeminiCliBackend()
    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc
        backend.spawn("hello", session_id=None, attachments=[], system_prompt="I am Aparicio")


@then('it writes "I am Aparicio" to GEMINI.md in the workspace')
def check_gemini_md_write(test_context):
    workspace = test_context["workspace"]
    gemini_md = workspace / "GEMINI.md"
    assert gemini_md.exists()
    assert gemini_md.read_text(encoding="utf-8") == "I am Aparicio"


# ── Direct unit tests (no BDD) for the gemini-cli stream-json parser. ──


def _drain_stream(backend):
    """Run the async generator to completion and collect events."""

    async def _run():
        return [ev async for ev in backend._stream()]

    return asyncio.run(_run())


def _make_backend_with_lines(lines: list[str]):
    from canivete.bot.backends.gemini_cli import GeminiCliBackend

    backend = GeminiCliBackend()
    proc = MagicMock()
    proc.stdout.readline.side_effect = [*lines, ""]
    backend.proc = proc
    return backend


def test_gemini_parser_aggregates_assistant_deltas():
    """Real gemini-cli output: type=message with delta=true chunks, role=assistant."""

    lines = [
        '{"type":"init","session_id":"abc-123","model":"gemini-3"}\n',
        '{"type":"message","role":"user","content":"oi"}\n',
        '{"type":"message","role":"assistant","content":"Olá","delta":true}\n',
        '{"type":"message","role":"assistant","content":", Franklin","delta":true}\n',
        '{"type":"message","role":"assistant","content":"!","delta":true}\n',
    ]
    events = _drain_stream(_make_backend_with_lines(lines))
    text_events = [e for e in events if isinstance(e, TextEvent)]
    assert len(text_events) == 1
    assert text_events[0].text == "Olá, Franklin!"


def test_gemini_parser_handles_tool_use_and_result():
    from canivete.bot.backends.base import ToolCallEvent, ToolResultEvent

    lines = [
        '{"type":"message","role":"assistant","content":"vou listar","delta":true}\n',
        '{"type":"tool_use","tool_name":"glob","tool_id":"g_1","parameters":{"pattern":"*.md"}}\n',
        '{"type":"tool_result","tool_id":"g_1","status":"success","output":"a.md"}\n',
    ]
    events = _drain_stream(_make_backend_with_lines(lines))
    # text flush before tool_use, then tool_call, then tool_result
    assert len(events) == 3
    assert isinstance(events[0], TextEvent)
    assert events[0].text == "vou listar"
    assert isinstance(events[1], ToolCallEvent)
    assert events[1].tool == "glob"
    assert events[1].args == {"pattern": "*.md"}
    assert events[1].call_id == "g_1"
    assert isinstance(events[2], ToolResultEvent)
    assert events[2].ok is True
    assert events[2].output == "a.md"


def test_gemini_parser_flushes_text_on_eof():
    """No explicit done event — pending assistant text must still be yielded."""

    lines = [
        '{"type":"message","role":"assistant","content":"resposta","delta":true}\n',
        '{"type":"message","role":"assistant","content":" final","delta":true}\n',
    ]
    events = _drain_stream(_make_backend_with_lines(lines))
    assert len(events) == 1
    assert events[0].text == "resposta final"


def test_gemini_parser_captures_session_id_from_init():
    lines = ['{"type":"init","session_id":"sess-xyz","model":"m"}\n']
    backend = _make_backend_with_lines(lines)
    _drain_stream(backend)
    assert backend._session_id == "sess-xyz"


def test_bot_cli_reads_canivete_bot_backend_env(monkeypatch):
    """Compose files in the wild set CANIVETE_BOT_BACKEND, not AGENT_BACKEND."""

    monkeypatch.setenv("CANIVETE_BOT_BACKEND", "claude-code")
    monkeypatch.delenv("AGENT_BACKEND", raising=False)

    with patch("canivete.bot.daemon.run_daemon") as mock_run:
        result = runner.invoke(app, ["bot"])
    assert result.exit_code == 0, result.stdout
    mock_run.assert_called_once_with("claude-code")


def test_edit_message_skips_empty_text():
    """Empty text used to spam Telegram with HTTP 400 errors when the backend
    produced no renderable events."""
    from canivete.bot import daemon as d

    with patch.object(d, "_post_json") as mock_post:
        d.edit_message(123, 456, "")
        mock_post.assert_not_called()

        d.edit_message(123, 456, "hi")
        mock_post.assert_called_once()


def test_edit_message_skips_unchanged_text():
    """Telegram returns HTTP 400 'message is not modified' when an edit's text
    equals the current message text. The daemon edits on a 1s tick after
    every event, so without dedup it spams 400s when tool_result/done events
    follow a final text chunk with no further text changes."""
    from canivete.bot import daemon as d

    d._last_edit_text.clear()
    with patch.object(d, "_post_json") as mock_post:
        d.edit_message(1, 99, "hello")
        d.edit_message(1, 99, "hello")  # same text — must skip
        assert mock_post.call_count == 1

        d.edit_message(1, 99, "hello world")  # changed — sends
        assert mock_post.call_count == 2

        d.edit_message(2, 99, "hello world")  # different chat — sends
        assert mock_post.call_count == 3


def test_build_system_prompt_puts_soul_first_even_when_alphabetically_late(tmp_path):
    """Mesmo que outros nomes apareçam antes alfabeticamente, SOUL.md tem que
    abrir o system prompt — a persona precisa fixar a voz antes de qualquer
    instrução operacional."""
    from canivete.bot.daemon import build_system_prompt

    (tmp_path / "AGENTS.md").write_text("Agents body", encoding="utf-8")
    (tmp_path / "IDENTITY.md").write_text("Identity body", encoding="utf-8")
    (tmp_path / "SOUL.md").write_text("Soul body", encoding="utf-8")
    (tmp_path / "TOOLS.md").write_text("Tools body", encoding="utf-8")

    sp = build_system_prompt(tmp_path)

    soul = sp.find("Soul body")
    agents = sp.find("Agents body")
    identity = sp.find("Identity body")
    tools = sp.find("Tools body")

    assert soul != -1
    assert agents != -1
    assert identity != -1
    assert tools != -1
    # SOUL primeiro
    assert soul < agents
    assert soul < identity
    assert soul < tools
    # Resto em ordem alfabética: AGENTS < IDENTITY < TOOLS
    assert agents < identity < tools


def test_build_system_prompt_includes_full_paths(tmp_path):
    """Cada bloco precisa carregar o fullpath do arquivo no header — assim o
    agente sabe de onde veio e pode abrir/editar via filesystem."""
    from canivete.bot.daemon import build_system_prompt

    (tmp_path / "SOUL.md").write_text("Hi.", encoding="utf-8")

    sp = build_system_prompt(tmp_path)

    assert f"FILE: {tmp_path / 'SOUL.md'}" in sp


# ──────── Kilo backend step defs ──────────────────────────────────────────


@when('I spawn KiloBackend with a system prompt "I am Ireneo"')
def spawn_kilo_with_prompt(test_context, monkeypatch, tmp_path):
    from canivete.bot.backends.kilo import KiloBackend

    workspace = tmp_path / "kilo-workspace"
    workspace.mkdir()
    monkeypatch.setenv("WORKSPACE", str(workspace))
    test_context["kilo_workspace"] = workspace

    backend = KiloBackend()
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock()
        backend.spawn(
            "hello",
            session_id=None,
            attachments=[],
            system_prompt="I am Ireneo",
            is_new_session=True,
        )


@then('it writes "I am Ireneo" to AGENTS.md in the workspace')
def check_agents_md_write(test_context):
    workspace = test_context["kilo_workspace"]
    agents_md = workspace / "AGENTS.md"
    assert agents_md.exists()
    assert agents_md.read_text(encoding="utf-8") == "I am Ireneo"


@when('I spawn KiloBackend with prompt "Hello"')
def spawn_kilo_simple(test_context, tmp_path, monkeypatch):
    from canivete.bot.backends.kilo import KiloBackend

    monkeypatch.setenv("WORKSPACE", str(tmp_path))
    backend = KiloBackend()
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock()
        backend.spawn(
            "Hello",
            session_id=None,
            attachments=[],
            is_new_session=True,
        )
        test_context["kilo_simple_args"] = mock_popen.call_args[0][0]


@then('the kilo command includes "run", "--auto", "--format", "json"')
def check_kilo_args(test_context):
    args = test_context["kilo_simple_args"]
    assert args[0] == "kilo"
    assert "run" in args
    assert "--auto" in args
    assert "--format" in args
    fmt_idx = args.index("--format")
    assert args[fmt_idx + 1] == "json"


@then('the kilo command ends with positional prompt "Hello"')
def check_kilo_prompt_positional(test_context):
    args = test_context["kilo_simple_args"]
    assert args[-1] == "Hello"


@when("the daemon asks Kilo backend for a new session_id")
def daemon_asks_kilo_session_id(test_context):
    from canivete.bot.backends import REGISTRY

    backend_cls = REGISTRY.get("kilo")
    backend = backend_cls()
    test_context["kilo_generated_id"] = backend.generate_session_id()
