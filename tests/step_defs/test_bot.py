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


@then('the string contains "# SOUL.md" and "# TOOLS.md"')
def check_soul_tools_headers(test_context):
    sp = test_context["system_prompt"]
    assert "# SOUL.md" in sp
    assert "# TOOLS.md" in sp
    # Ordem alfabética
    assert sp.find("# SOUL.md") < sp.find("# TOOLS.md")


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
