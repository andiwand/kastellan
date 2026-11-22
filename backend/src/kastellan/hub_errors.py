"""Saying what is wrong with config.yaml without quoting it: it holds password hashes and tokens,
and messages reach the app and the log."""

from __future__ import annotations

from pydantic import ValidationError
from ruamel.yaml.error import YAMLError


class ConfigProblem(Exception):
    """A config.yaml that can't be used, and why (safe to show)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def yaml_problem(e: YAMLError) -> str:
    """A YAML error by its line. ruamel's own text may quote values ("found duplicate key
    'password' with value 'scrypt$...'"), so only known kinds of problem are named."""
    mark = getattr(e, "problem_mark", None) or getattr(e, "context_mark", None)
    problem = str(getattr(e, "problem", None) or "")
    if "duplicate key" in problem:
        what = "a key appears twice"
    elif problem and "'" not in problem and '"' not in problem:
        what = problem[:120]  # e.g. "mapping values are not allowed here"
    else:
        what = "the YAML can't be read"
    return f"line {mark.line + 1}, column {mark.column + 1}: {what}" if mark else what


def describe(e: ValidationError) -> str:
    """What is wrong, without the values: config.yaml holds password hashes, and a pasted plain
    password must not end up in a log or an API answer."""
    problems = []
    for err in e.errors(include_input=False, include_url=False):
        where = ".".join(str(p) for p in err["loc"])
        problems.append(f"{where}: {err['msg']}" if where else err["msg"])
    return "; ".join(problems)[:600]


def first_line(text: str) -> str:
    return text.strip().splitlines()[0][:300] if text.strip() else "unreadable"
