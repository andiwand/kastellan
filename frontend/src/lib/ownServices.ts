import type { CheckStatus, Device } from '#lib/api.js';

/** A service the device itself reports: an OpenWrt init script (rc list), or a failed systemd
 * unit on a Linux host (its collector). Shown in the device's Services, under the check it
 * answers when Kastellan knows which (dropbear answers the SSH port), else as a row of its own. */
export interface OwnService {
	name: string;
	state: 'running' | 'stopped' | 'failed';
	enabled: boolean | null; // started at boot (OpenWrt); null where unknown
	source: 'openwrt' | 'systemd';
	actable: boolean; // Kastellan may start, stop, ... it (OpenWrt: on its services list)
}

const port = (c: CheckStatus): number | null => {
	if (c.type === 'tcp') return Number(c.target) || null;
	if (c.type === 'http' && typeof c.target === 'string') {
		try {
			const u = new URL(c.target);
			return Number(u.port) || (u.protocol === 'https:' ? 443 : 80);
		} catch {
			return null;
		}
	}
	return null;
};

const SSH = (c: CheckStatus) => port(c) === 22 || /\bssh\b/i.test(c.name);
const WEB = (c: CheckStatus) => c.type === 'http' || [80, 443, 8080, 8443].includes(port(c) ?? 0);
const DNS = (c: CheckStatus) => c.type === 'dns' || port(c) === 53;
const DHCP = (c: CheckStatus) => c.type === 'dhcp';
const VPN = (c: CheckStatus) => [1194, 51820].includes(port(c) ?? 0) || /vpn/i.test(c.name);

/** Which checks a service by this name answers. */
const ANSWERS: Record<string, (c: CheckStatus) => boolean> = {
	dropbear: SSH,
	sshd: SSH,
	'ssh.service': SSH,
	'sshd.service': SSH,
	uhttpd: WEB,
	nginx: WEB,
	lighttpd: WEB,
	'nginx.service': WEB,
	'apache2.service': WEB,
	'caddy.service': WEB,
	dnsmasq: (c) => DNS(c) || DHCP(c),
	odhcpd: DHCP,
	unbound: DNS,
	adguardhome: DNS,
	'named.service': DNS,
	'unbound.service': DNS,
	openvpn: VPN,
	'openvpn.service': VPN,
	'wg-quick@wg0.service': VPN
};

export function ownServicesOf(device: Device): OwnService[] {
	const out: OwnService[] = [];
	const router = device.router;
	if (router?.services) {
		const allowed = new Set(router.services_allowed ?? []);
		for (const s of router.services) {
			// Worth seeing: running ones, enabled ones, and the ones Kastellan may act on.
			if (!s.running && !s.enabled && !allowed.has(s.name)) continue;
			out.push({
				name: s.name,
				state: s.running ? 'running' : 'stopped',
				enabled: s.enabled,
				source: 'openwrt',
				actable: allowed.has(s.name)
			});
		}
	}
	for (const unit of device.host_report?.services?.failed ?? [])
		out.push({ name: unit, state: 'failed', enabled: null, source: 'systemd', actable: false });
	return out;
}

/** The device's own services split: those under a check (by its name), and the rest. A service
 * goes under the first check it answers. */
export function groupOwn(
	device: Device,
	own: OwnService[]
): { byCheck: Record<string, OwnService[]>; rest: OwnService[] } {
	const byCheck: Record<string, OwnService[]> = {};
	const rest: OwnService[] = [];
	for (const s of own) {
		const answers = ANSWERS[s.name];
		const check = answers ? device.checks.find((c) => answers(c)) : undefined;
		if (check) (byCheck[check.name] ??= []).push(s);
		else rest.push(s);
	}
	// Failed ones first, then running, then the others; each by name.
	const rank = (s: OwnService) => (s.state === 'failed' ? 0 : s.state === 'running' ? 1 : 2);
	rest.sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
	return { byCheck, rest };
}
