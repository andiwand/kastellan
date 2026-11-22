import { expect, it } from 'vitest';
import { containerAsk, sameTagUpdates, tagOf, updatesOf } from './updates';
import type { Device } from './api';

const device = (more: Partial<Device>) =>
	({ id: 'ap', name: 'ap', kind: 'openwrt', checks: [], ...more }) as Device;

it('shows new OpenWrt abilities on the helpers row when one setup brings both', () => {
	const rows = updatesOf([
		device({ outdated_scripts: ['openwrt'], new_abilities: { openwrt: ['roaming'] } })
	]);
	expect(rows.map((u) => u.kind)).toEqual(['scripts']);
	expect(rows[0]).toMatchObject({ names: ['openwrt'], offered: ['roaming'] });
});

it('keeps the abilities row when the helpers are current', () => {
	const rows = updatesOf([device({ new_abilities: { openwrt: ['roaming'], actor: ['logs'] } })]);
	expect(rows.map((u) => u.kind)).toEqual(['abilities', 'abilities']);
});

it('leaves out container updates said to be not needed', () => {
	const c = { key: 'bind', name: 'bind', tag: '9.20', update: true, unchecked: null };
	const host = (skipped: boolean) =>
		device({
			kind: 'linux',
			containers: [{ ...c, update_to: '9.21', skipped }] as Device['containers']
		});
	expect(updatesOf([host(false)])).toMatchObject([{ kind: 'container', key: 'bind', to: '9.21' }]);
	expect(updatesOf([host(true)])).toEqual([]);
});

it('asks about compose files and new major versions before another tag', () => {
	const ask = containerAsk({
		name: 'bind',
		from: '9.20',
		to: '10.0',
		compose: '/srv/dns/compose.yaml'
	});
	expect(ask).toContain('from 9.20 to 10.0');
	expect(ask).toContain('major version');
	expect(ask).toContain('/srv/dns/compose.yaml');
	expect(containerAsk({ name: 'app', from: 'latest', to: 'newer build' })).not.toContain('compose');
	expect(tagOf('newer build')).toBeUndefined();
	expect(tagOf('9.21')).toBe('9.21');
});

it('detects major upgrades without an intermediate offer', () => {
	expect(containerAsk({ name: 'app', from: 'v9.20-alpine', to: 'v10.0-alpine' })).toContain(
		'major version'
	);
	expect(containerAsk({ name: 'app', from: '9.20', to: '9.21' })).not.toContain('major version');
	expect(containerAsk({ name: 'app', from: 'latest', to: 'stable' })).not.toContain(
		'major version'
	);
});

it('takes a missing target (WUD) as a new build of the same tag', () => {
	const c = (key: string, update_to: string | null, skipped = false) =>
		({
			key,
			name: key,
			tag: 'latest',
			update: true,
			unchecked: null,
			update_to,
			skipped
		}) as NonNullable<Device['containers']>[number];
	const host = device({
		kind: 'linux',
		containers: [c('a', null), c('b', 'newer build'), c('c', '2.0'), c('d', null, true)]
	});
	expect(updatesOf([host]).map((u) => u.kind === 'container' && u.to)).toEqual([
		'newer build',
		'newer build',
		'2.0'
	]);
	expect(sameTagUpdates(host.containers!).map((c) => c.key)).toEqual(['a', 'b']);
	expect(tagOf(null)).toBeUndefined();
});
