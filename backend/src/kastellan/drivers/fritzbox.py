"""FRITZ!Box, through TR-064's public device description (port 49000, no sign-in needed).

    <modelName>FRITZ!Box 7530</modelName>
    <friendlyName>gw1</friendlyName>
    <Display>164.08.25</Display>      hardware 164, FRITZ!OS 08.25

What needs an account (clients, mesh, Wi-Fi, config export) comes later, through a FRITZ!Box
user with only the rights it needs.
"""

from __future__ import annotations

import asyncio
import logging
import re
import xml.etree.ElementTree as ET

import httpx

from .base import DeviceInfo, Driver

log = logging.getLogger(__name__)

PORT = 49000

_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:-(\d+))?")


def firmware_name(version: str | None) -> str | None:
    """AVM's version string ("164.08.25", a lab build "164.08.30-117523": hardware, major, minor,
    build) as people know it ("FRITZ!OS 8.25"). The one spelling for every source (tr64desc's
    Display, TR-064's NewSoftwareVersion), so the firmware log sees no change between them."""
    if not version:
        return None
    if m := _VERSION.fullmatch(version.strip()):
        _, major, minor, build = m.groups()
        return f"FRITZ!OS {int(major)}.{minor}" + (f" (build {build})" if build else "")
    return f"FRITZ!OS {version.strip()}"


def parse_description(xml: str) -> DeviceInfo:
    """The root device of tr64desc.xml; namespaces vary between FRITZ!OS versions, so tags are
    matched by local name."""
    root = ET.fromstring(xml)

    def first(tag: str) -> str | None:
        for el in root.iter():
            if el.tag.rsplit("}", 1)[-1] == tag and el.text and el.text.strip():
                return el.text.strip()
        return None

    display = first("Display")
    firmware, details = None, {}
    if display and (m := _VERSION.fullmatch(display)):
        firmware = firmware_name(display)
        details = {"hardware": m.group(1)}
    return DeviceInfo(
        model=first("modelName"), name=first("friendlyName"), firmware=firmware, details=details
    )


class FritzBox(Driver):
    name = "fritzbox"

    async def info(self) -> DeviceInfo | None:
        host = f"[{self.host}]" if ":" in self.host else self.host  # IPv6
        url = f"http://{host}:{PORT}/tr64desc.xml"
        try:
            async with (
                asyncio.timeout(15),
                httpx.AsyncClient(timeout=8) as client,
                client.stream("GET", url) as response,
            ):
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > 512 * 1024:  # the real file is ~15 kB
                        raise ValueError("description too large")
            return parse_description(body.decode("utf-8", errors="replace"))
        except (httpx.HTTPError, httpx.InvalidURL, ET.ParseError, ValueError, TimeoutError) as e:
            log.info("%s: no TR-064 description (%s)", self.host, e)
            return None
