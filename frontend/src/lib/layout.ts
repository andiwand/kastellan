import type { Device } from './api';

export interface Node {
	device: Device;
	x: number; // left edge
	y: number; // vertical center
	depth: number;
}

export interface Link {
	from: Node;
	to: Node;
}

export interface Layout {
	nodes: Node[];
	links: Link[];
	width: number;
	height: number;
}

/** A left-to-right tree: depth goes right, leaves stack top to bottom, each parent sits at the
 * middle of its children, roots one under the other. Home networks are wide and shallow, so this
 * fits a landscape screen. Devices whose parent is unknown (or part of a cycle) become roots. */
export function layoutTree(
	devices: Device[],
	{ w = 200, h = 64, gapX = 64, gapY = 14 } = {}
): Layout {
	const ids = new Set(devices.map((d) => d.id));
	const children = new Map<string, Device[]>();
	const roots: Device[] = [];
	for (const d of devices) {
		if (d.parent && ids.has(d.parent) && d.parent !== d.id) {
			children.set(d.parent, [...(children.get(d.parent) ?? []), d]);
		} else roots.push(d);
	}
	// Sort: devices with children first keeps the branches together, then by name.
	const order = (list: Device[]) =>
		[...list].sort(
			(a, b) =>
				Number(children.has(b.id)) - Number(children.has(a.id)) || a.name.localeCompare(b.name)
		);

	const nodes: Node[] = [];
	const links: Link[] = [];
	const placed = new Set<string>();
	let slot = 0;
	let maxDepth = 0;

	const place = (d: Device, depth: number): Node => {
		placed.add(d.id);
		maxDepth = Math.max(maxDepth, depth);
		const kids = order(children.get(d.id) ?? []).filter((c) => !placed.has(c.id));
		const x = depth * (w + gapX);
		let y: number;
		const childNodes = kids.map((c) => place(c, depth + 1));
		if (childNodes.length) {
			y = (childNodes[0].y + childNodes[childNodes.length - 1].y) / 2;
		} else {
			y = slot * (h + gapY) + h / 2;
			slot += 1;
		}
		const node = { device: d, x, y, depth };
		nodes.push(node);
		for (const c of childNodes) links.push({ from: node, to: c });
		return node;
	};

	for (const r of order(roots)) place(r, 0);
	// Anything left sits in a cycle; place it as a root.
	for (const d of devices) if (!placed.has(d.id)) place(d, 0);

	return {
		nodes,
		links,
		width: (maxDepth + 1) * (w + gapX) - gapX,
		height: Math.max(slot, 1) * (h + gapY) - gapY
	};
}

/** Devices that take part in a link (they have a known parent, or something hangs off them), and
 * the rest. Most end devices are unlinked: which access point a laptop uses is not worth a line. */
export function splitLinked(devices: Device[]): { linked: Device[]; unlinked: Device[] } {
	const ids = new Set(devices.map((d) => d.id));
	const parents = new Set(devices.map((d) => d.parent).filter((p) => p && ids.has(p)));
	const linked: Device[] = [];
	const unlinked: Device[] = [];
	for (const d of devices) {
		const hasParent = !!d.parent && ids.has(d.parent) && d.parent !== d.id;
		(hasParent || parents.has(d.id) ? linked : unlinked).push(d);
	}
	return { linked, unlinked };
}
