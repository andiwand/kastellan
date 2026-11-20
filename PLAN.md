# Plan

Where Kastellan is going, and the decisions made on the way. The changelog has what is done.

## The idea

One self-hosted place to watch and look after every device on a home network: routers, mesh
access points, switches, servers, PCs, laptops, printers, cameras. About 20 devices to start
(a few Windows machines, several routers and access points, a couple of Linux servers).

It stands on its own; it does not talk to Norn.

## Decisions

- **Reached only through a VPN** (WireGuard / Tailscale). Never exposed to the internet.
- **Build on proven pieces, don't reinvent them.** Remote desktop through Apache Guacamole's
  `guacd` (RDP, VNC, SSH) with `guacamole-common-js` in the browser, which carries the screen,
  mouse and keyboard; the terminal with xterm.js over a WebSocket to asyncssh; backups with
  restic; device protocols through existing libraries (fritzconnection for TR-064, ...).
- **Both directions.** Agentless first: the hub logs in (SSH, vendor APIs like TR-064, SNMP,
  HTTP). A small agent (Go: one static binary for Windows, macOS, Linux, ARM) only where needed:
  Windows/macOS desktops, laptops away from home, remote desktop. The agent connects out to the
  hub, so clients open no ports.
- **Files, not a database.** `config.yaml` is the configuration (edited by hand or by the app,
  comments kept, as in Norn). The history is CSV, one file per day. SQLite only if queries over
  long ranges get slow; `history.py` is the one place to swap.
- **Stack** as in Norn: Python + FastAPI hub, SvelteKit (static) + Tailwind web app, one Docker
  image.

### Security

- Keys, not passwords: the hub has its own SSH key (later an SSH CA signing short-lived
  certificates); host keys are pinned. Agents enrol with a one-time token, then use mutual TLS.
- Each agent decides locally what the hub may do (watch only / shell / screen), so a
  compromised hub is bounded by what each device allowed.
- Stored credentials (router passwords, API tokens) encrypted at rest with `age`.
- Roles: **observer** (sees everything, never credentials, opens no sessions) and **admin**.
  Every admin action goes to an audit log.
- **Updates are suggested, not installed.** Automatic only when both hold: a known
  vulnerability affects the installed version, *and* the device or service faces the internet
  (a flag in config.yaml, later also seen from the router's port forwards).

## Done (0.1)

Reachability checks (ping, TCP, HTTP), history and uptime, the web app (overview, devices,
device page, devices as a list or a graph), demo mode, restoring state from the history after a restart.

## Next

1. **Terminal in the browser**: SSH via asyncssh + xterm.js, keys held by the hub.
2. **Remote desktop**: guacd as a sidecar container, RDP (Windows Pro) and VNC (Windows Home,
   macOS, Linux desktops), full mouse and keyboard.
3. **Sign-in and roles** (observer / admin) and the audit log, before 1 and 2 ship.
4. **Drivers**: one module per kind of device, declaring what it can do (as Norn's camera
   drivers). First FRITZ!Box (TR-064: clients, mesh, Wi-Fi, firmware, config export), OpenWrt
   (ubus), printers (IPP/SNMP: toner, state).
5. **Docker image updates**: which containers on which host run an outdated image (registry
   digests, as WUD/Diun do).
6. **Opening device web interfaces through the hub** (a reverse proxy per device, on its own
   subdomain: path-based proxies break most device UIs).

## Later

- The agent (Go) for Windows, macOS and laptops: metrics, tunnel for shell and screen.
- Disk, CPU and memory (over SSH for Linux, the agent elsewhere).
- Firmware: versions against the newest, update through the driver.
- **Network and Wi-Fi configuration**: read SSIDs, bands, mesh roles; keep settings in sync
  across the mesh from one desired state in config.yaml (show the difference, then apply).
- **Jobs**: run something on a device on a schedule. Backups are jobs: config exports through
  drivers, full backups with restic.
- Discovery (ARP, mDNS, the router's client list) to suggest devices for config.yaml.
- Topology from LLDP / the router's mesh view instead of hand-written parents (infrastructure
  only: which access point a client uses is not worth drawing).
- Notifications (web push, e-mail) when an always-on device goes down.
