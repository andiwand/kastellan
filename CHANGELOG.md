# Changelog

## Unreleased

## 0.15.0 (2026-11-22)

A reworked interface: one list behind every count, an overview as a status board, Wi-Fi made
over, one table per kind of thing, device pages ordered by what the device is for, grouped
navigation and a phone bar; disks over time; down services alarm urgently unless a check says
otherwise.

### Upgrading

- Set up the Linux collector again (Updates lists it) so containers say which service they
  serve and disks report their activity.

### Changed

- The interface, reworked for one way of showing things:
  - Every count (the overview, the sidebar's badges, device pages) comes from one list of what
    needs attention, so they agree; one set of state words with one look; status colour only
    in dots, bars and charts; one time format.
  - The overview is a status board: what needs attention with its fix, one row per area
    (internet, Wi-Fi, storage, backups, sign-ins, updates), devices with problems first.
  - Wi-Fi: findings first, then tables of clients (the FRITZ!Box's too), access points and
    networks, a channel chart per band and channel suggestions as a before/after table.
  - One table per kind of thing (containers, VMs, volumes, disks, backups, ways in,
    vulnerabilities), the same on its page and on a device's page; on a phone, tables drop
    columns instead of scrolling sideways.
  - Device pages: the facts in one block, a line with the device's problems, and sections in an
    order that starts with what the device is for.
  - Updates are grouped by urgency with clear verbs; the Internet page says where slowness is.
  - The sidebar is grouped; Containers and VMs share a page, Keys moved into Settings (old
    addresses redirect); a phone shows five places and More.
- A service that is down now raises an urgent alarm, as its red badge says. Each check can say
  otherwise (`alarm: warning` or `alarm: off`, also in the device form).
- Those who may only look see the Wi-Fi as a phone nearby would: networks, access points,
  channels and what they hear, without clients or password comparisons.

### Added

- A device's own services (an OpenWrt device's init scripts) are kept over time like checks:
  each has its status, since when, and its uptime in the same row layout as the checks, under
  the check it answers (dropbear under SSH) or below them. Not started at boot shows grey,
  not red.
- A check served by a container names it, and the container says which check it serves (by the
  ports it publishes).
- Disks over time: each disk of a host shows its day as a strip (asleep or spinning, how
  busy) and opens charts of busy, asleep and temperature over a day, a week or a month. Read
  from the kernel's I/O counters and `smartctl -n standby`, which never wakes a sleeping disk.
- Updates renews an outdated OpenWrt login in place (root's password once), keeping what it
  may do now, as it does for the collector and the actor.

### Fixed

- Renewing the actor from Updates marked its new abilities as not needed.
- OpenWrt renewal reads the live service permissions and refuses to continue if they cannot
  be read, instead of silently removing them.
- Disk read failures no longer look like sleeping disks. History expires after 30 days even
  without other vitals; history errors no longer prevent container refreshes.
- Wi-Fi findings stay distinct for each network; device Wi-Fi refreshes while open. Device
  navigation clears the previous device's history, and backup summaries show failures and
  missing first backups correctly.
- Backup actions and disk activity strips remain visible on phones. Disk charts handle
  failed requests and rapid range changes.

## 0.14.0 (2026-11-22)

Vitals for every device, device pages by topic, "Not needed" for every kind of update, a view
of what Kastellan itself costs, and a full audit: many fixes, less code, faster tests.

### Upgrading

- OpenWrt routers: set up again (Updates lists the outdated script) so the package check no
  longer leaves its lists in the router's memory.
- For CPU use and temperature, set up the OpenWrt login (its new `vitals` helper) and the Linux
  collector again; until then OpenWrt shows its load, memory and storage.
- Set up the actor, the OpenWrt logins and restricted OpenVPN scripts again (Updates lists them):
  their scripts and access list changed with this release's fixes.

### Added

- CPU, memory, storage and temperature readings from Linux, OpenWrt and FRITZ!Box. The device
  page shows 24-hour, 7-day and 30-day charts; the device list shows current values. History
  stays for 30 days in `vitals.db` and is included in hub backups when history is enabled.
- "Not needed" for an OpenWrt firmware step, a wanted reboot and a host's system updates on
  Updates: each stays away until it changes (a newer release, a reboot for other packages,
  other updates); a silenced reboot sends no notification.
- Settings → Resources: what Kastellan costs the machine it runs on (its CPU and memory next to
  the machine's, its part of the disk) and its data store by store: size, growth a day, how
  much is kept, and where each levels off and when; what only grows (the audit and alarm logs)
  says when the disk would be full.

### Changed

- Device pages group readings into Services, Software, Wi-Fi, Network, and Storage and hardware.
- Updates leaves out OpenWrt packages, which come with firmware; Software still lists them.
- Sign-ins: "Worth a look" is a table with columns (from, why, where, as, the counts, last).
- Containers: the buttons have a column of their own on wide screens, so rows line up.
- Passwords have no minimum length any more; only an empty one is refused (sign-in still slows
  down guessing). `kastellan hash-password --short` is gone.
- Sizes and rates read the same everywhere (kB/MB/GB, Mbit/s), and range and filter buttons
  look alike on every page.

### Fixed

- Skip package checks on OpenWrt routers with less than 64 MB usable memory. Use temporary
  package lists and report failed checks as unknown, keeping their errors until a successful check.
- Use one FRITZ!OS version spelling across sources to prevent false firmware-change notices.
- Preserve CPU counters and Compose paths through collector report validation.
- Handle missing and invalid vitals, serialize concurrent samples, close the history database,
  and use consistent SQLite snapshots for backups. Old readings are labelled.
- Keep chart ranges correct when responses arrive out of order. Reset device-specific panels
  on navigation; fit charts, filters and hardware tables at phone widths. Plot negative temperatures.
- Handle unusual service names and keep unrelated DNS lookups separate. Hide service actions
  that the router's access helper refuses.
- Alarms of the hub itself (its backup, its names) were resolved and sent again every minute.
- Saving the config blanked OpenWrt and FRITZ!Box model and firmware until the next poll; a
  removed FRITZ!Box kept showing its last readings.
- Rotating the hub's SSH key counted devices it couldn't check as done; they now stay open until
  forgotten, and devices skipped at the switch are listed.
- Kastellan's key for password fingerprints could be revealed on Keys.
- A rollback trusts the container's own label only for a valid tag of the same image, and an
  actor action that fails halfway is always logged on the host.
- OpenWrt 25.12: a mesh change was always rolled back. A denied call no longer makes a new
  rpcd session; readings kept across a reboot or a firmware update are read anew. A kept
  connection the device closed no longer fails a poll round.
- FRITZ!Box: the action after a confirmation on the box got only what was left of the wait;
  changing Wi-Fi dropped the WireGuard reading until the next read.
- Smaller: an escalated alarm lost the devices behind it, a user name ending in a newline
  passed the check, one bad log entry or VPN handshake time stopped that source, a malformed
  terminal message closed the terminal, and internal version keys showed in Activity.

## 0.13.0 (2026-11-22)

Container updates to newer tags, "Not needed" for updates, and much less load on routers' web
servers (kept connections, rarely changing settings read less often).

### Upgrading

- Linux hosts: set up the actor and the collector again (Updates says where) for updates to a
  newer tag and for the compose hint; until then a newer tag can't be applied from Kastellan.

### Added

- Container tag upgrades, with an alternative within the current major version when available.
  The actor keeps settings and the previous image for rollback. Confirmations identify major
  upgrades and Compose files that need the same tag change.
- "Not needed" hides a container update and its notification until a newer offer arrives.

### Changed

- Router requests reuse pinned TLS connections briefly. Settings that rarely change are cached
  for a few minutes; Kastellan's changes invalidate them. FRITZ!Box WireGuard is read every
  10 minutes, with backoff on failures. OpenWrt log polls fetch 200 lines once caught up.
- Background Wi-Fi page refreshes reuse cached readings; opening the page refreshes readings
  older than five minutes.

### Fixed

- Setup accepts every available ability. Validation errors show the affected fields.
- Updates combines outdated OpenWrt helpers and new abilities in one setup row, with separate
  links when VPN scripts also need renewal. Container controls wrap at phone widths.
- Router actions aren't replayed after a lost response. HTTP framing uses h11; closing a client
  ends its connections, and stale reads cannot repopulate invalidated caches.
- Tag changes apply even when both tags name the same image. Bulk confirmations include major
  upgrade and Compose warnings. Skipped offers belong to the image and running tag.
- Malformed log entries cannot hide a backlog. Replacing a FRITZ!Box login clears its backoff.

## 0.12.0 (2026-11-22)

Sign-ins, VPN servers, provider and line checks, Wi-Fi client diagnostics and roaming checks,
discovery of unknown devices, and device pages opened signed in.

### Upgrading

- OpenWrt logins: set them up again (Updates says which) for the new helper scripts and two new
  abilities: bandwidth per device (needs nlbwmon on the router) and Wi-Fi roaming (usteer).
  From now on Updates also says when a router's helpers are older than the hub's.
- OpenVPN in restricted mode: install the PKI and CRL scripts again (VPN → Setup) once; they
  now tell their version, and Updates says when they are older.
- Device pages behind a proxy: `web:` takes `zone` and `dns` (the site's DNS server) for the
  new check under Settings → Names & HTTPS.

### Added

- Sign-ins: SSH, VPN, router and hub events grouped by source address, with repeated failures
  and new outside successes flagged. Known WireGuard client addresses suggest VPN paths.
- VPN: every server on the network (OpenVPN instances and WireGuard on OpenWrt, a FRITZ!Box's
  WireGuard, VPN containers), whether it runs, how it listens and who is connected.
- Logs: filter by group; alarms and events of the same devices show among the lines.
- Internet: the provider's first routers are checked with every look (an outage there is the
  provider's); reconnects, DSL errors and little noise margin raise warnings; speed tests can
  use an iperf3 server; who uses the line, from nlbwmon on OpenWrt.
- Wi-Fi: clients with signal, noise and rates and what is off about each link; the radios'
  country; a scan two minutes after a channel plan is applied.
- Group roaming checks: 802.11r/k/v, channels, usteer peers and memory. Assistance aligns shared
  personal networks with matching credentials. Alarms flag stopped usteer and missing peers.
- Devices found on the network (router client lists) that Kastellan doesn't know: add them or
  set them aside.
- Container updates: the newest version within the running major one when a new major is out.
- HTTP checks can require a text on the page (`contains:`).
- Notification channels may name `secret:<name>` for URLs, tokens and SMTP logins; Keys lists
  secrets that are named but not set.
- Device pages: "Open signed in" with a stored login (LuCI, HTTP Basic): the hub signs in, the
  password never reaches the browser.
- Settings → Names & HTTPS: the hub's and device pages' names (the same as on the internet), the
  proxy's certificates, the routers' rebind exception and forward for the zone; warnings when a
  certificate nears its end or names stop resolving.

### Fixed

- The hub's answers carry isolation headers, and its API refuses requests from device pages on
  sibling names; device pages get their own agent cluster.
- Device forms preserve HTTP text conditions and accept DHCP checks.
- Stored web sessions stop when the configured device address changes. Notification references
  cannot expose internal encryption keys; invalid references leave other channels working.
- Sign-in reports retain the newest events and flag unverified syslog. Only verified successes
  trigger outside-login alerts; reading sign-ins requires an admin login outside demo mode.
- Traceroute uses the supported library arguments; cancelled speed tests stop their iperf3 process.
- Roaming changes check network identity before applying. Stale readings cannot raise roaming
  alarms or show current bandwidth; failed VPN checks retain known renewal notices.
- Demo discovery preferences stay in temporary storage.

## 0.11.0 (2026-11-21)

FRITZ!Box Wi-Fi, mesh and WireGuard, DHCP monitoring, logins kept in the key rings, and
Kastellan's own backup opening with the key rings.

### Upgrading

- OpenWrt logins: set them up again to read the DHCP pool (the read group may now read the
  `dhcp` configuration); without that, the leases show but not the pool size.
- Set up the actor again on Linux hosts to give it the journal and container-log abilities
  (Updates lists them); FRITZ!Box backups need the box's button pressed, or that confirmation
  turned off in FRITZ!OS.
- The `dhcp` check needs the hub on the network it asks (host networking) and allowed to use
  UDP port 68 (root).

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
