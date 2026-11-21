import { describe, expect, it } from 'vitest';
import { deviceBuckets, bucketStatus, mergeBuckets, statusOf } from './status';
import type { Device } from './api';
import { stateFor } from './format';

it('uses reachability checks for a network UPS without a collector reading', () => {
	const device = { kind: 'ups', state: 'up', checks: [{}], ups: null } as Device;
	expect(statusOf(device)).toBe('up');
	expect(statusOf({ ...device, checks: [] })).toBe('waiting');
	expect(statusOf({ ...device, ups: { state: 'on_battery' } as Device['ups'] })).toBe('degraded');
});

it('shows a UPS by the worse of its reading and its own checks', () => {
	const ups = (state: string) => ({ state }) as Device['ups'];
	const device = { kind: 'ups', state: 'down', always_on: true, checks: [{}] } as Device;
	expect(statusOf({ ...device, ups: ups('online') })).toBe('down'); // its network card is down
	expect(statusOf({ ...device, state: 'up', ups: ups('no_contact') })).toBe('degraded');
	expect(statusOf({ ...device, state: 'up', ups: ups('low_battery') })).toBe('down');
	expect(statusOf({ ...device, checks: [], ups: ups('online') })).toBe('up'); // read only
});

describe('deviceBuckets', () => {
	it('agrees with the badge: one failing check of two is degraded, not down', () => {
		const [b] = deviceBuckets({
			ping: [{ uptime: 1, latency_ms: 2 }],
			web: [{ uptime: 0, latency_ms: null }]
		});
		expect(b).toEqual({ uptime: 0.5, latency_ms: 2 });
		expect(bucketStatus(b.uptime, true)).toBe('degraded');
	});
	it('is down only when every check was down', () => {
		const [b] = deviceBuckets({
			ping: [{ uptime: 0, latency_ms: null }],
			web: [{ uptime: 0, latency_ms: null }]
		});
		expect(bucketStatus(b.uptime, true)).toBe('down');
		expect(bucketStatus(b.uptime, false)).toBe('offline');
	});
});

describe('stateFor', () => {
	const now = Date.parse('2026-01-01T12:00:00Z');
	it('says "at least" when the start is not known exactly', () => {
		expect(stateFor({ since: '2026-01-01T09:00:00Z', since_exact: false }, now)).toBe(
			'at least 3 h 00 min'
		);
		expect(stateFor({ since: '2026-01-01T11:48:00Z', since_exact: true }, now)).toBe('12 min');
		expect(stateFor({ since: null }, now)).toBe('');
	});
});

describe('mergeBuckets', () => {
	it('averages neighbours and keeps empty stretches empty', () => {
		const b = (uptime: number | null, latency_ms: number | null = null) => ({ uptime, latency_ms });
		expect(mergeBuckets([b(1, 2), b(0), b(null), b(null), b(1, 4)], 2)).toEqual([
			b(0.5, 2),
			b(null),
			b(1, 4)
		]);
		const same = [b(1)];
		expect(mergeBuckets(same, 1)).toBe(same);
	});
});
