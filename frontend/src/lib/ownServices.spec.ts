import { expect, it } from 'vitest';
import type { CheckStatus, Device } from './api';
import { groupOwn, ownServicesOf } from './ownServices';

const check = (name: string, type: CheckStatus['type'], target: string | number | null) =>
	({ name, type, target }) as CheckStatus;

it('puts a service under the check it answers, the rest as rows', () => {
	const device = {
		checks: [
			check('ping', 'ping', null),
			check('ssh', 'tcp', 22),
			check('web', 'http', 'https://r.lan/')
		],
		router: {
			services: [
				{ name: 'dropbear', running: true, enabled: true },
				{ name: 'uhttpd', running: true, enabled: true },
				{ name: 'openvpn', running: false, enabled: false },
				{ name: 'firewall', running: true, enabled: true },
				{ name: 'sysfixtime', running: false, enabled: false }
			],
			services_allowed: ['openvpn']
		},
		host_report: { services: { failed: ['backup.service'] } }
	} as unknown as Device;
	const own = ownServicesOf(device);
	expect(own.map((s) => s.name)).not.toContain('sysfixtime'); // neither running, at boot nor ours
	const { byCheck, rest } = groupOwn(device, own);
	expect(byCheck.ssh.map((s) => s.name)).toEqual(['dropbear']);
	expect(byCheck.web.map((s) => s.name)).toEqual(['uhttpd']);
	// failed first, then running, then the rest; openvpn has no check here
	expect(rest.map((s) => s.name)).toEqual(['backup.service', 'firewall', 'openvpn']);
	expect(rest.find((s) => s.name === 'openvpn')?.actable).toBe(true);
});
