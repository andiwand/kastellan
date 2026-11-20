# AGENTS.md

Orientation for agents: what this is, the layout, the build loop, and the conventions. The plan
and its decisions are in [`PLAN.md`](PLAN.md), the user documentation in [`README.md`](README.md).

## What this is

Kastellan is a self-hosted hub that watches every device on a home network (routers, access
points, servers, PCs, printers, cameras) and is meant to grow into one place to administer them.
It runs locally, reached through a VPN, as one Docker image. The name may change later.

## Layout

- `backend/src/kastellan/` (Python 3.13, FastAPI, managed with uv):
  - `config.py`: `config.yaml` is the only configuration; there is no database. A `Device` has a
    `host`, a `kind`, an optional `parent` (what it hangs off: alarms fold a device into its parent's; the "via" link; meant for infrastructure links, not which access point
    a client uses), `tags`, `always_on` (default: false
    for `pc` and `laptop`) and `checks`. A check is written `ping`, `tcp: <port>` or
    `http: <url>` (`Check._shorthand`); its `name` defaults to the type and target.
  - `checks.py`: runs one check and returns a `Result`; it never raises. Ping is unprivileged
    ICMP (icmplib). HTTP does not verify certificates by default (home devices are
    self-signed, and a check sends no credentials); any status below 500 is up unless `expect`.
  - `monitor.py`: one asyncio task per (device, check). `apply(config)` keeps unchanged loops
    running. `restore(rows)` takes the last known state from the history at start, so `since`
    survives restarts; `since` is None when the state began before the history.
  - `software.py`: what a service says it is, without credentials: the greeting of
    server-first protocols (SSH, FTP, SMTP; a TCP check listens `GREETING_WAIT` after connecting)
    and an HTTP response's `Server` header, page title, known page markers (LuCI), then
    `X-Powered-By`. Tested on real banners; add new ones to `tests/test_software.py`.
  - `versions.py`: `<data>/versions.log` (JSON lines): first sighting and every change of a
    service's software. The monitor writes it; `/api/changes` merges real changes
    (`kind: "version"`) with ups and downs (`kind: "state"`); the hub restores the last software
    per check from it at start.
  - `drivers/`: one module per kind of device, registered in `drivers/__init__.py` and chosen
    with `driver:` in config.yaml. `info()` returns a `DeviceInfo` (model, name, firmware); the hub
    polls it every 10 minutes and writes firmware changes to versions.log (check name
    `firmware`). Only what devices answer without credentials so far; FRITZ!Box reads TR-064's
    public `tr64desc.xml`. OpenWrt reveals nothing without an account (comes with secrets).
  - `docker.py`: containers from WUD's API (`docker: {wud: url}`), polled with the drivers.
    `unchecked` says why an update can't be known (unsupported registry, a non-semver tag whose
    digest WUD doesn't watch); never show those as "up to date". Each container's running
    image (`image:tag (id)`) goes to versions.log as `container <name>`.
  - `alarms.py`: `evaluate()` (pure) turns status into problems: critical for an always-on device
    down `down_after` seconds, warning for a down service; a down device folds in the devices
    behind it. `Alarms` keeps open ones in `<data>/alarms.json`, logs to `<data>/alarms.log`,
    sends info events once. `notify.py`: ntfy and webhooks with `min` and `quiet` hours (critical
    always). `alarms_api.py`: list, acknowledge, mute, test. The hub evaluates every 30 s.
  - Version-log keys starting with `@` are not checks: `@firmware`, `@container <key>`.
  - `vulns.py`: products (`from_software`, `from_image`; CPE for NVD, ecosystem/package for
    OSV) and `Sources` (NVD with its rate limit, OSV, CISA KEV; cached a day in `<data>/vulns/`).
    `None` from a lookup means "couldn't ask", never "safe". A missing patch level is `-` in a
    CPE, never `*` (that matched 29 CVEs for OpenSSH 9.8 instead of 1). A distribution's package
    revision makes NVD findings `confidence="upstream"`. `vuln_policy.py`: the table from
    findings to alarms, and `auto_update_reason` (all conditions or nothing). The hub sweeps two
    minutes after start and every `vulns.every_hours`.
  - Alarms only from fresh results (after `hub.started`, not overdue); folding to the top-most
    down ancestor; `RESOLVE_AFTER` hysteresis; silent resolves for muted, removed or folded
    devices. Failed notifications wait in `hub._outbox` and are retried with backoff for a day.
  - VPN: `vpn/wireguard.py` (wg-easy 15.5's API, Basic auth), `vpn/openvpn.py` (talks only to
    `vpn/scripts/kastellan-pki` and `kastellan-crl`: installed and bound with `command=` to the
    hub's separate VPN key in restricted mode, or sent over a normal login per call in direct
    mode; `install_script()` is both the copyable instructions and what "Install for me" runs), `vpn_api.py` (lists for everyone signed in; changes and downloads for
    signed-in admins, audited; configs hold private keys: `no-store`). `secrets.py`: files in
    `<data>/secrets` (0600), named from config.yaml, set with `kastellan secret set`.
  - Keys go onto devices only through `access.authorize()`: written as the target user (su), never
    by root into a home that user controls (a symlink there would aim root's write anywhere);
    dropbear (`/etc/openwrt_release`) gets plain lines, OpenSSH `from=` (and `restrict`).
  - A command run over SSH with nothing to send gets `stdin=asyncssh.DEVNULL`: with `input=""`
    asyncssh leaves stdin open and a script that reads it waits forever.
  - `tls.py`: the certificate an https check meets (its own handshake, one overall time limit,
    cached 10 min, failures 2 min); `checks._http` waits for it at most 0.4 s, so it never makes a
    service down. `Check.watch_cert`: public names, `verify: true` or `tls: true` raise alarms;
    `evaluate(..., unjudged)` lists certificate alarms that can't be judged now, which the hub
    carries (they end only when a good certificate is seen).
  - `config_edit.py` + `config_api.py`: changing config.yaml from the app. **Every write to
    config.yaml goes through `config_edit.write`** (backup in `<data>/config-backups/`, atomic
    replace) after `hub.check` (the load rules, including "sign-in never turns itself off"),
    then `hub.save_config` reloads at once. Device forms edit the YAML in place (ruamel
    round-trip): only `FORM_FIELDS` change, defaults aren't written, unchanged checks keep their
    comments. Only signed-in admins (`require_signin`), never in demo mode; nobody saves away
    their own admin role; a version hash refuses saving over a change made meanwhile; the audit
    log gets the diff with password hashes masked.
  - `sshkeys.py` + `sshkeys_api.py`: authorized_keys on devices (listed over the `ssh:` login;
    other users' files read and changed *as them* via `su`), removing a key, and rotating the
    hub key (`Rotator`: next key added + verified with `connect(only=...)` everywhere, then
    `Keys.promote_next`, then the old key removed; state in `<data>/ssh/rotation.json`;
    `Keys.connect` offers current, next and old keys meanwhile).
  - `networks.py`: which networks a device is in (by address: `networks:` cidrs, else its private
    /24; public addresses are the internet; `network:` names them, also several; the hub's
    default gateway is also on the internet), and its `public_ways` (checks by a public name or
    address). `hub._resolve_hosts` resolves names every 5 min. The web app's NetworkMap draws a
    zone per network and devices in two on the boundary.
  - `history.py`: check results as CSV, one file per UTC day under `<data>/checks/`. The folder
    is the index; retention deletes files. The only place to change if this becomes SQLite.
  - `stats.py`: buckets (uptime share and mean latency per slice) and changes (up/down moments)
    computed from history rows, for the web app.
  - `hub.py`: ties it together; reloads config.yaml when its mtime changes, keeps the last good
    config on an error (`config_error`, shown in the app).
  - `demo.py`: `KASTELLAN_DEMO=1` swaps in a made-up network with simulated results (a pure
    function of device, check and time) and a backfilled day of history in a temp folder. It
    never reads config.yaml or touches the real data. Used for trying it out and the website.
  - `auth.py`: password hashes (scrypt), sessions (`<data>/sessions.json`, only SHA-256 of the
    tokens, 0600), and `Brake` (5 failed sign-ins per address in 5 minutes). `audit.py`:
    `<data>/audit.log`, JSON lines, append only.
  - `auth_api.py`: `Guard`, plain ASGI middleware in front of /api (HTTP and WebSockets). Users
    and roles come from config.yaml (`users:`); sign-in is off while there are none. Observers
    only read; writes need an admin and the header `X-Kastellan: 1` (CSRF). Use `require_admin`
    in routes that change things, and `require_signin` in anything that hands out access to a
    device (terminal, remote desktop): it refuses while sign-in is off.
  - `ssh.py`: the hub's SSH key (`<data>/ssh/hub_ed25519`) and pinned host keys
    (`<data>/ssh/known_hosts`); never trust on first use by itself: `UnknownHost` until an admin
    trusts the fingerprint, `ChangedHost` until someone forgets the old key. No ssh-agent, no
    passwords. `terminal_api.py`: the terminal WebSocket (JSON input/resize in, binary output),
    trust/forget routes; audit open/close, never keystrokes.
  - `guac.py`: the Guacamole protocol (instructions, handshake with guacd). `desktop_api.py`:
    one-time tickets (POST, 60 s, bound to the session) carry the desktop password, so it is never
    in a URL; the WebSocket (subprotocol `guacamole`) relays instructions between guacd and
    guacamole-common-js, answers the tunnel's pings itself, and ends with the session like the
    terminal. Tested against a fake guacd only (tests/test_desktop.py); guacd 1.6 must listen
    on localhost (compose.yaml).
  - In a WebSocket handler's `finally`, write the audit line before the first `await`: a cancelled
    handler (page gone, server stopping) is ended at its next await.
  - Fail closed (`hub.py`): a broken config.yaml at start locks the API (`hub.locked`, 503);
    after sign-in was on (`<data>/signin-required`), a missing, empty, invalid or user-less
    config never turns it off. Config errors never include values (`_describe`), since
    config.yaml holds password hashes.
  - `api.py`: `/api/health`, `/api/devices`, `/api/devices/{id}/history`, `/api/series`,
    `/api/changes`, and the web app (single-page, index.html fallback).
- `frontend/` (SvelteKit, static adapter, Svelte 5 runes, Tailwind 4, Lucide icons, Geist):
  - `src/lib/state.svelte.ts`: the store; polls devices every 5 s, buckets and changes every
    30 s, while a page uses it.
  - `src/lib/status.ts`: what a state means to a person. `statusOf` maps a device to
    up / degraded / down / offline / waiting: a down device that isn't `always_on` is
    **offline**, not an alarm, and doesn't count as needing attention.
  - Components: `UptimeStrip` (one bar per bucket, hover tooltip), `LatencyChart` (line + area,
    crosshair), `StatusBadge`/`StatusDot` (color always with a word), `KindIcon`, `Logo`.
  - `NetworkMap`: the devices by network (networks.py): a zone per network, devices in two on the
    boundary (zones ordered so linked ones are neighbours; otherwise inside, "also ..."), the
    ways in from the internet listed in its zone.
  - `ServicesTable` (service, software, status, response, for, uptime), used by the device page
    and `/services`; `ActivityItem` (an up/down or a software update).
  - `UptimeStrip` merges neighbouring buckets (`mergeBuckets`) so a bar keeps ~5 px; it never
    overflows a narrow column.
  - Routes: `/` overview, `/services` (every tcp/http check on every device), `/devices` (list, or networks with `?view=graph`; search and filters
    apply to both, the networks view fades non-matches), `/access`, `/config`, `/devices/[id]`.
- `website/`: the project website, plain HTML/CSS/JS, published by `.github/workflows/pages.yml`.
  Screenshots in `website/img/` come from demo mode, in light and dark (`<picture>`).

## Build and test

```sh
cd backend && uv sync && uv run pytest && uv run ruff check . && uv run ruff format --check .
cd frontend && npm install && npm run check && npm test && npm run lint && npm run build
# Run: the hub serves the built app from backend/src/kastellan/web
cp -r frontend/build backend/src/kastellan/web
cd backend && KASTELLAN_DEMO=1 uv run kastellan     # http://localhost:8100
# Or the dev server with hot reload (proxies /api to :8100):
cd frontend && npm run dev
```

Node 24 (`frontend/.nvmrc`); older Node fails to install the toolchain.

**The lockfile:** `npm install <pkg>` next to an existing `node_modules` drops other platforms'
optional packages (`@emnapi/*`) from package-lock.json, and `npm ci` then fails on Linux (CI,
Docker). After adding a package, regenerate it without node_modules:
`mv node_modules /tmp/nm && rm package-lock.json && npm install --package-lock-only && npm ci`
(then `grep -c node_modules/@emnapi package-lock.json` says 3).

## Conventions

- Plain files over databases; YAML for configuration.
- Colors: status colors (`--good`, `--warning`, `--critical`, `--offline`) are reserved for
  state and always come with an icon or a word. Healthy uptime cells use the calmer
  `--good-soft`, so problems stand out. Light and dark are both designed (tokens in
  `frontend/src/routes/layout.css`, mirrored in `website/style.css`).
- Check layouts in a browser (both themes, and phone width) before calling UI work done.
- Build on existing protocols and libraries (Guacamole, xterm.js, restic, vendor libraries)
  instead of writing our own.
- Anything that changes a device is admin-only and goes through the audit log (once roles land).
