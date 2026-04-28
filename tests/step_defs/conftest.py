"""Shared fixtures and step definitions for the BDD suite.

`pytest-bdd` discovers steps per-module, so anything used by more than
one feature lives here in conftest.py to be available everywhere."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import httpx
import pytest
import respx
from pytest_bdd import parsers, then, when
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
