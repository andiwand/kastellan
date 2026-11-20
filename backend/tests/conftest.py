import pytest

from kastellan import checks


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failed checks are retried after a pause; tests don't wait for it."""
    monkeypatch.setattr(checks, "RETRY_AFTER", 0)


@pytest.fixture(autouse=True)
def _test_host_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """The test client calls itself testserver (and hub.lan in some tests); let the guard's host
    check (against DNS rebinding) accept those names."""
    from kastellan import auth_api

    monkeypatch.setattr(auth_api, "LOCAL_NAMES", {"localhost", "testserver", "hub.lan"})
