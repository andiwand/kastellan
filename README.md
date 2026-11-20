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
`KASTELLAN_LOG_LEVEL`, `KASTELLAN_DEMO`.

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
public key to put into `~/.ssh/authorized_keys` on the device. The first connection shows the
host's key fingerprint to compare and trust; if it later changes, Kastellan refuses until you
say the host was reinstalled. Opening and closing a terminal goes to the audit log; what is
typed does not.

If config.yaml is broken when the hub starts, it locks itself (everything but /api/health
answers 503) rather than starting without sign-in. Once sign-in has been on, a config.yaml
without users is refused too; to turn sign-in off on purpose, also delete
`data/signin-required`.

## Security

Run Kastellan only where your own network or VPN can reach it, never on the internet. The security design (keys instead of passwords, per-device limits, roles,
updates only on known vulnerabilities of internet-facing devices) is in [PLAN.md](PLAN.md).

## License

MIT
