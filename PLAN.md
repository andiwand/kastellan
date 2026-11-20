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
- **No single place with root on everything** (decided 2026-11-20). Routine work (status,
  containers, services, disk, updates, backups) goes **through the agent when there is one, else
  SSH**, and either way through a narrow API that can only do named things:
  - The agent: read-only by default; what it may do is set on the device's side (a hub that is
    taken over gets only what each device allowed). Actions are named and enabled one by one
    (restart container X, apply updates), never arbitrary commands.
  - How it is installed depends on the device, whatever makes sense there (decided
    2026-11-20): natively (one Go binary and a systemd/procd unit or Windows service, installed
    from the device page over SSH or by hand) wherever it should also administer; as a container
    on Docker hosts where it only watches (the host's /proc, /sys read-only, Docker through a
    filtering socket proxy). The hub's own host needs no agent: the hub can read it directly.
  - Without an agent: an SSH key restricted in authorized_keys
    (`restrict,command="kastellan-collect"`) that can only run one fixed read-only script.
  - The terminal is separate: human access, opened by a signed-in admin, as a normal user (sudo
    is the person's own step), later with short-lived SSH certificates instead of a standing key.
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

The order agreed on 2026-11-20. After each stage an independent review, and its findings
fixed, before the next one starts.

1. **Services, versions and their uptime.** Each check keeps what the service tells about
   itself without credentials: the SSH banner (`OpenSSH_10.2p1 Ubuntu`), the HTTP `Server`
   header (`Tasmota/14.4.0`), and so on. The device page lists the services with version, up
   since and uptime; a version change is an event ("OpenSSH 9.8 → 9.9"). Deeper data comes with
   the later stages: Docker containers and systemd services over SSH (stage 3), firmware
   through drivers (stage 5), newer versions (stage 6).
2. **Sign-in and roles** (observer / admin) and the audit log.
3. **Terminal in the browser**: SSH via asyncssh + xterm.js, keys held by the hub, host keys
   pinned. Refuses to run while sign-in is off. Docker containers and systemd services are
   read through the agent or the restricted collector key (see the decision above), not through
   the terminal's access.
   **SSH access management** rides on the same access: which keys may log in where (every
   host's `authorized_keys`, with each key's name and age), and **key rotation as one guided
   action**: make a new key, add it everywhere, prove it works on each host, then remove the old
   one, host by host, with a way back while both work. Every step in the audit log. Done
   (2026-11-20): listing and removing keys per device, rotating the hub key; not yet: the VPN
   key's rotation, key age (authorized_keys has no dates), an SSH CA (short-lived certificates)
   instead of keys spread over hosts.
4. **Remote desktop**: guacd 1.6 as a sidecar container, RDP (Windows Pro) and VNC (Windows
   Home, macOS, Linux desktops), full mouse and keyboard, through guacamole-common-js.
5. **Drivers**: one module per kind of device, declaring what it can do (as Norn's camera
   drivers). The home network has OpenWrt (router + 2 APs: ubus/rpcd over HTTPS, or SSH) and a
   FRITZ!Box (TR-064): clients, mesh, Wi-Fi, firmware, config export.
6. **Docker image updates**: which containers on which host run an outdated image. SV1 already
   runs WUD (What's Up Docker), so read its API first instead of asking registries ourselves.
7. **Notifications and alarms** (see below).
8. **Opening device web interfaces through the hub** (a reverse proxy per device, on its own
   subdomain: path-based proxies break most device UIs).

## VPN access (asked for on 2026-11-20)

Today: OpenVPN on the OpenWrt router with its easy-rsa PKI on SV1, and WireGuard through wg-easy
on SV1. Goal: one place to give and take away VPN access.

- **OpenVPN** (built): easy-rsa stays installed on SV1 (decided: no container of our own), owned
  by a dedicated user; Kastellan drives it only through `kastellan-pki` (backend/src/kastellan/vpn/scripts), and pushes the
  CRL with `kastellan-crl`. Install both from the VPN page's Setup section (or `mode: direct`).
- **WireGuard** (built): through wg-easy's API; its web interface can then leave the LAN.
- One "VPN" page: who has access, by which method, last seen; revoke in one place.
- Needs: SSH (or the agent) to SV1 and the router, so it comes after those are set up.

## Beyond the home network

- **Devices in other networks** (a Hetzner VM, a server at a friend's place): any host or
  address already works in config.yaml. A `site` per device (home, hetzner, ...) groups them in
  the list and graph, and alarms say which site. Hosts behind NAT come in through the agent.
- **Outside services we depend on** (the ISP, DNS resolvers, a mail provider, the domain's
  web server): devices of kind `external`, whose host is a name. New checks: `dns` (does the
  name resolve, to what) and HTTP with an expected text. Done: every https check sees its
  certificate (valid, why not, days left; alarms for public names), and a check by name shows the
  address it led to, so the ways into a service (at home, its public domain) are watched one by
  one. Checking from outside (is it reachable from the internet) needs a vantage point outside:
  later, through an agent elsewhere. Their uptime shows like everything else, so an outage outside
  can be told from one inside.

## Notifications and alarms

Alarms come from what the monitor already knows; notifications deliver them.

- **Alarm rules**, with sensible defaults and overrides per device in config.yaml:
  - an always-on device down for N minutes (default 3), a check down for N minutes;
  - recovered (once an alarm fired);
  - flapping (more than N changes in an hour) as one alarm, not many;
  - later: disk nearly full, certificate expiring, a new version with a known vulnerability on
    an internet-facing device, an unknown device appearing on the network.
- **No alarm storms**: when a device is down, the devices behind it (its `parent` chain) are
  reported as one alarm ("AP 1 down, 4 devices behind it unreachable"), not five.
- **Delivery**: web push to the installed app (as in Norn), and ntfy / Gotify / e-mail /
  Telegram through one small interface. Per user: which channels, minimum severity, quiet hours
  (critical still comes through).
- **State**: alarms are open, acknowledged (with who and a note) or resolved; the app shows
  open alarms first, and they go to the audit log. Kept in a file, like the rest.
- Mute a device or a check for a time (maintenance), from the app.

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
