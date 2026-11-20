import { describe, expect, it } from 'vitest';
import type { Device } from './api';
import { layoutTree, splitLinked } from './layout';

const device = (id: string, parent: string | null = null): Device => ({
	id,
	name: id,
	host: id,
	kind: 'other',
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
	vulns: null,
	exposed: false,
	state: 'unknown',
	checks: []
});

describe('layoutTree', () => {
	it('centers parents beside their children', () => {
		const { nodes, links, width, height } = layoutTree(
			[device('router'), device('a', 'router'), device('b', 'router'), device('c', 'b')],
			{ w: 100, h: 50, gapX: 10, gapY: 20 }
		);
		const at = Object.fromEntries(nodes.map((n) => [n.device.id, n]));
		expect(links).toHaveLength(3);
		expect(at.router.y).toBe((at.a.y + at.b.y) / 2);
		expect(at.c.y).toBe(at.b.y);
		expect(at.c.x).toBe(220);
		expect([width, height]).toEqual([320, 120]);
	});

	it('places every device despite cycles and unknown parents', () => {
		const { nodes } = layoutTree([device('a', 'b'), device('b', 'a'), device('c', 'gone')]);
		expect(nodes.map((n) => n.device.id).sort()).toEqual(['a', 'b', 'c']);
	});
});

describe('splitLinked', () => {
	it('keeps devices without any link apart', () => {
		const { linked, unlinked } = splitLinked([
			device('router'),
			device('switch', 'router'),
			device('laptop'),
			device('phone', 'gone')
		]);
		expect(linked.map((d) => d.id)).toEqual(['router', 'switch']);
		expect(unlinked.map((d) => d.id)).toEqual(['laptop', 'phone']);
	});
});
