import { expect, it } from 'vitest';
import { alarmTone, areaOf, attentionOf, hrefOf, issuesIn, issuesOf } from './attention';
import type { Alarm, Alarms, CheckStatus, Device } from './api';

const check = (name: string, up: boolean, more: Partial<CheckStatus> = {}) =>
	({ name, type: name === 'ping' ? 'ping' : 'http', target: null, up, ...more }) as CheckStatus;
const device = (id: string, more: Partial<Device> = {}) =>
	({
		id,
		name: id.toUpperCase(),
		kind: 'server',
		state: 'up',
		always_on: true,
		checks: [check('ping', true)],
		...more
	}) as Device;
const alarm = (key: string, device: string, more: Partial<Alarm> = {}): Alarm => ({
	key,
	severity: 'warning',
	device,
	title: key,
	detail: '',
	opened: '2026-11-22T10:00:00Z',
	since: null,
	hidden: [],
	acknowledged_by: null,
	acknowledged_note: null,
	acknowledged_at: null,
	...more
});
const alarms = (open: Alarm[], more: Partial<Alarms> = {}): Alarms => ({
	open,
	muted: {},
	channels: 0,
	...more
});

it('counts every open alarm, so the headline matches the list', () => {
	const a = attentionOf(
		[device('nas'), device('homeserver')],
		alarms([
			alarm('host:homeserver:disk:/srv/media', 'homeserver'),
			alarm('cert:homeserver:web', 'homeserver'),
			alarm('backup:borg', 'homeserver')
		])
	);
	expect(a.count).toBe(3);
	expect(a.issues).toHaveLength(a.count);
	expect(issuesIn(a, 'storage').map((i) => i.href)).toEqual(['/devices/homeserver#storage']);
	expect(issuesOf(a, 'homeserver')).toHaveLength(3);
});

it('lists a down service once: by its alarm when there is one, else by its check', () => {
	const nas = device('nas', {
		state: 'partial',
		checks: [check('ping', true), check('jellyfin', false, { detail: 'HTTP 502' })]
	});
	const alarmed = attentionOf([nas], alarms([alarm('service:nas:jellyfin', 'nas')]));
	expect(alarmed.issues.map((i) => [i.key, i.tone, i.alarm?.key])).toEqual([
		['service:nas:jellyfin', 'critical', 'service:nas:jellyfin']
	]);
	const early = attentionOf([nas], alarms([])); // before the alarm's 3 minutes
	expect(early.issues).toMatchObject([
		{
			key: 'service:nas:jellyfin',
			tone: 'critical',
			title: 'NAS: jellyfin is down',
			detail: 'HTTP 502'
		}
	]);
	expect(early.problemDevices.map((d) => d.id)).toEqual(['nas']);
});

it('shows a down service as critical, like its check', () => {
	expect(alarmTone({ key: 'service:nas:jellyfin', severity: 'warning' })).toBe('critical');
	expect(alarmTone({ key: 'cert:nas:web', severity: 'warning' })).toBe('warning');
	expect(alarmTone({ key: 'down:nas', severity: 'critical' })).toBe('critical');
});

it('leaves out a device behind a device that is down, and muted ones', () => {
	const down = { state: 'down' as const, checks: [check('ping', false)] };
	const devices = [device('router', down), device('ap', { ...down, parent: 'router' })];
	const a = attentionOf(devices, alarms([alarm('down:router', 'router', { hidden: ['ap'] })]));
	expect(a.issues.map((i) => i.key)).toEqual(['down:router']);
	const muted = attentionOf(
		[device('ap', down)],
		alarms([], { muted: { ap: '2999-01-01T00:00:00Z' } })
	);
	expect(muted.count).toBe(0);
});

it("doesn't count a laptop that is off", () => {
	const laptop = device('laptop', {
		kind: 'laptop',
		always_on: false,
		state: 'down',
		checks: [check('ping', false)]
	});
	const a = attentionOf([laptop], null);
	expect(a.count).toBe(0);
	expect(a.tone).toBe('good');
});

it('counts every update, and makes issues only of those that matter', () => {
	const host = device('homeserver', {
		host_report: { updates: { available: 3, security: 1 } } as Device['host_report']
	});
	const ap = device('ap', {
		router: {
			firmware: { current: '23.05.2', next_step: '23.05.6', supported: false }
		} as Device['router']
	});
	const a = attentionOf([host, ap], alarms([alarm('router:ap:firmware', 'ap')]));
	expect(a.updates).toHaveLength(2);
	// The firmware's alarm speaks for it; the security update is an issue of its own.
	expect(a.issues.map((i) => i.key).sort()).toEqual([
		'router:ap:firmware',
		'updates:homeserver:security'
	]);
	expect(issuesIn(a, 'updates')).toHaveLength(2);
});

it('puts critical issues first, then the newest', () => {
	const a = attentionOf(
		[],
		alarms([
			alarm('cert:a:web', 'a', { opened: '2026-11-22T09:00:00Z' }),
			alarm('backup:b', 'b', { opened: '2026-11-22T11:00:00Z' }),
			alarm('down:c', 'c', { severity: 'critical', opened: '2026-11-22T08:00:00Z' })
		])
	);
	expect(a.issues.map((i) => i.key)).toEqual(['down:c', 'backup:b', 'cert:a:web']);
	expect(a.tone).toBe('critical');
});

it('knows where each kind of alarm belongs', () => {
	expect(areaOf('host:hs:disk:/srv')).toBe('storage');
	expect(areaOf('host:hs:raid:md0')).toBe('storage');
	expect(areaOf('host:hs:collector')).toBe('devices');
	expect(areaOf('host:hs:services')).toBe('services');
	expect(areaOf('router:ap:firmware')).toBe('updates');
	expect(areaOf('hubbackup')).toBe('backups');
	expect(areaOf('internet:down')).toBe('internet');
	expect(hrefOf('backups', null)).toBe('/backups');
	expect(hrefOf('internet', 'fritzbox')).toBe('/internet');
	expect(hrefOf('certificates', 'vps')).toBe('/devices/vps#services');
});
