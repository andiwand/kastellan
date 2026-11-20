"""What software a service says it is, from what it volunteers without credentials: the greeting
of server-first protocols (SSH, FTP, SMTP), and an HTTP response's headers and title.

Everything here is a best guess from public answers; a service may hide or fake it.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Software:
    name: str
    version: str | None = None
    extra: str | None = None  # e.g. the distribution: "Ubuntu"

    def __str__(self) -> str:
        parts = [self.name, self.version or ""]
        text = " ".join(p for p in parts if p)
        return f"{text} ({self.extra})" if self.extra else text


# SSH-2.0-OpenSSH_10.2p1 Ubuntu-2ubuntu3.8   SSH-2.0-dropbear_2022.83   SSH-2.0-dropbear
_SSH = re.compile(r"^SSH-[\d.]+-([A-Za-z][\w.-]*?)(?:[_-](\d[\w.]*))?(?:\s+(.+))?$")
# 220 ProFTPD 1.3.8 Server   220 (vsFTPd 3.0.5)   220 mail.example ESMTP Postfix (Ubuntu)
_KNOWN_GREETINGS = [
    (re.compile(r"\bProFTPD\s+([\d.]+\w*)", re.I), "ProFTPD"),
    (re.compile(r"\bvsFTPd\s+([\d.]+)", re.I), "vsftpd"),
    (re.compile(r"\bPure-FTPd\b", re.I), "Pure-FTPd"),
    (re.compile(r"\bFileZilla Server\s*([\d.]+)?", re.I), "FileZilla Server"),
    (re.compile(r"\bPostfix\b", re.I), "Postfix"),
    (re.compile(r"\bExim\s+([\d.]+)", re.I), "Exim"),
    (re.compile(r"\bDovecot\b", re.I), "Dovecot"),
]
_DISTROS = ("Ubuntu", "Debian", "Raspbian", "FreeBSD", "Alpine", "Fedora", "Red Hat", "OpenWrt")


def from_greeting(text: str) -> Software | None:
    """The first line a server sends on connect."""
    line = text.strip().splitlines()[0].strip() if text.strip() else ""
    if not line:
        return None
    if m := _SSH.match(line):
        name, version, rest = m.groups()
        if name.lower().startswith("openssh"):
            name = "OpenSSH"
        elif name.lower() == "dropbear":
            name = "Dropbear"
        extra = next((d for d in _DISTROS if rest and d.lower() in rest.lower()), None)
        return Software(name, version, extra)
    for pattern, name in _KNOWN_GREETINGS:
        if m := pattern.search(line):
            version = m.group(1) if m.groups() else None
            extra = next((d for d in _DISTROS if d.lower() in line.lower()), None)
            return Software(name, version, extra)
    if line.startswith("220"):
        return Software("FTP" if "ftp" in line.lower() else "SMTP" if "smtp" in line.lower() else "Server")
    return None


_PARENS = re.compile(r"\(([^)]*)\)")


def _product(value: str) -> Software | None:
    """nginx/1.24.0 (Ubuntu), Tasmota/14.4.0 (ESP8266EX), Apache/2.4.58 (Ubuntu) OpenSSL/3.0,
    gSOAP/2.8, ReeCam IP Camera: the first product, its version and the first note in brackets."""
    paren = _PARENS.search(value)
    extra = paren.group(1).strip() or None if paren else None
    head = value[: paren.start()] if paren else value
    if "/" in head:
        name, _, rest = head.partition("/")
        version = rest.split()[0].lstrip("vV") if rest.split() else None
    else:
        name, version = head, None
    name = name.strip()
    if not name or not name[0].isalpha():
        return None
    return Software(name, version or None, extra)


_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


# Pages that don't name themselves in headers or a title, recognised by their HTML.
_MARKERS = [
    (re.compile(r"/luci-static/|\bLuCI\b"), "OpenWrt LuCI"),
]


def from_http(headers: dict[str, str], body: str = "") -> Software | None:
    """From an HTTP response: the Server header, else the page title, else what the page is
    recognised as, else X-Powered-By (that names a framework, like Express, not the product)."""
    lower = {k.lower(): v for k, v in headers.items()}

    def header(name: str) -> Software | None:
        value = lower.get(name, "").strip()
        # Some devices send nonsense here (Home Assistant has been seen sending a header line).
        if not value or ":" in value or len(value) > 100:
            return None
        return _product(value)

    if server := header("server"):
        return server
    page = body[:20000]
    if m := _TITLE.search(page):
        title = " ".join(html.unescape(m.group(1)).split())
        # Error pages are not a product name.
        if title and len(title) <= 60 and not re.match(r"^\d{3}\b", title):
            return Software(title)
    for pattern, name in _MARKERS:
        if pattern.search(page):
            return Software(name)
    return header("x-powered-by")
