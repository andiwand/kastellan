import type { Bucket, CheckStatus, Device } from './api';

/** What a device's state means to a person: a laptop that is off is offline, not down. */
export type Status = 'up' | 'degraded' | 'down' | 'offline' | 'waiting';

export const statusLabel: Record<Status, string> = {
	up: 'Online',
	degraded: 'Degraded',
	down: 'Down',
	offline: 'Offline',
	waiting: 'Checking'
};

/** CSS color and text color for each status. */
export const statusColor: Record<Status, { fill: string; text: string }> = {
	up: { fill: 'var(--good)', text: 'var(--good-text)' },
	degraded: { fill: 'var(--warning)', text: 'var(--warning-text)' },
	down: { fill: 'var(--critical)', text: 'var(--critical-text)' },
	offline: { fill: 'var(--offline)', text: 'var(--muted)' },
	waiting: { fill: 'var(--offline)', text: 'var(--muted)' }
};

/** A UPS's state as a status: on battery, a battery to replace or no contact (an alarm says so)
 * is degraded, low battery down; nothing read yet is waiting. */
export const upsStatus: Record<string, Status> = {
	online: 'up',
	on_battery: 'degraded',
	replace_battery: 'degraded',
	low_battery: 'down',
	no_contact: 'degraded',
	unknown: 'waiting'
};
export const upsLabel: Record<string, string> = {
	online: 'On mains',
	on_battery: 'On battery',
	replace_battery: 'Replace battery',
	low_battery: 'Battery low',
	no_contact: 'No contact',
	unknown: 'Unknown',
	'': 'Not read yet'
};

export function statusOf(device: Device): Status {
	if (device.kind !== 'ups') return checkedStatus(device);
	// A UPS: what its host reads of it and its own checks (a network card), the worse of the two.
	const read = device.ups ? (upsStatus[device.ups.state] ?? 'waiting') : 'waiting';
	const checked = device.checks.length ? checkedStatus(device) : 'waiting';
	const rank: Partial<Record<Status, number>> = {
		waiting: 0,
		up: 1,
		offline: 2,
		degraded: 3,
		down: 4
	};
	return (rank[read] ?? 0) >= (rank[checked] ?? 0) ? read : checked;
}

function checkedStatus(device: Device): Status {
	switch (device.state) {
		case 'up':
			return 'up';
		case 'partial':
			return 'degraded';
		case 'down':
			return device.always_on ? 'down' : 'offline';
		default:
			return 'waiting';
	}
}

/** One check's status: down only matters while its device is on (or should be); a laptop that
 * is off has its services offline, but a service down on a laptop that is on is down. */
export function checkStatusOf(device: Device, check: CheckStatus): Status {
	if (check.up == null) return 'waiting';
	if (check.up) return 'up';
	return statusOf(device) === 'offline' ? 'offline' : 'down';
}

/** Whether someone should look at it: a degraded device, or a down one that should be on. */
export function needsAttention(device: Device) {
	const s = statusOf(device);
	return s === 'down' || s === 'degraded';
}

/** One device's buckets from its checks' buckets: the mean of its checks' uptimes, so a bucket
 * is fully up only when every check was, fully down only when none was, and in between (degraded)
 * when some checks failed, which matches the device's badge. Latency from the first check that has
 * one (ping, usually). */
export function deviceBuckets(checks: Record<string, Bucket[]> | undefined): Bucket[] {
	const lists = Object.values(checks ?? {});
	if (!lists.length) return [];
	return lists[0].map((_, i) => {
		const ups = lists.map((l) => l[i]?.uptime).filter((u): u is number => u != null);
		const lat = lists.map((l) => l[i]?.latency_ms).find((v) => v != null) ?? null;
		return {
			uptime: ups.length ? ups.reduce((a, b) => a + b, 0) / ups.length : null,
			latency_ms: lat
		};
	});
}

/** Share of time up over buckets that have data, or null. */
export function uptimeOf(buckets: Bucket[]): number | null {
	const known = buckets.map((b) => b.uptime).filter((u): u is number => u != null);
	return known.length ? known.reduce((a, b) => a + b, 0) / known.length : null;
}

/** A bucket's status for the uptime strip. */
export function bucketStatus(uptime: number | null, alwaysOn: boolean): Status | 'empty' {
	if (uptime == null) return 'empty';
	if (uptime >= 0.999) return 'up';
	if (uptime <= 0.001) return alwaysOn ? 'down' : 'offline';
	return 'degraded';
}

/** Merge every `group` neighbouring buckets into one: the mean of the known uptimes and
 * latencies. A merged bucket without any data stays empty. */
export function mergeBuckets(buckets: Bucket[], group: number): Bucket[] {
	if (group <= 1) return buckets;
	const out: Bucket[] = [];
	for (let i = 0; i < buckets.length; i += group) {
		const part = buckets.slice(i, i + group);
		const ups = part.map((b) => b.uptime).filter((u): u is number => u != null);
		const lats = part.map((b) => b.latency_ms).filter((v): v is number => v != null);
		out.push({
			uptime: ups.length ? ups.reduce((a, b) => a + b, 0) / ups.length : null,
			latency_ms: lats.length ? lats.reduce((a, b) => a + b, 0) / lats.length : null
		});
	}
	return out;
}
