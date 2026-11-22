import { describe, expect, it } from 'vitest';
import type { FritzReport } from './api';
import { waysIn, waysInUnknown } from './router';

const fritz = (over: Partial<FritzReport>): FritzReport =>
	({
		model: null,
		firmware: null,
		uptime: null,
		internet: {},
		wifi: [],
		clients: [],
		forwards: [],
		remote: { remote_access: false, myfritz: false },
		...over
	}) as FritzReport;

describe('waysIn', () => {
	it('lists enabled port forwards, remote access and MyFRITZ!, the open ones marked', () => {
		const rows = waysIn(
			fritz({
				forwards: [
					{
						enabled: true,
						protocol: 'TCP',
						port: 443,
						to: '192.168.178.20',
						to_port: 8443,
						name: 'web'
					},
					{
						enabled: false,
						protocol: 'UDP',
						port: 51820,
						to: '192.168.178.2',
						to_port: 51820,
						name: null
					}
				],
				remote: { remote_access: true, remote_port: 8443, myfritz: true }
			})
		);
		expect(rows.map((r) => [r.kind, r.what, r.warn])).toEqual([
			['Port forward', 'TCP 443 → 192.168.178.20:8443', true],
			['Remote access', 'HTTPS, port 8443', true],
			['MyFRITZ!', null, false]
		]);
	});

	it('knows when "none" is only what could be read', () => {
		expect(waysIn(fritz({}))).toEqual([]);
		expect(waysInUnknown(fritz({}))).toBe(false);
		expect(waysInUnknown(fritz({ forwards: null }))).toBe(true);
		expect(waysInUnknown(fritz({ remote: {} }))).toBe(true);
	});
});
