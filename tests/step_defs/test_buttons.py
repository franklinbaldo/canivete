"""Bind the buttons feature scenarios. Steps come from ``conftest.py``."""

import json
import shlex
import urllib.parse
from unittest.mock import MagicMock, patch

from pytest_bdd import parsers, scenarios, then, when
from typer.testing import CliRunner

from canivete.cli import app

scenarios("../features/buttons.feature")

runner = CliRunner()


@when(parsers.parse('I run tg buttons with "{argstr}"'), target_fixture="result")
def run_tg_buttons_mocked(argstr, monkeypatch):
    """Run `canivete tg buttons` with mocked urllib.request.urlopen."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_token")
    monkeypatch.setenv("CRON_CHAT_ID", "123")

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {"ok": True, "result": {"message_id": 42}}
        ).encode()
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        argstr = argstr.replace('\\"', '"')
        args = shlex.split(argstr)

        result = runner.invoke(app, args)

        # Monkeypatch the result to include mock_urlopen
        result.mock_urlopen = mock_urlopen
        return result


@then("the command exits with code 0")
def the_command_exits_with_code_0(result):
    if hasattr(result, "exit_code"):
        assert result.exit_code == 0, (
            f"Command failed: {result.stdout} exception: {getattr(result, 'exception', '')}"
        )
    else:
        assert result.returncode == 0, f"Command failed: {result.stdout}"


@then(
    parsers.parse(
        'the telegram api is called with text "{expected_text}" and inline_keyboard "{label1}:{data1}", "{label2}:{data2}"'
    )
)
def api_called(result, expected_text, label1, data1, label2, data2):
    mock_urlopen = result.mock_urlopen
    assert mock_urlopen.called
    req = mock_urlopen.call_args[0][0]

    data = req.data.decode()
    parsed_data = urllib.parse.parse_qs(data)

    assert parsed_data["text"][0] == expected_text

    reply_markup_str = parsed_data["reply_markup"][0]
    reply_markup = json.loads(reply_markup_str)

    kb = reply_markup["inline_keyboard"]
    assert len(kb) == 1
    assert kb[0][0]["text"] == label1
    assert kb[0][0]["callback_data"] == data1
    assert kb[0][1]["text"] == label2
    assert kb[0][1]["callback_data"] == data2


@then(parsers.parse('the output contains "{needle}"'))
def the_output_contains(result, needle):
    haystack = result.stdout if hasattr(result, "exit_code") else result.stdout + result.stderr
    assert needle.replace("\\n", " ") in haystack.replace("\\n", " ") or needle in haystack, (
        f"missing {needle!r} in:\\n{haystack}"
    )
