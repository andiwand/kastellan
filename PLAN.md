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
- [ ] **Wi-Fi page:** a top-level view by network, not by device. Each network name (SSID)
      once, with the access points that broadcast it, bands, encryption, clients, and where the
      members differ (password fingerprints, encryption, a radio off). Change name, password,
      encryption, band and on/off for a network everywhere it is broadcast, member by member
      with each device's rollback; add a network to more access points or remove it. Reuses the
      group line-up (`openwrt/sync.py`) beyond groups: devices that broadcast the same name are
      one network. OpenWrt first; other vendors (FRITZ!Box) read-only where their API allows.
      The home for roaming settings (below) once those exist.
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
