import { describe, expect, it } from 'vitest';
import type { Device, WifiNetwork, WifiRadio, WifiScanView } from './api';
import {
	accessPoints,
	busiest,
	channelOf,
	channelUse,
	clientMatches,
	covers,
	networkState,
	openFindings,
	radioState,
	signalState,
	signalText
} from './wifi';

const radio = (over: Partial<WifiRadio>): WifiRadio => ({
	device: 'ap',
	radio: 'radio0',
	kind: 'openwrt',
	band: '2g',
	channel: 6,
	width: 20,
	htmode: 'HE20',
	clients: 1,
	neighbours: 0,
	hears: {},
	busy: null,
	scanned: null,
	allowed: null,
	...over
});

it('keeps a network partly on when one radio of its access point is disabled', () => {
	const network = {
		on: [
			{ device: 'ap', disabled: true },
			{ device: 'ap', disabled: false },
			{ device: 'box', disabled: true }
		]
	} as WifiNetwork;
	expect(networkState(network).word).toBe('Partly off');
	expect(networkState(network, 'ap').word).toBe('Partly off');
	expect(networkState(network, 'box').word).toBe('Off');
	expect(networkState(network, 'missing').word).toBe('Not read');
});

describe('signal in words', () => {
	it('reads dBm from OpenWrt and percent from a FRITZ!Box', () => {
		expect(signalState({ kind: 'openwrt', signal: -48 })?.word).toBe('Good');
		expect(signalState({ kind: 'openwrt', signal: -70 })?.word).toBe('Fair');
		expect(signalState({ kind: 'openwrt', signal: -80 })).toEqual({
			word: 'Weak',
			tone: 'warning'
		});
		expect(signalState({ kind: 'fritzbox', signal: 64 })?.word).toBe('Good');
		expect(signalState({ kind: 'fritzbox', signal: 22 })?.word).toBe('Weak');
		expect(signalState({ kind: 'openwrt', signal: null })).toBeNull();
		expect(signalText({ kind: 'fritzbox', signal: 64 })).toBe('64 %');
		expect(signalText({ kind: 'openwrt', signal: -48 })).toBe('-48 dBm');
	});
});

describe('access points', () => {
	it('lists radios with their networks and the FRITZ!Mesh repeaters', () => {
		const networks: WifiNetwork[] = [
			{
				ssid: 'Castle',
				on: [
					{
						device: 'ap',
						section: 's',
						radio: 'radio0',
						band: '2g',
						encryption: null,
						key: null,
						disabled: false,
						kind: 'openwrt',
						guest: false
					},
					{
						device: 'box',
						section: 'wlan1',
						radio: 'wlan1',
						band: '2g',
						encryption: null,
						key: null,
						disabled: false,
						kind: 'fritzbox',
						guest: false
					}
				],
				devices: ['ap', 'box'],
				bands: ['2g'],
				same_key: true,
				same_encryption: true,
				off: [],
				guest: false
			},
			{
				ssid: 'Guests',
				on: [
					{
						device: 'box',
						section: 'wlan3',
						radio: 'wlan3',
						band: '2g',
						encryption: null,
						key: null,
						disabled: false,
						kind: 'fritzbox',
						guest: true
					}
				],
				devices: ['box'],
				bands: ['2g'],
				same_key: null,
				same_encryption: true,
				off: [],
				guest: true
			}
		];
		const box = {
			id: 'box',
			name: 'Box',
			fritz: {
				mesh: {
					members: [
						{
							uid: 'n1',
							name: 'fritz.box',
							model: null,
							role: 'master',
							mac: null,
							firmware: null
						},
						{
							uid: 'n2',
							name: 'Repeater hall',
							model: 'FRITZ!Repeater',
							role: 'repeater',
							mac: null,
							firmware: null
						}
					],
					links: [{ a: 'n1', b: 'n2', type: 'WLAN', rate_rx: null, rate_tx: null, signal: -58 }]
				}
			}
		} as unknown as Device;
		const rows = accessPoints(
			[
				radio({ radio: 'radio1', band: '5g', channel: 36, width: 80, mesh: 'm', state: 'off' }),
				radio({}),
				radio({ device: 'box', radio: 'wlan1', kind: 'fritzbox' })
			],
			networks,
			[box],
			(id) => ({ ap: 'AP', box: 'Box' })[id] ?? id
		);
		expect(rows.map((r) => `${r.name} ${r.band ?? ''}`.trim())).toEqual([
			'AP 2g',
			'AP 5g',
			'Box 2g',
			'Repeater hall'
		]);
		expect(rows[0].networks).toEqual(['Castle']);
		expect(rows[1].note).toBe('5 GHz · mesh m');
		expect(rows[1].state.word).toBe('Off');
		expect(rows[2].networks).toEqual(['Castle', 'Guests']);
		expect(rows[2].note).toBe('2.4 GHz · FRITZ!Mesh master');
		expect(rows[3].note).toBe('FRITZ!Mesh repeater · Wi-Fi -58 dBm to Box · FRITZ!Repeater');
		expect(rows[3].device).toBeNull();
	});
	it('says a radio meant to be on that is not up is down', () => {
		expect(radioState({ state: 'down' }).tone).toBe('critical');
		expect(radioState({}).word).toBe('On');
	});
});

describe('channels', () => {
	it('knows what a radio covers', () => {
		expect(channelOf(2437)).toBe(6);
		expect(channelOf(5180)).toBe(36);
		expect(covers(6, 20, '2g')).toEqual([4, 5, 6, 7, 8]);
		expect(covers(1, 20, '2g')).toEqual([1, 2, 3]);
		expect(covers(44, 80, '5g')).toEqual([36, 40, 44, 48]);
		expect(covers(108, 40, '5g')).toEqual([108, 112]);
		expect(covers(36, 20, '5g')).toEqual([36]);
	});
	it('takes the busiest measure per channel and who was heard there', () => {
		const scan = (busy: number, signal: number): WifiScanView => ({
			t: 0,
			band: '2g',
			channel: 6,
			neighbours: { '6': [{ ssid: 'Nb', signal, width: 20 }] },
			survey: [{ mhz: 2437, busy, noise: -92 }]
		});
		const use = channelUse('2g', { 'a/radio0': scan(40, -70), 'b/radio0': scan(64, -60) }, (id) =>
			id.toUpperCase()
		);
		const six = use.find((u) => u.channel === 6)!;
		expect(use).toHaveLength(13);
		expect(six.busy).toBe(64);
		expect(six.by).toEqual([
			{ name: 'A', busy: 40 },
			{ name: 'B', busy: 64 }
		]);
		expect(six.heard).toEqual([{ ssid: 'Nb', signal: -60 }]);
		expect(use[0].busy).toBeNull();
		expect(busiest({ x: scan(64, -60) })).toEqual({ band: '2g', channel: 6, busy: 64 });
		expect(busiest({})).toBeNull();
	});
});

describe('client search', () => {
	it('finds a client by name, address, network or access point', () => {
		const c = {
			device: 'ap',
			radio: 'radio0',
			band: '5g',
			ssid: 'Castle',
			kind: 'openwrt' as const,
			mac: '3c:22:fb:10:bb:00',
			name: 'Pixel 9',
			signal: -48,
			issues: []
		};
		expect(clientMatches(c, 'pixel', 'Kitchen')).toBe(true);
		expect(clientMatches(c, 'kitchen', 'Kitchen')).toBe(true);
		expect(clientMatches(c, 'bb:00', 'Kitchen')).toBe(true);
		expect(clientMatches(c, 'mac', 'Kitchen')).toBe(false);
		expect(clientMatches(c, ' ', 'Kitchen')).toBe(true);
	});
});

describe('findings', () => {
	it('leaves out what an open alarm says and keeps one device', () => {
		const f = (key: string, devices: string[], alarms: string[] = []) => ({
			key,
			level: 'warning' as const,
			text: key,
			fix: '',
			devices,
			action: null,
			alarms
		});
		const all = [
			f('view', ['a'], ['roaming:g:a:view']),
			f('wps', ['box']),
			f('two', ['a', 'b'], ['x', 'y'])
		];
		expect(openFindings(all, ['roaming:g:a:view', 'x']).map((x) => x.key)).toEqual(['wps', 'two']);
		expect(openFindings(all, [], 'a').map((x) => x.key)).toEqual(['view', 'two']);
	});
});
