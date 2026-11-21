<p align="center"><img src="website/favicon.svg" width="72" alt=""></p>

<h1 align="center">Kastellan</h1>

<p align="center"><b>Every device on your home network in one place.</b><br>
A self-hosted hub that watches routers, access points, servers, PCs, printers and cameras, and
lets you look after them: through narrow doors you open host by host.</p>

<p align="center">
<a href="https://github.com/andiwand/kastellan/actions/workflows/ci.yml"><img src="https://github.com/andiwand/kastellan/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
<a href="https://github.com/andiwand/kastellan/releases"><img src="https://img.shields.io/github/v/release/andiwand/kastellan?include_prereleases" alt="Release"></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-brightgreen.svg" alt="License: MIT"></a>
</p>

<p align="center"><a href="https://andiwand.github.io/kastellan/">Website</a> ·
<a href="#quick-start">Quick start</a> · <a href="#configuration">Configuration</a> ·
<a href="#access-to-your-hosts">Access</a> · <a href="#development">Development</a></p>

---

> Kastellan is an early preview, built on one real home network; see [PLAN.md](PLAN.md).

- **Checks**: ping, ports, web pages and DNS names per device, with history (CSV files),
  uptime, the software and version each service reports, and its certificate. Devices at other
  sites (a rented server, a friend's place) are grouped by site.
- **Networks**: devices by network, the router on the boundary to the internet, and every way
  in from outside that Kastellan knows.
- **Alarms** without storms: one alarm for a device and everything behind it, quiet hours, mute,
  acknowledge. To ntfy, Gotify, Telegram, e-mail, a webhook, or as notifications on your phone
  and computer (web push: needs Kastellan reached over HTTPS, e.g. through a reverse proxy).
- **Vulnerabilities** in what devices and containers run (NVD, OSV, CISA KEV), urgent where a
  device faces the internet.
- **Hosts**: disks and space, RAID, disk health, temperatures, failed services, pending
  system updates and reboots, Docker containers and their updates; installing system updates,
  updating or restarting a container, a reboot: as far as the host allows.
- **Terminal and remote desktop** in the browser (SSH; RDP and VNC through Apache Guacamole).
- **VPN**: any number of VPNs, WireGuard clients through wg-easy and OpenVPN clients through
  easy-rsa, each added on the VPN page.
- **SSH keys**: which keys may log in where; rotating Kastellan's own; a group's
  authorized_keys and known_hosts side by side (several users per device), changed on all at once.
- **A YAML file, no database**: edit `config.yaml` by hand or in the app; comments stay.
- **Local**: no account, no cloud, no telemetry. Reach it from home or through a VPN only.

## Quick start

With Docker (Linux; amd64 or arm64), using [compose.yaml](compose.yaml):

```yaml
services:
  kastellan:
    image: ghcr.io/andiwand/kastellan:latest
    restart: unless-stopped
    network_mode: host # the checks see the network as the host does
    environment:
      TZ: Etc/UTC
    volumes:
      - ./config:/config # config.yaml
      - ./data:/data # history, sign-in, Kastellan's SSH keys, the audit log
```

```sh
docker compose up -d
```

Open `http://<host>:8100`. To try it first: `docker run --rm -p 8100:8100 -e KASTELLAN_DEMO=1
ghcr.io/andiwand/kastellan` shows a made-up network.

Ping runs without root; on Linux the host must allow that once:
`sysctl -w net.ipv4.ping_group_range="0 2147483647"` (persist it in `/etc/sysctl.d/`). Remote
desktop needs guacd next to it (in [compose.yaml](compose.yaml), listening on localhost only).

## Configuration

Everything is in `config.yaml` (`/config` in the container), editable in the app once sign-in is
on: as forms on the Config, Groups and VPN pages and on each device's page, or as the file itself. Kastellan picks up changes by itself. Settings left out keep their defaults:

```yaml
check_interval: 60 # seconds

devices:
  router: # the id: history and alarms are filed under it
    name: FRITZ!Box
    host: 192.168.1.1
    kind: router # router, access-point, switch, server, pc, laptop, printer, camera, other
    driver: fritzbox # model and firmware, without a password
    checks:
      - ping
      - http: https://192.168.1.1
        name: web
  nas:
    host: nas.lan
    kind: server
    exposed: true # all of it reachable from the internet: vulnerabilities count as urgent
    ssh: { user: andreas } # the terminal, and Kastellan's SSH access
    collect: {} # the collector (see below)
    act: {} # the actor (see below)
    checks:
      - tcp: 22
        name: ssh
      - http: https://media.example.org # by its public name: its certificate is watched
        name: jellyfin (public)
      - tcp: 32400
        exposed: true # only this service is reachable from the internet (a port forward)
  vps:
    host: 203.0.113.20
    site: hetzner # not at home: the list groups by site, alarms say where
    checks:
      - ping
      - dns: www.example.org # the name resolves (to this address, if expect: is given)
        expect: 203.0.113.20
  pc:
    host: 192.168.1.30
    kind: pc # may be off: offline, not down
    desktop: { protocol: rdp, user: andreas }
    checks: [{ tcp: 3389 }] # Windows answers no ping by default
  ap-attic:
    host: 192.168.1.3
    kind: access-point
    openwrt: {} # OpenWrt through its own API (set up from the device's page)

groups: # devices that belong together; for OpenWrt members Wi-Fi, mesh, root password in sync
  aps: { name: Access points, devices: [ap-attic, ap-garden] }

networks: # optional: names for networks, or devices in several (network: [home, vpn])
  home: { name: Home, cidr: 192.168.1.0/24 }

users: # sign-in; off while empty
  andreas:
    role: admin # or observer: sees everything, changes nothing
    password: scrypt$... # from: kastellan hash-password

alarms:
  down_after: 180 # seconds down before an alarm
  cert_days: 14 # a watched certificate ending sooner
  disk_warning: 85 # percent full; disk_critical: 95

notify:
  - ntfy: https://ntfy.sh/your-secret-topic
    min: warning # info, warning, critical
    quiet: "22:00-07:00" # only critical then
  - telegram: 123456789
    token: "123456:ABC-your-bot-token" # also: gotify, email (smtp: smtps://user:pass@host), webhook
  - push: ada # ada's browsers that turned notifications on (the bell); * for everyone's

heartbeat: https://hc-ping.com/<id> # called every minute: a dead man's switch tells you when it stops

vpns: # any number, of either kind (the VPN page adds and changes them)
  home: { kind: wireguard, name: Home, wg_easy: http://nas.lan:51821 } # its password: on the VPN page
  office: { kind: openvpn, name: Office, pki: nas, server: router } # easy-rsa on the nas, OpenVPN on the router
```

| Variable              | Default                 | What                                                        |
| --------------------- | ----------------------- | ----------------------------------------------------------- |
| `KASTELLAN_CONFIG`    | `data/config.yaml`      | The config file (`/config/config.yaml` in the image).       |
| `KASTELLAN_DATA`      | the config's folder     | History, sign-in, keys, logs (`/data`).                     |
| `KASTELLAN_PORT`      | `8100`                  | The web app and the API.                                    |
| `KASTELLAN_HOSTS`     |                         | Names you open it by (`kastellan.lan`); addresses always work. Others are refused (DNS rebinding). |
| `KASTELLAN_GUACD`     | `127.0.0.1:4822`        | guacd, for remote desktop.                                  |
| `KASTELLAN_DEMO`      |                         | `1`: a made-up network.                                     |
| `KASTELLAN_LOG_LEVEL` | `info`                  | `debug`, `info`, `warning`, `error`.                        |

### Sign-in

Off until the first user is in `config.yaml`; then everyone signs in, and it never turns itself
off again. **Observers** see everything; **admins** also change things. Whatever reaches into a
device (terminal, desktop, installs, keys, editing the config) needs sign-in on, every change
goes to `data/audit.log`. Short passwords are allowed after a warning (`--short`).

### Keys (key rings)

Passwords, tokens and Kastellan's own SSH keys that can change things, encrypted in
`data/vault.json`; none of them, nor the keys that open them, is on disk as plain text. An
entry's name is visible (so the page can say where it is used while a ring is locked); a login's
user name and notes are encrypted with it. There
are rings: **Kastellan's** (what it uses by itself: router logins, its keys, push's key) and
**people's logins** (kept for you, never used by Kastellan by itself), and any you add. Each
ring opens its own ways: an admin's password (**signing in opens it**), a key file, an
environment variable, or a key kept in config.yaml; each has a recovery key, shown once. While a
ring Kastellan needs is locked, checks go on and the collector still reads; what needs a secret
waits and says so. Make them on the **Keys** page (it takes in what was kept as files so far and
removes those files), then choose how secure:

- **Convenient**: Kastellan's ring opens by itself at start, with a key in config.yaml, so
  everything works again after a power cut; whoever has a copy of the config folder can open it.
- **Balanced** (the default): nothing opens by itself; once an admin signs in, Kastellan's ring
  stays open until a restart, people's logins close again when no admin has used the app for 30
  minutes (a page left open doesn't count).
- **Strict**: as balanced, and people's logins close after 15 minutes without use.

Per ring you can choose otherwise, e.g. a key file from a Docker secret
(`unlock: [password, {keyfile: /run/secrets/kastellan}]`) or an environment variable. People's
logins can be set to open by themselves too: then they are readable by whoever has that key,
with nobody signed in (Kastellan still never uses them by itself). Lock closes a ring until it is
unlocked again, also one that opens by itself. The config editor shows config.yaml as it is, a
ring key in it included; when a way to open a ring is no longer chosen, its old key (also one
left in a config backup) opens nothing any more once the ring is next open. Showing a stored
password takes your own password, and it is hidden again after a minute or when its ring
closes. If your password changes while a ring is locked, open that ring once with its recovery
key (or let another admin sign in): your slot then follows the new password. With key rings,
`kastellan secret set` hands over to the Keys page.

What this protects against: a copy of `data/` (a backup, a stolen disk) reveals no secret
without a way to open its ring. Someone who can change the data folder while Kastellan runs is
an admin already; that isn't what it guards against.

### Device web pages through Kastellan

Every device's web interface on a name of its own, through the hub: with

```yaml
web:
  domain: kastellan.home.arpa # the hub is opened at this name
```

and one wildcard DNS entry (`kastellan.home.arpa` and `*.kastellan.home.arpa` → the hub), the NAS
is at `nas.kastellan.home.arpa` (an id's `_` becomes `-`). Open it with the device page's "Open
web interface": only signed-in admins get through, each device name with its own access tied to
your Kastellan session (signing out ends it), and a device's page can't use another's. You sign
in to the device itself as usual (Kastellan keeps no device logins). The page is the device's
first `http` check (or `web: https://...` on the device). Behind an HTTPS reverse proxy, the proxy
needs a wildcard certificate for those names and must pass the `Host` header on.

## Access to your hosts

Kastellan holds no root anywhere by itself. You open each door on the device's page, in a dialog:
either run the commands it shows yourself (Kastellan never sees a login), or type your login and
password (root through sudo, or su with root's password), used for that one connection, never
stored, the output shown as it runs. Each door is a user of its own with a key bound to one
command (`restrict`, `command=`, only from the hub's address):

| Door          | User                  | What it can do                                                                 |
| ------------- | --------------------- | ------------------------------------------------------------------------------ |
| SSH access    | yours, or `kastellan` | The terminal, as that user.                                                     |
| Collector     | `kastellan-collect`   | Run one root-owned, read-only script: system, space, RAID, SMART, temperatures, services, updates, containers (never their settings). |
| Actor         | `kastellan-act`       | Only the abilities you tick, each a root-owned script with one sudo rule: update or restart chosen containers, install system updates (apt), reboot. |
| VPN           | `kastellan-pki`, root | Only the client-certificate and CRL scripts.                                    |
| OpenWrt       | rpcd login `kastellan` | Through the device's own API (HTTPS, certificate pinned), with an access list of its own: read always; reboot, Wi-Fi settings, firmware, chosen services (start/stop/restart, at boot), who is connected to its OpenVPN server only if ticked. |

A taken-over hub gets exactly what the doors allow. Container updates are asked of the registries
directly; updating pulls the container's own tag again (only an image that came from its
registry) and recreates it with its settings, and puts the old one back if the new one doesn't
run steadily. System updates keep changed config files and never remove a package. Automatic updates
(`auto_update: true`) happen only for a known vulnerability on an `exposed` device.

OpenWrt routers and access points show their state, Wi-Fi clients, package updates and whether
their release still gets fixes. Wi-Fi changes are applied with the device's own rollback (it
undoes them if it can't be reached afterwards). Firmware moves one release series at a time: an
image built for the model by OpenWrt's image builder with its packages, checked, tested on the
device, installed keeping the settings. A group of them (`groups:`) keeps Wi-Fi networks, the
mesh and root's password the same: the Groups page shows where they differ and changes them on all.

Host keys are pinned (trusted once on the page). Kastellan's own key can be rotated on the
**Access** page: added and tried everywhere, then switched, then the old one removed.

## Development

`backend/` (Python, FastAPI), `frontend/` (SvelteKit, Tailwind), `website/` (GitHub Pages).
[AGENTS.md](AGENTS.md) explains the architecture.

```sh
cd frontend && npm install && npm run build && cp -r build ../backend/src/kastellan/web
cd backend && KASTELLAN_DEMO=1 uv run kastellan  # http://localhost:8100
cd frontend && npm run dev                        # http://localhost:5173, with hot reload

cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest
cd frontend && npm run lint && npm run check && npm test
```

## License

MIT.
