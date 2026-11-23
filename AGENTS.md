# AGENTS.md

Orientation for agents: what this is, the layout, the build loop, and the conventions. The plan
and its decisions are in [`PLAN.md`](PLAN.md), the user documentation in [`README.md`](README.md).

## What this is

Kastellan is a self-hosted hub that watches every device on a home network (routers, access
points, servers, PCs, printers, cameras) and is meant to grow into one place to administer them.
It runs locally, reached through a VPN, as one Docker image. The name may change later.

## Layout

- `backend/src/kastellan/` (Python 3.13, FastAPI, managed with uv):
  - `config.py`: `config.yaml` is the only configuration; environment variables are the
    process's (paths, port, guacd, demo, log level), except `KASTELLAN_HOSTS`, which
    `hub.seed_from_env` writes into `hosts:` once (marker `<data>/env-seeded.json`; if it can't
    write, the variable counts for that run only); saving refuses a config without the name the
    page was opened by. SQLite is used only for device logs and vitals. A `Device` has a
    `host`, a `kind`, an optional `parent` (what it hangs off: alarms fold a device into its parent's; the "via" link; meant for infrastructure links, not which access point
    a client uses), `tags`, `always_on` (default: false
    for `pc` and `laptop`), a `site` (where it is; None is `home`), an optional `runs_on:
    {host, vm}` (a libvirt VM on another device: alarms fold it into its host like a parent;
    the API resolves the VM's state, a host's VMs name their device; `config.VM_NAME` is the
    one name pattern, never a number or a UUID) and `checks`. A UPS is `kind: ups` with
    `ups: {via, name}` (the host whose collector reads it: apcupsd's apcaccess or NUT's upsc;
    `host` may then be empty: no checks, its state only from that report); any device may
    have `powered_by: <ups>`. `ups.py` resolves a UPS's reading and its state word; alarms
    (`ups:<id>:battery|replace|contact`) fold devices that go down while their UPS is on
    battery (or just back) into its alarm; `PUT /api/devices/{ups}/powered` sets what it
    powers. A check is
    written `ping`, `tcp: <port>`, `http: <url>` or `dns: <name>` (`Check._shorthand`); its
    `name` defaults to the type and target. `sites:` names sites; `Config.label(id)` is a
    device's name for alarms and notifications, with its site when that isn't home; the device
    list groups by site only when devices are at more than one.
  - `checks.py`: runs one check and returns a `Result`; it never raises. Ping is unprivileged
    ICMP (icmplib). HTTP does not verify certificates by default (home devices are
    self-signed, and a check sends no credentials); any status below 500 is up unless `expect`.
    DNS (dnspython): A and AAAA from `server` or the system's resolvers; with `expect`, every
    listed address must be among the answers. A dns check is no way in (networks.py).
  - `monitor.py`: one asyncio task per (device, check). `apply(config)` keeps unchanged loops
    running. `restore(rows)` takes the last known state from the history at start, so `since`
    survives restarts; when the state began before the history, `since` is its oldest row and
    `since_exact` is false.
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
  - `alarms.py`: `evaluate()` (pure, but for `switched`: the hub's memory of when each UPS
    switched, which NUT doesn't tell) turns status into problems: critical for an always-on device
    down `down_after` seconds, warning for a down service; a down device folds in the devices
    behind it. `Alarms` keeps open ones in `<data>/alarms.json`, logs to `<data>/alarms.log`,
    sends info events once. `notify.py`: ntfy, webhooks, Gotify, Telegram, e-mail and web push,
    with `min` and `quiet` hours (critical always). `alarms_api.py`: list, acknowledge, mute,
    test. The hub evaluates every 30 s.
  - Web push: `push.py` (VAPID key `<data>/push/vapid.pem`, or the key ring entry `push/vapid`, subscriptions per user, aes128gcm
    via http-ece, 404/410 drop a browser), `push_api.py` (signed-in people, observers too: `OWN`
    in auth_api are the only writes an observer may make). A `push: <user>` (or `*`) notify
    channel; `frontend/static/sw.js` shows them; browsers need HTTPS (a reverse proxy).
  - Version-log keys starting with `@` are not checks: `@firmware`, `@container <key>`.
  - `vulns.py`: products (`from_software`, `from_image`; CPE for NVD, ecosystem/package for
    OSV) and `Sources` (NVD with its rate limit, OSV, CISA KEV; cached a day in `<data>/vulns/`).
    `None` from a lookup means "couldn't ask", never "safe". A missing patch level is `-` in a
    CPE, never `*` (that matched 29 CVEs for OpenSSH 9.8 instead of 1). A distribution's package
    revision makes NVD findings `confidence="upstream"`; an unknown patch level with
    patch-specific NVD entries or bounds becomes `confidence="patch"` unless an applicable
    entry covers all updates of the release. Neither raises alarms or authorizes automatic
    updates. Cache schema changes require a fresh lookup. `vuln_policy.py`: the table from
    findings to alarms, and `auto_update_reason` (all conditions or nothing). The hub sweeps two
    minutes after start and every `vulns.every_hours`.
  - `maintenance.py`: planned windows (config `maintenance:`, once or weekly; naive and weekly
    times use the hub’s timezone, one-off forms send UTC; for devices, groups, sites) and ones started from the app (`Alarms.maintenance`,
    `POST /api/devices|groups/{id}/maintenance`, audited, ended by time in `_watch_alarms`).
    `evaluate` doesn't know of them: `Alarms.update(..., held=)` opens a held device's alarms
    without a message (`Alarm.held`), sends it when the maintenance ends with the problem still
    there, and resolves held ones silently.
    `remind` (config `alarms.remind_hours`): an unacknowledged alarm is said again ("Still: …").
  - `backups.py` + `backups_api.py`: config `backups:` (id → name, device, within_hours); jobs
    report with `POST /api/backups/<id>/ok|fail` and a bearer token (SHA-256 kept in
    `<data>/backups.json`; made on the device page, audited). The Guard lets exactly those two
    paths through without session or CSRF header (`auth_api.REPORT_PATH`); wrong tokens hit a
    `Brake`. `Backups.problems` (warning, `backup:<id>`, on its device): failed, late, never.
  - `logs.py` + `logs_poll.py` + `logs_api.py`: central logs in SQLite (`<data>/logs.db`, opened on
    first use; `logs.days`, `logs.max_lines`). Lines come from OpenWrt's logd (`log read`, the
    login's `logs` ability; cursor = newest time + ids at it, in the `state` table), a FRITZ!Box's
    event log (`fritzbox/client.log_lines`, its local time), Linux hosts' journals (the actor's
    `journal` ability: `act-journal <cursor|->`, JSON entries after the cursor, at most 2000 a
    call; containers logging to journald come as source `container`), containers' own logs
    (`docker-logs <since|->`: every container not on journald, Docker API with timestamps,
    the newest "<s>.<ns>" as the next since), WireGuard presence (`logs_poll.wireguard`: a
    line when a wg-easy client's handshake turns recent or goes quiet for 5 min), syslog over
    TLS (`syslog_tls.py`: Kastellan's own CA, key in the secret `syslog-ca-key`, cert
    `<data>/syslog/ca.pem`; client certificates per device, CN = device id, serials in
    `issued.json`, revocable; the listener's certificate is made at each start; RFC 6587
    framing; lines verified) and the opt-in UDP syslog port
    (`logs.syslog`, off by default; only from known devices' addresses, `verified = False`).
    `hub.log_status` says per device how fetching went. Reading: signed-in admins (demo excepted); a search is audited
    (filters only, never lines; `X-Kastellan-Auto` follow requests aren't). `group=` filters by a
    group's members; answers carry `marks`: alarms opened/resolved and info events of the same
    devices in the same time (`Alarms.log_between`), shown among the lines.
  - `devbackups.py` + `devbackups_api.py`: device configuration backups (OpenWrt `sysupgrade -b`
    via cgi-io's `cgi-backup`, the `backup` ability; FRITZ!Box export with the secret
    `fritzbox-export-<id>`). Files content-addressed in `<data>/device-backups/objects`,
    AES-GCM under the secret `device-backups` (hash as AAD), an index per device; a backup equal
    to the newest version (exact content and ownership, `File.key`) only moves its `checked`.
    Changed FRITZ!Box ciphertext must be kept: it may be a changed password. Exports retain
    their original bytes; downloads bundle the export and its password in a ZIP. Diffs mask
    known UCI credential fields; unknown file formats are hidden (`secret_file`). HMAC
    fingerprints use `group-fingerprints`. Taken by `devbackups.run` every
    `device_backups.every_hours`; failures beyond two periods warn (`devbackup:<id>`).
  - `hubbackup.py` + `hubbackup_api.py`: hub snapshots in `<data>/hub-backup`, using
    `devbackups.Store` with an authenticated index (format 2). The key is derived from a
    generated passphrase or random in key-ring mode, and kept as `hub-backup-key`. A key-ring
    store's `keyring.json` opens with credentials from its latest successful snapshot; a `.kbak`
    carries its selected version's vault. Recovery checks test the saved vault. Config is
    `@config/config.yaml`; sessions, caches and hub backups are
    excluded. Optional SQLite logs use online backup. Files are bounded (128 MiB each,
    512 MiB total); unreadable or changing state fails the snapshot. An operation lock covers
    snapshots, copies, downloads and rotation; browser disconnects don't cancel backups.
    Copies go into `kastellan-<key id>` under each local/SFTP target; only owned objects are
    pruned, and old generations remain. `kastellan restore` stages and validates every file
    before writing; `--force` replaces existing state. Old unsigned stores can still be
    downloaded as authenticated `.kbak` files from the original hub. Recovery checks don't
    unlock rings. External unlock keys must be backed up separately.
  - `dhcp.py`: the `dhcp` check (a DHCPDISCOVER from UDP 68 with a fixed local MAC, offers
    collected; up when the device's address offers), `UNEXPECTED` servers for
    `hub._dhcp_problems` (also a pool at 90 %); router pools: OpenWrt `report["dhcp"]` (leases +
    uci dhcp start/limit, the read group's `uci: [dhcp]` scope), FRITZ!Box LANHostConfigManagement.
    New lease MACs are info events (`newdevice:<mac>`, many at once summed up).
  - Stored logins (`setup_run.stored_logins`/`resolve`/`remember`): a setup's `Login` may name a
    People-ring entry stored for that device (never another), or keep the typed one there;
    `WebLogin.svelte` copies a device's stored web login after the admin's password.
  - `fritzbox/web.py`: the FRITZ!Box web interface's undocumented API (login_sid.lua PBKDF2,
    data.lua: `chan`/`environment` neighbours, `shareWireguard`), port 443, same pin as TR-064.
    `fritzbox.client.confirmed`: AVM's second factor (866) via X_AVM-DE_Auth; its token is in
    the SOAP header for that task only, never concurrent polls or unrelated actions.
  - `fritzbox/client.py` + `fritzbox_api.py`: TR-064 over HTTPS (49443) with a FRITZ!Box user
    (`fritzbox: {user, port}`, secret `fritzbox-<id>`), Digest auth by hand over a TLS connection
    whose certificate is pinned (`<data>/fritzbox/<id>.sha256`, confirmed by an admin at setup:
    `POST .../fritzbox/certificate`, then `/setup`). `read()` → `status.fritz` every 60 s
    (`hub._poll_fritzboxes`), sections that rarely change kept `KEEP` seconds (`_kept`, with
    their errors; `Fritz.changing` ends them); WireGuard (web.py) every `WG_EVERY`, backing off
    after failures (`hub._wg_next`); events for FRITZ!OS updates, enabled port forwards, remote access,
    MyFRITZ!. AVM's 866 (second factor) says to confirm on the box. Tested against a fake box
    with Digest (tests/test_fritzbox.py).
  - `internet.py` + `internet_api.py`: a look every `internet.every` s: router ping, the line as
    the router says (`line_from`: FRITZ!Box connection, OpenWrt WAN interface), outside targets
    (v4, v6 when the hub has it), DNS, the provider's first routers (`hops`, found hourly by
    traceroute, `provider_hops`); `look` places a failure at router → line → provider → internet
    → dns. Line trouble (`Internet.trouble`): 3 reconnects in 24 h, DSL errored seconds over 60
    an hour, little noise margin (warnings). Speed tests by HTTP transfers or `speedtest.iperf3`
    (the image has iperf3). Who uses the line: nlbwmon on OpenWrt (`bandwidth` ability,
    `hub._read_bandwidth`, `clients_view`).
    Samples per day in `<data>/internet/*.jsonl` (31 days), day sums in `state.json` (3 years),
    outages/events/speed tests as JSON lines. `internet:down` (critical, two looks) and
    `internet:slow` (three tests below half the booked speed). The router: `internet.router`,
    else the device at the hub's gateway, the only FRITZ!Box or OpenWrt router. Speed tests
    (`internet.speedtest`, off by default) defer scheduled tests while the line is busy.
  - `wifi.py` + `wifi_api.py`: networks by SSID over OpenWrt (`sync.survey`) and FRITZ!Boxes
    (`lineup`); radios from the routers' reports; scans (`scan` ability: iwinfo scan/survey/
    freqlist/info) kept in `<data>/wifi/scans.jsonl`; `plan()` picks channels per band
    (exhaustive for few radios): our radios apart (by what they hear of each other, else assumed
    near), away from loud neighbours and busy channels, 2.4 GHz on 1/6/11 at 20 MHz, 5 GHz in
    whole blocks of the width (40 MHz tried when 80 can't keep them apart), DFS only when asked.
    Changes go as streamed jobs; OpenWrt uses rollback, FRITZ!Box does not. `wifi_api.run`: a daily scan at
    `wifi.scan_at` (local time, default 04:00; null: only on request), marked done per day in
    `<data>/wifi/last-scheduled`, skipped if the hub starts more than two hours after it.
    Applying a channel plan scans again two minutes later. Clients with their radio link
    (`openwrt.client.assoc_client`: signal, SNR, rates both ways; FRITZ!Box: percent and speed)
    and what is off (`wifi.diagnose`); radios' regulatory `country` (one for all: `country_note`).
    `wifi.findings`: the Wi-Fi page's Findings (sentences with a fix and an `action` kind:
    passwords/encryption that differ, open or WPA networks, WPS, our radios' channels overlapping
    over OpenWrt and FRITZ!Box (meshed radios excepted), FRITZ!Mesh weak links, weak clients,
    no scan, read errors, and each group's `roaming.check` without its channel findings; `alarms`
    names the alarm keys saying the same).
  - `roaming.py`: roaming in groups. `check` (group page): 802.11r with one mobility domain and
    k/v per shared network, channels apart (not for meshed radios), usteer on every member and
    each seeing the others (usteer `remote_info` hosts vs. the members' addresses), memory.
    `assistance` → `POST /api/groups/{id}/roaming` sets 802.11r/k/v member by member (rollback
    each); 802.11v (`bss_transition`) only where hostapd has it (`sync.survey` `can_steer`: LuCI's
    hostapd `eap`, which the basic wpad variants lack with WNM; they refuse the option and send
    nothing), and off again where it is set but can't work. `problems`: usteer stopped where it ran, a member out of view, a small device low on
    memory. usteer's view per AP (`roaming` ability: local/remote_info, connected_clients,
    get_config) in `hub.roaming`, shown on the device page (`GET /api/devices/{id}/roaming`).
  - `signins.py` + `signins_api.py`: the Sign-ins page. SSH (OpenSSH, dropbear), OpenVPN,
    WireGuard presence, FRITZ!Box logins from the logs (`LogStore.matching` with `LIKES`), the
    hub's own from the audit log; per address with flags (`repeated`, `outside-ok`,
    `new-outside`: first success from that outside address this week, `<logs> state
    signins:known-ok`, told once as an info event). Kastellan's own SSH checks (the hub's
    address, attempts) are left out. Paths: a VPN client's address (`hub.vpn_addresses`, from
    wg-easy) marks logins from it as "via".
    The page requires a signed-in admin (except demo); unverified syslog remains labelled and
    cannot establish a successful-login alert. Bounded reads keep the newest matching lines.
  - `vpn_servers.py`: every VPN server seen (OpenVPN instances and WireGuard interfaces on OpenWrt,
    a FRITZ!Box's WireGuard, VPN containers by image), linked to the managed VPN it serves; the
    openvpn-status script also prints `!port/!proto/!dev/!running/!age` per server.
  - `discovery.py`: clients the routers report (FRITZ!Box hosts, OpenWrt leases and Wi-Fi
    clients) that match no device; Devices page "Found on the network", set aside by MAC
    (`<data>/discovery-ignored.json`).
  - `credentials.py`: which secret is used where (`GET /api/secrets`); notification channels may
    name `secret:<name>` for URLs, tokens and SMTP logins (`notify.resolved` at send time).
  - `web_setup.py`: DNS and HTTPS for a site (TLS ends at a proxy in front, never in the hub):
    `web.domain` (the hub), `*.web.domain` (device pages), `web.zone` (default: the domain's
    parent), `web.dns` (the site's DNS server). Checks names (also against a public resolver:
    no split DNS), the proxy's certificates, and the OpenWrt routers' dnsmasq (`rebind_domain`,
    `server=/zone/dns`, read through the read login's `dhcp` scope); every 6 h, warnings for a
    certificate under 14 days and names that don't resolve. `GET /api/web/setup?again=`.
  - `weblogin.py`: "Open signed in": the hub signs in to a device page with a stored People login
    (LuCI form, HTTP Basic); the device's session cookie (LuCI) goes with the ticket, Basic stays
    on the hub (`webproxy.Access.basic`). Tickets and sessions are bound to the configured
    upstream; changing it invalidates access. The password never reaches the browser.
  - Helper versions: OpenWrt setup writes `/usr/libexec/kastellan/versions` (`helper_digests`),
    read each poll (`router_outdated`); the VPN scripts answer `version` (`OpenVpn.outdated_helpers`,
    twice a day). Both show as outdated scripts on Updates.
  - `releases.py`: GitHub’s highest published version (including 0.x prereleases), daily (`hub._check_releases`, not in demo mode, off
    with `check_releases: false`); `/api/health` `newer`, and an info event once per version.
  - Alarms only from fresh results (after `hub.started`, not overdue); folding to the top-most
    down ancestor; `RESOLVE_AFTER` hysteresis; silent resolves for muted, removed or folded
    devices. Failed notifications wait in `hub._outbox` and are retried with backoff for a day.
  - VPNs: `vpns:` (config.py), id -> a VPN of `kind` wireguard or openvpn, any number of each;
    the old one-of-each `vpn: {wireguard, openvpn}` reads as ids `wireguard`/`openvpn` and is
    written as `vpns:` once the app changes them (config_api `PUT/DELETE /api/vpns/{id}`). Per
    VPN: its wg-easy secret (`wg-easy-<id>`; `wg-easy` for the old one), clients under
    `/api/vpns/{id}/...`, CRL state, who is connected and last seen (`hub.crl_state[id]`,
    `hub.ovpn_status[id]`, `hub.seen(id)`), and on the hosts its own settings files
    (`/etc/kastellan-pki-<id>.conf`, `-crl-<id>`; plain names for id `openvpn`). One VPN key for
    all: two restricted VPNs can't share a login on a host (refused by the config).
  - VPN: `vpn/wireguard.py` (wg-easy 15.5's API, Basic auth), `vpn/openvpn.py` (talks only to
    `vpn/scripts/kastellan-pki` and `kastellan-crl`: installed and bound with `command=` to the
    hub's separate VPN key in restricted mode, or sent over a normal login per call in direct
    mode; `install_script()` is both the copyable instructions and what "Install for me" runs), `vpn_api.py` (lists for everyone signed in; changes and downloads for
    signed-in admins, audited; configs hold private keys: `no-store`). `secrets.py`: secrets by
    name (from config.yaml): the key ring's entries once it exists, else files in `<data>/secrets`;
    `OWN`, Kastellan's own secrets (backup keys, the fingerprint salt, the syslog CA key): no
    config name may use them and Keys never shows or edits them.
  - `vault.py` + `vault_api.py`: key rings, `<data>/vault.json`. Each ring: its own key, entries
    AES-GCM under it (id, ring and kind bound in), the ring key only wrapped once per way to open
    it (slots): `password` per admin (scrypt; opened by `hub.unlock_on_signin`), `recovery` (shown
    once), `keyfile` / `env` / `config` (a key read at start: `hub.open_rings_by_themselves`),
    `session` (an admin's browser: made when they give their password while the ring is open,
    wrapped with a key from the session token, which the hub keeps only hashed; its session id
    and end bound in; `SESSION_DAYS`; opened only by `POST /api/vault/unlock-browser`, the
    banner's Unlock when `/api/health` `vault_by_browser`; gone at sign-out, session end or
    expiry, `hub.drop_session_slots`; never for `people`, and never counted as a ring's way
    besides its recovery key). Levels convenient and balanced have it for `kastellan`.
    Two rings from the start: `kastellan` (Kastellan uses its entries by itself) and `people`
    (only `get(id, by=<user>)` from an action a signed-in admin started; background use raises,
    whatever config says). The policy is config.yaml `vault:` (`level`: convenient | balanced |
    strict, per-ring `unlock`, `stays_unlocked`, `background`, `key` for `config`), applied by
    `vault.policy()`; `hub._relock` closes rings that stay open only while an admin uses the app
    (a request within `ACTIVE_WINDOW`, 30 min; requests marked `X-Kastellan-Auto` — a page
    refreshing on its own — don't count) or until idle. Slots follow the policy whenever a ring is
    open: after every unlock and config reload (`hub.sync_slots`); a locked ring's slots are never
    touched, and a ring never ends with only its recovery key. Locked by hand, a ring isn't opened
    by itself again until unlocked or restarted. Slots: scrypt N=2**17 (tests use less, conftest);
    key-file/env/config slots carry an HMAC of their key so syncing needs no scrypt. A login's user
    and notes are sealed with it; names and SSH public halves are plain (checked against the
    private key when a ring opens). Showing a secret needs the asker's own password slot of that
    ring. Background use of a `people` entry: `vault.NotForKastellan` (409). A damaged vault.json:
    `vault.Damaged`, state "damaged", never stops sign-in, start or a reload.
    Locked: `vault.Locked`, 423 in the API, the pollers' error; `/api/health` `vault_waiting` lists
    locked rings Kastellan needs. `ssh.Keys`: hub/act/vpn keys (and next/old) in the `kastellan`
    ring, public halves readable locked; the collector's read-only key stays a file. Making them
    (`/api/vault/create`) takes in `<data>/secrets/*`, those keys and push's key, each read back
    before its file is shredded (after that the rings stay, whatever happens to the files; a
    symlink is only unlinked). A ring key in config.yaml is masked (`config_edit.SECRET_KEY`).
  - VPN last seen: WireGuard from wg-easy (`latestHandshakeAt`); OpenVPN from the server's status
    files, through its OpenWrt login's `openvpn` ability (one fixed script,
    `setup.OPENVPN_STATUS`, paths from the device's own config, client lines only), read with
    the router poll (`hub._read_openvpn`); `openvpn.Seen` keeps the last time per client in
    `<data>/vpn/openvpn-seen[-<id>].json` (status files list only who is connected now).
  - VPN key rotation (`vpn/rotation.py`, restricted mode): the scripts' `add-key OLD NEW` copies
    the line bound to themselves with the new key (options by field, exactly), `keep-key NEW`
    leaves only NEW bound to them (a key added with a leaked one goes too); the same text in both
    scripts (a test checks). A host that can't be reached holds the end and the old key until it
    works (it gets the new key from the old one) or is forgotten (audited, warned). Same stages as
    the hub key's; `Keys` methods take `of="vpn"`. Tested against a fake sshd that enforces
    `command=`.
  - Keys go onto devices only through `access.authorize()`: written as the target user (su), never
    by root into a home that user controls (a symlink there would aim root's write anywhere);
    dropbear (`access.DROPBEAR`: OpenWrt without a running sshd) gets plain lines, OpenSSH
    (also OpenWrt with openssh-server) `from=` (and `restrict`).
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
    comments. A device as text (`/api/devices/{id}/yaml`, `config_edit.device_text` and
    `set_device_text`) replaces only its lines; every other line stays byte for byte. Only signed-in admins (`require_signin`), never in demo mode; nobody saves away
    their own admin role; a version hash refuses saving over a change made meanwhile; the audit
    log gets the diff with password hashes masked.
  - `sshkeys.py` + `sshkeys_api.py`: authorized_keys on devices (listed over the `ssh:` login;
    other users' files read and changed *as them* via `su`), removing a key, and rotating the
    hub key (`Rotator`: next key added + verified with `connect(only=...)` everywhere, then
    `Keys.promote_next`, then the old key removed; state in `<data>/ssh/rotation.json`;
    `Keys.connect` offers current, next and old keys meanwhile). A device that can't be checked
    at the end (gone from config.yaml, another login) stays open until an admin forgets it
    (`POST /api/ssh/rotate/forget`, audited); switching `anyway` keeps the devices without the
    new key as steps "skipped at the switch". `sshkeys.kastellan_keys`: its own keys, never
    removed by hand.
  - `sshsync.py` (+ routes in `groups_api.py`, `GroupSsh.svelte`): a group's authorized_keys or
    known_hosts, fetch → edit → push. A column per device and user (`@login`: its ssh: login;
    others via su, as sshkeys); rows by entry (key type+data; known host by hosts+type). Push per
    column: refused if its version (hash) changed, only the chosen entries removed / missing lines
    appended (every other line kept, bytes too), written as that user next to the file and moved,
    read back, audited per column (`ssh.sync`, fingerprints only). Kastellan's keys are refused.
  - `networks.py`: which networks a device is in (by address: `networks:` cidrs, else its private
    /24; public addresses are the internet; `network:` names them, also several; the hub's
    default gateway is also on the internet), and its `public_ways` (checks by a public name or
    address). `hub._resolve_hosts` resolves names every 5 min. The web app's NetworkMap draws a
    zone per network and devices in two on the boundary.
  - `host/collector.py` + `host/scripts/kastellan-collect` + `host_api.py`: the collector (a
    root-owned read-only Python script on the host, one sudo rule, the `collect` key bound to it).
    `collect()` reads its JSON; `hub._poll_hosts` every minute; `alarms.host_problems` turns a
    report into problems. The script runs on the hosts' python3 with the stdlib only: keep it so.
    `registry.py`: update checks against the registries (anonymous tokens, digests, version tags),
    cached for hours; `docker.from_collector` builds containers from the report with them.
    `setup_run.connect` + `setup_run.stream`: log in once with a typed or stored login and run a
    script (as root).
    Virtual machines: the collector's `vms` section reads libvirt through `virsh` (fixed argv,
    `-r list --all --uuid --name`, `dominfo`, `domblklist`; never the XML), shown as `vms` in /api/devices;
    an autostart VM that isn't running is a warning (`alarms.host_problems`).
    `disks.py`: hourly disk usage per host and mount (`<data>/disk-usage.json`, 30 days), a line
    through the last 7 days (from the last clean-up) gives `host_forecast` (days until full);
    `host_problems` warns under `alarms.disk_days` (same key as the full-disk alarm).
  - `own_services.py`: an OpenWrt device's services over time (`hub.services`): each router
    poll writes their running state as History rows in `<data>/services` (same retention and
    backup rule as the checks), and keeps since when; `/api/series?device=` adds `services`,
    `/api/devices` `services_since`. ServicesTable shows them in the checks' columns, under
    the check they answer (`ownServices.ts`). Containers carry `ports` (published, from the
    collector): `servedBy`/`serves` connect them with checks on those ports.
  - `footprint.py` (`GET /api/hub/resources`, admins; Settings → Resources): the hub process's
    CPU (`os.times`, between reports) and memory (/proc; the peak elsewhere), the machine's, and
    the data store by store, each measured where its owner keeps it (`hub.data`: a temporary
    folder in demo mode). Kept for a time: rate = size over its span, levels off at rate × days;
    kept by count (`counts()`, `generations()`): at that many versions; the audit and alarm logs
    only grow and say when the disk fills. No psutil: the standard library and /proc.
  - `vitals.py`: CPU %, memory, storage (the fullest filesystem) and temperature in one shape
    (`Vitals`) from the collector (`system.cpu`: /proc/stat counters), OpenWrt (`system info`'s
    memory and root, and the read group's `vitals` helper: counters and the hottest thermal
    zone) and a FRITZ!Box's energy monitor (web.py `data("ecoStat")`, every `ECO_EVERY`,
    backing off like WireGuard). `Cpu` turns counters into a percentage between readings.
    `hub.note_vitals` sets `status.vitals` (in /api/devices) and keeps a sample at most every
    `SAMPLE_EVERY` in `<data>/vitals.db` (30 days); `/api/vitals` gives bucketed means for the
    device page's tiles and charts (`VitalsPanel`) — the devices list (`VitalMeters`) uses only
    the current values. Demo: `demo.vitals`, a week filled at start. Disks: the collector's
    `activity` (busy ms from /sys/block/*/stat; `smartctl -n standby -i` for spinning ones,
    which never wakes them) becomes `DiskSample`s (`Busy`: a share of two readings; SMART's
    temperature) in the same database's `disks` table; `/api/devices/{id}/disks` buckets them
    for `DiskActivity` (Storage and hardware). Demo: `demo.disks`.
    `host.report.normalize` must preserve new collector fields (including `system.cpu` and
    container `compose`). Hub backups snapshot `vitals.db` through SQLite when history is
    enabled; WAL/SHM files are never copied or restored separately.
  - `host/actor.py` + `host/scripts/act-*` + `act_api.py`: the actor. `act-dispatch` (as
    kastellan-act, bound to the act key) accepts exact forms and execs `sudo -n python3 -I -S
    <ability> <arg>` (update-container: `<name> [<tag>]`); each ability is `act-common` + `act-<ability>`, root-owned, checks its
    argument again and `/etc/kastellan/act/<ability>.allow`, logs to syslog (also an uncaught
    error, through `sys.excepthook`). act-common holds the patterns every ability shares (`NAME`,
    `TAG`, `UNIT`, `CURSOR`, `SINCE`, `ESCAPES`); a test keeps them equal to actor.py, config.py
    and act-dispatch (standalone, not prepended). `install_script` is
    declarative (abilities not chosen are removed). Containers: update, restart, start, stop
    (`CONTAINER_ABILITIES`, names `NAME`); update and rollback (`REPLACING`) share
    `act-containers` (settings for the new container, the swap that puts the old one back): an
    update keeps the image it ran before as `kastellan-previous:<name>` (the collector reports it
    as a container's `previous`), rollback points the container's reference at it and swaps.
    `update-container <name> <tag>` (`TAG`) goes to another tag of the same image (from its
    registry); act_api passes only a tag the hub found (`update_to`/`update_within`, else 409).
    The new container's label `kastellan.previous-image` names the reference it ran, so rollback
    returns to that tag (and the label flips for going forth); a same-tag update drops it. The
    collector reports `compose` (the compose file) for the app's warning. `skipped.py`: update
    offers an admin said aren't needed (`<data>/updates-skipped.json`, per device and container
    key: image, running tag and offered tag or build digest); `api` marks containers `skipped`, Updates and the
    update notifications leave them out until a newer offer;
    `container-logs` and `unit-logs` (`READING`: no lock, no re-collect after; journal by
    `UNIT` name, allow list `*`) return `lines`, excluded from hub and host audit logs. Renewal uses `allowed` to preserve
    each ability’s targets, including restricted unit lists.
    VMs: vm-start, vm-shutdown (ACPI, waits 3 min),
    vm-reboot, vm-force-off (`VM_ABILITIES`, names `VM_NAME`, allows `+`); each its own allow
    list (`install_script(..., vms=)`). Tests run the real scripts against a fake Docker API and
    a fake `virsh` (tests/test_actor.py). Root scripts take nothing from the environment.
  - Actor steps: an ability says how far it got with `step(text)` (act-common: one
    `KASTELLAN-STEP` line on stderr, never failing: the hub may have gone). `actor.act(...,
    on_step=)` reads them as they come; `POST .../act/{ability}/stream` sends them, then
    `KASTELLAN-DONE {json}`, as a hub task (finishes and is audited when the page leaves). The
    frontend's queue (`lib/runner.svelte.ts`) keeps each job's log and the batch's progress;
    `JobStatus` shows both, the same on Updates, a device's containers and its system updates.
  - Removing them: `collector.uninstall_script` / `actor.uninstall_script` (user, scripts, sudo
    rules; logs stay), run like a setup (`/collect/uninstall`, `/act/uninstall`), then
    `setup_run.remove_device_key`. A setup's unknown host key is trusted from its dialog
    (`/ssh/trust` takes a `port` for devices without `ssh:`).
  - Script versions: the collector's report (`script`) and `act-dispatch abilities`
    (`versions:`) carry a hash of each installed file; `collector.digest` is what the hub's own
    would hash to. Older ones show as `outdated_scripts` (Updates, the device page). Renewing is
    setting up again with a login: the hub's keys may run these scripts, never replace them.
  - `pinned_http.py`: h11 handles HTTP/1.1 framing over pinned TLS; `Pool` keeps idle connections
    for 8 seconds and checks the pin before sending credentials on each new connection. A request
    is sent again on a new connection only when it is idempotent (by default GET/HEAD; ubus
    `READS`, SOAP Get actions and data.lua reads say so): a write never is. Also `Pins`
    (`<data>/openwrt|fritzbox/<id>.sha256`), `ChangedCertificate`, `fingerprint_of_der` and
    `Changing(forget)`, the change lock both clients use. Ubus and Fritz each have a pool;
    `hub.drop_clients` closes them.
  - `openwrt/` + `openwrt_api.py`: OpenWrt devices through ubus over HTTPS (as LuCI). `Ubus.slow`
    keeps reads that rarely change (board, uci dhcp, services.allow, session access, versions)
    `SLOW_EVERY`; `Ubus.changing`, every new login and a lower uptime (`note_uptime`: a reboot or
    an upgrade) end them; the FRITZ!Box's `read()` drops its kept sections the same way. A -32002
    is a lost session only if `session access` is refused too, else a denial (no new login). Logs: the newest `RECENT_LINES` once
    caught up (logs_poll). `setup.py`
    makes an rpcd login `kastellan` (md5crypt hash; the password is the secret
    `openwrt-<id>`) and `/usr/share/rpcd/acl.d/kastellan.json` with one group per ability
    (reboot, wifi, firmware, password, services, openvpn, logs, backup, scan, bandwidth, roaming);
    services are acted on only through `/usr/libexec/kastellan/service` (allow list in
    `/etc/kastellan/services.allow`; rpcd, uhttpd, firewall and network restart only), since rpcd's `rc init` would allow any;
    `client.py` reads/acts (certificate pinned as `<data>/openwrt/<id>.sha256`), Wi-Fi via uci
    apply with rollback + confirm (confirmed only when the radios are up and every network sent
    before is on the air again, iwinfo's SSID; up to ~100 s for a radar channel); each access
    point interface's `on_air`, `off_air_since` kept by `note_off_air`, an alarm
    (`router:<id>:air:…`) after `OFF_AIR_AFTER`; `firmware.py` advises one series step at a time from
    `.versions.json` and builds images via sysupgrade.openwrt.org. `hub._poll_routers` every
    120 s into `status.router`. Tests use a fake rpcd that enforces the real ACL.
    `openwrt/sync.py` + `groups_api.py`: `groups:` read live (no stored desired state), networks
    lined up by SSID, passwords as HMAC fingerprints (secret `group-fingerprints`); changes go
    device by device as streamed jobs (`setup_run.stream_work`).
  - Settings as forms: `config_api` GET /api/settings (secrets as `__keep__`), PUT
    /api/settings/{section} (SECTIONS: top-level keys per form; `_unmask` puts secrets back and
    hashes users' passwords, `_compact` leaves defaults out, `config_edit.set_top` merges into
    the YAML keeping comments), PUT /api/secrets/{name} (write-only). Frontend:
    `lib/settings.svelte.ts` + `SettingsCard`/`SettingsForms`; Groups and VPN use the same.
  - `setup_run.py`: running a setup once with a typed login (sudo, sudo with another password, or
    su with root's password over a pty), streamed as text, the last line `KASTELLAN-DONE {json}`;
    passwords are masked in every output line; `add_device_key` writes collect:/act:/ssh: after.
    The run is a hub task (`spawn`, `hub_task`), not the page's: a browser leaving halfway doesn't stop it,
    and it is audited ("started", then the outcome) either way. sudo is probed (`sudo -n true`)
    so no password lands on a script's stdin; su gets a marker to tell "root now" from a prompt.
  - `history.py`: check results as CSV, one file per UTC day under `<data>/checks/`. The folder
    is the index; retention deletes files. The only place to change if this becomes SQLite.
  - `stats.py`: buckets (uptime share and mean latency per slice), changes (up/down moments)
    and `activity` (the day as a person tells it) computed from history rows, for the web app.
  - `hub.py`: ties it together; reloads config.yaml when its mtime changes, keeps the last good
    config on an error (`config_error`, shown in the app).
  - `demo_more.py`: the demo's FRITZ!Box report, logs, device backups (fixed key), a week of
    internet samples and Wi-Fi scans, seeded into the demo's temporary folder at start.
  - `demo.py`: `KASTELLAN_DEMO=1` swaps in a made-up network with simulated results (a pure
    function of device, check and time) and a backfilled day of history in a temp folder. From
    config.yaml it takes only the users; sign-in state (sessions, audit log, key rings, push)
    stays in the real data folder. Used for trying it out and the website.
  - `auth.py`: password hashes (scrypt), sessions (`<data>/sessions.json`, only SHA-256 of the
    tokens, 0600), and `Brake` (5 failed sign-ins per address in 5 minutes). `audit.py`:
    `<data>/audit.log`, JSON lines, append only.
  - `auth_api.py`: `Guard`, plain ASGI middleware in front of /api (HTTP and WebSockets). Users
    and roles come from config.yaml (`users:`); sign-in is off while there are none. Observers
    only read; writes need an admin and the header `X-Kastellan: 1` (CSRF). Use `require_admin`
    in routes that change things, and `require_signin` in anything that hands out access to a
    device (terminal, remote desktop): it refuses while sign-in is off.
  - `webproxy.py`: `WebProxy`, ASGI outside the Guard: `<id>.<web.domain>` forwards (HTTP,
    streaming, WebSockets) to the device's `web:` or first http check, for signed-in admins only.
    The hub's cookie stays host-only: `GET /api/devices/{id}/web` mints a one-time ticket, the
    device name's `/.kastellan/enter` trades it for its own cookie (`Access`: hub session +
    device), re-checked per request and every 15 s on open streams. Requests from another page
    (Sec-Fetch-Site same-site/cross-site, a foreign Origin; WebSockets need their own Origin) are
    refused. Kastellan's cookies and X-Kastellan never reach the device; a device's Set-Cookie
    loses its Domain; Location/Origin/Referer are rewritten only on an exact origin match.
  - `ssh.py`: the hub's SSH keys (hub, vpn, collect, act; with key rings all but the collector's
    read-only key live in the Kastellan ring, else `<data>/ssh/`) and pinned host keys
    (`<data>/ssh/known_hosts`); never trust on first use by itself: `UnknownHost` until an admin
    trusts the fingerprint, `ChangedHost` until someone forgets the old key. No ssh-agent, no
    passwords. `terminal_api.py`: the terminal WebSocket (JSON input/resize in, binary output),
    trust/forget routes; audit open/close, never keystrokes. A device has SSH when it has `ssh:`
    or an SSH check (`terminal_api.ssh_port`: named ssh, or port 22). Without `ssh:` (or when the
    hub key is refused) the socket asks the page for a login (`login-needed`), then takes a typed
    one (this connection only; optionally stored in the People ring) or a stored People entry
    (`vault.get(id, by=<the admin>)`); typed passwords go through a per-address `Brake`; the
    audit says `way` (key, password, stored), never a password. Stored logins are
    `setup_run.stored_logins`/`remember`, the same as setups use. `terminals.py`: the limits
    both the terminal and the desktop keep (idle, longest, per person, re-check) and `watch`,
    which ends a session when its sign-in, rights or device go. A password typed again to
    confirm (revealing, "Open signed in") is `auth_api.confirm_password` (up to `MAX_PASSWORD`).
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
    `/api/changes`, `/api/activity`, `/api/networks`, `/api/vitals`, the not-needed routes
    (abilities, container updates, a device's firmware/reboot/packages), and the web app
    (single-page, index.html fallback).
- `frontend/` (SvelteKit, static adapter, Svelte 5 runes, Tailwind 4, Lucide icons, Geist):
  - `src/lib/state.svelte.ts`: the store; polls devices every 5 s, buckets and changes every
    30 s, while a page uses it.
  - `src/lib/status.ts`: what a state means to a person. `statusOf` maps a device to
    up / degraded / down / offline / waiting: a down device that isn't `always_on` is
    **offline**, not an alarm, and doesn't count as needing attention. `checkStatusOf` does the
    same for one check (a service down on a laptop that is on is down); use it, don't re-derive.
  - `src/lib/attention.ts`: the one list of what needs attention (open alarms, down checks not
    alarmed yet, security updates and firmware out of support) and every count from it:
    `store.attention` has `issues`, `count` (the overview headline = the list's length), `tone`,
    `problemDevices` (the Devices badge = the list's Problems filter) and `updates` (the Updates
    page and badge). Read it; never recount alarms, down checks or updates on a page. An issue
    has an `area` (storage, services, backups, …) and an `href` to the device page's section
    (`#services`, `#storage`, `#software`, `#backups`, `#vulnerabilities`).
  - `src/lib/states.ts`: the State vocabulary, one capitalized word and one tone (good, warning,
    critical, neutral, accent) per state of each kind of thing (devices, containers, VMs, VPN
    servers, backups, sign-ins, disks, the line, updates). Status colour only in the State badge,
    dots, strips, bars and chart marks; never in running text, addresses or numbers.
    `src/lib/glossary.ts`: the words for things (check, service, issue, alarm, …), `count()`,
    `attentionHeadline()`. `src/lib/format.ts`: the one time format (`ago`, `clock` "14:05",
    `date`, `day` "Nov 22, 14:05", `dayClock` for lists, `duration`, `stamp`, `hoursLabel`).
  - `src/lib/components/ui/`: the building blocks every page uses: `PageHeader` (title, one line,
    actions, controls or tabs), `Section` (heading outside the card, count, meta, actions, anchor
    `id`), `KeyValues` (label/value pairs; "Not read · Set up" when missing), `Table` (the thin
    table primitive: column priorities so phones drop columns instead of scrolling sideways,
    row links, expandable rows, group rows; entity tables build on it), `IssueRow` (a problem as
    a sentence with its fixing action), `EmptyState` (empty, unavailable, error), `RangePicker`
    (1 h · 24 h · 7 d · 30 d, in hours), `Tile`, `StateBadge`/`StateDot`. Type and spacing scale
    in `layout.css` (`text-title` 24, `text-section`/`text-body` 14, `text-cell` 13, `text-meta`
    12 px; `h-row` 44 px; `mt-section`).
  - Wi-Fi (`src/lib/wifi.ts`: signal words Good/Fair/Weak, access point rows incl. FRITZ!Mesh
    repeaters, channel use per band, `openFindings`): entity components `WifiFindings`,
    `WifiClientsTable`, `AccessPointsTable`, `WifiNetworksTable`, `ChannelChart`, `ChannelPlan`,
    each with a `device` filter for device pages.
  - Other entity tables on `ui/Table` (one per kind of thing; the same on overview pages and
    device pages): `ContainersTable`, `VmsTable` (actions on the row; the question, the
    job's progress and, below `lg`, the buttons in the Table's `below` line), `VolumesTable`,
    `DisksTable` (`lib/storage.ts`), `WaysInTable`, `RouterClientsTable` (`lib/router.ts`),
    `BackupJobsTable`, `ConfigBackupsTable` (`device` filter), `VulnsList` (`lib/vulns.ts`).
  - Dialogs: `use:modal` (`src/lib/modal.ts`) opens a `<dialog>` and locks the page's scroll.
  - Components: `UptimeStrip` (one bar per bucket, hover tooltip), `LatencyChart` (line + area,
    crosshair), `StatusBadge`/`StatusDot` (a device or check status as `ui/StateBadge`/`StateDot`), `KindIcon`, `Logo`.
  - `NetworkMap`: the devices by network (networks.py): a zone per network, devices in two on the
    boundary (zones ordered so linked ones are neighbours; otherwise inside, "also ..."), the
    ways in from the internet listed in its zone.
  - The device page (`PageHeader`: name, State, maintenance, actions; Status / Setup / Config):
    the Status tab opens with `DeviceAttention` (one line of the device's issues from
    `store.attention`, each linking to its section here: `devicePage.ts` `issueHref`) and
    `DeviceFacts` (KeyValues: kind, model, firmware or kernel, address, up for, connected
    through, site, power, readings with their source and age; "Not read · Set up" once when a
    reading could be set up and isn't). Then sections in a fixed order by kind
    (`devicePage.ts` `sectionsOf`, empty ones hidden): a router Internet (`RouterInternet`,
    FRITZ!Box), Wi-Fi (`DeviceWifi`: the Wi-Fi page's findings and tables filtered to the
    device, from GET /api/wifi, admins; `RoamingInfo`), Network (`NetworkSection
    internet={false}`); an access point Wi-Fi first; a host Vitals, Storage, Services,
    Containers, VMs, Backups; a UPS Power; others Services, Vitals; response time and activity
    last. Anchors: `#services`, `#storage`, `#software`, `#backups`, `#vulnerabilities`,
    `#wifi`, `#internet`, `#network`, `#ups`, `#containers`, `#vms`, `#config-backups`. Services
    is `ServicesTable` with the device's own services from `lib/ownServices.ts` (OpenWrt init
    scripts and failed systemd units under the check they answer, the rest as rows).
  - `ServicesTable` (service, software, status, response, for, uptime), used by the device page
    and `/services`; `ActivityItem` (an up/down or a software update).
  - `UptimeStrip` merges neighbouring buckets (`mergeBuckets`) so a bar keeps ~5 px; it never
    overflows a narrow column.
  - Navigation (`src/lib/nav.ts`): the sidebar groups about ten places (Overview, Devices;
    Network: Internet, Wi-Fi, VPN; Workloads: Services, Containers & VMs; Maintenance: Updates,
    Backups, Logs, Sign-ins; Settings), badges from the attention model (`badgeOf`); the phone's
    bottom bar has Overview, Devices, Internet, Wi-Fi and More (a sheet with the rest; its badge
    carries the Updates count). Routes: `/` overview, `/services` (every tcp/http check on every device), `/devices` (list, or networks with `?view=graph`; search and filters
    apply to both, the networks view fades non-matches), `/groups`, `/updates` (grouped by urgency:
    security and out of support, reboots, the rest, Kastellan's helpers; `updates.ts` `byUrgency`),
    `/vpn`, `/internet` (a sentence and the way out saying where it is slow or down: `internet.ts`
    `diagnose`), `/wifi`, `/logs`, `/backups`, `/signins`, `/containers` (Containers & VMs:
    `?tab=vms`; `/vms` redirects there),
    `/settings` (tabs: forms, `?tab=access` Kastellan's SSH key, `?tab=keys` the key rings,
    `?tab=file` config.yaml; `/config`, `/access` and `/keys` redirect there), `/devices/[id]`. Watching and setting up are split everywhere
    (`Tabs`): a device has Status / Setup / Config (`?tab=setup|config`, Config with a Form | YAML
    switch, `&view=yaml`), the VPN Clients / Setup, Groups Status / Setup; Setup and Config are
    for admins, observers see Status only. Adding a device is an inline form on `/devices`.
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

Tests: one test per behavior, through the public path; exact text only where it is a contract
(a script, an access list, a status, an audit field). `tests/conftest.py` has `admin_client`
(a hub with a signed-in admin), `demo_client` (one demo hub for the session) and `password_hash`
(cached); use them instead of building an app by hand. Patch timeouts and waits, never sleep for
real; the suite should stay around a minute.

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
