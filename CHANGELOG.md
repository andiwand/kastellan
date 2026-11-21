# Changelog

## Unreleased

- Security: device web logins are never kept in the proxy's shared HTTP client or reused for
  another browser. Removing a collector or actor removes its SSH key as its own user, so a
  symlink in that user's home cannot direct root's deletion elsewhere.
- Security: the OpenVPN CRL helper checks the signature verification result, including on
  OpenSSL/LibreSSL versions that return success for a bad signature. Older CRLs are refused by
  issue time and, when present, CRL number (including large hexadecimal numbers). OpenSSL is
  required. **Set the VPN server's helper up again to apply these checks to existing hosts.**
- UPS reports stop claiming a current state when stale; missing readings report no contact,
  and unknown readings don't resolve battery alarms. Slow UPS tools share a time limit and
  preserve other readings. Alarm folding keeps all descendants, including VMs, and mutual
  dependencies such as a virtual router and its host keep one visible alarm.
- Actor permission lists keep valid numeric container names separate from VM names; actions
  refuse unexpected targets and report failed stops accurately. Removing devices clears their
  VM and power links. Invalid IDs and cyclic parent or VM-host chains are refused.
- A failed atomic config replacement preserves the original file; only a bind-mounted file
  uses the overwrite fallback. Oversized numbers in host reports and invalid history ranges
  are handled without exceptions. Device response charts fit phone screens and zero latency.
- UPSes (`kind: ups`): read through the host they're plugged into (apcupsd or NUT, by the
  collector; no address needed), with their own page: state, charge, runtime left, load,
  voltages, the last time on battery and why, the battery's age. Devices say which UPS powers
  them (`powered_by`, or from the UPS's page). Alarms: on battery (critical when low, below
  30 % or 5 minutes), a battery to replace, no contact; what goes down while its UPS is on
  battery waits in that alarm, which names what runs on battery. Set the collector up again
  on the host to read it.
- A device can be a virtual machine on another (`runs_on: {host, vm}`, or "A virtual machine
  on another device" in its form, with a suggestion when a VM is named like it): its page
  shows the VM's state and actions, the VM list shows the device, the network graph says "on
  <host>", and when the host is down the device waits in its alarm; a VM that is shut off is
  named in the device's own alarm.
- Containers (a new page): the Docker containers on every host, grouped by host, with
  filters (running, stopped, with updates), "Update all" across hosts, and starting and
  stopping them (two new actor abilities) besides restart and update.
- Virtual machines (a new page): libvirt/KVM VMs on every host, read by the collector (state,
  vCPUs, memory, disks, whether it starts with the host); started, shut down, restarted or
  forced off through four new actor abilities, each on the VMs you choose. A VM set to start
  with the host that isn't running is a warning. Set the collector and the actor up again on a
  host to get them.
- Watching and setting up are apart: a device has Status, Setup (how Kastellan reaches it, what
  it may do) and Config (a form, or its YAML); the VPN has Clients and Setup; Groups have
  Status and Setup. Observers see the status only.
- Config is now Settings, with Kastellan's SSH key (formerly Access) as a tab, in short cards;
  old links still work. Making the key rings is explained in a sentence, the rest on request.
- VPNs, reviewed: setting a VPN up again on a login another one used replaces its key line
  (the first match counts, so the old one would win); the daily CRL renewal goes on when a VPN
  is removed meanwhile; several OpenVPN servers on one router are told apart (`instance:`),
  each VPN counting only its own clients; a rotation started before several VPNs, or with no
  restricted OpenVPN left, still ends; the VPN form keeps options it doesn't show and refuses
  saving over a change made meanwhile; a missing wg-easy password points to the VPN's Settings.
- The terminal: a Terminal button on every device that has SSH (an ssh: login, or an SSH
  check); a device Kastellan has no key on asks for a login there (used once, or stored in the
  People ring), or offers a stored login. A host key it doesn't know yet is compared and trusted
  (or not) right there, also from the SSH access card; a changed one is forgotten on purpose,
  never trusted in one click.
- The collector and the actor are offered only for computers (servers, PCs, laptops), not for
  routers, access points, printers or cameras, unless they are set up there already.
- VPNs: any number, of either kind (two WireGuards, an OpenVPN per site). The VPN page shows a
  card per VPN with its clients, its settings and its setup; "Add a VPN" asks which kind, then
  that kind's form. config.yaml's `vpn:` (one of each) still works and becomes `vpns:` when a
  VPN is changed in the app.
- Updates: choose several (or all Kastellan can do) and update them in one go. A queue runs
  them one after another on each device (a reboot last) and devices side by side; the
  Updates page, a device's containers (now with "Update all") and its system updates share
  it, with the same words and buttons, also while moving between pages.
- Updates show how far they got: each step the host reports (pulling, creating, waiting until
  it runs steadily, the package being set up), a progress card for the batch (done, failed,
  waiting, what runs now and for how long), the count in the menu while it works, and per
  update its details (every step with its time, the answer, how long it took).
- Groups and the VPN have their own icons in the menu (no more key next to the Keys page).
- Updates and the terminal, reviewed: a reboot queued after a failed update on the same host
  doesn't run, and a reboot is refused while apt or dpkg still works; an odd byte in apt's
  output no longer stops an upgrade halfway; a proxy in front no longer cuts a quiet update
  short (the hub says it is still working); a stored login goes only to the devices it is for
  (set on the Keys page with your password), and only for an admin with their own way into the
  People ring; a terminal asks about the host's key before any password.

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
