"""`kastellan hash-password`: any password but an empty one, typed twice the same."""

import pytest

from kastellan import __main__ as cli
from kastellan.auth import verify_password


def run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, password: str, again: str | None = None
):
    answers = iter([password, password if again is None else again])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    cli.hash_password_command()
    return capsys.readouterr()


def test_any_length(monkeypatch, capsys) -> None:
    for password in ("correct horse battery", "1234"):
        out = run(monkeypatch, capsys, password)
        assert verify_password(password, out.out.strip()) and out.err == ""


def test_empty_or_not_the_same(monkeypatch, capsys) -> None:
    with pytest.raises(SystemExit, match="No password"):
        run(monkeypatch, capsys, "")
    with pytest.raises(SystemExit, match="don't match"):
        run(monkeypatch, capsys, "1234", again="12345")
