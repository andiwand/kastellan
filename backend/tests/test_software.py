import pytest

from kastellan.software import Software, from_greeting, from_http


@pytest.mark.parametrize(
    ("greeting", "expected"),
    [
        ("SSH-2.0-OpenSSH_10.2p1 Ubuntu-2ubuntu3.8\r\n", "OpenSSH 10.2p1 (Ubuntu)"),
        ("SSH-2.0-OpenSSH_9.8", "OpenSSH 9.8"),
        ("SSH-2.0-dropbear", "Dropbear"),
        ("SSH-2.0-dropbear_2022.83", "Dropbear 2022.83"),
        ("220 ProFTPD 1.3.8 Server (Debian) [::ffff:1.2.3.4]", "ProFTPD 1.3.8 (Debian)"),
        ("220 (vsFTPd 3.0.5)", "vsftpd 3.0.5"),
        ("220 FTP server ready.", "FTP"),
        ("220 mail.example.org ESMTP Postfix (Ubuntu)", "Postfix (Ubuntu)"),
    ],
)
def test_greetings(greeting: str, expected: str) -> None:
    assert str(from_greeting(greeting)) == expected


def test_no_greeting() -> None:
    assert from_greeting("") is None
    assert from_greeting("\x00\x01binary") is None


@pytest.mark.parametrize(
    ("headers", "body", "expected"),
    [
        ({"Server": "nginx/1.24.0 (Ubuntu)"}, "", Software("nginx", "1.24.0", "Ubuntu")),
        ({"Server": "Tasmota/14.4.0 (ESP8266EX)"}, "", Software("Tasmota", "14.4.0", "ESP8266EX")),
        ({"Server": "ReeCam IP Camera"}, "", Software("ReeCam IP Camera")),
        ({"Server": "gSOAP/2.8"}, "", Software("gSOAP", "2.8")),
        (
            {"Server": "X-Frame-Options: SAMEORIGIN"},
            "<title>Home Assistant</title>",
            Software("Home Assistant"),
        ),
        ({}, "<html><title>\n  WUD </title>", Software("WUD")),
        ({}, "<title>503 Service Temporarily Unavailable</title>", None),
        ({"X-Powered-By": "PHP/8.3.6"}, "", Software("PHP", "8.3.6")),
        ({"Server": "Apache/2.4.58 (Ubuntu) OpenSSL/3.0.13"}, "", Software("Apache", "2.4.58", "Ubuntu")),
        ({"Server": "mcdhttpd/1.2"}, "", Software("mcdhttpd", "1.2")),
        ({}, "", None),
        ({"X-Powered-By": "Express"}, "<title>WUD</title>", Software("WUD")),
        ({}, '<link href="/luci-static/bootstrap/cascade.css">', Software("OpenWrt LuCI")),
    ],
)
def test_http(headers: dict, body: str, expected: Software | None) -> None:
    assert from_http(headers, body) == expected
