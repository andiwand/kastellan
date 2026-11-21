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
- **Local storage.** YAML configuration and daily CSV history; SQLite for indexed device logs.
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
- Central logs: routers, Linux journals, Docker, VPN presence and UDP/TLS syslog.
- Encrypted device configuration backups with deduplicated versions and masked diffs.
- FRITZ!Box accounts, internet health and speed tests, Wi-Fi scans and channel suggestions.

Published images may lag behind `main`; see the changelog's Unreleased section.

## Next

- [ ] Version checks and renewal notices for VPN and OpenWrt helpers.
- [ ] Same-major container update candidates even when a newer major exists.
- [ ] Consistent DNS + HTTPS setup for the hub and proxied device pages.
- [ ] Credential references for service accounts, notification tokens and API keys; show usage.
- [ ] Stronger isolation for proxied device pages (separate origins and request guards exist;
      the names still share a browser site).
- [ ] HTTP checks with expected response text.
- [ ] **Logs:** group filters, links to device events and alarms on matching lines.
      WireGuard handshakes indicate presence, not a login.
- [ ] **Access page:** correlate VPN, SSH, hub and router login events. Show account, source,
      destination and outcome; flag repeated failures and new outside addresses. Correlations
      suggest paths, not identities. Keep address lookups local by default.
- [ ] **Internet:** client bandwidth, provider-hop checks and iperf3 support. Current speed
      tests use HTTP transfers; scheduled tests defer while the line is busy.
- [ ] **FRITZ!Box:** mesh topology, line-error and reconnect alarms, VPN details and discovery
      from its client list. Current support includes pinned TR-064 access, line and Wi-Fi
      status, clients, port forwards, events, configuration exports and device actions.
- [ ] **Wi-Fi:** roaming, client radio diagnostics, FRITZ!Box neighbour data, country settings,
      and a scan after applying a channel plan. OpenWrt changes use rollback; FRITZ!Box changes
      do not. Channel suggestions require reported allowed channels.
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

1. **Hub recovery, the rest:** restore from the web app on a fresh hub, S3/WebDAV targets.
   (Done: Kastellan's own backup, `kastellan restore`, passphrase and recovery-key checks.)
2. **MFA:** passkeys or TOTP, recovery codes and step-up authentication for secret reveals and
   destructive actions. VPN access does not replace stronger hub authentication.
3. **Discovery before an agent:** suggest devices from router clients, ARP and mDNS; require
   approval before adding them. Unknown-device alerts need this inventory first.
4. **SNMPv3 monitoring:** read switch ports, printer consumables and network UPS status on
   devices that cannot run the Linux collector. Keep initial support read-only.

## Later

- [ ] Outbound agent for Windows, macOS and roaming laptops: metrics, shell and screen.
- [ ] Scheduled jobs and file backups through restic; additional device export formats.
- [ ] Topology from LLDP or router mesh data.
- [ ] External checks through a probe outside the home network.
- [ ] Short-lived SSH certificates.
- [ ] Assisted login to proxied device pages, after credential references and isolation.
