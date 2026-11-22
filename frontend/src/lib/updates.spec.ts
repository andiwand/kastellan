import { expect, it } from 'vitest';
import {
	byUrgency,
	containerAsk,
	sameTagUpdates,
	setupFor,
	skipOf,
	tagOf,
	updateStateOf,
	updatesOf,
	urgencyOf
} from './updates';
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

it('puts security fixes and firmware out of support first, then reboots, then the rest', () => {
	const host = device({
		id: 'srv',
		kind: 'linux',
		act: true,
		host_report: { updates: { available: 4, security: 1, reboot: true } },
		containers: [
			{ key: 'app', name: 'app', tag: '1', update: true, unchecked: null, update_to: '2' }
		] as Device['containers']
	} as Partial<Device>);
	const ap = device({
		router: { firmware: { current: '22.03', next_step: '23.05', supported: false } }
	} as Partial<Device>);
	const helper = device({ id: 'h', outdated_scripts: ['collector'] });
	const rows = byUrgency(updatesOf([helper, host, ap]));
	expect(rows.map((u) => [u.kind, urgencyOf(u)])).toEqual([
		['firmware', 'urgent'],
		['packages', 'urgent'],
		['reboot', 'reboot'],
		['container', 'other'],
		['scripts', 'kastellan']
	]);
	expect(rows.map((u) => updateStateOf(u).word)).toEqual([
		'Out of support',
		'Security update',
		'Reboot waiting',
		'Update available',
		'Update available'
	]);
});

it('says what to set up instead of a blocker, and skips with one verb', () => {
	const [pk] = updatesOf([
		device({
			id: 'srv',
			name: 'Server',
			host_report: { updates: { available: 2 } }
		} as Partial<Device>)
	]);
	expect(setupFor(pk)).toMatchObject({ label: 'Set up actions', href: '/devices/srv?tab=setup' });
	expect(setupFor({ ...pk, device: { ...pk.device, act: true } as Device })).toBeNull();
	const fw = updatesOf([
		device({
			router: { firmware: { current: 'a', next_step: 'b', supported: true } }
		} as Partial<Device>)
	])[0];
	expect(setupFor(fw)?.label).toBe('Set up the OpenWrt login');
	expect(skipOf(fw).label).toBe('Skip this version');
	expect(skipOf(pk).label).toMatch(/^Skip /);
});
