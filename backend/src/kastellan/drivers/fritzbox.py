"""FRITZ!Box, through TR-064's public device description (port 49000, no sign-in needed).

    <modelName>FRITZ!Box 7530</modelName>
    <friendlyName>gw1</friendlyName>
    <Display>164.08.25</Display>      hardware 164, FRITZ!OS 08.25

What needs an account (clients, mesh, Wi-Fi, config export) comes later, through a FRITZ!Box
user with only the rights it needs.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET

import httpx

from .base import DeviceInfo, Driver

log = logging.getLogger(__name__)

PORT = 49000


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
    if display and (m := re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-(\d+))?", display)):
        hardware, major, minor, build = m.groups()
        firmware = f"FRITZ!OS {int(major)}.{minor}" + (f" (build {build})" if build else "")
        details = {"hardware": hardware}
    return DeviceInfo(
        model=first("modelName"), name=first("friendlyName"), firmware=firmware, details=details
    )


class FritzBox(Driver):
    name = "fritzbox"

    async def info(self) -> DeviceInfo | None:
        url = f"http://{self.host}:{PORT}/tr64desc.xml"
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                response = await client.get(url)
            response.raise_for_status()
            return parse_description(response.text[:200_000])
        except (httpx.HTTPError, ET.ParseError, ValueError) as e:
            log.info("%s: no TR-064 description (%s)", self.host, e)
            return None
