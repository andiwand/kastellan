import type { Bucket, Device } from './api';

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

export function statusOf(device: Device): Status {
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

/** Whether someone should look at it: a degraded device, or a down one that should be on. */
export function needsAttention(device: Device) {
	const s = statusOf(device);
	return s === 'down' || s === 'degraded';
}

/** One device's buckets from its checks' buckets: the worst check decides, latency from the
 * first check that has one (ping, usually). */
export function deviceBuckets(checks: Record<string, Bucket[]> | undefined): Bucket[] {
	const lists = Object.values(checks ?? {});
	if (!lists.length) return [];
	return lists[0].map((_, i) => {
		const ups = lists.map((l) => l[i]?.uptime).filter((u): u is number => u != null);
		const lat = lists.map((l) => l[i]?.latency_ms).find((v) => v != null) ?? null;
		return { uptime: ups.length ? Math.min(...ups) : null, latency_ms: lat };
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
