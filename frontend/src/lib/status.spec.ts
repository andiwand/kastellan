import { describe, expect, it } from 'vitest';
import { deviceBuckets, bucketStatus, mergeBuckets } from './status';
import { stateFor } from './format';

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
