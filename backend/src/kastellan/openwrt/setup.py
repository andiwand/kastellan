"""Setting up Kastellan's login on an OpenWrt device (run once as root over SSH, like the
collector): an rpcd login `kastellan` with a password only Kastellan knows (the router keeps
its hash), and an access list of its own (/usr/share/rpcd/acl.d/kastellan.json) with one group
per ability. The login gets only the groups chosen; rpcd refuses every other call, whatever the
hub asks. Running it again with another choice changes the login to exactly that choice.

Abilities:
- read (always): board, system, interfaces, Wi-Fi state and clients, DHCP leases, available
  package updates (through one fixed script);
- reboot;
- wifi: read and change the `wireless` configuration only, applied with an automatic rollback;
- firmware: put an image at /tmp/firmware.bin, test it, and start the upgrade (keeping the
  settings);
- openvpn: who is connected to its OpenVPN servers, through one fixed script that prints only
  the client lines of the status files its own openvpn config names (no file path is ever taken
  from Kastellan);
- logs: read its system log (logd's `log read`), for the Logs page;
- backup: download its configuration backup (`sysupgrade -b`, through cgi-io as LuCI does); it
  holds every password and key on the device, so Kastellan keeps it encrypted;
- scan: look for neighbouring Wi-Fi networks and how busy each channel is (a scan can disturb
  its clients for a moment), for the Wi-Fi page's channel plan.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import shlex

USER = "kastellan"
ACL = "/usr/share/rpcd/acl.d/kastellan.json"
UPGRADABLE = "/usr/libexec/kastellan/upgradable"
SERVICE = "/usr/libexec/kastellan/service"
SERVICES_ALLOW = "/etc/kastellan/services.allow"
OPENVPN_STATUS = "/usr/libexec/kastellan/openvpn-status"
SERVICE_NAME = r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$"
# Kastellan's own way in: these may be restarted, never stopped or disabled.
# (and what stopping would open up or cut off: the firewall, the network).
KEEP_RUNNING = ("rpcd", "uhttpd", "firewall", "network")
ABILITIES = {
    "reboot": "Reboot it",
    "wifi": "Change its wireless configuration (the page changes names, passwords, channels, on/off,"
    " and adds a mesh; the login itself could change any wireless option)",
    "firmware": "Install new firmware (an image built for it, tested on it first; settings kept)."
    " This amounts to full control of the device",
    "password": "Set root's password (to keep it the same across a group). Full control of the device",
    "services": "Start, stop, restart, enable or disable the services ticked below (nothing else)",
    "openvpn": "See who is connected to its OpenVPN server (names, addresses, since when), for the"
    " VPN page's last seen",
    "logs": "Read its system log (for the Logs page: logins, VPN, services, the kernel)",
    "backup": "Download its configuration backup (every password and key on it; kept encrypted)",
    "scan": "Scan for neighbouring Wi-Fi networks and channel use (for the Wi-Fi page's channel"
    " plan; clients may notice a moment's pause)",
}

# The one command the services group may run: an action on a service in the allow list.
_SERVICE_SCRIPT = f"""#!/bin/sh
# Kastellan: start/stop/restart/enable/disable one service from {SERVICES_ALLOW}. Nothing else.
PATH=/usr/sbin:/usr/bin:/sbin:/bin
name="$1"; action="$2"
case "$action" in start|stop|restart|enable|disable) ;; *) echo "no such action" >&2; exit 2;; esac
case "$name" in ''|.*|*[!A-Za-z0-9_.-]*) echo "not a service name" >&2; exit 2;; esac
grep -qxF -- "$name" {SERVICES_ALLOW} 2>/dev/null || {{ echo "$name isn't allowed" >&2; exit 3; }}
case "$name:$action" in
  {"|".join(f"{n}:stop|{n}:disable" for n in KEEP_RUNNING)})
    echo "Kastellan reaches the device through $name: restart only" >&2; exit 3;;
esac
[ -x "/etc/init.d/$name" ] || {{ echo "no service $name" >&2; exit 4; }}
logger -t kastellan "service $name $action"
exec "/etc/init.d/$name" "$action"
"""

# The one command the read group may run: what packages could be updated. It takes no input.
_UPGRADABLE_SCRIPT = """#!/bin/sh
# Kastellan: which packages have updates (opkg on OpenWrt up to 24.10, apk from 25.12). No input.
if command -v apk >/dev/null 2>&1; then
  apk update >/dev/null 2>&1; apk list --upgradable 2>/dev/null
else
  opkg update >/dev/null 2>&1; opkg list-upgradable 2>/dev/null
fi
exit 0
"""


# The one command the openvpn group may run: the client lines of each OpenVPN server's status
# file, where /etc/config/openvpn (or the server's own config file) says it is. No input: the
# paths come from the device's config, never from Kastellan; only client lines are printed.
_OPENVPN_STATUS_SCRIPT = r"""#!/bin/sh
# Kastellan: who is connected to this device's OpenVPN servers. No input, client lines only.
PATH=/usr/sbin:/usr/bin:/sbin:/bin
OPENVPN_DIR=/etc/openvpn
seen=" "
# The status file named by a config file (`status <file> [seconds]`).
status_of() { sed -n 's/^[[:space:]]*status[[:space:]]\{1,\}\([^[:space:]]*\).*/\1/p' "$1" | head -n 1; }
show() {
  echo "== $1"
  [ -n "$2" ] || { echo "!no-status"; return; }
  case "$seen" in *" $2 "*) return ;; esac
  seen="$seen$2 "
  [ -r "$2" ] || { echo "!unreadable"; return; }
  head -c 1048576 "$2" | awk '
    /^(HEADER[,\t])?CLIENT_LIST[,\t]/ { print; next }
    /^Common Name,Real Address/ { v1 = 1; print; next }
    /^ROUTING TABLE/ { v1 = 0 }
    v1 { print }'
}
found=0
for s in $(uci -q show openvpn | sed -n "s/^openvpn\.\([A-Za-z0-9_]*\)=openvpn$/\1/p"); do
  case "$(uci -q get "openvpn.$s.enabled")" in 1|true|yes|on) ;; *) continue ;; esac
  found=1
  f=$(uci -q get "openvpn.$s.status" | cut -d' ' -f1)
  conf=$(uci -q get "openvpn.$s.config")
  if [ -z "$f" ] && [ -n "$conf" ] && [ -r "$conf" ]; then f=$(status_of "$conf"); fi
  show "$s" "$f"
done
# Servers started from their own files (OpenWrt's init also runs /etc/openvpn/*.conf).
for conf in "$OPENVPN_DIR"/*.conf; do
  # A server's file, not a client's (server-poll-timeout is a client option).
  is_server='^[[:space:]]*(server|server-bridge|mode[[:space:]]+server)([[:space:]]|$)'
  [ -r "$conf" ] && grep -Eq "$is_server" "$conf" || continue
  found=1
  show "${conf##*/}" "$(status_of "$conf")"
done
[ "$found" = 1 ] || echo "!no-servers"
exit 0
"""


# Firmware and root's password go through rpcd's rpc-sys plugin, which LuCI doesn't always bring.
_RPCSYS = [
    "# rpc-sys (firmware, root's password): installed if missing.",
    "if [ ! -e /usr/lib/rpcd/rpcsys.so ]; then",
    "  echo 'installing rpcd-mod-rpcsys'",
    "  if command -v apk >/dev/null; then apk update && apk add rpcd-mod-rpcsys;"
    " else opkg update && opkg install rpcd-mod-rpcsys; fi",
    "fi",
    "[ -e /usr/lib/rpcd/rpcsys.so ] || { echo 'rpcd-mod-rpcsys is missing' >&2; exit 1; }",
]
_CGI_IO = [
    "if [ ! -e /www/cgi-bin/cgi-upload ]; then",
    "  echo 'needs cgi-io for firmware and backups (LuCI brings it)' >&2; exit 1",
    "fi",
]

# The web server's certificate, for Kastellan to pin (it trusts this connection's host key).
_CERT = [
    "# The web server's certificate, for Kastellan to pin.",
    "crt=$(uci -q get uhttpd.main.cert || echo /etc/uhttpd.crt)",
    'if [ ! -s "$crt" ]; then echo "no certificate at $crt (is HTTPS on?)" >&2; exit 1; fi',
    'if grep -q "BEGIN CERTIFICATE" "$crt"; then sed "s/^/KASTELLAN-CERT pem /" "$crt";',
    'else echo "KASTELLAN-CERT der $(sha256sum "$crt" | cut -d" " -f1)"; fi',
]


def acl() -> dict:
    """The access list: one group per ability, each naming exactly what it may call."""
    return {
        "kastellan-read": {
            "description": "Kastellan: read the device's state",
            "read": {
                "ubus": {
                    "system": ["board", "info"],
                    "network.interface": ["dump"],
                    "network.wireless": ["status"],
                    "rc": ["list"],  # services: enabled, running
                    "iwinfo": ["devices", "info", "assoclist", "freqlist", "txpowerlist"],
                    "network.device": ["status"],  # the WAN's byte counters
                    "luci-rpc": ["getDHCPLeases"],
                    "luci": ["getFeatures"],  # whether its Wi-Fi software can do mesh
                    "file": ["exec", "read"],
                    "session": ["access"],  # which of these groups it has (the page shows them)
                },
                "file": {UPGRADABLE: ["exec"], SERVICES_ALLOW: ["read"]},
            },
        },
        "kastellan-reboot": {
            "description": "Kastellan: reboot",
            "write": {"ubus": {"system": ["reboot"]}},
        },
        "kastellan-wifi": {
            "description": "Kastellan: the wireless settings, nothing else",
            "read": {"ubus": {"uci": ["get", "changes"]}, "uci": ["wireless"]},
            "write": {
                "ubus": {"uci": ["set", "add", "delete", "apply", "confirm", "rollback", "revert"]},
                "uci": ["wireless"],
            },
        },
        "kastellan-services": {
            "description": "Kastellan: act on the allowed services (through one script)",
            "read": {"ubus": {"file": ["exec"]}, "file": {SERVICE: ["exec"]}},
        },
        "kastellan-openvpn": {
            "description": "Kastellan: who is connected to its OpenVPN servers (through one script)",
            "read": {"ubus": {"file": ["exec"]}, "file": {OPENVPN_STATUS: ["exec"]}},
        },
        "kastellan-logs": {
            "description": "Kastellan: read the system log",
            "read": {"ubus": {"log": ["read"]}},
        },
        "kastellan-backup": {
            "description": "Kastellan: download the configuration backup (through cgi-io)",
            "read": {"cgi-io": ["backup"]},
        },
        "kastellan-scan": {
            "description": "Kastellan: scan for Wi-Fi networks and channel use",
            "read": {"ubus": {"iwinfo": ["scan", "survey"]}},
        },
        "kastellan-password": {
            "description": "Kastellan: set root's password",
            "write": {"ubus": {"rpc-sys": ["password_set"]}},
        },
        "kastellan-firmware": {
            "description": "Kastellan: install a firmware image, keeping the settings",
            "read": {"ubus": {"rpc-sys": ["packagelist"]}},
            "write": {
                "ubus": {"file": ["remove"], "rpc-sys": ["upgrade_test", "upgrade_start"]},
                # written through cgi-io (/cgi-bin/cgi-upload), which checks this
                "file": {"/tmp/firmware.bin": ["write"]},
            },
        },
    }


def md5crypt(password: str, salt: str | None = None) -> str:
    """The `$1$` password hash rpcd checks a login against (crypt(3)'s MD5 scheme, as
    `uhttpd -m` makes it): the router never sees the password itself stored."""
    salt = (salt or secrets.token_urlsafe(6).replace("-", ".").replace("_", "/"))[:8]
    pw, s = password.encode(), salt.encode()
    alt = hashlib.md5(pw + s + pw).digest()
    ctx = hashlib.md5(pw + b"$1$" + s)
    for i in range(len(pw), 0, -16):
        ctx.update(alt[: min(16, i)])
    i = len(pw)
    while i:
        ctx.update(b"\0" if i & 1 else pw[:1])
        i >>= 1
    final = ctx.digest()
    for i in range(1000):
        c = hashlib.md5()
        c.update(pw if i & 1 else final)
        if i % 3:
            c.update(s)
        if i % 7:
            c.update(pw)
        c.update(final if i & 1 else pw)
        final = c.digest()
    itoa = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

    def to64(v: int, n: int) -> str:
        out = ""
        for _ in range(n):
            out += itoa[v & 0x3F]
            v >>= 6
        return out

    f = final
    text = (
        to64((f[0] << 16) | (f[6] << 8) | f[12], 4)
        + to64((f[1] << 16) | (f[7] << 8) | f[13], 4)
        + to64((f[2] << 16) | (f[8] << 8) | f[14], 4)
        + to64((f[3] << 16) | (f[9] << 8) | f[15], 4)
        + to64((f[4] << 16) | (f[10] << 8) | f[5], 4)
        + to64(f[11], 2)
    )
    return f"$1${salt}${text}"


def install_script(chosen: set[str], password_hash: str, services: list[str] | None = None) -> str:
    """Shell commands (OpenWrt's ash, as root) giving Kastellan's login exactly these abilities
    (and reading, always); with `services`, the ones it may act on."""
    unknown = chosen - set(ABILITIES)
    if unknown:
        raise ValueError(f"unknown abilities: {', '.join(sorted(unknown))}")
    services = sorted(set(services or [])) if "services" in chosen else []
    bad = [n for n in services if not re.match(SERVICE_NAME, n)]
    if bad:
        raise ValueError(f"not service names: {', '.join(bad[:5])}")
    if not password_hash.startswith("$1$") or "'" in password_hash:
        raise ValueError("not an md5crypt hash")
    q = shlex.quote
    groups = ["kastellan-read", *(f"kastellan-{a}" for a in sorted(chosen))]
    lines = [
        "set -eu",
        '[ -f /etc/openwrt_release ] || { echo "this is not OpenWrt" >&2; exit 1; }',
        "if [ ! -d /usr/share/rpcd/acl.d ] || ! command -v uci >/dev/null; then",
        '  echo "needs rpcd (LuCI brings it)" >&2; exit 1',
        "fi",
        "# The access list: one group per ability, each naming exactly what it may call.",
        f"cat > {ACL}.new <<'KASTELLAN_ACL_EOF'",
        json.dumps(acl(), indent=1),
        "KASTELLAN_ACL_EOF",
        f"chmod 644 {ACL}.new && mv {ACL}.new {ACL}",
        "# The one command reading may run: which packages have updates.",
        f"mkdir -p {UPGRADABLE.rsplit('/', 1)[0]}",
        f"cat > {UPGRADABLE}.new <<'KASTELLAN_UPG_EOF'",
        _UPGRADABLE_SCRIPT.rstrip("\n"),
        "KASTELLAN_UPG_EOF",
        f"chmod 755 {UPGRADABLE}.new && mv {UPGRADABLE}.new {UPGRADABLE}",
        "# The services Kastellan may act on (none unless that is ticked), and the one script.",
        f"mkdir -p {SERVICES_ALLOW.rsplit('/', 1)[0]}",
        f"printf '%s\\n' {' '.join(q(n) for n in services)} > {SERVICES_ALLOW}.new"
        if services
        else f": > {SERVICES_ALLOW}.new",
        f"chmod 644 {SERVICES_ALLOW}.new && mv {SERVICES_ALLOW}.new {SERVICES_ALLOW}",
        f"cat > {SERVICE}.new <<'KASTELLAN_SVC_EOF'",
        _SERVICE_SCRIPT.rstrip("\n"),
        "KASTELLAN_SVC_EOF",
        f"chmod 755 {SERVICE}.new && mv {SERVICE}.new {SERVICE}",
        f"cat > {OPENVPN_STATUS}.new <<'KASTELLAN_OVPN_EOF'",
        _OPENVPN_STATUS_SCRIPT.rstrip("\n"),
        "KASTELLAN_OVPN_EOF",
        f"chmod 755 {OPENVPN_STATUS}.new && mv {OPENVPN_STATUS}.new {OPENVPN_STATUS}",
        *(_RPCSYS if chosen & {"firmware", "password"} else []),
        *(_CGI_IO if chosen & {"firmware", "backup"} else []),
        *_CERT,
        "# The login: an earlier one goes, the new one gets exactly the chosen groups.",
        f"for s in $(uci -q -X show rpcd | sed -n \"s/^rpcd\\.\\([^.]*\\)\\.username='{USER}'$/\\1/p\"); do",
        '  uci delete "rpcd.$s"',
        "done",
        "s=$(uci add rpcd login)",
        f'uci set "rpcd.$s.username={USER}"',
        f'uci set "rpcd.$s.password="{q(password_hash)}',
    ]
    for g in groups:
        lines.append(f'uci add_list "rpcd.$s.read={g}"')
        if g != "kastellan-read":
            lines.append(f'uci add_list "rpcd.$s.write={g}"')
    lines += [
        "uci commit rpcd",
        "/etc/init.d/rpcd reload",
        f"echo {q('Kastellan may now: read' + ''.join(', ' + a for a in sorted(chosen)) + '.')}",
    ]
    return "\n".join(lines) + "\n"
