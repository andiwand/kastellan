# Changelog

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
