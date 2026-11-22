import { expect, it } from 'vitest';
import type { CheckStatus, Container, Device } from './api';
import { actionsOf, groupOwn, ownServicesOf, servedBy, serves } from './ownServices';

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

it('keeps unusual names and external DNS lookups from breaking service grouping', () => {
	const d = {
		checks: [check('__proto__', 'tcp', 22), check('lookup', 'dns', 'example.org')]
	} as Device;
	const services = ['dropbear', 'constructor', 'dnsmasq'].map((name) => ({
		name,
		source: 'openwrt' as const,
		state: 'running' as const,
		enabled: true,
		actable: true
	}));
	const groups = groupOwn(d, services);
	expect(groups.byCheck.__proto__.map((s) => s.name)).toEqual(['dropbear']);
	expect(groups.rest.map((s) => s.name)).toEqual(['constructor', 'dnsmasq']);
	expect(actionsOf(services[0])).toEqual(['restart', 'stop', 'disable']);
	for (const name of ['uhttpd', 'rpcd', 'network', 'firewall'])
		expect(actionsOf({ ...services[0], name })).toEqual(['restart']);
	expect(actionsOf({ ...services[0], actable: false })).toEqual([]);
});

it('connects containers to the checks on the ports they publish', () => {
	const ha = { name: 'homeassistant', key: 'homeassistant', ports: [8123] } as unknown as Container;
	const db = { name: 'postgres', key: 'postgres', ports: [] } as unknown as Container;
	const web = check('home assistant', 'http', 'http://homeserver.lan:8123');
	const ssh = check('ssh', 'tcp', 22);
	expect(servedBy(web, [ha, db]).map((c) => c.name)).toEqual(['homeassistant']);
	expect(servedBy(ssh, [ha, db])).toEqual([]);
	expect(serves(ha, [web, ssh]).map((c) => c.name)).toEqual(['home assistant']);
	expect(servedBy(check('ping', 'ping', null), [ha])).toEqual([]);
});
