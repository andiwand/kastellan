from types import SimpleNamespace

from kastellan import web_setup


def test_the_routers_dns_for_the_zone() -> None:
    values = {
        "cfg01": {
            ".type": "dnsmasq",
            "rebind_protection": "1",
            "rebind_domain": ["moos.stefl.at"],
            "server": ["/moos.stefl.at/192.168.15.10", "/lan/127.0.0.1"],
        }
    }
    got = web_setup.router_dns(values, "moos.stefl.at", "192.168.15.10")
    assert got["rebind_ok"] and got["forward_ok"] and got["forwards"] == ["/moos.stefl.at/192.168.15.10"]
    bare = web_setup.router_dns({"cfg01": {".type": "dnsmasq"}}, "moos.stefl.at", None)
    assert bare["rebind_ok"] is False and bare["forward_ok"] is False
    off = web_setup.router_dns({"x": {".type": "dnsmasq", "rebind_protection": "0"}}, "z.at", None)
    assert off["rebind_ok"]
    assert web_setup.router_dns({}, "z.at", None)["dnsmasq"] is False
    lines = web_setup.commands("moos.stefl.at", "192.168.15.10")
    assert "server='/moos.stefl.at/192.168.15.10'" in lines and "rebind_domain='moos.stefl.at'" in lines
    assert "address=" not in lines  # never split DNS


def test_warnings_for_the_proxy_certificate_and_names() -> None:
    hub = SimpleNamespace(
        config=SimpleNamespace(web=SimpleNamespace(domain="kastellan.moos.stefl.at")),
        web_state={
            "domain": "kastellan.moos.stefl.at",
            "certificates": [{"name": "kastellan.moos.stefl.at", "certificate": {"days_left": 5}}],
            "names": [{"name": "x.kastellan.moos.stefl.at", "ok": False}],
        },
    )
    keys = [p.key for p in web_setup.problems(hub)]  # type: ignore[arg-type]
    assert keys == ["web:cert:kastellan.moos.stefl.at", "web:dns"]
    hub.web_state["certificates"][0]["certificate"] = {"valid": False, "days_left": 60, "error": "wrong name"}
    assert web_setup.problems(hub)[0].detail == "wrong name"
    hub.config.web.domain = "another.example.org"
    assert web_setup.problems(hub) == []


async def test_dns_uses_proxy_labels_and_missing_public_answers_are_unknown(monkeypatch) -> None:
    from kastellan.config import Config

    async def resolve(name, nameserver=None):
        assert "_" not in name
        return [] if nameserver else ["192.168.1.2"]

    async def certificate(*args):
        return None

    monkeypatch.setattr(web_setup, "resolve", resolve)
    monkeypatch.setattr(web_setup.tls, "certificate", certificate)
    hub = SimpleNamespace(
        config=Config.model_validate(
            {"web": {"domain": "k.example.org"}, "devices": {"nas_box": {"host": "nas"}}}
        ),
        own_address=None,
    )
    state = await web_setup.look(hub)
    assert state["names"][-1]["name"] == "nas-box.k.example.org"
    assert all(n["same"] is None for n in state["names"])


def test_web_zone_defaults_to_the_domains_parent() -> None:
    import pytest

    from kastellan.config import WebSettings

    assert WebSettings(domain="kastellan.moos.stefl.at").the_zone == "moos.stefl.at"
    assert WebSettings(domain="k.example.org", zone="example.org", dns="192.168.1.2").dns == "192.168.1.2"
    with pytest.raises(ValueError):
        WebSettings(domain="k.example.org", dns="ns.example.org")
