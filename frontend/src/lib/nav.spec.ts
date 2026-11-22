import { expect, it } from 'vitest';
import { badgeOf, isActive, moreBadge, NAV, navFor } from './nav';
import type { Attention, Issue } from './attention';
import type { Device } from './api';
import type { Update } from './updates';

const issue = (area: Issue['area'], tone: Issue['tone'] = 'warning'): Issue => ({
	key: `${area}:x`,
	tone,
	area,
	device: null,
	title: area,
	detail: '',
	since: null,
	href: '/',
	alarm: null
});
const attention = (issues: Issue[], more: Partial<Attention> = {}): Attention => ({
	issues,
	count: issues.length,
	tone: issues.some((i) => i.tone === 'critical') ? 'critical' : issues.length ? 'warning' : 'good',
	problemDevices: [],
	updates: [],
	...more
});
const items = NAV.flatMap((g) => g.items);

it('keeps about ten places, five of them in the phone bar', () => {
	expect(items.length).toBeLessThanOrEqual(12);
	expect(items.filter((i) => i.phone).map((i) => i.label)).toEqual([
		'Overview',
		'Devices',
		'Internet',
		'Wi-Fi'
	]);
	// Observers don't see admin places; groups left empty go.
	const observer = navFor(false).flatMap((g) => g.items.map((i) => i.href));
	expect(observer).not.toContain('/settings');
	expect(observer).toContain('/updates');
});

it('marks old addresses as their new place', () => {
	const at = (path: string) => items.filter((i) => isActive(i, path)).map((i) => i.href);
	expect(at('/vms')).toEqual(['/containers']);
	expect(at('/keys')).toEqual(['/settings']);
	expect(at('/devices/nas')).toEqual(['/devices']);
	expect(at('/')).toEqual(['/']);
	expect(at('/wifi')).toEqual(['/wifi']);
});

it('counts badges from the attention model', () => {
	const a = attention([issue('internet'), issue('services', 'critical'), issue('updates')], {
		problemDevices: [{ id: 'nas' } as Device],
		updates: [{ kind: 'reboot' }, { kind: 'reboot' }] as Update[]
	});
	expect(badgeOf('/', a)).toMatchObject({ count: 3, tone: 'critical' });
	expect(badgeOf('/devices', a)).toMatchObject({ count: 1, tone: 'critical' });
	expect(badgeOf('/updates', a)).toMatchObject({ count: 2, tone: 'warning' });
	expect(badgeOf('/internet', a)).toMatchObject({ tone: 'warning' });
	expect(badgeOf('/internet', a)?.count).toBeUndefined(); // a dot: the overview counts
	expect(badgeOf('/wifi', a)).toBeNull();
	expect(badgeOf('/', attention([]))).toBeNull();
});

it('puts what is under More on its tab: the worst tone and the updates waiting', () => {
	const more = items.filter((i) => !i.phone);
	const a = attention([issue('backups', 'critical')], {
		updates: [{ kind: 'reboot' }] as Update[]
	});
	expect(moreBadge(more, a)).toMatchObject({ count: 1, tone: 'critical' });
	// Updates alone don't make it look like a problem.
	expect(moreBadge(more, attention([], { updates: a.updates }))).toMatchObject({
		tone: 'neutral'
	});
	expect(moreBadge(more, attention([]))).toBeNull();
});
