import type { Device } from './api';
import type { Issue } from './attention';

/** The device page's Status tab: its sections in a fixed order that starts with what the
 * device is for (a router's internet and Wi-Fi, an access point's Wi-Fi, a server's vitals and
 * storage), its facts (where its readings come from), and where an issue is seen on the page. */

export type SectionId =
	| 'ups' // the UPS's own reading (kind ups)
	| 'internet' // the router's internet connection (FRITZ!Box)
	| 'wifi'
	| 'network' // DHCP, WireGuard, who is connected through it
	| 'vitals'
	| 'storage'
	| 'services'
	| 'containers'
	| 'vm' // the virtual machine it is (on its host)
	| 'vms' // the virtual machines on it
	| 'software'
	| 'backups'
	| 'vulnerabilities'
	| 'history'; // response time, activity, what is connected through it

/** Hosts: a collector and an actor can be set up there. */
export const HOSTS = ['server', 'pc', 'laptop', 'other'];

const HOST: SectionId[] = [
	'vitals',
	'storage',
	'services',
	'containers',
	'vms',
	'vm',
	'backups',
	'software',
	'vulnerabilities',
	'network'
];
const ORDER: Record<string, SectionId[]> = {
	router: [
		'internet',
		'wifi',
		'network',
		'services',
		'vitals',
		'software',
		'backups',
		'vulnerabilities'
	],
	'access-point': [
		'wifi',
		'network',
		'services',
		'vitals',
		'software',
		'backups',
		'vulnerabilities'
	],
	switch: ['network', 'services', 'vitals', 'software', 'backups', 'vulnerabilities'],
	server: HOST,
	pc: HOST,
	laptop: HOST,
	ups: ['ups', 'services', 'vitals', 'software', 'backups']
};
// Printers, cameras and the rest: what they answer, then whatever else is read of them.
const OTHER: SectionId[] = ['services', 'vitals', 'software', 'backups', 'vulnerabilities'];
// Every section once: a kind's own order first, the rest after it (a router with containers
// still shows them), the history last.
const ALL: SectionId[] = [...new Set([...HOST, ...Object.values(ORDER).flat(), ...OTHER])];

/** The sections of a device of this kind, in the kind's order, keeping those `present` (empty
 * ones are hidden). The same for every device of a kind; the history always comes last. */
export function sectionsOf(kind: string, present: Iterable<SectionId>): SectionId[] {
	const have = new Set(present);
	const own = ORDER[kind] ?? OTHER;
	const order = [...own, ...ALL.filter((s) => !own.includes(s)), 'history' as const];
	return order.filter((s) => have.has(s));
}

/** Which sections have something to show for this device (whether the viewer may see them is
 * the page's business). `own`: how many of its own services (OpenWrt's, failed systemd units)
 * have no check of their own to sit under. */
export function presentOf(d: Device, own = 0): SectionId[] {
	const radios = d.router?.radios?.length ?? 0;
	const has: Record<SectionId, boolean> = {
		ups: d.kind === 'ups',
		internet: !!d.fritz?.internet,
		wifi: radios > 0 || !!d.fritz?.wifi?.length,
		network: !!(d.router?.dhcp || d.fritz?.dhcp || d.fritz?.wireguard || d.fritz?.clients?.length),
		vitals: !!d.vitals,
		storage: !!d.host_report,
		services: d.checks.length > 0 || own > 0,
		containers: !!(d.containers || d.containers_error),
		vm: !!d.runs_on,
		vms: !!d.vms?.length,
		software: !!(d.router || d.fritz || d.host_report?.updates),
		backups: true, // its backup jobs and configuration backups say themselves whether there are any
		vulnerabilities: !!d.vulns,
		history: true
	};
	return (Object.keys(has) as SectionId[]).filter((s) => has[s]);
}

/** Where the device's readings come from (OpenWrt's API, TR-064, the collector), when they
 * were read and what went wrong. */
export interface Source {
	name: string;
	at: string | null;
	error: string | null;
	collector: boolean; // it can be read again from here
}
export function sourcesOf(d: Device): Source[] {
	const s: Source[] = [];
	if (d.openwrt || d.router)
		s.push({
			name: "OpenWrt's API",
			at: d.router_checked ?? null,
			error: d.router_error ?? null,
			collector: false
		});
	if (d.fritzbox || d.fritz)
		s.push({
			name: 'TR-064',
			at: d.fritz_checked ?? null,
			error: d.fritz_error ?? null,
			collector: false
		});
	if (d.collect || d.host_report)
		s.push({
			name: 'the collector',
			at: d.host_checked ?? null,
			error: d.host_error ?? null,
			collector: true
		});
	return s;
}

/** Whether something can be set up to read this device (Setup offers it): a host's collector,
 * a router's or access point's OpenWrt or FRITZ!Box login. Printers and cameras are only
 * checked, so a missing reading isn't news there. */
export function readable(d: Device): boolean {
	return (
		!!d.host &&
		(HOSTS.includes(d.kind) || ['router', 'access-point', 'switch'].includes(d.kind) || !!d.driver)
	);
}

/** What it reports of its parts that couldn't be read ("disks (permission denied)"). */
export function unreadOf(d: Device): string[] {
	return Object.entries({
		...(d.router?.errors ?? {}),
		...(d.fritz?.errors ?? {}),
		...(d.host_report?.errors ?? {})
	}).map(([k, v]) => `${k} (${v})`);
}

/** How long it has been up, in seconds, as it says. */
export function uptimeOf(d: Device): number | null {
	return d.router?.system.uptime ?? d.fritz?.uptime ?? d.host_report?.system?.uptime ?? null;
}

/** Its firmware (a router, a printer: as it reports it) or a host's system (its kernel). */
export function systemOf(d: Device): { label: string; value: string } | null {
	if (d.info?.firmware) return { label: 'Firmware', value: d.info.firmware };
	const kernel = d.host_report?.system?.kernel;
	return kernel ? { label: 'Kernel', value: kernel } : null;
}

/** "NAS: jellyfin is down" on the NAS's own page is "jellyfin is down". */
export function shortTitle(title: string, name: string): string {
	const prefix = `${name}: `;
	if (!title.startsWith(prefix)) return title;
	const rest = title.slice(prefix.length);
	return rest.charAt(0).toUpperCase() + rest.slice(1);
}

// Issues whose area has a section on this page but no anchor of the attention model's own
// (that one sends Wi-Fi issues to the Wi-Fi page, a UPS's to the top of its page).
const LOCAL: Partial<Record<Issue['area'], SectionId>> = {
	wifi: 'wifi',
	power: 'ups',
	network: 'network',
	internet: 'internet'
};

/** Where an issue of this device is on its own page: its section's anchor ("#storage"), else
 * where the attention model sends it. */
export function issueHref(i: Issue, device: string, shown: SectionId[]): string {
	const own = `/devices/${encodeURIComponent(device)}`;
	if (i.href.startsWith(`${own}#`)) return i.href.slice(own.length);
	const local = LOCAL[i.area];
	if (local && shown.includes(local)) return `#${local}`;
	return i.href === own ? '#top' : i.href;
}
