<p align="center"><img src="website/favicon.svg" width="72" alt=""></p>

<h1 align="center">Kastellan</h1>

<p align="center"><b>Monitor and manage your home network.</b><br>
Self-hosted checks, alerts and device administration, with permissions you choose per host.</p>

<p align="center">
<a href="https://github.com/andiwand/kastellan/actions/workflows/ci.yml"><img src="https://github.com/andiwand/kastellan/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
<a href="https://github.com/andiwand/kastellan/releases"><img src="https://img.shields.io/github/v/release/andiwand/kastellan?include_prereleases" alt="Release"></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-brightgreen.svg" alt="License: MIT"></a>
</p>

<p align="center"><a href="https://andiwand.github.io/kastellan/">Website</a> ·
<a href="#quick-start">Quick start</a> · <a href="#configuration">Configuration</a> ·
<a href="#access-to-your-hosts">Access</a> · <a href="#development">Development</a></p>

---

> Early preview. This README describes `main`; published images may lag behind.
> See [releases](https://github.com/andiwand/kastellan/releases), [CHANGELOG.md](CHANGELOG.md)
> and [PLAN.md](PLAN.md).

- **Monitor:** ping, TCP, HTTP, DNS and DHCP; uptime, latency, software versions and certificates.
- **Find problems:** dependency-aware alarms, UPS power status, disk forecasts, backup reports and vulnerability
  lookups (NVD, OSV, CISA KEV). Notifications through ntfy, Gotify, Telegram, e-mail, webhooks
  and browser push, with maintenance windows and optional reminders.
- **Manage:** Docker containers, libvirt VMs, apt updates, OpenWrt and FRITZ!Box Wi-Fi and firmware;
  container rollback and logs, SSH terminals, RDP/VNC desktops and device web pages.
- **Control access:** WireGuard via wg-easy, OpenVPN via easy-rsa, SSH key rotation and group
  synchronization, encrypted key rings.
- **Keep it local:** one Docker image, YAML configuration and file-based history. No vendor
  account or telemetry. Checks and integrations contact devices and external services as configured.
  Daily GitHub release checks can be disabled with `check_releases: false`.

## Quick start

Docker on Linux, amd64 or arm64. For a demo without contacting your devices:

```sh
docker run --rm -p 8100:8100 -e KASTELLAN_DEMO=1 ghcr.io/andiwand/kastellan:latest
```

Open `http://localhost:8100`.

For your network, save [compose.yaml](compose.yaml), create `config/config.yaml` using the
example below, and run:

```sh
mkdir -p config data
# Save config/config.yaml before starting.
docker compose up -d
```

Open `http://<host>:8100`. Keep Kastellan on your LAN or VPN. The Compose file uses host
networking and pins a release; build this checkout with `docker compose up -d --build` after
uncommenting `build: .` to use unreleased features.

Ping needs `net.ipv4.ping_group_range="0 2147483647"` on the Linux host; set it with `sysctl`
and persist it in `/etc/sysctl.d/`. The optional guacd service in Compose provides remote
desktop and must listen on localhost only.

A `dhcp` check broadcasts on the hub's local network and listens on UDP 68 (root required).
It does not cross routers or VPNs; configure it only for a DHCP server on that network.

## Configuration

Create `config/config.yaml` (mounted at `/config/config.yaml`) and change the addresses:

```yaml
devices:
  router:
    host: 192.168.1.1
    kind: router
    checks:
      - ping
      - http: https://192.168.1.1
  nas:
    host: nas.lan
    kind: server
    parent: router
    checks:
      - ping
      - tcp: 22
        name: ssh
  laptop:
    host: laptop.lan
    kind: laptop # being off is normal
```

Changes reload automatically. After enabling sign-in, edit devices in **Config**, or use
**Settings**, **Groups** and **VPN**. Forms preserve YAML comments; Settings also has a file
editor. See [config.example.yaml](config.example.yaml) for sites, DNS, UPSes and VM links.

HTTP checks accept status codes below 500 unless `expect` specifies a code. TLS verification
is off by default for local self-signed devices; enable `verify: true` to require it. Certificate
alarms watch public names and checks with `verify: true` or `tls: true`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `KASTELLAN_CONFIG` | `data/config.yaml` | Config path; `/config/config.yaml` in Docker. |
| `KASTELLAN_DATA` | Config directory | State, keys and logs; `/data` in Docker. |
| `KASTELLAN_PORT` | `8100` | HTTP port. |
| `KASTELLAN_HOSTS` | Empty | Allowed hostnames, comma-separated; IP addresses and localhost also work. |
| `KASTELLAN_GUACD` | `127.0.0.1:4822` | Remote desktop daemon. |
| `KASTELLAN_DEMO` | Unset | `1` enables simulated devices. |
| `KASTELLAN_LOG_LEVEL` | `info` | Logging verbosity. |

### Sign-in

Without users, monitoring is readable without sign-in; device access and configuration changes
require a signed-in admin. Generate a password hash:

```sh
docker compose run --rm kastellan kastellan hash-password
```

Add it to `config.yaml`:

```yaml
users:
  admin:
    role: admin # observer allows monitoring only
    password: 'scrypt$...' # replace with the generated hash
```

Once enabled, sign-in cannot be disabled by removing users or breaking the config.
Administrative actions are recorded in `data/audit.log`. Use HTTPS when sending passwords;
browser push also requires HTTPS.

### Key rings

Create key rings on **Keys** to migrate stored secrets into encrypted `data/vault.json`.
The **Kastellan** ring supplies background tasks; **People** stores logins used only by an
admin's explicit action. Entry names and SSH public keys remain visible while locked.

| Policy | Kastellan ring | People ring |
| --- | --- | --- |
| Balanced (default) | Opens on admin sign-in; stays open until locked or restarted. | Closes after 30 minutes without admin activity. |
| Convenient | Opens at startup using a key in config.yaml. | Same as Balanced. |
| Strict | Same as Balanced. | Closes after 15 minutes without use. |

Each ring has a recovery key shown once. Keep it separately. You can choose password, key-file,
environment or config-based unlocking per ring. A copy of both the vault and its unlock key
can reveal the secrets; Convenient stores that key in the config. The raw config editor shows
it, while audit diffs mask it. Before migration, file-based secrets are not encrypted.

Locked rings pause tasks that need their secrets; checks and the read-only collector continue.
If a password changes while a ring is locked, unlock it through recovery or another admin to
refresh its password slot.

### UPSes and virtual machines

Set up the collector on the host first, then add:

```yaml
# Under devices:
ups:
  kind: ups
  ups: { via: nas } # apcupsd or NUT on nas; add name: for a specific NUT UPS
home-assistant:
  host: homeassistant.lan
  runs_on: { host: nas, vm: haos } # libvirt domain name
  powered_by: ups
```

The UPS page shows power, battery and runtime readings and lets you choose what it powers.
On-battery alarms become critical below 30% charge or 5 minutes remaining by default; devices
that fail during the outage are grouped into that alarm. VM actions require the host's actor
and an allow list of VM names.

### Device web pages

Set `web: { domain: kastellan.home.arpa }` and point that name and `*.kastellan.home.arpa` at the
hub. **Open web interface** uses the device's first HTTP check or its `web:` URL.
Only signed-in admins can use the proxy; device logins remain separate. An HTTPS reverse proxy
needs certificates for the hub and device names and must preserve `Host`.

## Access to your hosts

Use a device's **Setup** tab to copy installation commands or run them with a one-time login.
Collector and actor permissions are enforced on the device:

| Access | Permissions |
| --- | --- |
| Collector | Read host health, updates, containers, libvirt VMs and UPSes through one restricted SSH command. |
| Actor | Selected container/VM actions, apt updates and reboot; separate sudo rules and allow lists. |
| Restricted OpenVPN | Certificate and revocation-list helpers only. Direct mode uses a normal SSH login. |
| OpenWrt | Pinned HTTPS connection with selected rpcd abilities; Wi-Fi changes use rollback. |
| FRITZ!Box | Pinned HTTPS/TR-064 with a dedicated account; changes have no automatic rollback. |
| Normal SSH | The configured user's permissions, including root or passwordless sudo if you grant them. |

Normal SSH access is broader than the restricted helpers. OpenSSH source-address restrictions
are added when the hub's address is known; Dropbear does not support them. Host keys require
explicit trust. Rotate the hub key in **Settings → Access** and the VPN key on **VPN**.

Container updates can pull a new build or an offered tag of the same image. The old container
returns if startup fails; stored data is not rolled back. Update the Compose file's tag too if
it manages the container. Apt updates retain modified config files and avoid package removals.
OpenWrt firmware advances one release series at a time, using an image built for the device
and its packages.

Automatic updates are opt-in and limited to containers on devices marked `exposed: true`,
with a confirmed high/critical or exploited vulnerability and an eligible update. Version
changes must stay within the same major and include a known fix when available; automatic actor updates
remain limited to a newer build of the existing tag.

**Updates** lists outdated collector and actor scripts. Reinstall from Setup with a host login;
their restricted keys cannot replace them. VPN and OpenWrt helpers do not yet report versions.
After upgrading Kastellan, check the changelog for helper reinstallation requirements.

## Backup and recovery

Stop Kastellan before copying both `config/` and `data/` for a consistent backup. Keep recovery
keys and any external unlock keys separately. Restore both directories and their permissions
before starting the same Kastellan version. Backups contain credentials; protect them accordingly.
Device backups preserve OpenWrt configuration archives and FRITZ!Box exports. Extract a FRITZ!Box
ZIP before restoring its `.export`; the included `export-password.txt` contains the restore password.
For scheduled hub backups, choose key rings or a separate passphrase on **Backups**, then add
a mounted folder or SFTP destination in **Settings → Kastellan's own backup**. Keep recovery
keys or the passphrase separately. Each backup key gets its own `kastellan-…` destination folder;
old generations remain until removed.

Restore a copied folder or downloaded `.kbak` using the same Kastellan version, with the hub
stopped and empty data/config directories:

```sh
docker compose stop kastellan
docker compose run --rm --no-deps -v /path/to/backup:/restore:ro kastellan kastellan restore /restore
docker compose up -d
```

The command prompts for the passphrase, or an admin login or Kastellan recovery key. A folder
uses credentials from its latest backup; a `.kbak` uses those from the downloaded version.
For folders, `--list` lists versions and `--version ID` selects one. `--force` replaces existing
state, including sessions and old secrets. After restoring, create a new backup key on
**Backups** to resume scheduled backups. External unlock
keys are not included. Central logs are optional; backups currently allow 128 MiB per
file and 512 MiB total. Unreadable or changing state fails the backup instead of silently omitting files.

Backups from the first hub-backup implementation have unsigned folder indexes. Download a
`.kbak` from the original hub before creating a new passphrase; those files remain restorable.

## Development

Python 3.13 with uv; Node 24 (`frontend/.nvmrc`). Run each block from the repository root:

```sh
(cd backend && uv sync)
(cd frontend && npm ci && npm run build)
mkdir -p backend/src/kastellan/web
cp -R frontend/build/. backend/src/kastellan/web/
(cd backend && KASTELLAN_DEMO=1 uv run kastellan)
```

For frontend hot reload, run `(cd frontend && npm run dev)` in another terminal.

```sh
(cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest)
(cd frontend && npm run lint && npm run check && npm test && npm run build)
```

[AGENTS.md](AGENTS.md) covers architecture; [PLAN.md](PLAN.md) lists remaining work.

## License

MIT.
