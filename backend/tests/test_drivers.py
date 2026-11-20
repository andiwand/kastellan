from kastellan.drivers.fritzbox import parse_description

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


def test_fritzbox_description() -> None:
    info = parse_description(TR64)
    assert (info.model, info.name, info.firmware) == ("FRITZ!Box 7530", "gw1", "FRITZ!OS 8.25")
    assert info.details == {"hardware": "164"}
    labor = parse_description(TR64.replace("164.08.25", "164.08.30-117523"))
    assert labor.firmware == "FRITZ!OS 8.30 (build 117523)"
