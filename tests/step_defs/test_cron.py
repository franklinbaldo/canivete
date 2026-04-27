"""Bind the cron feature scenarios + cron-specific steps."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from pytest_bdd import given, parsers, scenarios, then, when

scenarios("../features/cron.feature")


def _canivete(args: list[str], env_log: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["CRON_LOG"] = str(env_log)
    return subprocess.run(
        [sys.executable, "-m", "canivete", *args],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
        env=env,
    )


@given("the cron log is empty")
def _empty_log(cron_log: Path) -> None:
    if cron_log.exists():
        cron_log.unlink()


@given(parsers.parse('a job is scheduled in {duration} with prompt "{prompt}"'))
def _seed_job(cron_log: Path, cli_state: dict, duration: str, prompt: str) -> None:
    r = _canivete(["cron", "add", "--in", duration, prompt], cron_log)
    assert r.returncode == 0, r.stderr
    for tok in r.stdout.split():
        if tok.startswith("j_"):
            cli_state["last_job_id"] = tok
            break


@when(
    parsers.parse('I schedule a job in {duration} with prompt "{prompt}"'),
    target_fixture="result",
)
def _schedule(cron_log: Path, duration: str, prompt: str) -> subprocess.CompletedProcess:
    return _canivete(["cron", "add", "--in", duration, prompt], cron_log)


@when("I remove the most recently added job", target_fixture="result")
def _remove_last(cron_log: Path, cli_state: dict) -> subprocess.CompletedProcess:
    jid = cli_state["last_job_id"]
    assert jid, "no last_job_id captured by the seed step"
    return _canivete(["cron", "rm", jid], cron_log)


@then(parsers.parse('listing no longer shows "{needle}"'))
def _list_doesnt_show(cron_log: Path, needle: str) -> None:
    r = _canivete(["cron", "list"], cron_log)
    assert r.returncode == 0, r.stderr
    assert needle not in (r.stdout + r.stderr), f"unexpectedly found {needle!r}"
