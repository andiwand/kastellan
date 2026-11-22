from kastellan.drivers.fritzbox import firmware_name, parse_description

# Trimmed from a FRITZ!Box 7530 with FRITZ!OS 8.25 (the root device comes first).
TR64 = """<?xml version="1.0"?>
<root xmlns="urn:dslforum-org:device-1-0">
 <specVersion><major>1</major><minor>0</minor></specVersion>
 <systemVersion><HW>164</HW><Major>8</Major><Minor>25</Minor><Patch>0</Patch><Buildnumber>0</Buildnumber>
  <Display>164.08.25</Display></systemVersion>
 <device>
  <deviceType>urn:dslforum-org:device:InternetGatewayDevice:1</deviceType>
  <friendlyName>gw1</friendlyName>
  <manufacturer>FRITZ! GmbH</manufacturer>
  <modelName>FRITZ!Box 7530</modelName>
  <deviceList><device><friendlyName>LANDevice - gw1</friendlyName>
   <modelName>LANDevice - FRITZ!Box 7530</modelName></device></deviceList>
 </device>
</root>"""


def test_fritzbox_description_and_one_firmware_spelling() -> None:
    info = parse_description(TR64)
    assert (info.model, info.name, info.firmware) == ("FRITZ!Box 7530", "gw1", "FRITZ!OS 8.25")
    assert info.details == {"hardware": "164"}
    labor = parse_description(TR64.replace("164.08.25", "164.08.30-117523"))
    assert labor.firmware == "FRITZ!OS 8.30 (build 117523)"
    # tr64desc's Display and TR-064's NewSoftwareVersion say the same thing: spelled alike, the
    # firmware log doesn't flip between "FRITZ!OS 8.25" and "FRITZ!OS 164.08.25" every poll.
    assert firmware_name("164.08.25") == info.firmware
    assert firmware_name("164.08.30-117523") == labor.firmware
    assert firmware_name("8.25") == "FRITZ!OS 8.25"  # an unknown shape is kept as it is
    assert firmware_name(None) is None and firmware_name("") is None


def test_wud_container() -> None:
    from kastellan.docker import from_wud

    norn = from_wud(
        {
            "name": "norn",
            "status": "running",
            "image": {
                "id": "sha256:cce1058c2f6fb741e15dfe27",
                "registry": {"name": "ghcr.public"},
                "name": "andiwand/norn",
                "tag": {"value": "0.10.0", "semver": True},
            },
            "updateAvailable": True,
            "updateKind": {"kind": "tag", "semverDiff": "minor"},
            "result": {"tag": "0.11.0"},
        }
    )
    assert (norn.update, norn.update_to, norn.image_id) == (True, "0.11.0", "cce1058c2f6f")
    assert norn.running_version == "andiwand/norn:0.10.0 (cce1058c2f6f)"

    unwatched = from_wud(
        {
            "name": "antennas",
            "image": {
                "name": "thejf/antennas",
                "tag": {"value": "latest", "semver": False},
                "digest": {"watch": False},
            },
            "updateAvailable": False,
            "result": {"tag": "latest"},
        }
    )
    assert unwatched.unchecked and "digest" in unwatched.unchecked  # not "up to date"

    latest = from_wud(
        {
            "name": "plex",
            "image": {"name": "plexinc/pms-docker", "tag": {"value": "latest"}, "digest": {"watch": True}},
            "updateAvailable": True,
            "updateKind": {"kind": "digest"},
            "result": {"tag": "latest"},
        }
    )
    assert latest.update_to == "newer build"

    lscr = from_wud(
        {
            "name": "sonarr",
            "image": {"name": "linuxserver/sonarr", "tag": {"value": "latest"}},
            "error": {"message": "Unsupported Registry unknown"},
        }
    )
    assert lscr.unchecked == "Unsupported Registry unknown" and not lscr.update


def test_wud_honesty_and_bad_records() -> None:
    from kastellan.docker import from_wud

    fresh = from_wud({"name": "new", "image": {"name": "x/y", "tag": {"value": "1.2.3", "semver": True}}})
    assert fresh.unchecked == "not checked by WUD yet"
    moving = from_wud(
        {
            "name": "pg",
            "image": {"name": "library/postgres", "tag": {"value": "15", "semver": True}},
            "result": {"tag": "15"},
        }
    )
    assert moving.unchecked and "new builds of :15" in moving.unchecked
    exact = from_wud(
        {
            "name": "n",
            "image": {"name": "a/n", "tag": {"value": "0.10.0", "semver": True}},
            "result": {"tag": "0.10.0"},
        }
    )
    assert exact.unchecked is None  # really checked: up to date
    for broken in (
        {"image": "x"},
        {"image": {"tag": "latest"}},
        {"result": "x", "updateAvailable": True},
        {"updateKind": "tag"},
        {"image": {"digest": "x"}},
        {},
    ):
        from_wud(broken)  # never raises
    assert from_wud({"image": {"tag": {"value": "1"}}}).running_version is None  # no id: no "update"
