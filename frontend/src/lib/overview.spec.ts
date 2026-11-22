import { expect, it } from 'vitest';
import type {
	Alarm,
	Alarms,
	Backup,
	CheckStatus,
	Device,
	InternetView,
	SignInsSummary
} from './api';
import { attentionOf } from './attention';
import {
	areasOf,
	fleetCounts,
	fullestVolume,
	internetStrip,
	sentenceOf,
	wifiCounts,
	type AreaInput
} from './overview';

const check = (name: string, up: boolean | null) =>
	({ name, type: name === 'ping' ? 'ping' : 'http', target: null, up }) as CheckStatus;
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
const alarm = (key: string, dev: string, more: Partial<Alarm> = {}): Alarm => ({
	key,
	severity: 'warning',
	device: dev,
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
const alarms = (open: Alarm[]): Alarms => ({ open, muted: {}, channels: 0 });
const vitals = (storage: number, mount: string) =>
	({ storage, storage_mount: mount, checked: '' }) as Device['vitals'];

const internet = (state: string, more: Partial<InternetView> = {}) =>
	({
		enabled: true,
		now: { state, ms: 12.6, why: state === 'down' ? 'the line is down' : null },
		series: [
			{ t: 1000, state: 'up', ms: 12, loss: 0, down: null, up: null },
			{ t: 1900, state: 'down', ms: null, loss: 1, down: null, up: null },
			{ t: 2800, state: null, ms: null, loss: null, down: null, up: null }
		],
		days: [{ day: '2026-11-22', availability: 0.9998, down_seconds: 17, degraded_seconds: 0 }],
		...more
	}) as unknown as InternetView;
const signins = (flags: SignInsSummary['flags'] = {}): SignInsSummary => ({
	hours: 24,
	total: 40,
	flags,
	summary: {
		by_service: { ssh: { ok: 5, failed: 12, attempt: 20 }, vpn: { ok: 3, failed: 1, attempt: 0 } },
		outside_addresses: 2,
		outside_ok: 0
	}
});
const job = (state: Backup['state']) => ({ id: state, state }) as Backup;

function input(more: Partial<AreaInput> = {}, devices: Device[] = [], open: Alarm[] = []) {
	return {
		attention: attentionOf(devices, alarms(open)),
		devices,
		admin: true,
		internet: internet('up'),
		backups: [job('ok')],
		configBackups: [],
		signins: signins(),
		...more
	} satisfies AreaInput;
}
const byId = (rows: ReturnType<typeof areasOf>) => Object.fromEntries(rows.map((r) => [r.id, r]));

it('counts Wi-Fi clients the way the Wi-Fi page lists them', () => {
	const ap = device('ap', {
		router: {
			radios: [
				{ interfaces: [{ clients: [{ mac: 'a' }, { mac: 'b' }] }, { clients: [] }] },
				{ interfaces: [{ clients: [{ mac: 'c' }] }] }
			]
		} as unknown as Device['router']
	});
	const box = device('box', {
		fritz: {
			wifi: [{ clients: 9, associated: [{ mac: 'd' }, { mac: 'e' }] }, { clients: 1 }]
		} as unknown as Device['fritz']
	});
	expect(wifiCounts([ap, box, device('pc')])).toEqual({ clients: 5, accessPoints: 2 });
});

it('finds the fullest volume across hosts', () => {
	const a = device('a', { vitals: vitals(67, '/') });
	const b = device('b', { vitals: vitals(92, '/srv/media') });
	expect(fullestVolume([a, b, device('c')])).toMatchObject({ device: b, mount: '/srv/media' });
	expect(fullestVolume([device('c')])).toBeNull();
});

it('turns the internet series into a strip: down is 0, unknown is empty', () => {
	const s = internetStrip(internet('up'))!;
	expect(s.buckets.map((b) => b.uptime)).toEqual([1, 0, null]);
	expect(s.step).toBe(900);
	expect(s.start).toBe(new Date(1_000_000).toISOString());
});

it('says an area’s issues in one sentence', () => {
	const a = attentionOf([], alarms([alarm('backup:x', 'nas'), alarm('backup:y', 'nas')]));
	expect(sentenceOf(a.issues)).toMatch(/, and 1 more$/);
	expect(sentenceOf([])).toBeUndefined();
});

it('shows every area in order, all OK on a quiet day', () => {
	const ap = device('ap', {
		router: { radios: [{ interfaces: [{ clients: [{ mac: 'a' }] }] }] } as Device['router'],
		vitals: vitals(40, '/')
	});
	const rows = areasOf(input({}, [ap]));
	expect(rows.map((r) => r.id)).toEqual([
		'internet',
		'wifi',
		'storage',
		'backups',
		'signins',
		'updates'
	]);
	expect(rows.map((r) => r.state.word)).toEqual(['Online', 'OK', 'OK', 'OK', 'OK', 'Up to date']);
	expect(rows.every((r) => !r.sentence)).toBe(true);
	const r = byId(rows);
	expect(r.internet.value).toBe('13 ms');
	expect(r.internet.sub).toBe('99.98 % online today');
	expect(r.wifi.value).toBe('1');
	expect(r.wifi.sub).toBe('client on 1 access point');
	expect(r.storage.value).toBe('40 %');
	expect(r.backups.value).toBe('1 of 1');
	expect(r.signins.value).toBe('13'); // failed tries
	expect(r.updates.value).toBe('0');
});

it('leaves out areas with nothing to show', () => {
	const rows = areasOf(input({ backups: [] }));
	expect(rows.map((r) => r.id)).toEqual(['internet', 'signins', 'updates']);
});

it('shows backup failures before alarms arrive and unreadable configuration backups', () => {
	expect(byId(areasOf(input({ backups: [job('failed')] }))).backups.state.word).toBe('Failed');
	expect(byId(areasOf(input({ backups: [job('waiting')] }))).backups.state.word).toBe('Waiting');
	expect(byId(areasOf(input({ configBackups: 'error' }))).backups.state.word).toBe('Not read');
	expect(byId(areasOf(input({ backups: [], configBackups: null }))).backups.state.word).toBe(
		'Checking'
	);
});

it('takes the state and the sentence from the issues, and links to the single one', () => {
	const hs = device('homeserver', { vitals: vitals(92, '/srv/media') });
	const rows = byId(
		areasOf(
			input(
				{},
				[hs],
				[
					alarm('host:homeserver:disk:/srv/media', 'homeserver', {
						title: 'Home server: /srv/media is 92 % full'
					}),
					alarm('backup:borg', 'homeserver', { severity: 'critical', title: 'Borg failed' })
				]
			)
		)
	);
	expect(rows.storage.state).toEqual({ word: 'Warning', tone: 'warning' });
	expect(rows.storage.sentence).toBe('Home server: /srv/media is 92 % full');
	expect(rows.storage.href).toBe('/devices/homeserver#storage');
	expect(rows.storage.sub).toBe('/srv/media on HOMESERVER, the fullest');
	expect(rows.backups.state).toEqual({ word: 'Critical', tone: 'critical' });
	expect(rows.backups.href).toBe('/devices/homeserver#backups'); // the one issue's place
});

it('says what is wrong with the line, from its alarm or its own reading', () => {
	const own = byId(areasOf(input({ internet: internet('down') })));
	expect(own.internet.state.word).toBe('Down');
	expect(own.internet.sentence).toBe('the line is down');
	const alarmed = byId(
		areasOf(input({}, [], [alarm('internet:slow', '', { title: 'The internet is slow' })]))
	);
	expect(alarmed.internet.state).toEqual({ word: 'Warning', tone: 'warning' });
	expect(alarmed.internet.sentence).toBe('The internet is slow');
});

it('reads states while loading, failing, and for those who may not see', () => {
	const loading = byId(areasOf(input({ internet: null, signins: null, backups: null })));
	expect([
		loading.internet.state.word,
		loading.signins.state.word,
		loading.backups.state.word
	]).toEqual(['Checking', 'Checking', 'Checking']);
	const failed = byId(areasOf(input({ internet: 'error', signins: 'error' })));
	expect(failed.internet.state.word).toBe('Not read');
	const observer = byId(areasOf(input({ admin: false, internet: null, signins: null })));
	expect(observer.internet.state.word).toBe('Admins only');
	expect(observer.signins.state.word).toBe('Admins only');
	const off = byId(areasOf(input({ internet: internet('up', { enabled: false }) })));
	expect(off.internet.state.word).toBe('Off');
});

it('flags sign-ins by the worst flag', () => {
	const s = byId(areasOf(input({ signins: signins({ repeated: 2, 'new-outside': 1 }) })));
	expect(s.signins.state.word).toBe('New from outside');
	expect(s.signins.sentence).toBe('1 outside address signed in for the first time this week');
	const r = byId(areasOf(input({ signins: signins({ repeated: 2 }) })));
	expect(r.signins.sentence).toBe('2 addresses with many failed tries');
});

it('rates updates: out of support, security, available', () => {
	const hs = device('hs', {
		host_report: { updates: { available: 4, security: 1 } } as Device['host_report']
	});
	const r = byId(areasOf(input({}, [hs])));
	expect(r.updates.state.word).toBe('Security update'); // as worrying as its issue
	expect(r.updates.value).toBe('1');
	expect(r.updates.sentence).toBe('HS: 1 security update waiting');
	const plain = device('pc', {
		host_report: { updates: { available: 2, security: 0 } } as Device['host_report']
	});
	expect(byId(areasOf(input({}, [plain]))).updates.state.word).toBe('Update available');
});

it('counts devices online and services up, leaving out devices that are off', () => {
	const nas = device('nas', {
		state: 'partial',
		checks: [check('ping', true), check('web', false)]
	});
	const pc = device('pc', {
		state: 'down',
		always_on: false,
		checks: [check('ping', false), check('rdp', false)]
	});
	expect(fleetCounts([nas, pc, device('ok')])).toEqual({
		online: 2,
		devices: 3,
		servicesUp: 0,
		services: 1
	});
});
