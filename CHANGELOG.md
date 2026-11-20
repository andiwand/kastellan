# Changelog

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
