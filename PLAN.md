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
- **Plain files.** YAML configuration, daily CSV history, no database. Consider SQLite only
  if history queries require it.
- **Stack.** FastAPI, SvelteKit, one Docker image.
- **Controlled updates.** Suggestions by default; automatic container updates require opt-in,
  whole-device exposure, a confirmed serious vulnerability and an eligible update.
- **Script replacement needs privileged access.** Restricted helper keys cannot install code.
  Collector and actor versions are checked; VPN and OpenWrt helper versions are still missing.

## Implemented in main

- Checks: ping, TCP, HTTP, DNS; history, uptime, latency, software and certificate tracking.
- Overview, services, devices, network map, sites and simulated demo data.
- Dependency-aware alarms, UPS power alarms, host health, backup reports and vulnerability findings;
  notification channels, browser push, quiet hours, mute, maintenance windows,
  acknowledgement, reminders and heartbeat.
- Sign-in, admin/observer roles, audit log, encrypted key rings and stored human logins.
- Browser SSH, RDP/VNC and device web proxy; SSH key rotation and group key/known-host sync.
- Linux collector and actor: host health, disk-full forecasts, apt updates, containers, libvirt
  VMs and UPSes.
- Container and VM lifecycle controls, container rollback, container and service logs;
  update queues with progress;
  collector/actor renewal notices.
- FRITZ!Box identification; OpenWrt monitoring, Wi-Fi, services and firmware; group configuration.
- Multiple WireGuard and OpenVPN instances, client management, last seen and VPN key rotation.
- Config forms and YAML editing; separate Status, Setup and Config views.
- Notice of a newer Kastellan release.

Published images may lag behind `main`; see the changelog's Unreleased section.

## Next

- [ ] Version checks and renewal notices for VPN and OpenWrt helpers.
- [ ] Same-major container update candidates even when a newer major exists.
- [ ] Consistent DNS + HTTPS setup for the hub and proxied device pages.
- [ ] Credential references for service accounts, notification tokens and API keys; show usage.
- [ ] Stronger isolation for proxied device pages (separate origins and request guards exist;
      the names still share a browser site).
- [ ] HTTP checks with expected response text.
- [ ] **Logs page:** interleave device and container logs by time, with source/device/group,
      severity and time filters, text search, live follow, and links to device events.
      Enable collection per device with explicit source permissions; existing collector access
      must not silently gain access to logs.
  - Fetch Linux journals and container logs through restricted collector commands with cursors;
    OpenWrt logs through a new read-only rpcd ability over pinned HTTPS.
  - Include OpenVPN connection/authentication events and sshd/Dropbear login outcomes.
    Show WireGuard handshake activity as inferred peer presence, not proof of a login.
  - Optional UDP/TCP syslog on port 514: disabled by default, limited to known device addresses,
    and marked unverified. Plain syslog is unencrypted and forgeable; prefer TLS on 6514 with
    client authentication or a VPN tunnel.
  - Cap storage and retention, index by time, restrict reading to admins, and audit access
    without copying log content. Later: alarms on matching lines.
- [ ] **Access page:** combine reported VPN, SSH, hub and router login events, plus forwarded
      connections where logged. Show account, source address, destination, method and outcome.
      Group attempts across devices; highlight success after repeated failures and new outside
      addresses for an admin. Time/address correlations suggest possible paths, not proven
      identities. Keep address lookups local unless external lookup is enabled.
- [ ] **Internet connection:** its own place on the overview (and a page for the history).
  - Health from outside the router: latency, jitter and loss to a few outside targets (more
    than one provider, so one target's trouble isn't an outage), DNS resolution, IPv4 and IPv6
    apart. An outage is located step by step: router reachable → line up (router's sync) →
    first hop at the provider → the internet → DNS; the alarm says which step failed.
  - Uptime: outages and their length, monthly availability, reconnects and public address
    changes (from the router where it tells: FRITZ!Box TR-064, OpenWrt's WAN interface;
    otherwise from the checks). The record to show the provider.
  - Bandwidth in use: up/down over time from the router's counters (no load added), and
    which clients use it where the router knows.
  - Speed: measured tests, opt-in, on a schedule and on request (they use the line: rarely,
    never while it is busy), against a chosen server (librespeed, or iperf3 on your own server
    outside); also latency under load (bufferbloat). Compared with the booked speed, which is
    set once; alarms when it stays far below.
- [ ] **FRITZ!Box with an account:** today only its public TR-064 description (model, firmware).
      Next, through TR-064 over HTTPS (port 49443, certificate pinned like OpenWrt's) with a
      FRITZ!Box user of its own, holding only the rights it needs, its password in the vault:
  - Read: internet connection (DSL/cable/fibre sync rates, line errors, public address, uptime,
    reconnects), clients and the mesh (which access point, band, signal), Wi-Fi incl. guest
    network, firmware and whether an update is offered, port forwards, MyFRITZ and its VPN
    (security-relevant: shown on the device page, later on the Access page), the event log
    (into central logs).
  - Alarms: internet down or reconnecting often, line errors rising, a new port forward or
    remote access turned on, firmware update offered.
  - Act: reboot, firmware update, Wi-Fi name/password/on-off and guest network (through the
    Wi-Fi page), WPS off. Changes FRITZ!OS wants confirmed on the box (its second factor)
    say so instead of failing silently.
  - Config export (password-protected, through the vault) as the FRITZ!Box's backup, and its
    client list as a source for device discovery.
- [ ] **Wi-Fi page:** a top-level view by network, not by device. Each network name (SSID)
      once, with the access points that broadcast it, bands, encryption, clients, and where the
      members differ (password fingerprints, encryption, a radio off). Change name, password,
      encryption, band and on/off for a network everywhere it is broadcast, member by member
      with each device's rollback; add a network to more access points or remove it. Reuses the
      group line-up (`openwrt/sync.py`) beyond groups: devices that broadcast the same name are
      one network. OpenWrt and FRITZ!Box (with an account, above): a network on both is still one network.
      The home for roaming settings (below) once those exist.
  - Radio settings per access point: channel, width, transmit power, country; shown next to
    each other for all access points, so overlaps are visible.
  - Channel diagnostics: neighbouring networks per radio (scan: who, channel, signal), how busy
    each channel is (`iw survey`: busy and noise), which of our access points hear each other
    and how loud, clients' signal, retries and rates, DFS radar events. Kept over time, since
    one scan is a moment. A scan can disturb clients briefly: run on request or in a quiet
    hour. FRITZ!Box where its API offers it.
  - Channel plan: a suggestion from the diagnostics (our access points apart from each other,
    2.4 GHz on 1/6/11, 5 GHz on the least busy channels, DFS only where the radio handles
    radar), with the reason per radio. Applied on approval, one access point at a time with
    its rollback, ideally in a maintenance window; never on its own by default. A check
    afterwards shows whether it got better.
- [ ] **Wi-Fi roaming in groups:** Kastellan watches and lines up roaming; the access points
      decide (usteer, 802.11r/k/v), so Wi-Fi never depends on the hub. In order:
  - Consistency checks per group: 802.11r on with the same mobility domain, fixed channels that
    don't overlap (2.4 GHz 1/6/11, 5 GHz apart), usteer running on every member and every
    member seeing the others. Findings on the group page, with what to change.
  - Roaming on the device page: clients per access point with signal, handovers and steering
    kicks, from usteer over the OpenWrt API (a new read-only rpcd ability). Shows whether the
    thresholds suit the home, or kick too often.
  - Alarms: usteer stopped, a member missing from the others' view, a client kicked over and
    over; memory warnings for small devices (32 MB) before they run out.
  - Set up from the group ("roaming assistance"): the same usteer settings and 802.11k/v
    where the device's wpad supports them, applied member by member with a check after each.
    Last: installing on small devices can exhaust memory (install from the package file,
    without package lists, and lower usteer's OOM priority there).

## Proposed priorities

These are suggestions, not implemented features or release commitments.

1. **Hub recovery:** export/restore config and state, recovery-key checks and a tested restore
   procedure. Config-file backups alone cannot recover the vault, keys or history.
2. **MFA:** passkeys or TOTP, recovery codes and step-up authentication for secret reveals and
   destructive actions. VPN access does not replace stronger hub authentication.
3. **Discovery before an agent:** suggest devices from router clients, ARP and mDNS; require
   approval before adding them. Unknown-device alerts need this inventory first.
4. **SNMPv3 monitoring:** read switch ports, printer consumables and network UPS status on
   devices that cannot run the Linux collector. Keep initial support read-only.

## Later

- [ ] Outbound agent for Windows, macOS and roaming laptops: metrics, shell and screen.
- [ ] Scheduled jobs and device backups: config exports through drivers, restic for files.
- [ ] Topology from LLDP or router mesh data.
- [ ] External checks through a probe outside the home network.
- [ ] Short-lived SSH certificates.
- [ ] Assisted login to proxied device pages, after credential references and isolation.
