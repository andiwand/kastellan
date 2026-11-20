export type CheckState = 'unknown' | 'up' | 'partial' | 'down';

export interface CheckStatus {
	name: string;
	type: 'ping' | 'tcp' | 'http';
	target: string | number | null;
	up?: boolean | null;
	latency_ms?: number | null;
	detail?: string;
	checked?: string | null;
	since?: string | null;
}

export interface Device {
	id: string;
	name: string;
	host: string;
	kind: string;
	parent: string | null;
	tags: string[];
	always_on: boolean;
	state: CheckState;
	checks: CheckStatus[];
}

export interface Health {
	ok: boolean;
	version: string;
	config_error: string | null;
	demo: boolean;
}

export interface Bucket {
	uptime: number | null;
	latency_ms: number | null;
}

export interface Series {
	start: string;
	step_seconds: number;
	devices: Record<string, Record<string, Bucket[]>>;
}

export interface Change {
	time: string;
	device: string;
	check: string;
	up: boolean;
	detail: string;
}

async function get<T>(path: string, params: Record<string, string | number | undefined> = {}) {
	const query = new URLSearchParams();
	for (const [k, v] of Object.entries(params)) if (v !== undefined) query.set(k, String(v));
	const response = await fetch(`/api${path}${query.size ? `?${query}` : ''}`);
	if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
	return (await response.json()) as T;
}

export const api = {
	health: () => get<Health>('/health'),
	devices: () => get<Device[]>('/devices'),
	series: (hours: number, buckets: number, device?: string) =>
		get<Series>('/series', { hours, buckets, device }),
	changes: (hours = 24, device?: string, limit = 100) =>
		get<Change[]>('/changes', { hours, device, limit })
};
