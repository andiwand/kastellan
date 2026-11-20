import pytest

from kastellan.cvss import base_score


@pytest.mark.parametrize(
    ("vector", "score"),
    [
        ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 9.8),  # e.g. CVE-2021-44228's family
        ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H", 10.0),
        ("CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:H/A:H", 8.1),  # CVE-2024-6387 (regreSSHion)
        ("CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:N/I:N/A:H", 5.9),  # CVE-2025-26466
        ("CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N", 6.1),  # typical reflected XSS
        ("CVSS:3.0/AV:L/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H", 7.8),
        ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:N", 0.0),
    ],
)
def test_known_scores(vector: str, score: float) -> None:
    assert base_score(vector) == score


def test_not_a_vector() -> None:
    assert base_score("CVSS:4.0/AV:N/AC:L/AT:N") is None
    assert base_score("CVSS:3.1/AV:X/AC:L") is None
    assert base_score("") is None
