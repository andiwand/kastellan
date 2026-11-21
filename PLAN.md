# Plan

Where Kastellan is going, and the decisions made on the way. The changelog has the details of
what is done.

## The idea

One self-hosted place to watch and look after every device on a home network (routers, access
points, switches, servers, PCs, laptops, printers, cameras; about 20 to start). It stands on its
own; it does not talk to Norn.

## Decisions

- **Reached only through a VPN.** Never exposed to the internet.
- **Build on proven pieces**: guacd + guacamole-common-js (remote desktop), xterm.js + asyncssh
  (terminal), restic (backups), vendor APIs and libraries for devices.
- **No single place with root on everything** (2026-11-20). Routine work goes through narrow
  doors that can only do named things, allowed on the device's side: a read-only collector, an
  actor with abilities chosen one by one, a restricted login on OpenWrt. Never arbitrary
  commands. The terminal is separate: human access by a signed-in admin.
- **Agentless first** (SSH, vendor APIs, HTTP); a small Go agent only where needed (Windows,
  macOS, laptops away from home), connecting out to the hub.
- **Files, not a database**: config.yaml (by hand or from the app, comments kept), history as
  daily CSV. SQLite only if long ranges get slow (`history.py` is the one place to swap).
- **Stack**: Python + FastAPI, SvelteKit (static) + Tailwind, one Docker image.
- **Security**: keys, not passwords; pinned host keys; roles observer / admin; every admin action
  audited; updates suggested, automatic only for a known serious vulnerability on an
  internet-facing device (opt-in).
- **The hub never replaces its own scripts on hosts**: they report their version, renewing them
  takes a login (setting up again).

## Done

- [x] Checks (ping, TCP, HTTP), history, uptime, state kept across restarts
- [x] Web app: overview, devices (list and network graph), device pages, services, demo mode
- [x] Services with their software and version; version changes as events
- [x] Sign-in, roles, audit log, brake on password guessing
- [x] Terminal in the browser; host keys pinned
- [x] SSH access: keys per device, removing keys, rotating the hub key
- [x] Remote desktop (RDP, VNC) through guacd
- [x] Drivers: FRITZ!Box (model, firmware); OpenWrt through its own API
- [x] OpenWrt: state, Wi-Fi with rollback, services, packages, firmware updates
- [x] Groups: Wi-Fi networks, mesh and root password across devices at once
- [x] Docker containers and updates (WUD or the registries directly)
- [x] Collector (host health) and actor (update/restart containers, system updates, reboot)
- [x] Updates page: everything waiting, with its action; outdated Kastellan scripts
- [x] Alarms: down devices and services, one alarm for what is behind a device, hysteresis,
      acknowledge with a note, mute; disks, RAID, heat, certificates, vulnerabilities
- [x] Notifications: ntfy, Gotify, Telegram, e-mail, webhooks; per channel minimum and quiet
      hours; heartbeat
- [x] Known vulnerabilities (NVD, OSV, CISA KEV) by exposure; opt-in automatic updates
- [x] Certificates of every https check; the ways into a service (home, public name)
- [x] Networks: zones, the ways in from the internet; exposure per service
- [x] VPN page: WireGuard (wg-easy) and OpenVPN (easy-rsa) clients, configs, QR codes
- [x] Config from the app: settings as forms, devices as forms or text, config.yaml editor
- [x] Device web pages through the hub (one wildcard name, admins only)
- [x] Push notifications to browsers (behind HTTPS)
- [x] VPN: its key rotated; last seen per client
- [x] Sites; DNS checks

## Next

- [ ] A consistent DNS + HTTPS setup for Kastellan and the services (to discuss)
- [ ] Signing in to device pages for you (needs stored passwords: see the next item)
- [ ] Stored credentials encrypted at rest; maybe unlocked by the user's own sign-in (to think
      through)
- [ ] Device pages kept apart from each other (they share one site through the hub now)
- [ ] Versions for the OpenWrt and VPN helper scripts (as for the collector and actor)
- [ ] Same-major updates offered next to a newer major (auto-update needs them)
- [ ] SSH CA with short-lived certificates
- [ ] HTTP checks with an expected text
- [ ] Alarms for a new, unknown device on the network

## Later

- [ ] The Go agent (Windows, macOS, laptops): metrics, a tunnel for shell and screen
- [ ] Jobs on a schedule; backups (config exports through drivers, restic)
- [ ] Discovery (ARP, mDNS, the router's clients) to suggest devices
- [ ] Topology from LLDP / the router's mesh view instead of hand-written parents
- [ ] Checks from outside (reachable from the internet), through an agent elsewhere
