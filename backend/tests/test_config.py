from pathlib import Path

import pytest
from pydantic import ValidationError

from kastellan.config import Check, CheckType, Config, load


def test_shorthands_and_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
devices:
  router:
    host: 192.168.178.1
    kind: router
    checks:
      - ping
      - http: https://192.168.178.1
  nas:
    host: nas.lan
    parent: router
    checks:
      - tcp: 22
        name: ssh
  printer:
    host: printer.lan
"""
    )
    config = load(path)
    router, nas, printer = config.devices["router"], config.devices["nas"], config.devices["printer"]
    assert router.name == "router"
    assert [(c.type, c.name) for c in router.checks] == [
        (CheckType.PING, "ping"),
        (CheckType.HTTP, "http:https://192.168.178.1"),
    ]
    assert nas.checks[0].target == 22 and nas.checks[0].name == "ssh"
    assert [c.type for c in printer.checks] == [CheckType.PING]


def test_missing_file_is_empty(tmp_path: Path) -> None:
    assert load(tmp_path / "nope.yaml").devices == {}


@pytest.mark.parametrize(
    "devices",
    [
        {"a": {"host": "x", "parent": "b"}},  # unknown parent
        {"A": {"host": "x"}},  # bad id
        {"a\n": {"host": "x"}},  # no trailing newline in an id
        {"a": {"host": "x", "parent": "a"}},
        {"a": {"host": "x", "parent": "b"}, "b": {"host": "y", "parent": "a"}},
        {
            "a": {"host": "x", "runs_on": {"host": "b", "vm": "a"}},
            "b": {"host": "y", "runs_on": {"host": "a", "vm": "b"}},
        },
        {"a": {"host": "x", "checks": [{"tcp": "ssh"}]}},  # tcp without a port
        {"a": {"host": "x", "checks": [{"http": "example.com"}]}},  # http without a scheme
        {"a": {"host": "x", "checks": ["ping", "ping"]}},  # duplicate names
        {"a": {"host": "x", "checks": [{"ping": None, "tcp": 22}]}},  # two kinds
        {"a": {"host": "x", "checks": [{"tcp": 70000}]}},  # no such port
        {"a": {"host": "x", "checks": [{"tcp": 22, "interval": 0}]}},  # a busy loop
        {"a": {"host": "x", "checks": [{"tcp": 22, "timeout": -1}]}},
    ],
)
def test_invalid(devices: dict) -> None:
    with pytest.raises(ValidationError):
        Config.model_validate({"devices": devices})


def test_global_interval_must_be_sane() -> None:
    with pytest.raises(ValidationError):
        Config.model_validate({"check_interval": 0})


def test_dns_checks() -> None:
    from kastellan.config import Check

    c = Check.model_validate({"dns": "www.example.org", "expect": "192.0.2.1", "server": "9.9.9.9"})
    assert c.type is CheckType.DNS and c.name == "dns:www.example.org" and c.expect == ["192.0.2.1"]
    assert Check.model_validate({"dns": "example.org", "expect": ["192.0.2.1", "2001:db8::1"]}).expect
    for bad in (
        {"dns": "not a name"},
        {"dns": "example.org", "expect": "nope"},
        {"dns": "example.org", "server": "resolver.lan"},
        {"tcp": 53, "server": "9.9.9.9"},
        {"http": "http://x", "expect": "192.0.2.1"},
    ):
        with pytest.raises(ValidationError):
            Check.model_validate(bad)


def test_sites() -> None:
    one = Config.model_validate({"devices": {"a": {"host": "a"}, "b": {"host": "b", "site": "home"}}})
    assert not one.multi_site and one.label("a") == "a"
    away = {"host": "b", "site": "friends-place"}
    two = Config.model_validate({"devices": {"a": {"host": "a"}, "b": away}})
    assert two.multi_site and two.label("b") == "b (Friends place)" and two.label("a") == "a"
    with pytest.raises(ValidationError):
        Config.model_validate({"devices": {"a": {"host": "a", "site": "Not An Id"}}})


def test_a_dns_expect_is_addresses() -> None:
    with pytest.raises(ValueError):
        Check.model_validate({"dns": "example.org", "expect": 200})


def test_runs_on_names_a_vm_on_another_device() -> None:
    ok = Config.model_validate(
        {"devices": {"sv1": {"host": "s"}, "ha": {"host": "h", "runs_on": {"host": "sv1", "vm": "haos"}}}}
    )
    assert ok.devices["ha"].runs_on is not None and ok.devices["ha"].runs_on.vm == "haos"
    for bad in (
        {"host": "nope", "vm": "haos"},  # not a device
        {"host": "ha", "vm": "haos"},  # itself
        {"host": "sv1", "vm": "12"},  # a number: virsh would read a domain id
        {"host": "sv1", "vm": "a b"},
    ):
        with pytest.raises(ValueError):
            Config.model_validate({"devices": {"sv1": {"host": "s"}, "ha": {"host": "h", "runs_on": bad}}})
