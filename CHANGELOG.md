# Changelog

## Unreleased

### Added

- FRITZ!Box Wi-Fi: its neighbour networks and channel use feed the Wi-Fi page's scans and
  channel plan; encryption and hidden networks can be changed; each network's clients with
  signal and speed. FRITZ!Mesh members and links on the device and Wi-Fi pages. Its WireGuard
  connections on the device and VPN pages. Neighbours and WireGuard come from the FRITZ!Box web
  interface's own (undocumented) API, signed in with Kastellan's FRITZ!Box user.
- Kastellan's own backup can open with the key rings: an admin's password (as it was at that
  backup) or the Kastellan ring's recovery key, with no passphrase to keep. The passphrase stays
  for hubs without key rings. `kastellan restore` asks for whichever the backup needs.
- DHCP: a `dhcp` check asks the network for an address like a new device and is up when the
  device's own server answers; another DHCP server answering raises a warning. OpenWrt and
  FRITZ!Box show their pool and leases, warn when the pool is nearly used up, and a device that
  gets an address for the first time is notified once.
- Stored logins: setups (collector, actor, access, OpenWrt) and Renew can use a login stored
  in the People key ring for that device, and keep a typed one there. A device with a web
  interface gets "Login" next to it: its stored logins, the password copied after typing your
  own again, never shown.
- Updates lists abilities Kastellan offers that a device's actor or OpenWrt login hasn't been
  given yet, with "Set up" and "Not needed".

### Upgrading

- OpenWrt logins: set them up again to read the DHCP pool (the read group may now read the
  `dhcp` configuration); without that, the leases show but not the pool size.

### Fixed

- Stored logins resolve before setup validation; concurrent update dismissals are preserved.
- FRITZ!Box confirmation requests carry AVM's token; unreadable WireGuard data stays unknown.
- Older hub-backup downloads keep their own recovery credentials. Recovery checks test the
  saved key ring; failed snapshots don't replace it.
- DHCP alarms expire, respect configured servers, and ignore stale pool readings. Concurrent
  checks share one probe; DHCP never appears as an internet entry point.
- Wi-Fi readings refresh after edits and config reloads. Stored-login dialogs fit phone screens.

- Setting up the actor with sudo or su failed with "Argument list too long" (the script grew
  past Linux's 128 KB per argument): large scripts go compressed.
- A FRITZ!Box's settings export waits for its second factor when asked from "Back up now"
  (press the button on the box); the daily backup says why it can't, instead of warning.

- OpenWrt 25.12: radios and Wi-Fi clients are read through iwinfo, since `network.wireless
  status` refuses calls over HTTP there (the session field uhttpd adds); Wi-Fi changes check
  the radios the same way. Radios that report `hwmode` (OpenWrt 22.03) count as 2.4/5 GHz.
- Device header: actions wrap within half the width instead of squeezing the name; Linux
  hosts with an actor get Reboot at the top.

## 0.10.0 (2026-11-21)

Kastellan's own backup, central logs, device configuration backups, FRITZ!Box accounts,
internet monitoring and a Wi-Fi page.

### Upgrading

- Make the hub backup's passphrase on the Backups page (shown once: write it down) and add a
  place for copies away from the hub (Settings → Kastellan's own backup).
- Set up OpenWrt logins again and tick the new abilities (logs, backup, scan); existing logins
  keep exactly what they had.
- For hosts' journals and container logs, set the actor up again and tick "Read the host's
  journal" and "Read every container's log".
- A FRITZ!Box gets its own Kastellan user from its Setup tab.

### Added

- **Hub backups:** encrypted configuration and state, local or SFTP copies, offline restore,
  and passphrase/recovery-key checks. Copies are isolated by passphrase; restoring validates
  the complete backup before replacing state. Optional logs use consistent SQLite snapshots.

- **Logs:** searchable router events, Linux journals, Docker logs and WireGuard presence,
  with live follow, retention limits and opt-in UDP or client-authenticated TLS syslog. Reading requires a signed-in admin; UDP messages
  from known device addresses remain marked unverified.
- **Device backups:** daily and manual OpenWrt configuration archives and FRITZ!Box exports,
  encrypted storage, deduplication and masked comparisons. FRITZ!Box downloads include the
  export password. The Backups page also lists reporting backup jobs.
- **FRITZ!Box accounts:** pinned HTTPS/TR-064 access for line status, Wi-Fi, clients, port
  forwards, events, exports, reboot and firmware updates.
- **Internet:** connectivity checks, outages, availability history, router traffic counters
  and opt-in speed tests with latency under load.
- **Wi-Fi:** networks across OpenWrt and FRITZ!Box, scans and channel suggestions. Changes
  require approval; OpenWrt uses rollback. Optional daily scans default to 04:00 hub time.
- Settings forms for internet checks, scans, logs and device backups.

### Fixed

- Missing patch levels are marked uncertain when NVD distinguishes affected updates, including
  patch-bounded ranges. These findings cannot raise alarms or authorize automatic updates.
  Duplicate CVEs retain firm evidence; legacy caches require a fresh lookup.
- Notifications distinguish unknown patch levels from possible distribution fixes.
- Long service errors stay within their columns; refresh buttons and alarm times align.

## 0.9.0 (2026-11-21)

Maintenance windows, alarm reminders, disk forecasts, backup reports, container rollback and
logs, and release notices.

### Upgrading

- To use rollback, container logs and service journals, open device Setup and select the new
  **actor** abilities. Renew on Updates preserves existing permissions. Renewing the update
  script enables retaining the previous image for subsequent updates.
- Renew outdated collectors so they report each container's previous image.

### Added

- Maintenance windows for devices, groups or sites: once, weekly, or started from a page.
  Problems remain visible; notifications wait until maintenance ends.
- Optional reminders for unacknowledged alarms (Settings → Alarms).
- Disk-full forecasts from hourly usage, with warnings below 14 days by default.
- Container rollback through the actor’s new `rollback-container` ability. Updates retain the
  previous image; stored data and database migrations are not rolled back.
- Container logs and service journals through new read-only actor abilities. Audit logs
  record who read what, never the content.
- Backup job reports (Settings → Backups): token-authenticated `/ok` and `/fail` calls;
  missing successes and failures raise alarms.
- Daily Kastellan release checks, shown on Updates and notified once. Disable with
  `check_releases: false`; no inventory or credentials are sent to GitHub.
- Collector/actor renewal from Updates, preserving each ability’s permissions.
- “Read again” for host health, containers and VMs.

### Changed

- Device Setup uses cards and dialogs; VPN setup choices are clearer buttons.
- OpenWrt reboot and firmware actions moved to the device header; Wi-Fi settings to Setup.
- Container rows show their latest action and refresh immediately after changes.
- Registry lookups in progress say “checking…” instead of “can't check”.

### Fixed

- OpenWrt with OpenSSH uses its authorized_keys path, including VPN key rotation.
  Access instructions distinguish OpenSSH’s source-address restriction from Dropbear.
- Rollback runs through SSH; replacement checks inherited image health checks.
- Script renewal refuses when existing permissions cannot be read. Log permissions include
  container targets; log output stays bounded.
- Backup reports preserve report order, reject oversized bodies and fail on storage errors.
  Removed or reassigned jobs lose their tokens; token responses cannot be cached.
- Maintenance holds existing alarms and notification retries; one-off times retain their timezone.
- Nested setup dialogs restore page scrolling correctly. Runtime SSH keys stay out of Git and
  Docker build contexts.

## 0.8.0 (2026-11-21)

UPS and VM management, multiple VPNs, update queues, and security fixes.

### Upgrading

- Reinstall the **VPN server helper** through VPN Setup in restricted mode to receive the CRL
  security fixes. OpenSSL is now required. Direct mode sends the current helper on each call.
- Reinstall the **collector and actor** on hosts to enable UPS/VM reporting and the new
  container/VM actions. Choose allowed actions and targets during setup.
- Existing `vpn:` configuration still reads; editing it in the app migrates it to `vpns:`.
  The former Config and Access URLs redirect to Settings.

### Added

- UPS devices read through apcupsd or NUT: battery, runtime, power alarms and `powered_by`
  links. Devices affected by a power outage share the UPS alarm.
- Libvirt VMs: state, resources, autostart warnings, start/shutdown/reboot/force-off actions,
  and `runs_on` links between monitored guests and hosts.
- Containers and Virtual machines pages; container start/stop and batch updates.
- Update queues with live steps and per-job logs, sequential work per host and parallel work
  across hosts. A failed update prevents a queued reboot on that host.
- Multiple WireGuard/OpenVPN instances, separate client state and setup per VPN.
- Terminal login with a one-time password or stored People-ring entry when no hub key works.
  Stored logins are restricted to their assigned devices; host trust precedes password entry.
- Status/Setup/Config tabs on device pages, Status/Setup on groups, Clients/Setup on VPNs;
  Settings combines forms, the config editor and the hub's SSH key.

### Fixed

- Security: the web proxy never retains device cookies in its shared HTTP client or forwards
  another browser's login. Helper removal deletes SSH keys as their owner, avoiding root
  deletion through a user-controlled symlink.
- Security: CRL installation checks OpenSSL's verification message as well as its exit code,
  requires OpenSSL, and rejects rollback by issue time and CRL number.
- Alarm folding preserves descendants and handles mixed VM/network dependencies. Stale or
  missing UPS readings cannot report healthy power or silently resolve battery alarms.
- Actor target validation distinguishes container names from VM names; failed stops report
  failure. Collectors bound slow UPS/VM queries and tolerate oversized report values.
- Config writes preserve the original after atomic-replace errors, except for the required
  bind-mount fallback. Device deletion clears VM/power links; cyclic dependencies and invalid
  IDs/history ranges are rejected.
- VPN renewal and key rotation tolerate removed VPNs; server instances have separate clients.
  Setup replaces stale key bindings, and forms preserve hidden options and reject stale saves.
- Update streams survive quiet periods, apt output tolerates invalid bytes, and reboot refuses
  active package operations. Response charts fit phone screens and handle zero latency.
- UPS and VM links: a short time on battery no longer takes over devices that were already
  down (only what went down in the outage folds into the UPS's alarm); a muted UPS folds
  nothing; apcupsd's "SHUTTING DOWN" and NUT's FSD/OFF count as low battery; a host with
  apcupsd installed but NUT in use reads NUT; a UPS's badge is the worse of its reading and its
  own checks; a UPS may have its own address without a "read by" host; a device without an
  address can't be set up over SSH (that would reach the hub's own machine); the host reading a
  UPS can't be deleted from under it; an old host report says nothing about its VMs; NUT gets
  on-battery times from the hub; a UPS without an address shows no empty response-time panel.
- README and website shortened, security claims corrected, examples validated and demo
  screenshots refreshed.

## 0.7.0 (2026-11-21)

Key rings: Kastellan's secrets and people's logins encrypted, opened as securely as you choose.
And the actor works with today's Docker again.

- Key rings (Keys): passwords, tokens and Kastellan's SSH keys that can change things,
  encrypted, with nothing of them (nor the keys that open them) on disk as plain text. Rings
  for Kastellan's own secrets and for people's logins (Kastellan never uses those by itself), and
  any you add; entries move between them. Each ring opens its own ways: an admin signing in, a
  key file, an environment variable or a key in config.yaml, and a recovery key shown once.
  How secure is a level (convenient, balanced, strict) or your own choice per ring, including how
  long a ring stays open (until a restart, while an admin is signed in, or until idle). While a
  ring Kastellan needs is locked, whatever needs its secrets waits and says so (checks and the
  collector go on). Making them takes in the secrets and keys kept as files so far. A login's
  user name and notes are encrypted too; showing a password takes your own password and hides
  it again after a minute; Lock holds until you unlock; a damaged key ring file never stops a
  sign-in.
- Fixed: updating and restarting containers failed on Docker 29 ("client version 1.41 is too
  old"): the actor now asks the daemon which API version to speak, from Docker 1.13 (API 1.25)
  to today's (checked up to API 1.52); a pull error in the newer format counts, and a
  container's own MAC address moves to its network where newer Docker wants it. Set the actor
  up again on your hosts to get it.

## 0.6.0 (2026-11-21)

SSH files kept alike across a group; the collector and actor removed or set up again from the
page; a host key trusted where a setup meets it. And a review of 0.5.0's new parts, its findings
fixed.

- SSH across a group: on the Groups page, the members' authorized_keys or known_hosts side by
  side, for one or more users per device (its login, root, a name): fetch, mark keys or hosts to
  add where missing, change or remove everywhere (or per device), review, push. A file changed
  since it was fetched is left alone (checked again on the device right before writing); every
  other line stays; Kastellan's own keys are neither removed nor added there; a linked file is
  written where it points. The review warns when root gets new keys, and when OpenWrt's dropbear
  may ignore a key's options.
- The collector and the actor can be removed from a host (with your login, like setting them
  up: their user, scripts and sudo rules go, and the entry leaves config.yaml), or set up again.
- A setup that meets a host Kastellan doesn't know yet offers to trust its key right there
  (shown to compare), then goes on; a changed key is still never one click.
- Device pages through Kastellan: Kastellan's sign-in cookie stays on its own name; each device
  page gets its own (a one-time ticket from "Open web interface"). A device's page can't send
  requests to another's; a closed tab, or a device that never answers, no longer holds a
  connection; access ends with the sign-in.
- Push: a browser that fails no longer makes the others get it again; at most 10 browsers per
  person; only the browsers' own push services are accepted; one slow channel no longer holds
  the others back; a refusal that may be the hub's own (a clock far off) no longer drops
  subscriptions; clicking a notification always opens the page.
- The VPN key's rotation ends with only the new key on each host (a key added with a leaked one
  is removed too); a skipped host keeps the rotation open; lines are rebuilt with exactly their
  options, and authorized_keys is never left half written. OpenVPN: no stale "connected";
  servers in /etc/openvpn/*.conf are found, client configs aren't taken for servers.
- A device's config.yaml tab: a commented-out line no longer cuts it short; it can't change
  other devices; it never keeps another device's text when going from one device to another.
- DNS checks: single names use the search domains; a router that drops AAAA questions isn't
  "down" and doesn't slow the check; `expect: 200` on a dns check is refused instead of
  crashing the config load.
- After setting up the actor again, Updates stops saying it's older right away.

## 0.5.0 (2026-11-21)

Device web pages through the hub, push notifications, sites and DNS checks, the VPN's own key
rotated and its clients' last contact; a code audit with many fixes.

- Device web pages through Kastellan: with `web: {domain: ...}` and one wildcard DNS name, each
  device's page opens at `<id>.<domain>` for signed-in admins; Kastellan's cookie never reaches
  the device (you sign in to the device itself).
- Push notifications to browsers and the installed app (needs the hub behind HTTPS): a `push:`
  channel per user, turned on per device from the bell next to sign-out.
- Sites: `site:` per device (and names under `sites:`); the device list groups by site, alarms
  name the site of devices away from home.
- DNS checks: `dns: <name>`, with `expect:` addresses and a `server:` to ask.
- The VPN: when each client was last seen (WireGuard from wg-easy; OpenVPN from the router's
  status, a new read-only OpenWrt permission); Kastellan's VPN key rotated like the hub's
  (restricted mode; set the VPN scripts up again first).
- A device is edited on its own page (Edit tab), and its part of config.yaml as text (a second
  tab), e.g. to drop `docker: wud:`; the rest of the file stays exactly as it was.
- The VPN is back in the main menu; Devices, Groups and Services have clearer icons.
- Kastellan's scripts on hosts (collector, actor) say which version they are; older ones show on
  Updates and the device page, to be renewed by setting them up again.
- The setup cards on a device page sit side by side on wide screens, with smaller text.
- Fixed: every SSH login failed in a container whose user has no name ("No username set in the
  environment").
- Fixed (audit): the app's polling restarted itself on every refresh (many requests a second);
  the actor setup asked the host again and again; a reconnected terminal showed "session
  ended"; Groups couldn't save after a reload; a down service on a laptop that is on showed as
  offline; stale vulnerability caches lost their "exploited" marks; official Docker images from
  the collector were never looked up; a rebuild of the same tag counted as fixing a CVE; alarms
  never got milder; signing in as yourself reset the brake on guessing another password; apt
  could be killed by a closed pipe; a reboot could interrupt an upgrade; uci changes could leak
  into the next one; and more (see the commit).
- The sidebar says "Connecting to the hub" while the page loads, not "Hub not reachable".

## 0.4.0 (2026-11-21)

Settings, groups and the VPN set up from forms instead of YAML; everything waiting to be updated
on one page.

- Settings as forms: the Config page has the checking interval, history, heartbeat, alarms,
  notifications, vulnerability lookups, networks and users as forms (each part saved on its own,
  comments elsewhere in the file kept, tokens and password hashes never sent to the page); the
  file itself on a second tab.
- Groups of any devices, made and changed on the Groups page; the Wi-Fi, mesh and root-password
  part shows for the OpenWrt members.
- The VPN set up from a form (wg-easy's password kept as a secret); the VPN left the main menu:
  the Services page lists it, with its clients.
- Updates: one page with everything waiting (containers, system packages, reboots, OpenWrt
  firmware), and its action where the device allows it.
- Reachable from the internet per service (a port forward, a proxy), not only per device:
  vulnerabilities in what that service runs count as urgent; it shows as a way in.
- The UI: button labels without "…", checkboxes and radio buttons aligned and alike, copy buttons
  inside their boxes, compact choices, readable widths, fold-outs with a chevron.
- A failed SSH connection says what failed and on which port.

## 0.3.0 (2026-11-20)

Looking after things, not only watching them: Linux hosts through narrow doors you open
yourself, and OpenWrt routers and access points through their own API. Tested against fakes that
behave like the real thing (a fake Docker API, a fake rpcd enforcing the real access list), not
yet on every real device.

- OpenWrt: routers and access points through their own API with a login of Kastellan's that may
  only what you tick (read always; reboot, Wi-Fi settings, firmware, chosen services). Their state, Wi-Fi clients,
  services (running, at boot), package updates; an alarm when their release no longer gets security fixes. Wi-Fi changes
  with the device's own rollback; firmware one release series at a time, built for the model by
  OpenWrt's image builder, tested on the device, settings kept.
- Groups of OpenWrt devices (`groups:` in config.yaml, the Groups page): their Wi-Fi networks
  lined up by name, where passwords or encryption differ or a network is missing; a network's
  name, password and encryption, the mesh (set where it is, added on a chosen radio where it
  isn't) and root's password changed on every member at once, each with its own rollback.
- Setting up a door (collector, actor, SSH access) in a dialog: your login, root through sudo
  (also with another password) or su with root's password, the output live, then done; or the
  commands to run yourself.
- System updates: pending ones read from apt (a dry run), installed through the actor's new
  ability (apt upgrade, keeps config files, removes nothing).
- README rewritten, short; the website shows hosts and an up-to-date roadmap.
- The collector: how a Linux host is doing (space, RAID, disk health, temperatures, failed
  services, updates, reboots, Docker containers) through one read-only door: a user that may run
  one root-owned script, and a key bound to it. Installed from the device page. Alarms for full
  disks, a degraded RAID, failing disks, heat, failed services.
- Container updates asked of the registries directly (no WUD needed).
- The actor: what Kastellan may change on a host, installed ability by ability (update or
  restart chosen containers, reboot). One root-owned script and one sudo rule per chosen
  ability, a dispatcher the act key is bound to; updates roll back if the new container fails.
- Heartbeat: a dead man's switch Kastellan calls every minute.
- The device form: fields in even rows, a service's default name as its placeholder, and the
  page behind no longer scrolls while it is open.
- `kastellan hash-password` takes a password under 10 characters after a warning (or with
  `--short`) instead of refusing it.

## 0.2.0 (2026-11-20)

Changing things from the app: the config, devices, SSH keys. Editing needs sign-in on (add a
user to config.yaml once) and a config folder the container's user can write. SSH key listing,
removal and rotation are tested against a real shell, not yet on real devices.

- "Last 24 hours" tells the day: outages and how long, short drops counted, many devices
  unreachable at once as one entry (the hub or its network, not each device), when devices that
  may be off were online, and software updates.
- The graph shows networks: a zone per network (home, other sites, the internet), devices in
  two networks on the boundary between them (the router between home and the internet, found
  by itself on Linux with host networking, as compose.yaml runs it), a globe on what can be
  reached from the internet, and the ways in listed. `networks:` names networks; `network:`
  puts a device in one or several.
- More notification channels: Gotify, Telegram and e-mail (SMTP with TLS or STARTTLS).
- SSH keys: every device's authorized_keys on its page (Kastellan's own marked), removing a
  key; rotating Kastellan's key from the new Access page (added and tried everywhere first,
  then switched, then the old one removed).
- An unidentified device (kind other) shows a question mark instead of a box.
- Change the config in the app (signed-in admins): add, edit and remove devices and their
  services in forms, and the whole config.yaml in an editor. Comments kept, checked before
  saving, conflicts refused, backups kept, every change audited.
- Certificates: every https check shows its certificate (valid or why not, when it ends, the
  issuer); alarms for public names (critical when not valid, a warning 14 days before it ends),
  `tls: true/false` per check. A check by name shows the address it led to, so each way into a
  service (at home, by its public domain) can be watched.

## 0.1.0 (2026-11-20)

The first release. Early: the checks, history, alarms and vulnerability lookups run on a real
home network; the terminal, remote desktop, VPN and SSH access setup are new and have seen few
real devices yet.

- Checks: ping, TCP and HTTP per device, on an interval; config.yaml reloads by itself.
- History as daily CSV files, with retention; state survives restarts.
- Web app: overview, device list, device pages with uptime and response time; devices as a list or a
  graph of how they are connected.
  Light and dark, phone and desktop.
- Services: every service with the software and version it reports (SSH banners, HTTP
  headers and titles), status, how long, and uptime; software updates appear in the activity.
- Sign-in with users and roles (admin, observer) from config.yaml, sessions, a brake on
  password guessing, and an audit log.
- Terminal in the browser over SSH (xterm.js), with the hub's own key and pinned host keys.
- Remote desktop (RDP, VNC) in the browser through guacd; not yet tested against real desktops.
- Drivers, starting with the FRITZ!Box: model, name and FRITZ!OS version, firmware updates in the activity.
- Docker containers and their updates from WUD, honest about what can't be checked.
- Alarms and notifications (ntfy, webhooks): devices and services down, updates; one alarm for a
  device and what is behind it; acknowledge and mute.
- Known vulnerabilities from NVD, OSV and CISA KEV for software, firmware and container images;
  alarms by exposure; opt-in automatic container updates only for confirmed serious findings on
  internet-facing devices.
- VPN access: WireGuard clients through wg-easy, OpenVPN through easy-rsa, with config downloads
  and QR codes. OpenVPN in two modes: restricted (small scripts bound to their own key) or direct
  (over the normal login); the VPN page checks the hosts, shows the install commands and can run them.
- SSH access setup from the device page: Kastellan's key for an existing user, or a user of its own
  with sudo; the commands to copy, or run once with a login and password that are never kept.
- Docker image for amd64 and arm64: `ghcr.io/andiwand/kastellan`.
- Demo mode (`KASTELLAN_DEMO=1`): a made-up network with a day of simulated history.
- Project website (`website/`).
