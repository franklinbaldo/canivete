import io
import json
import shlex
import urllib.error
import urllib.request
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when
from typer.testing import CliRunner

from canivete.cli import app

scenarios("../features/jules.feature")

runner = CliRunner()


@pytest.fixture
def run_context() -> dict[str, Any]:
    return {"env": {}, "res": None, "req_body": None}


@given("I clear the environment")
def clear_env(run_context: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JULES_API_KEY", raising=False)


@given(parsers.parse('I set the environment variable "{key}" to "{val}"'))
def set_env(
    key: str, val: str, run_context: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(key, val)


@given("I mock the Jules API to return a new session")
def mock_new_session(run_context: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def mock_urlopen(req, *args, **kwargs):
        if hasattr(req, "data") and req.data:
            run_context["req_body"] = json.loads(req.data.decode("utf-8"))

        resp_data = json.dumps(
            {"name": "sessions/fake-session-123", "title": "Fix bug", "state": "PENDING_PLAN"}
        ).encode("utf-8")

        class MockResponse:
            def __init__(self, data):
                self.data = data

            def read(self):
                return self.data

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        return MockResponse(resp_data)

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)


@given("I mock the Jules API to return 400 Bad Request")
def mock_400(run_context: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def mock_urlopen(req, *args, **kwargs):
        err_data = json.dumps({"error": {"message": "INVALID_ARGUMENT"}}).encode("utf-8")
        fp = io.BytesIO(err_data)
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", hdrs={}, fp=fp)

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)


@when(parsers.parse('I run "{cmd}"'))
def run_cmd(cmd: str, run_context: dict[str, Any]) -> None:
    # Remove "canivete " prefix to match typer testing
    args = shlex.split(cmd)
    if args[0] == "canivete":
        args = args[1:]
    res = runner.invoke(app, args, catch_exceptions=False)
    run_context["res"] = res


@then("the command should succeed")
def cmd_succeed(run_context: dict[str, Any]) -> None:
    res = run_context["res"]
    assert res.exit_code == 0, f"Expected success, got {res.exit_code}. Output: {res.output}"


@then("the command should fail")
def cmd_fail(run_context: dict[str, Any]) -> None:
    res = run_context["res"]
    assert res.exit_code != 0, f"Expected failure, got 0. Output: {res.output}"


@then(parsers.parse('the output should contain "{text}"'))
def output_contain(text: str, run_context: dict[str, Any]) -> None:
    res = run_context["res"]
    assert text in res.output, f"Expected '{text}' in output:\n{res.output}"


@then(parsers.parse('the API request body should contain "{text}"'))
def req_contain(text: str, run_context: dict[str, Any]) -> None:
    body = run_context["req_body"]
    assert body is not None, "Request body was not captured."
    # Dump body as json string to check text presence
    assert text in json.dumps(body), f"Expected '{text}' in body:\n{json.dumps(body)}"


@then(parsers.parse('the API request body should specify startingBranch "{branch}"'))
def req_branch(branch: str, run_context: dict[str, Any]) -> None:
    body = run_context["req_body"]
    assert body is not None, "Request body was not captured."

    ctx = body.get("sourceContext", {}).get("githubRepoContext", {})
    actual_branch = ctx.get("startingBranch")
    assert actual_branch == branch, f"Expected branch '{branch}', got '{actual_branch}' in {ctx}"
