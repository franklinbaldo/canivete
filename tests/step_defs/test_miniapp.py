import json
import os
import shlex
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pytest_bdd import given, parsers, scenarios, then, when
from typer.testing import CliRunner

from canivete.cli import app

scenarios("../features/miniapp.feature")
runner = CliRunner()


@given("TELEGRAM_BOT_TOKEN is set")
def telegram_bot_token_is_set(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "mocked_token:123456")


@given("CRON_CHAT_ID is set")
def cron_chat_id_is_set(monkeypatch):
    monkeypatch.setenv("CRON_CHAT_ID", "99999999")


@given("a temporary HTML file exists", target_fixture="temp_html_file")
def temp_html_file(tmp_path: Path) -> Path:
    f = tmp_path / "test.html"
    f.write_text("<h1>Hello World</h1>", encoding="utf-8")
    return f


@pytest.fixture(autouse=True)
def mock_subprocess_and_telegram(monkeypatch):
    """Monkeypatch subprocess and urllib locally.
    Since conftest 'I run canivete with arguments' uses subprocess.run to spawn a new process,
    we must OVERRIDE it in this file using a local definition for `run_miniapp_args` that
    calls Typer runner instead, so our mocks apply."""


# We must mock at the application level because `_post_form` uses `urllib.request.urlopen`.
# But `respx` handles `httpx` and `requests`, NOT `urllib`. We need to mock `urllib.request.urlopen`
# or we use `respx` if the code was using `httpx`. The code uses `urllib.request.urlopen`!


@pytest.fixture
def api_mocks(monkeypatch):
    mock_run = MagicMock()
    # Need to mimic a CompletedProcess since we use `result.stdout`
    mock_cp = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="https://gist.github.com/franklinbaldo/1234abcd\n", stderr=""
    )
    mock_run.return_value = mock_cp
    monkeypatch.setattr(subprocess, "run", mock_run)

    # Mock urllib.request.urlopen
    mock_urlopen = MagicMock()

    class MockResponse:
        def __init__(self, json_data):
            self.json_data = json_data
            self.read_called = 0

        def read(self):
            return json.dumps(self.json_data).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def urlopen_side_effect(request, timeout=None):
        return MockResponse({"ok": True, "result": {"message_id": 999}})

    mock_urlopen.side_effect = urlopen_side_effect
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    return {"subprocess_run": mock_run, "urlopen": mock_urlopen}


# Redefine the `when` step locally so it uses Typer runner INSTEAD of spawning a process
# This ensures our mocks in `monkeypatch` take effect.
@when(parsers.parse('I run canivete with arguments "{args}"'), target_fixture="result")
def run_miniapp_args(args: str, request, api_mocks) -> subprocess.CompletedProcess:
    if "<html_file>" in args:
        temp_html_file = request.getfixturevalue("temp_html_file")
        args = args.replace("<html_file>", str(temp_html_file))

    os.environ["TELEGRAM_BOT_TOKEN"] = "mocked_token:123456"  # noqa: S105
    os.environ["CRON_CHAT_ID"] = "99999999"

    result = runner.invoke(app, shlex.split(args))

    return subprocess.CompletedProcess(
        args=shlex.split(args),
        returncode=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr or "",
    )


@then("the GitHub CLI was called to create a gist")
def gh_cli_called(api_mocks):
    mock_run = api_mocks["subprocess_run"]
    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert args[0:4] == ["gh", "gist", "create", "--public"]


@then("the GitHub CLI was NOT called")
def gh_cli_not_called(api_mocks):
    mock_run = api_mocks["subprocess_run"]
    mock_run.assert_not_called()


@then("a Telegram message was sent with a web_app button")
def tg_msg_sent(api_mocks):
    mock_urlopen = api_mocks["urlopen"]
    assert mock_urlopen.called
    req = mock_urlopen.call_args[0][0]

    body = urllib.parse.parse_qs(req.data.decode())
    reply_markup = json.loads(body["reply_markup"][0])

    btn = reply_markup["inline_keyboard"][0][0]
    assert "web_app" in btn
    assert btn["web_app"]["url"] == "https://franklinbaldo.github.io/intuit/?gist=1234abcd"


@then(parsers.parse('a Telegram message was sent with a web_app button for gist "{gist_id}"'))
def tg_msg_sent_gist(api_mocks, gist_id: str):
    mock_urlopen = api_mocks["urlopen"]
    assert mock_urlopen.called
    req = mock_urlopen.call_args[0][0]

    body = urllib.parse.parse_qs(req.data.decode())
    reply_markup = json.loads(body["reply_markup"][0])

    btn = reply_markup["inline_keyboard"][0][0]
    assert "web_app" in btn
    assert f"https://franklinbaldo.github.io/intuit/?gist={gist_id}" == btn["web_app"]["url"]
