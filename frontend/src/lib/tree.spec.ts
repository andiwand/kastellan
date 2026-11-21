import { describe, expect, it } from 'vitest';
import type { Device } from './api';
import { tree } from './tree';

const device = (id: string, parent: string | null = null): Device => ({
	id,
	name: id,
	host: id,
	kind: 'other',
	site: 'home',
	site_name: 'Home',
	parent,
	tags: [],
	always_on: true,
	ssh: null,
	desktop: null,
	driver: null,
	info: null,
	containers: null,
	containers_checked: null,
	address: null,
	networks: [],
	network_assumed: false,
	public_ways: [],
	containers_error: null,
	outdated_scripts: [],
	vulns: null,
	exposed: false,
	web_url: null,
	state: 'unknown',
	checks: []
});

describe('tree', () => {
	it('puts children after their parent', () => {
		const rows = tree([
			device('nas', 'router'),
			device('router'),
			device('ap', 'router'),
			device('cam', 'ap')
		]);
		expect(rows.map((r) => [r.device.id, r.depth])).toEqual([
			['router', 0],
			['ap', 1],
			['cam', 2],
			['nas', 1]
		]);
	});

	it('survives unknown parents and cycles', () => {
		const rows = tree([device('a', 'b'), device('b', 'a'), device('c', 'gone')]);
		expect(rows.map((r) => r.device.id).sort()).toEqual(['a', 'b', 'c']);
	});
});
