"""Smoke tests — verify the CLI loads, version flag works, and each
subcommand renders --help without crashing. No real network calls."""

from __future__ import annotations

import subprocess
import sys


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "canivete", *args],
        capture_output=True, text=True, timeout=20)


def test_version() -> None:
    r = _run("--version")
    assert r.returncode == 0
    assert "canivete" in r.stdout


def test_overview_renders() -> None:
    r = _run()
    assert r.returncode == 0
    out = r.stdout
    assert "canivete" in out
    assert "tg" in out
    assert "cron" in out


def test_tg_help() -> None:
    r = _run("tg", "--help")
    assert r.returncode == 0
    for sub in ("text", "photo", "document", "voice", "video", "audio"):
        assert sub in r.stdout


def test_cron_help() -> None:
    r = _run("cron", "--help")
    assert r.returncode == 0
    for sub in ("add", "list", "rm"):
        assert sub in r.stdout


def test_cron_list_empty(tmp_path, monkeypatch) -> None:
    """`cron list` should report 'no pending jobs' on a fresh log."""
    monkeypatch.setenv("CRON_LOG", str(tmp_path / "cron.jsonl"))
    r = _run("cron", "list")
    assert r.returncode == 0
    assert "no pending jobs" in r.stdout.lower()


def test_cron_add_in_then_list(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRON_LOG", str(tmp_path / "cron.jsonl"))
    r = _run("cron", "add", "--in", "1h", "remind me later")
    assert r.returncode == 0
    assert "✓" in r.stdout

    r = _run("cron", "list")
    assert r.returncode == 0
    assert "remind me later" in r.stdout


def test_cron_add_requires_at_or_in(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRON_LOG", str(tmp_path / "cron.jsonl"))
    r = _run("cron", "add", "no time spec")
    assert r.returncode == 2
