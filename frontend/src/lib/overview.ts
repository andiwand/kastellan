import type {
	Backup,
	Bucket,
	Device,
	DeviceBackupSummary,
	InternetView,
	SignInsSummary
} from './api';
import { issuesIn, type Attention, type Issue } from './attention';
import { ms, percent } from './format';
import { count } from './glossary';
import { statusOf } from './status';
import {
	backupState,
	configBackupState,
	internetState,
	signinFlag,
	toneRank,
	updateState,
	worst,
	type State,
	type Tone
} from './states';

/** The overview's status board: one row per area (Internet, Wi-Fi, Storage, Backups, Sign-ins,
 * Updates), each a state word, one number and, only when something is wrong, a sentence. The
 * sentences and the worst tone come from the attention model, so an area never says less than
 * the Needs attention list. */

export type AreaId = 'internet' | 'wifi' | 'storage' | 'backups' | 'signins' | 'updates';

export interface AreaRow {
	id: AreaId;
	label: string;
	href: string;
	state: State;
	value: string | null; // the one number ("13 ms", "92 %", "8")
	sub?: string; // what the number is of, muted ("/srv/media on Home server")
	sentence?: string; // only when something is wrong
}

/** Something read from the hub: the answer, null while it comes, 'error' when it didn't. */
export type Read<T> = T | null | 'error';

export interface AreaInput {
	attention: Attention;
	devices: Device[];
	admin: boolean; // Internet and Sign-ins are for admins
	internet: Read<InternetView>;
	backups: Read<Backup[]>;
	configBackups: Read<DeviceBackupSummary[]>; // admins only: null otherwise
	signins: Read<SignInsSummary>;
}

const OK: State = { word: 'OK', tone: 'good' };
const CHECKING: State = { word: 'Checking', tone: 'neutral' };
const NOT_READ: State = { word: 'Not read', tone: 'neutral' };
const ADMINS: State = { word: 'Admins only', tone: 'neutral' };

/** An issue tone as a state word (the same words as the Needs attention rows' dots). */
const issueState = (tone: Tone): State => ({
	word: tone === 'critical' ? 'Critical' : 'Warning',
	tone
});

/** The area's own state, unless its issues are worse. */
function withIssues(state: State, issues: Issue[]): State {
	if (!issues.length) return state;
	const tone = worst(issues.map((i) => i.tone));
	return toneRank[tone] < toneRank[state.tone] ? issueState(tone) : state;
}

/** The issues of an area in one sentence: the worst one's, and how many more. */
export function sentenceOf(issues: Issue[]): string | undefined {
	if (!issues.length) return undefined;
	const first = issues[0].title;
	return issues.length === 1 ? first : `${first}, and ${issues.length - 1} more`;
}

/** Where a row links: the single issue's own place, else the area's page. */
const linkOf = (issues: Issue[], page: string) => (issues.length === 1 ? issues[0].href : page);

/** Wi-Fi clients and access points, from what the routers report: OpenWrt radios' clients and
 * FRITZ!Box networks' associated clients (the same ones the Wi-Fi page lists). */
export function wifiCounts(devices: Device[]): { clients: number; accessPoints: number } {
	let clients = 0;
	let accessPoints = 0;
	for (const d of devices) {
		const radios = d.router?.radios ?? [];
		const nets = d.fritz?.wifi ?? [];
		if (radios.length || nets.length) accessPoints += 1;
		for (const r of radios) for (const i of r.interfaces) clients += i.clients.length;
		for (const n of nets) clients += n.associated?.length ?? 0;
	}
	return { clients, accessPoints };
}

/** The fullest filesystem on any host (vitals: each host's fullest). */
export function fullestVolume(
	devices: Device[]
): { device: Device; mount: string | null; percent: number } | null {
	let best: { device: Device; mount: string | null; percent: number } | null = null;
	for (const d of devices) {
		const p = d.vitals?.storage;
		if (p != null && (!best || p > best.percent))
			best = { device: d, mount: d.vitals?.storage_mount ?? null, percent: p };
	}
	return best;
}

/** The internet line's last day as uptime buckets (a strip): down is 0, online or slow 1. */
export function internetStrip(
	view: InternetView
): { buckets: Bucket[]; start: string; step: number } | null {
	const s = view.series;
	if (s.length < 2) return null;
	return {
		buckets: s.map((b) => ({
			uptime: b.state == null || b.state === 'unknown' ? null : b.state === 'down' ? 0 : 1,
			latency_ms: b.ms
		})),
		start: new Date(s[0].t * 1000).toISOString(),
		step: s[1].t - s[0].t
	};
}

function internetRow(input: AreaInput): AreaRow {
	const issues = issuesIn(input.attention, 'internet');
	const row = { id: 'internet', label: 'Internet', href: '/internet' } as const;
	const v = input.internet;
	if (!input.admin) return { ...row, state: withIssues(ADMINS, issues), value: null };
	if (v === null) return { ...row, state: withIssues(CHECKING, issues), value: null };
	if (v === 'error') return { ...row, state: withIssues(NOT_READ, issues), value: null };
	if (!v.enabled)
		return {
			...row,
			state: withIssues({ word: 'Off', tone: 'neutral' }, issues),
			value: null,
			sub: 'not watched'
		};
	const now = v.now;
	const state = withIssues(
		internetState[(now?.state ?? 'unknown') as keyof typeof internetState] ?? internetState.unknown,
		issues
	);
	const today = v.days.at(-1)?.availability;
	const wrong = state.tone === 'warning' || state.tone === 'critical';
	return {
		...row,
		state,
		value: now?.ms != null ? ms(now.ms) : null,
		sub: today != null ? `${percent(today)} online today` : undefined,
		sentence: sentenceOf(issues) ?? (wrong ? (now?.why ?? undefined) : undefined)
	};
}

function wifiRow(input: AreaInput): AreaRow | null {
	const issues = issuesIn(input.attention, 'wifi');
	const { clients, accessPoints } = wifiCounts(input.devices);
	if (!accessPoints && !issues.length) return null;
	return {
		id: 'wifi',
		label: 'Wi-Fi',
		href: '/wifi',
		state: withIssues(accessPoints ? OK : NOT_READ, issues),
		value: String(clients),
		sub: `${clients === 1 ? 'client' : 'clients'} on ${count(accessPoints, 'access point')}`,
		sentence: sentenceOf(issues)
	};
}

function storageRow(input: AreaInput): AreaRow | null {
	const issues = issuesIn(input.attention, 'storage');
	const fullest = fullestVolume(input.devices);
	if (!fullest && !issues.length) return null;
	return {
		id: 'storage',
		label: 'Storage',
		href: linkOf(
			issues,
			fullest ? `/devices/${encodeURIComponent(fullest.device.id)}#storage` : '/devices'
		),
		state: withIssues(OK, issues),
		value: fullest ? `${Math.round(fullest.percent)} %` : null,
		sub: fullest
			? `${fullest.mount ? `${fullest.mount} on ` : ''}${fullest.device.name}, the fullest`
			: undefined,
		sentence: sentenceOf(issues)
	};
}

function backupsRow(input: AreaInput): AreaRow | null {
	const issues = issuesIn(input.attention, 'backups');
	const row = { id: 'backups', label: 'Backups', href: linkOf(issues, '/backups') } as const;
	const { backups: jobs, configBackups: configs } = input;
	if (jobs === null) return { ...row, state: withIssues(CHECKING, issues), value: null };
	if (jobs === 'error') return { ...row, state: withIssues(NOT_READ, issues), value: null };
	const list = Array.isArray(configs) ? configs : [];
	const total = jobs.length + list.length;
	const states = [
		...jobs.map((b) => backupState[b.state]),
		...list.map((c) => configBackupState(c.status, !!c.latest)),
		...(input.admin && !Array.isArray(configs) ? [configs === 'error' ? NOT_READ : CHECKING] : [])
	];
	if (!total && !issues.length && !states.length) return null;
	const state = states.sort((a, b) => toneRank[a.tone] - toneRank[b.tone])[0] ?? OK;
	const ok =
		jobs.filter((b) => b.state === 'ok').length +
		list.filter((c) => c.latest && !c.status.error).length;
	return {
		...row,
		state: withIssues(state, issues),
		value: total ? `${ok} of ${total}` : null,
		sub: total ? 'backups OK' : undefined,
		sentence: sentenceOf(issues)
	};
}

function signinsRow(input: AreaInput): AreaRow {
	const row = { id: 'signins', label: 'Sign-ins', href: '/signins' } as const;
	const s = input.signins;
	if (!input.admin) return { ...row, state: ADMINS, value: null };
	if (s === null) return { ...row, state: CHECKING, value: null };
	if (s === 'error') return { ...row, state: NOT_READ, value: null };
	const failed = Object.values(s.summary.by_service).reduce((n, c) => n + c.failed, 0);
	const flag = (['new-outside', 'repeated', 'outside-ok'] as const).find((f) => s.flags[f]);
	const n = flag ? (s.flags[flag] ?? 0) : 0;
	const sentence =
		flag === 'new-outside'
			? `${count(n, 'outside address', 'outside addresses')} signed in for the first time this week`
			: flag === 'repeated'
				? `${count(n, 'address', 'addresses')} with many failed tries`
				: flag === 'outside-ok'
					? `${count(n, 'outside address', 'outside addresses')} signed in`
					: undefined;
	return {
		...row,
		state: flag ? signinFlag[flag] : OK,
		value: String(failed),
		sub: `failed of ${s.total} in 24 h`,
		sentence
	};
}

function updatesRow(input: AreaInput): AreaRow {
	const issues = issuesIn(input.attention, 'updates');
	const ups = input.attention.updates;
	const state = ups.some((u) => u.kind === 'firmware' && !u.supported)
		? updateState.unsupported
		: ups.some((u) => u.kind === 'packages' && u.security)
			? updateState.security
			: ups.length
				? updateState.available
				: updateState.current;
	return {
		id: 'updates',
		label: 'Updates',
		href: '/updates',
		state: withIssues(state, issues),
		value: String(ups.length),
		sub: ups.length === 1 ? 'update waiting' : 'updates waiting',
		sentence: sentenceOf(issues)
	};
}

/** The overview's area rows, in a fixed order; an area with nothing to show is left out. */
export function areasOf(input: AreaInput): AreaRow[] {
	return [
		internetRow(input),
		wifiRow(input),
		storageRow(input),
		backupsRow(input),
		signinsRow(input),
		updatesRow(input)
	].filter((r): r is AreaRow => r !== null);
}

/** The header's line of counts: devices online, services up. Offline devices (asleep, off by
 * choice) aren't online, and their services aren't counted: they aren't problems. */
export function fleetCounts(devices: Device[]): {
	online: number;
	devices: number;
	servicesUp: number;
	services: number;
} {
	let online = 0;
	let services = 0;
	let servicesUp = 0;
	for (const d of devices) {
		const s = statusOf(d);
		if (s === 'up' || s === 'degraded') online += 1;
		if (s === 'offline') continue;
		for (const c of d.checks) {
			if (c.type === 'ping') continue;
			services += 1;
			if (c.up) servicesUp += 1;
		}
	}
	return { online, devices: devices.length, servicesUp, services };
}
