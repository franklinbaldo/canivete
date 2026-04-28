"""Shared fixtures and step definitions for the BDD suite.

`pytest-bdd` discovers steps per-module, so anything used by more than
one feature lives here in conftest.py to be available everywhere."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from pytest_bdd import given, parsers, then, when
from typer.testing import CliRunner

from canivete.cli import app

# ── fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def cron_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated CRON_LOG path. Each scenario starts with a clean slate;
    subprocess invocations inherit the env via monkeypatch."""
    p = tmp_path / "cron.jsonl"
    monkeypatch.setenv("CRON_LOG", str(p))
    return p


@pytest.fixture
def cli_state() -> dict:
    """Mutable scratchpad shared across steps in a single scenario:
    last subprocess result, IDs of scheduled jobs, etc."""
    return {"result": None, "last_job_id": None, "use_in_process": False, "mock_profile": {}}


# ── shared steps ─────────────────────────────────────────────────────


def _invoke(args: list[str], in_process=False, mock_profile=None) -> subprocess.CompletedProcess:  # noqa: C901
    """Run `python -m canivete <args>` with the inherited env (so the
    monkeypatched CRON_LOG flows through)."""
    if in_process:
        # Monkeypatch urllib.request.urlopen to use our respx mock
        def mock_urlopen(req, timeout=None):
            method = req.get_method()
            url = req.full_url
            headers = dict(req.headers)
            data = req.data
            with httpx.Client() as client:
                httpx_req = client.build_request(method, url, headers=headers, content=data)
                httpx_resp = client.send(httpx_req)

            class MockResponse:
                def __init__(self, resp):
                    self.resp = resp
                    self.status = resp.status_code

                def read(self):
                    return self.resp.content

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    pass

            return MockResponse(httpx_resp)

        original_urlopen = urllib.request.urlopen
        urllib.request.urlopen = mock_urlopen

        with respx.mock:
            # We setup the endpoints
            token = os.environ.get("TELEGRAM_BOT_TOKEN", "dummy")

            def set_name_handler(request):
                # parse urlencoded body

                body = request.content.decode()
                parsed = urllib.parse.parse_qs(body)
                if "name" in parsed:
                    mock_profile["name"] = parsed["name"][0]
                return httpx.Response(200, json={"ok": True, "result": True})

            respx.post(f"https://api.telegram.org/bot{token}/setMyName").mock(
                side_effect=set_name_handler
            )
            respx.post(f"https://api.telegram.org/bot{token}/setMyDescription").mock(
                return_value=httpx.Response(200, json={"ok": True, "result": True})
            )
            respx.post(f"https://api.telegram.org/bot{token}/setMyShortDescription").mock(
                return_value=httpx.Response(200, json={"ok": True, "result": True})
            )

            def get_name_handler(request):
                return httpx.Response(
                    200, json={"ok": True, "result": {"name": mock_profile.get("name", "")}}
                )

            respx.post(f"https://api.telegram.org/bot{token}/getMyName").mock(
                side_effect=get_name_handler
            )
            respx.post(f"https://api.telegram.org/bot{token}/getMyDescription").mock(
                return_value=httpx.Response(200, json={"ok": True, "result": {"description": ""}})
            )
            respx.post(f"https://api.telegram.org/bot{token}/getMyShortDescription").mock(
                return_value=httpx.Response(
                    200, json={"ok": True, "result": {"short_description": ""}}
                )
            )

            runner = CliRunner()
            # Inherit environment since the subprocess relies on environment variables
            env = os.environ.copy()
            if "TELEGRAM_BOT_TOKEN" not in env:
                env["TELEGRAM_BOT_TOKEN"] = "dummy"

            res = runner.invoke(app, args, env=env)

            # Restore
            urllib.request.urlopen = original_urlopen

            # Mock subprocess.CompletedProcess
            return subprocess.CompletedProcess(args, res.exit_code, res.stdout, "")

    return subprocess.run(
        [sys.executable, "-m", "canivete", *args],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
        env=os.environ.copy(),
    )


@when("I run canivete with no arguments", target_fixture="result")
def _run_bare(cli_state) -> subprocess.CompletedProcess:
    return _invoke(
        [], in_process=cli_state.get("use_in_process"), mock_profile=cli_state.get("mock_profile")
    )


@when(parsers.parse('I run canivete with arguments "{argstr}"'), target_fixture="result")
def _run_with_args(argstr: str, cli_state) -> subprocess.CompletedProcess:
    return _invoke(
        shlex.split(argstr),
        in_process=cli_state.get("use_in_process"),
        mock_profile=cli_state.get("mock_profile"),
    )


@when("I mock the Telegram API")
def _mock_telegram_api(cli_state):
    cli_state["use_in_process"] = True
    cli_state["mock_profile"] = {}


@then(parsers.parse("the command exits with code {code:d}"))
def _exits_with(result: subprocess.CompletedProcess, code: int) -> None:
    assert result.returncode == code, (
        f"expected {code}, got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


@then("the command exits with a non-zero code")
def _exits_nonzero(result: subprocess.CompletedProcess) -> None:
    assert result.returncode != 0


@then(parsers.parse('the output contains "{needle}"'))
def _output_contains(result: subprocess.CompletedProcess, needle: str) -> None:
    haystack = result.stdout + result.stderr
    assert needle in haystack, f"missing {needle!r} in:\n{haystack}"


@pytest.fixture
def mock_urllib(monkeypatch):
    mock = MagicMock()
    mock.return_value.__enter__.return_value.read.return_value = (
        b'{"ok": true, "result": {"message_id": 123}}'
    )
    monkeypatch.setattr("urllib.request.urlopen", mock)
    return mock


@when(parsers.parse("I run `{command}`"))
def run_command_with_mock(command, monkeypatch, mock_urllib, cli_state):
    """Run via CliRunner (same process) so the urllib monkeypatch
    actually intercepts the requests. Subprocess wouldn't see the mock."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_token")
    monkeypatch.setenv("CRON_CHAT_ID", "123")
    args = shlex.split(command)
    # remove "canivete"
    args = args[1:]

    runner = CliRunner()
    res = runner.invoke(app, args, catch_exceptions=False)
    cli_state["result"] = SimpleNamespace(
        returncode=res.exit_code,
        stdout=res.output or "",
        stderr=res.output or "",  # no separate stderr in CliRunner default
    )
    cli_state["urllib_mock"] = mock_urllib


@then("it exits with 0")
def exits_0(cli_state):
    assert cli_state["result"].returncode == 0, cli_state["result"].stderr


@then("it exits with 1")
def exits_1(cli_state):
    assert cli_state["result"].returncode == 1, cli_state["result"].stderr


@then(parsers.parse('urllib was called with "{method}"'))
def urllib_called_with(cli_state, method):
    mock = cli_state["urllib_mock"]
    assert mock.call_count > 0
    url = mock.call_args[0][0].full_url
    assert url.endswith(f"/{method}")


@then(
    parsers.parse('the urlopen request data has scope type "{scope_type}" and chat_id "{chat_id}"')
)
def urlopen_request_data_has_scope(cli_state, scope_type, chat_id):
    mock = cli_state["urllib_mock"]
    req = mock.call_args[0][0]
    data = req.data.decode()
    parsed = parse_qs(data)
    assert "scope" in parsed
    scope = json.loads(parsed["scope"][0])
    assert scope["type"] == scope_type
    assert str(scope["chat_id"]) == chat_id


@then(parsers.parse('the urlopen request data has commands with "{cmd1}" and "{cmd2}"'))
def urlopen_request_data_has_commands(cli_state, cmd1, cmd2):
    mock = cli_state["urllib_mock"]
    req = mock.call_args[0][0]
    data = req.data.decode()
    parsed = parse_qs(data)
    assert "commands" in parsed
    commands = json.loads(parsed["commands"][0])
    cmds = [c["command"] for c in commands]
    assert cmd1 in cmds
    assert cmd2 in cmds


@then(parsers.parse('stderr contains "{needle}"'))
def stderr_contains(cli_state, needle):
    assert needle in cli_state["result"].stderr


@then(parsers.parse('stdout contains "{needle}"'))
def stdout_contains(cli_state, needle):
    assert needle in cli_state["result"].stdout


@given("I mock urllib")
def given_mock_urllib(mock_urllib):
    pass


@given("I mock urllib with predefined response for list commands")
def given_mock_urllib_list_cmds(monkeypatch, mock_urllib):
    mock_urllib.return_value.__enter__.return_value.read.return_value = (
        b'{"ok": true, "result": [{"command": "pick_1", "description": "option one"}]}'
    )
