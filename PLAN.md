# Plan

A local hub to monitor and administer a home network. Independent of Norn.
Implementation details and release history belong in [AGENTS.md](AGENTS.md) and
[CHANGELOG.md](CHANGELOG.md).

## Decisions

- **LAN or VPN only.** Do not expose the hub to the internet.
- **Restricted routine access.** Collectors read; actors and OpenWrt logins have explicit
  abilities. Normal SSH and direct VPN mode retain the configured login's privileges.
- **Explicit administration.** Device access requires an admin login. Changes are audited;
  host keys and OpenWrt certificates are pinned.
- **Agentless first.** SSH and vendor APIs now; an outbound Go agent where needed later.
- **Reuse protocols and tools.** Guacamole, xterm.js, asyncssh, vendor APIs; restic for future backups.
- **Local storage.** YAML configuration and daily CSV history; SQLite for device logs and vitals.
- **Stack.** FastAPI, SvelteKit, one Docker image.
- **Controlled updates.** Suggestions by default; automatic container updates require opt-in,
  whole-device exposure, a confirmed serious vulnerability and an eligible update.
- **Script replacement needs privileged access.** Restricted helper keys cannot install code.
  Collector, actor, OpenWrt helper and VPN script versions are checked.

## Done

- Checks (ping, TCP, HTTP, DNS, DHCP), history, uptime, latency, certificates, software.
- Overview, services, devices and groups, network map, sites, demo data.
- Alarms with dependencies, notifications, push, quiet hours, mute, maintenance, heartbeat.
- Sign-in, roles, audit log, key rings, stored logins.
- Browser SSH, RDP/VNC, device web proxy; SSH key rotation and sync.
- Linux collector and actor: health, forecasts, updates, containers, VMs, UPSes, logs.
- OpenWrt: monitoring, Wi-Fi, services, firmware, logs, backups, scans.
- FRITZ!Box: status, clients, Wi-Fi, mesh, WireGuard, DHCP, backups.
- VPNs: WireGuard and OpenVPN clients, last seen, key rotation.
- Central logs with UDP and TLS syslog.
- Device configuration backups; Kastellan's own backup and `kastellan restore`.
- Internet health and speed tests; Wi-Fi page, scans and channel plans.
- Config forms and YAML; release notices.
- Sign-ins page; VPN servers; discovery of unknown devices on the network.
- Logs by group with alarms and events among the lines.
- Internet: provider hops, line trouble, iperf3, bandwidth per device (nlbwmon).
- Wi-Fi: client diagnostics, country, scan after a channel plan; roaming checks and assistance.
- Helper version checks (collector, actor, OpenWrt, VPN scripts); container tag upgrades and
  same-major alternatives; "Not needed" for container, firmware, reboot and system updates.
- Vitals (CPU, memory, storage, temperature) for Linux, OpenWrt and FRITZ!Box, with 30-day charts.
- Device pages by topic for every kind: services (with the device's own), software, Wi-Fi,
  network, storage and hardware.
- Kept TLS connections and slower reads of rarely changing data, sparing small routers.
- HTTP checks with expected text; credential references; isolation of device pages;
  "Open signed in" for device pages (LuCI, HTTP Basic).

Published images may lag behind `main`; see the changelog's Unreleased section.

## Next

- [ ] **DNS + HTTPS for a site:** each site gets its own zone (Moosbrunn: `moos.stefl.at`,
      delegated from easyname to BIND on the server). Let's Encrypt via DNS-01 into that zone
      (RFC2136, a key that may only write `_acme-challenge` TXT); nginx-proxy terminates TLS.
      Done in Kastellan: `web.zone` and `web.dns`, the check of names (no split DNS), the
      proxy's certificates and the routers' rebind exception and forward, with the commands.
      Open:
  - Applying the router DNS lines from Kastellan (a narrow `dns` ability), not only showing them.
  - Checks from outside: the delegation answering, port 53 answering only for the zone,
    private names refused from outside (needs the external probe under Later).
  - Optional, per device: Let's Encrypt certificates issued on the server, pushed over SSH
    (OpenWrt: uhttpd) with the pin updated in the same step; never the wildcard's key. Only for
    opening device pages directly; through the hub's proxy the pinned self-signed ones suffice.
- [ ] **Roaming:** usteer installation from the group (memory-safe on 32 MB devices); clients
      kicked over and over, from usteer's events.
- [ ] **Sign-ins:** router web logins on OpenWrt (rpcd/LuCI log lines), and the paths
      for OpenVPN clients (their addresses inside the VPN).
- [ ] **FRITZ!Box:** assisted login to its pages (its session id lives in the page, not a cookie).

## Proposed priorities

These are suggestions, not implemented features or release commitments.

1. **Hub recovery, the rest:** restore from the web app on a fresh hub, S3/WebDAV targets.
2. **MFA:** passkeys or TOTP, recovery codes and step-up authentication for secret reveals and
   destructive actions. VPN access does not replace stronger hub authentication.
3. **Discovery, the rest:** ARP and mDNS besides the routers' client lists (done: suggested
   from FRITZ!Box hosts, OpenWrt leases and Wi-Fi clients, added only by an admin).
4. **SNMPv3 monitoring:** read switch ports, printer consumables and network UPS status on
   devices that cannot run the Linux collector. Keep initial support read-only.
5. **Traffic:** what goes in and out through the WAN over time: volume per day and month, peaks,
   the busiest devices, and a warning on unusual traffic. The rate is already read (FRITZ!Box
   byte counters and online monitor, OpenWrt WAN counters) and per device on OpenWrt
   (nlbwmon); missing are keeping it, the FRITZ!Box's monthly counter (its web interface), and
   per device on a FRITZ!Box, which TR-064 doesn't offer.
6. **Diagnostics:** answers to "why", from what Kastellan already knows, as a guided check per
   device or VPN. Host not reachable: the path through its parents, its address and lease, ping
   versus port, the name it resolves to. VPN not working: the server running, the port forward
   on the router, the public address or name matching, the last handshake, the client's key.
7. **More devices and sources, toward an open replacement for UniFi and Omada controllers:**
   one place to watch and manage switches, access points and routers across vendors, still
   agentless with restricted logins. Candidates: OpenWrt as the managed platform (VLANs, more
   networks, switch ports), SNMP for read-only switches, MikroTik RouterOS and OPNsense through
   their APIs. Adopting UniFi or Omada hardware itself needs their protocols: decide later.

## Later

- [ ] Outbound agent for Windows, macOS and roaming laptops: metrics, shell and screen.
- [ ] Scheduled jobs and file backups through restic; additional device export formats.
- [ ] Topology from LLDP or router mesh data.
- [ ] External checks through a probe outside the home network.
- [ ] Short-lived SSH certificates.
