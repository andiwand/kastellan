import { issuesIn, type Area, type Attention } from './attention';
import { attentionHeadline, count } from './glossary';
import { worst, type Tone } from './states';

/** The main navigation: about ten places in a few groups (the sidebar), five of them in the
 * phone's bottom bar (the rest under More). Old addresses redirect: /vms to Containers & VMs,
 * /keys to Settings. */
export interface NavItem {
	href: string;
	label: string;
	short?: string; // in the phone bar
	icon: string; // a key of the layout's icon map
	admin?: boolean; // only for admins (password hashes, keys, Wi-Fi settings)
	also?: string[]; // other paths that belong to it (old addresses)
	phone?: boolean; // in the phone's bottom bar
}
export interface NavGroup {
	label: string | null;
	items: NavItem[];
}

export const NAV: NavGroup[] = [
	{
		label: null,
		items: [
			{ href: '/', label: 'Overview', icon: 'overview', phone: true },
			{ href: '/devices', label: 'Devices', icon: 'devices', phone: true, also: ['/groups'] }
		]
	},
	{
		label: 'Network',
		items: [
			{ href: '/internet', label: 'Internet', icon: 'internet', phone: true },
			{ href: '/wifi', label: 'Wi-Fi', icon: 'wifi', admin: true, phone: true },
			{ href: '/vpn', label: 'VPN', icon: 'vpn' }
		]
	},
	{
		label: 'Workloads',
		items: [
			{ href: '/services', label: 'Services', icon: 'services' },
			{ href: '/containers', label: 'Containers & VMs', icon: 'containers', also: ['/vms'] }
		]
	},
	{
		label: 'Maintenance',
		items: [
			{ href: '/updates', label: 'Updates', icon: 'updates' },
			{ href: '/backups', label: 'Backups', icon: 'backups', admin: true },
			{ href: '/logs', label: 'Logs', icon: 'logs', admin: true },
			{ href: '/signins', label: 'Sign-ins', icon: 'signins', admin: true }
		]
	},
	{
		label: null,
		items: [
			{ href: '/settings', label: 'Settings', icon: 'settings', admin: true, also: ['/keys'] }
		]
	}
];

/** The navigation as this person sees it: admin-only places left out, empty groups too. */
export function navFor(admin: boolean): NavGroup[] {
	return NAV.map((g) => ({ ...g, items: g.items.filter((i) => admin || !i.admin) })).filter(
		(g) => g.items.length
	);
}

/** Whether a place is the current page (or one under it). */
export function isActive(item: NavItem, path: string): boolean {
	if (item.href === '/') return path === '/';
	return [item.href, ...(item.also ?? [])].some((p) => path === p || path.startsWith(`${p}/`));
}

/** What a place shows next to its name, all from the one attention model: a number (Overview:
 * the issues, as the headline; Devices: those with problems, as its Problems filter; Updates:
 * every update waiting, as its list) or a dot when an area of the place has issues. */
export interface Badge {
	count?: number;
	tone: Tone;
	title: string;
}

const AREAS: Record<string, Area[]> = {
	'/internet': ['internet'],
	'/wifi': ['wifi'],
	'/services': ['services'],
	'/backups': ['backups']
};

export function badgeOf(href: string, a: Attention): Badge | null {
	if (href === '/')
		return a.count ? { count: a.count, tone: a.tone, title: attentionHeadline(a.count) } : null;
	if (href === '/devices') {
		const n = a.problemDevices.length;
		return n ? { count: n, tone: 'critical', title: `${count(n, 'device')} with problems` } : null;
	}
	if (href === '/updates') {
		const n = a.updates.length;
		const urgent = issuesIn(a, 'updates').map((i) => i.tone); // security, out of support
		return n
			? { count: n, tone: worst([...urgent, 'neutral']), title: `${count(n, 'update')} waiting` }
			: null;
	}
	const issues = (AREAS[href] ?? []).flatMap((area) => issuesIn(a, area));
	if (!issues.length) return null;
	return {
		tone: worst(issues.map((i) => i.tone)),
		title: count(issues.length, 'issue') + ' here'
	};
}

/** The phone's More: a dot when a place under it needs attention, with its worst tone (Updates
 * waiting count as neutral, so they don't raise an alarm on their own). */
export function moreBadge(items: NavItem[], a: Attention): Badge | null {
	const badges = items.map((i) => badgeOf(i.href, a)).filter((b): b is Badge => b !== null);
	if (!badges.length) return null;
	const updates = items.some((i) => i.href === '/updates') ? a.updates.length : 0;
	return {
		tone: worst(badges.map((b) => b.tone)),
		...(updates ? { count: updates } : {}),
		title: badges.map((b) => b.title).join(', ')
	};
}
