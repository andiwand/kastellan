import pytest

from kastellan import checks


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failed checks are retried after a pause; tests don't wait for it."""
    monkeypatch.setattr(checks, "RETRY_AFTER", 0)
