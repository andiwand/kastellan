"""Saying what is wrong with config.yaml without quoting it: it holds password hashes and tokens,
and messages reach the app and the log."""

from __future__ import annotations

from ruamel.yaml.error import YAMLError


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
