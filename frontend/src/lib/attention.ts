import type { Alarm, Alarms, Device } from './api';
import { checkStatusOf, needsAttention, statusOf } from './status';
import { updatesOf, type Update } from './updates';

/** The one list of what needs attention, and every count derived from it: the overview's
 * headline and list, the sidebar badges, the per-area counts and a device's own issues all read
 * this, so no two places can disagree.
 *
 * An issue is one of:
 * - an open alarm (the hub's issue model: alarms.py and friends; acknowledged ones stay, marked);
 * - a check that is down on a device that should be on, not (yet) alarmed: an alarm waits
 *   `down_after` (3 min) and skips muted devices and those in maintenance, the list doesn't wait
 *   (but leaves muted and maintained devices out too);
 * - an update that matters: security updates for system packages, firmware out of support
 *   (usually an alarm already). Other updates are counted (`updates`), not issues. */

export type IssueTone = 'critical' | 'warning';

/** Where an issue belongs: the overview's areas and the sections of a device page. */
export type Area =
	| 'devices' // a device down, unreachable, its collector or API silent, a VM, heat
	| 'services' // a check (service) down, failed systemd units
	| 'storage' // full disks, RAID, SMART, disk heat, unreadable storage
	| 'power' // UPS
	| 'updates' // firmware out of support, security updates
	| 'certificates'
	| 'backups' // backup jobs, configuration backups, Kastellan's own backup
	| 'security' // vulnerabilities
	| 'network' // DHCP
	| 'internet'
	| 'wifi'
	| 'hub'; // Kastellan itself (its names and HTTPS)

export interface Issue {
	key: string; // the alarm's key, or one in the same shape (service:<device>:<check>)
	tone: IssueTone;
	area: Area;
	device: string | null; // the device it is about (null: the network, the hub)
	title: string; // one sentence, with the device's name: "NAS: jellyfin is down"
	detail: string;
	since: string | null; // when the problem began (ISO), or when the alarm opened
	href: string; // where to see and fix it: a device page's section, or a page
	alarm: Alarm | null; // the open alarm behind it (acknowledge, held in maintenance)
}

export interface Attention {
	issues: Issue[]; // critical first, then the newest first
	count: number; // issues.length: the headline's number
	tone: 'good' | IssueTone; // the worst issue's
	/** Devices whose state needs attention (down or degraded and meant to be on): the Devices
	 * badge, the same set as the device list's Problems filter. */
	problemDevices: Device[];
	/** Every update waiting (Updates page, its badge, the overview's update count). */
	updates: Update[];
}

/** Section anchors on a device page (`/devices/<id>#<anchor>`) an issue of an area links to;
 * the other areas land on the top of the page. */
export const SECTION: Partial<Record<Area, string>> = {
	services: 'services',
	storage: 'storage',
	updates: 'software',
	certificates: 'services',
	backups: 'backups',
	security: 'vulnerabilities'
};

/** The area an alarm key belongs to (the keys alarms.py, backups.py, hub.py, ... make). */
export function areaOf(key: string): Area {
	const [kind, , what = ''] = key.split(':');
	switch (kind) {
		case 'down':
			return 'devices';
		case 'service':
			return 'services';
		case 'host': {
			if (/^(disk|raid|smart|disktemp|unread)$/.test(what)) return 'storage';
			if (what === 'services') return 'services';
			return 'devices'; // the collector, heat, a VM
		}
		case 'ups':
			return 'power';
		case 'router':
			return what === 'firmware' ? 'updates' : 'devices';
		case 'updates':
			return 'updates';
		case 'cert':
			return 'certificates';
		case 'backup':
		case 'devbackup':
		case 'hubbackup':
			return 'backups';
		case 'vuln':
			return 'security';
		case 'dhcp':
			return 'network';
		case 'internet':
			return 'internet';
		case 'roaming':
			return 'wifi';
		case 'web':
			return 'hub';
		default:
			return 'devices';
	}
}

/** Where an issue is seen and fixed. */
export function hrefOf(area: Area, device: string | null): string {
	if (area === 'internet') return '/internet';
	if (area === 'wifi') return '/wifi';
	if (area === 'hub') return '/settings?tab=web';
	if (area === 'backups' && !device) return '/backups';
	if (!device) return area === 'network' ? '/devices' : '/';
	const anchor = SECTION[area];
	return `/devices/${encodeURIComponent(device)}${anchor ? `#${anchor}` : ''}`;
}

/** An alarm's tone. A service that is down is down: critical, as its check's badge says, even
 * when its check sends the message as a warning (`alarm: warning`). */
export function alarmTone(a: Pick<Alarm, 'key' | 'severity'>): IssueTone {
	return a.severity === 'critical' || a.key.startsWith('service:') ? 'critical' : 'warning';
}

/** Everything that needs attention now. */
export function attentionOf(devices: Device[], alarms: Alarms | null, now = Date.now()): Attention {
	const issues: Issue[] = [];
	const open = (alarms?.open ?? []).filter((a) => a.severity !== 'info');
	const keys = new Set(open.map((a) => a.key));
	// Devices an alarm already speaks for: its own device, and those folded into it.
	const hidden = new Set(open.flatMap((a) => a.hidden ?? []));
	const quiet = (id: string) =>
		(alarms?.muted?.[id] && Date.parse(alarms.muted[id]) > now) || !!alarms?.maintenance?.[id];

	for (const a of open) {
		const area = areaOf(a.key);
		const device = a.device || null;
		issues.push({
			key: a.key,
			tone: alarmTone(a),
			area,
			device,
			title: a.title,
			detail: a.detail,
			since: a.since ?? a.opened,
			href: hrefOf(area, device),
			alarm: a
		});
	}

	// Down checks not alarmed (yet): on devices that should be on, not muted or in maintenance.
	const problemDevices = devices.filter(needsAttention);
	for (const d of problemDevices) {
		if (hidden.has(d.id) || keys.has(`down:${d.id}`) || quiet(d.id)) continue;
		if (statusOf(d) === 'down') {
			const since = d.checks.map((c) => c.since).filter((s): s is string => !!s);
			issues.push({
				key: `down:${d.id}`,
				tone: 'critical',
				area: 'devices',
				device: d.id,
				title: `${d.name} is down`,
				detail: d.checks.find((c) => c.detail)?.detail ?? '',
				since: since.sort().at(-1) ?? null,
				href: hrefOf('devices', d.id),
				alarm: null
			});
			continue;
		}
		for (const c of d.checks) {
			if (checkStatusOf(d, c) !== 'down' || c.alarm === 'off') continue; // off: not asked to be told
			if (keys.has(`service:${d.id}:${c.name}`) || keys.has(`cert:${d.id}:${c.name}`)) continue;
			issues.push({
				key: `service:${d.id}:${c.name}`,
				tone: 'critical',
				area: 'services',
				device: d.id,
				title: `${d.name}: ${c.name} is down`,
				detail: c.detail ?? '',
				since: c.since ?? null,
				href: hrefOf('services', d.id),
				alarm: null
			});
		}
	}

	// Updates that matter: security fixes, firmware out of support.
	const updates = updatesOf(devices);
	for (const u of updates) {
		if (u.kind === 'packages' && u.security) {
			const key = `updates:${u.device.id}:security`;
			issues.push({
				key,
				tone: 'warning',
				area: 'updates',
				device: u.device.id,
				title: `${u.device.name}: ${u.security} security ${u.security === 1 ? 'update' : 'updates'} waiting`,
				detail: `${u.count} system ${u.count === 1 ? 'update' : 'updates'} in all`,
				since: null,
				href: hrefOf('updates', u.device.id),
				alarm: null
			});
		} else if (
			u.kind === 'firmware' &&
			!u.supported &&
			!keys.has(`router:${u.device.id}:firmware`)
		) {
			issues.push({
				key: `router:${u.device.id}:firmware`,
				tone: 'warning',
				area: 'updates',
				device: u.device.id,
				title: `${u.device.name} runs ${u.from}, which no longer gets security fixes`,
				detail: `next: ${u.to}`,
				since: null,
				href: hrefOf('updates', u.device.id),
				alarm: null
			});
		}
	}

	const rank = (i: Issue) => (i.tone === 'critical' ? 0 : 1);
	const time = (i: Issue) => (i.since ? Date.parse(i.since) : 0);
	issues.sort((a, b) => rank(a) - rank(b) || time(b) - time(a) || a.title.localeCompare(b.title));
	return {
		issues,
		count: issues.length,
		tone: issues[0]?.tone ?? 'good',
		problemDevices,
		updates
	};
}

/** The issues in one area (the overview's area rows, a page's own count). */
export const issuesIn = (a: Attention, area: Area) => a.issues.filter((i) => i.area === area);

/** One device's issues (its page's attention strip). A device folded into another's alarm
 * (behind a device that is down) has none of its own: the alarm's device has it. */
export const issuesOf = (a: Attention, device: string) =>
	a.issues.filter((i) => i.device === device);
