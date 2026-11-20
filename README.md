# Kastellan

Every device on your network, in one place.

Kastellan is a self-hosted hub that watches your router, access points, switches, servers, PCs,
laptops, printers and cameras. It answers whether everything is all right, and if not, what is
wrong, since when, and what the device is connected through. It is early: today it watches;
next it learns to look after things (a terminal and remote desktop in the browser, device
drivers, updates, backups). See [PLAN.md](PLAN.md).

![The overview](website/img/overview-light.webp)

## What it does today

- **Checks**: ping, TCP ports and HTTP endpoints per device, on an interval.
- **Overview**: one line saying whether anything needs you, the failing checks, every device's
  last 24 hours as a strip, and the ups and downs of the day.
- **Calm by default**: a PC or laptop that is off is *offline*, not *down*.
- **Device pages**: response times, uptime over 1 h / 24 h / 7 d, and the activity of each check.
- **List or graph**: the devices as a searchable list, or as a graph of what is connected
  through what (infrastructure only; which access point a laptop uses isn't drawn).
- **Docker updates**: `docker: {wud: http://host:3000}` reads the containers and their updates
  from [WUD](https://getwud.github.io/wud/), and says which it can't check (a registry WUD
  doesn't know, or a `latest`-style tag whose digest WUD doesn't watch).
- **Drivers**: `driver: fritzbox` reads model and FRITZ!OS version (no password needed);
  firmware updates show in the activity.
- **Plain files**: `config.yaml` for the configuration, one CSV file per day for the history.

## Try it

Kastellan has no release yet. From a checkout, with [uv](https://docs.astral.sh/uv/) and
Node.js 24:

```sh
cd frontend && npm install && npm run build && cp -r build ../backend/src/kastellan/web
cd ../backend && KASTELLAN_DEMO=1 uv run kastellan
```

Open http://localhost:8100. Demo mode shows a made-up network with a day of simulated history;
nothing on your network is contacted.

With Docker: `docker compose up -d --build` (see [compose.yaml](compose.yaml)).

## Your own network

Copy [config.example.yaml](config.example.yaml) to `data/config.yaml` (Docker:
`config/config.yaml`) and describe your devices:

```yaml
devices:
  router:
    host: 192.168.178.1
    kind: router
    checks:
      - ping
      - http: https://192.168.178.1
  nas:
    host: nas.lan
    kind: server
    parent: router
    checks:
      - ping
      - tcp: 22
        name: ssh
```

Kastellan picks up changes to the file by itself. Run it without `KASTELLAN_DEMO`.

Notes:

- **Ping without root.** Kastellan pings unprivileged. macOS allows that; on Linux (and in
  Docker with host networking) the host must allow it once:
  `sysctl -w net.ipv4.ping_group_range="0 2147483647"` (persist it in `/etc/sysctl.d/`). The
  app shows a banner when ping is refused.
- **Windows** answers no ping by default. Check a port instead: `tcp: 3389` (remote desktop)
  or `tcp: 445` (file sharing), and set `kind: pc` so a PC that is off counts as offline.
- **False alarms.** Ping sends three packets and any reply counts; a failed port or HTTP check
  is tried once more after two seconds before it counts as down.

Settings (environment): `KASTELLAN_CONFIG` (default `data/config.yaml`), `KASTELLAN_DATA`
(default: the config's folder), `KASTELLAN_HOST`, `KASTELLAN_PORT` (8100),
`KASTELLAN_LOG_LEVEL`, `KASTELLAN_DEMO`, and `KASTELLAN_HOSTS`: the names you reach the hub by
(comma-separated, e.g. `kastellan.lan`). IP addresses and localhost always work; other names are
refused, which stops DNS-rebinding attacks from web pages you visit.

## Sign-in

Sign-in is off until config.yaml has a user. Make a password hash and add it:

```sh
cd backend && uv run kastellan hash-password      # Docker: docker exec -it kastellan kastellan hash-password
```

```yaml
users:
  andreas:
    role: admin          # admin, or observer: sees everything, changes nothing
    password: scrypt$... # the hash printed above
```

Sessions last 30 days of not being used; removing a user from config.yaml signs them out at
once. Sign-ins, failed attempts and sign-outs go to `data/audit.log`. While sign-in is off,
everyone who reaches the page is an admin, and what hands out access to devices (the terminal,
remote desktop) stays locked.

## Terminal

A device with `ssh:` gets a terminal in the browser (admins only, and only with sign-in on):

```yaml
devices:
  sv1:
    host: 192.168.15.10
    ssh:
      user: andreas  # a user whose authorized_keys has the hub's key
```

The hub has its own SSH key (`data/ssh/hub_ed25519`, made on first use); the app shows its
public key to put into `~/.ssh/authorized_keys` on the device. Prefer a normal user to root,
and tie the key to the hub's address, so a copy of it is no use elsewhere:
`from="192.168.15.10" ssh-ed25519 AAAA... kastellan-hub`.

A terminal ends when its session does: signing out, a new password or role in config.yaml, or
removing the user closes it at once. It also ends after 30 minutes without typing and after
8 hours in any case; a user can have 4 open. The first connection shows the
host's key fingerprint to compare and trust; if it later changes, Kastellan refuses until you
say the host was reinstalled. Opening and closing a terminal goes to the audit log; what is
typed does not.

If config.yaml is broken when the hub starts, it locks itself (everything but /api/health
answers 503) rather than starting without sign-in. Once sign-in has been on, a config.yaml
without users is refused too; to turn sign-in off on purpose, also delete
`data/signin-required`.

## Remote desktop

A device with `desktop:` gets its screen in the browser, with mouse and keyboard (admins only,
with sign-in on). It goes through Apache Guacamole's guacd, which compose.yaml starts next to
Kastellan on localhost:4822 (`KASTELLAN_GUACD` to change).

```yaml
devices:
  gaming-pc:
    host: 192.168.15.20
    kind: pc
    desktop:
      protocol: rdp   # Windows Pro; for Windows Home, macOS or Linux desktops: vnc
      user: andreas   # suggested in the form
```

The password is asked for when connecting, used once, and never stored or put in a URL.
Kastellan doesn't let the remote side reach the hub's files, printers or clipboard, and passes
only mouse, keyboard and screen size from the browser to guacd. It ends after 30 minutes without
input. Opening and closing goes to the audit log. Not tested against real desktops yet.

Mind the certificate: Windows' RDP certificate is self-signed and guacd can't pin it, so
`ignore_cert: true` (the default) means someone in the middle on your network could catch the
login. Prefer `security: nla`, and give the PC a certificate guacd trusts, then set
`ignore_cert: false`. guacd itself has no sign-in: anything on the hub's host can use it, which is
why it listens on localhost only.

## Alarms and notifications

Kastellan raises an alarm when a device that should always be on is down for 3 minutes
(critical), or one of its services is (warning). A device that is down covers the devices
behind it (its `parent` chain): one alarm, not one per device. Container updates and changed
software or firmware are sent as info. Alarms show on the overview; an admin can acknowledge
them, and mute a device for an hour. Every alarm goes to `data/alarms.log`.

```yaml
alarms:
  down_after: 180             # seconds
notify:
  - ntfy: https://ntfy.sh/your-secret-topic   # or your own ntfy server
    min: warning              # info, warning, critical
    quiet: "22:00-07:00"      # only critical at night
  - webhook: https://example.org/hook         # JSON: title, body, severity, device
```

## Vulnerabilities and automatic updates

Kastellan looks up what your devices run in public vulnerability databases: **NVD** (CVEs by
product and version), **OSV** (packages, e.g. Home Assistant), and CISA's **KEV** catalogue of
vulnerabilities exploited in the wild. It uses what it already knows: SSH and web server
banners, firmware from drivers, container images with a version tag. Results show on each
device and are refreshed every 6 hours (cached for a day). Only product names and versions are
sent to these services.

Mark devices reachable from the internet with `exposed: true`. Then:

| finding                         | exposed device | internal device |
|---------------------------------|----------------|-----------------|
| exploited (KEV) or critical     | critical alarm | warning alarm   |
| high                            | warning alarm  | message         |
| medium, low                     | shown          | shown           |

When software reports a distribution's package revision (OpenSSH on Ubuntu), the distribution
may have fixed a bug without changing the upstream version: such findings say "maybe fixed by
your distribution", are messages at most, and never trigger anything.

Automatic updates are off. With `auto_update: true` on a device, and a WUD trigger
(`docker: {wud: ..., trigger: docker.local}`), Kastellan updates a container by itself only when
the device is `exposed`, a confirmed high, critical or exploited vulnerability is in that
container, and WUD offers a newer image. It does so once per new image, writes it to the audit
log and tells you.

```yaml
vulns:
  every_hours: 6
  nvd_api_key: ...      # optional, faster NVD lookups
```

## VPN access

One page to give and take away VPN access: WireGuard through
[wg-easy](https://github.com/wg-easy/wg-easy) (15.x), OpenVPN through an
[easy-rsa](https://github.com/OpenVPN/easy-rsa) PKI. Admins (with sign-in on) add clients,
turn them off or revoke them, and download a ready `.conf`/`.ovpn` or show a QR code. Every
action goes to the audit log.

```yaml
vpn:
  wireguard:
    wg_easy: http://192.168.15.10:51821  # its admin login; 2FA must be off for its API
    user: admin                           # password: kastellan secret set wg-easy
  openvpn:
    pki: sv1          # the device with the easy-rsa PKI (its ssh: user runs kastellan-pki)
    server: openwrt   # the OpenVPN server (its ssh: user runs kastellan-crl)
    days: 365         # how long new client certificates last
```

**wg-easy**: once Kastellan manages the clients, wg-easy's web interface doesn't need to be on
your network. Publish only WireGuard's UDP port and let Kastellan reach the web port on a Docker
network both share (or on localhost when both run on the same host): its API uses the admin
password over HTTP Basic and needs 2FA off, so it must never be reachable from outside. The VPN
page warns when the password would cross the network in clear.

**OpenVPN**: Kastellan never gets a shell or the CA key. Two small scripts in `tools/` are the
only commands its key may run, bound with `command=` in `authorized_keys`:

- `tools/kastellan-pki` on the PKI host: list, issue, revoke, export, crl. Give it a dedicated
  user that owns the PKI (`chmod 700`), so nothing else can read the CA key. Install
  instructions are at the top of the script; `/etc/kastellan-pki.conf` says where the PKI and
  the client template (`{{ca}} {{cert}} {{key}} {{tls}}`) are.
  It only ever touches **client** certificates (their key usage says TLS client, not server);
  `server`, `ca` and the names in `RESERVED` can't be exported, revoked or issued.
- `tools/kastellan-crl` on the OpenVPN server: takes the new CRL after a revoke, so a revoked
  client is refused at once (`crl-verify /etc/openvpn/crl.pem` in the server config). With
  `openssl` there (`opkg install openssl-util`) it also checks that the CRL is signed by your CA,
  and, if CRLs carry numbers, that it isn't an older one.

Both take their config file as an argument fixed in `authorized_keys` (shown at the top of each
script), never from the environment. Trust both hosts' keys once, as for the terminal.

easy-rsa's CRLs expire after 180 days, and OpenVPN then refuses every client; Kastellan renews
the CRL and sends it to the server every day, and the VPN page shows until when it is valid. If a
revoke can't reach the server, the revoke still stands; the page says so and offers to send the
list again.

## Security

Run Kastellan only where your own network or VPN can reach it, never on the internet. The security design (keys instead of passwords, per-device limits, roles,
updates only on known vulnerabilities of internet-facing devices) is in [PLAN.md](PLAN.md).

## License

MIT
