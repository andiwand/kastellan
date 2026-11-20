"""`kastellan hash-password`: short passwords are allowed after a warning, or with --short."""

import io
import sys

import pytest

from kastellan import __main__ as cli
from kastellan.auth import verify_password


def run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    password: str,
    answer: str = "",
    short=False,
):
    monkeypatch.setattr("getpass.getpass", lambda prompt="": password)
    monkeypatch.setattr(sys, "stdin", io.StringIO(answer + "\n"))
    cli.hash_password_command(allow_short=short)
    return capsys.readouterr()


def test_a_long_password(monkeypatch, capsys) -> None:
    out = run(monkeypatch, capsys, "correct horse battery")
    assert verify_password("correct horse battery", out.out.strip()) and out.err == ""


def test_a_short_one_after_yes_or_with_short(monkeypatch, capsys) -> None:
    out = run(monkeypatch, capsys, "1234", answer="y")
    assert verify_password("1234", out.out.strip()) and "easy to guess" in out.err
    out = run(monkeypatch, capsys, "1234", short=True)
    assert verify_password("1234", out.out.strip()) and out.err == ""
    with pytest.raises(SystemExit, match="Not used"):
        run(monkeypatch, capsys, "1234", answer="")
    with pytest.raises(SystemExit, match="No password"):
        run(monkeypatch, capsys, "")
