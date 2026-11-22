import { expect, it } from 'vitest';
import type { Device } from './api';
import type { Issue } from './attention';
import {
	issueHref,
	presentOf,
	readable,
	sectionsOf,
	shortTitle,
	sourcesOf,
	systemOf,
	type SectionId
} from './devicePage';

const device = (more: Partial<Device> = {}) =>
	({
		id: 'nas',
		name: 'NAS',
		host: 'nas.lan',
		kind: 'server',
		tags: [],
		checks: [],
		...more
	}) as Device;
const all: SectionId[] = [
	'ups',
	'internet',
	'wifi',
	'network',
	'vitals',
	'storage',
	'services',
	'containers',
	'vm',
	'vms',
	'software',
	'backups',
	'vulnerabilities',
	'history'
];

it('starts with what the device is for', () => {
	expect(sectionsOf('router', all).slice(0, 4)).toEqual([
		'internet',
		'wifi',
		'network',
		'services'
	]);
	expect(sectionsOf('access-point', all)[0]).toBe('wifi');
	expect(sectionsOf('server', all).slice(0, 6)).toEqual([
		'vitals',
		'storage',
		'services',
		'containers',
		'vms',
		'vm'
	]);
	expect(sectionsOf('ups', all)[0]).toBe('ups');
	expect(sectionsOf('printer', all).slice(0, 2)).toEqual(['services', 'vitals']);
	expect(sectionsOf('camera', all)).toEqual(sectionsOf('printer', all));
});

it('shows every present section once, empty ones hidden, the history last', () => {
	for (const kind of ['router', 'access-point', 'switch', 'server', 'pc', 'ups', 'camera']) {
		const order = sectionsOf(kind, all);
		expect(new Set(order).size).toBe(all.length);
		expect(order.at(-1)).toBe('history');
	}
	expect(sectionsOf('router', ['history', 'services', 'wifi'])).toEqual([
		'wifi',
		'services',
		'history'
	]);
	// A router with containers still shows them, after its own sections.
	expect(sectionsOf('router', ['containers', 'internet'])).toEqual(['internet', 'containers']);
});

it('knows which sections have something', () => {
	expect(presentOf(device())).toEqual(['backups', 'history']);
	const host = device({
		checks: [{ name: 'ping' }] as Device['checks'],
		vitals: {} as Device['vitals'],
		host_report: { updates: { reboot: false, available: 0, security: 0 } },
		containers: []
	});
	expect(presentOf(host)).toEqual([
		'vitals',
		'storage',
		'services',
		'containers',
		'software',
		'backups',
		'history'
	]);
	expect(presentOf(device(), 2)).toContain('services');
	const box = device({
		kind: 'router',
		fritz: { internet: {}, wifi: [{}], clients: [] } as unknown as Device['fritz']
	});
	expect(presentOf(box)).toEqual(
		expect.arrayContaining(['internet', 'wifi', 'software'] as SectionId[])
	);
	expect(presentOf(box)).not.toContain('network');
});

it('says where the readings come from and whether one could be set up', () => {
	expect(sourcesOf(device())).toEqual([]);
	expect(readable(device())).toBe(true);
	expect(readable(device({ kind: 'printer' }))).toBe(false);
	expect(readable(device({ kind: 'ups', host: '' }))).toBe(false);
	const s = sourcesOf(device({ collect: true, host_checked: 'x', host_error: null }));
	expect(s).toEqual([{ name: 'the collector', at: 'x', error: null, collector: true }]);
	expect(sourcesOf(device({ openwrt: true, router_error: 'timeout' }))[0].error).toBe('timeout');
});

it('names the firmware, else the kernel', () => {
	expect(systemOf(device())).toBeNull();
	expect(
		systemOf(device({ info: { model: null, name: null, firmware: '24.10.1 r28597' } }))
	).toEqual({ label: 'Firmware', value: '24.10.1 r28597' });
	expect(
		systemOf(
			device({ host_report: { system: { kernel: '6.8.0' } } as unknown as Device['host_report'] })
		)
	).toEqual({ label: 'Kernel', value: '6.8.0' });
});

it('leaves the device name out of its own issues', () => {
	expect(shortTitle('NAS: jellyfin is down', 'NAS')).toBe('Jellyfin is down');
	expect(shortTitle('Internet is down', 'NAS')).toBe('Internet is down');
});

it('links an issue to its section on this page', () => {
	const issue = (area: Issue['area'], href: string) =>
		({
			key: 'k',
			tone: 'warning',
			area,
			device: 'nas',
			title: '',
			detail: '',
			since: null,
			href,
			alarm: null
		}) as Issue;
	expect(issueHref(issue('storage', '/devices/nas#storage'), 'nas', [])).toBe('#storage');
	expect(issueHref(issue('wifi', '/wifi'), 'nas', ['wifi'])).toBe('#wifi');
	expect(issueHref(issue('wifi', '/wifi'), 'nas', [])).toBe('/wifi');
	expect(issueHref(issue('devices', '/devices/nas'), 'nas', [])).toBe('#top');
});
