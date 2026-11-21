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
- [ ] Central logs: a first-party **Logs** page (its own sidebar entry) with every device's and
      container's lines interleaved by time, filtered by device, group, source (unit,
      container, program), level and time range, with text search and live follow; a line links
      to its device and sits next to that device's events. Ways in, chosen per device:
  - Fetched by the hub over channels it already authenticates and encrypts (the default):
    Linux hosts through the collector key (`journalctl` and container logs since a cursor,
    read-only), OpenWrt through its HTTPS API (`log read`, a new read-only rpcd ability;
    pinned certificate, Kastellan's own login).
  - VPN servers as a source of their own: the OpenVPN server's log (connections,
    authentication failures, revoked certificates refused) through the same collector or
    OpenWrt fetch, and for WireGuard (which logs next to nothing) lines made by Kastellan from
    the peer handshakes it reads: who connected, from where, when a peer went quiet.
  - SSH logins as a source of their own: sshd (journal) and Dropbear (OpenWrt), parsed into
    who, from where, how (key, password) and whether it worked.
  - Received on a syslog port (UDP/TCP 514), for devices that can only send: off by default,
    an explicit opt-in with the risk stated (unencrypted, unauthenticated: anyone on the LAN
    can read or forge lines), accepted only from known device addresses and tagged as
    unverified. Syslog over TLS (RFC 5425, port 6514) where the device supports it, or plain
    syslog only through a Kastellan VPN tunnel, as safer variants.
  - Stored in a capped, time-indexed store with a retention limit; who read what in the audit
    log. Later: alarms on matching lines.
- [ ] Access page, built on central logs: every login attempt into the network and its
      devices in one place — VPN connections, SSH, Kastellan's own sign-ins, router web logins
      and, where the router logs them, connections through forwarded ports. Each attempt as
      who, from which address, into what, how, and whether it worked; outside addresses kept
      apart from the LAN's. Correlated: attempts grouped by source address across devices
      (one address trying SSH on three hosts is one story), a success after failures from the
      same address highlighted, a login from an address never seen before for that user
      marked, a VPN connection followed by logins inside the network shown as one path.
      Alarms for the sharp cases (success after many failures, a new outside address for an
      admin login); nothing sent outside the hub to look addresses up unless turned on.

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
