import type { Device } from './api';

export interface Row {
	device: Device;
	depth: number;
}

/** The devices in network order: each one after its parent, children sorted by name. */
export function tree(devices: Device[]): Row[] {
	const ids = new Set(devices.map((d) => d.id));
	const children = new Map<string | null, Device[]>();
	for (const d of devices) {
		const parent = d.parent && ids.has(d.parent) ? d.parent : null;
		children.set(parent, [...(children.get(parent) ?? []), d]);
	}
	const rows: Row[] = [];
	const seen = new Set<string>();
	const walk = (parent: string | null, depth: number) => {
		const list = [...(children.get(parent) ?? [])].sort((a, b) => a.name.localeCompare(b.name));
		for (const device of list) {
			if (seen.has(device.id)) continue; // a cycle in parent links
			seen.add(device.id);
			rows.push({ device, depth });
			walk(device.id, depth + 1);
		}
	};
	walk(null, 0);
	// Devices only reachable through a cycle still show, at the top level.
	for (const d of devices) if (!seen.has(d.id)) rows.push({ device: d, depth: 0 });
	return rows;
}
