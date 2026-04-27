"""Shared fixtures and step definitions for the BDD suite.

`pytest-bdd` discovers steps per-module, so anything used by more than
one feature lives here in conftest.py to be available everywhere."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from pytest_bdd import parsers, then, when

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
    return {"result": None, "last_job_id": None}


# ── shared steps ─────────────────────────────────────────────────────


def _invoke(args: list[str]) -> subprocess.CompletedProcess:
    """Run `python -m canivete <args>` with the inherited env (so the
    monkeypatched CRON_LOG flows through)."""
    return subprocess.run(
        [sys.executable, "-m", "canivete", *args],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
        env=os.environ.copy(),
    )


@when("I run canivete with no arguments", target_fixture="result")
def _run_bare() -> subprocess.CompletedProcess:
    return _invoke([])


@when(parsers.parse('I run canivete with arguments "{argstr}"'), target_fixture="result")
def _run_with_args(argstr: str) -> subprocess.CompletedProcess:
    return _invoke(shlex.split(argstr))


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
