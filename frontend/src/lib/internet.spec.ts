import { expect, it } from 'vitest';
import { diagnose } from './internet';
import type { InternetSample, InternetView } from './api';

const sample = (more: Partial<InternetSample> = {}): InternetSample => ({
	t: 0,
	state: 'up',
	step: null,
	router_ms: 0.5,
	line: true,
	loss: 0,
	ms: 12,
	jitter: 1,
	v6_loss: null,
	v6_ms: null,
	dns_ms: 8,
	hop_ms: 6.8,
	hop_loss: 0,
	down: 11e6,
	up: 1e6,
	note: null,
	...more
});
const view = (now: InternetSample | null, more: Partial<InternetView> = {}) =>
	({
		now,
		speedtests: [],
		speedtest: { booked_down: null },
		...more
	}) as unknown as InternetView;

it('says the line is fine with the numbers along the way', () => {
	const d = diagnose(view(sample()), [], 'FRITZ!Box');
	expect(d.tone).toBe('good');
	expect(d.headline).toBe('The line is fine: FRITZ!Box 0.50 ms, provider 6.8 ms, internet 12 ms.');
	expect(d.hops.map((h) => [h.id, h.tone])).toEqual([
		['wifi', 'neutral'],
		['router', 'good'],
		['line', 'good'],
		['provider', 'good'],
		['internet', 'good'],
		['dns', 'good']
	]);
	expect(d.hops[0].seen).toBe(false); // the hub is wired: Wi-Fi isn't measured
});

it('places an outage at the step that fails', () => {
	const d = diagnose(view(sample({ state: 'down', step: 'provider', hop_ms: null, ms: null })));
	expect(d.tone).toBe('critical');
	expect(d.headline).toMatch(/^Down at the provider/);
	expect(d.hops.find((h) => h.id === 'provider')?.tone).toBe('critical');
	expect(d.hops.find((h) => h.id === 'router')?.tone).toBe('good');
});

it('finds where it is slow: home, provider or beyond', () => {
	const home = diagnose(view(sample({ state: 'degraded', router_ms: 45, ms: 180 })));
	expect(home.headline).toMatch(/^Slow at home/);
	const provider = diagnose(view(sample({ state: 'degraded', hop_ms: 90, ms: 160 })));
	expect(provider.headline).toMatch(/^Slow at the provider/);
	const beyond = diagnose(view(sample({ state: 'degraded', ms: 40, loss: 0.2 })));
	expect(beyond.headline).toMatch(/^Slow beyond the provider: .* loses 20 %/);
});

it('adds Wi-Fi issues, bufferbloat and a busy line', () => {
	const d = diagnose(
		view(sample({ down: 95e6 }), {
			speedtests: [{ t: 1, down: 1e8, up: 1e7, ms_idle: 10, ms_loaded: 80, server: 'x', by: null }],
			speedtest: { booked_down: 100 } as InternetView['speedtest']
		}),
		['warning']
	);
	expect(d.wifi).toBe(1);
	expect(d.hops[0]).toMatchObject({ id: 'wifi', tone: 'warning', value: '1 issue' });
	expect(d.notes.join(' ')).toContain('bufferbloat');
	expect(d.notes.join(' ')).toContain('busy');
});

it('says when nothing is read yet', () => {
	expect(diagnose(view(null)).tone).toBe('neutral');
	expect(diagnose(view(sample({ state: 'unknown', note: 'the hub may not ping' }))).headline).toBe(
		'Not read: the hub may not ping.'
	);
});
