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
	since_exact?: boolean;
	stale?: boolean;
	software?: string | null;
	software_since?: string | null;
	cert?: Cert | null;
	address?: string | null; // http to a name: the address it led to
}

/** The certificate an https service presents (tls.py). */
export interface Cert {
	valid: boolean;
	error: string | null;
	expires: string | null;
	days_left: number | null;
	issuer: string | null;
	names: string[];
	watched: boolean; // its alarms are on (a public name, or `tls: true`)
	problem: boolean; // watched, and not valid or ending soon
}

export interface Device {
	id: string;
	name: string;
	host: string;
	kind: string;
	parent: string | null;
	tags: string[];
	always_on: boolean;
	ssh: { user: string; port: number } | null;
	desktop: { protocol: 'rdp' | 'vnc'; user: string | null } | null;
	driver: string | null;
	info: { model: string | null; name: string | null; firmware: string | null } | null;
	containers: Container[] | null;
	containers_checked: string | null;
	containers_error: string | null;
	vulns: Vulns | null;
	exposed: boolean;
	state: CheckState;
	checks: CheckStatus[];
}

export interface Container {
	key: string;
	name: string;
	image: string;
	tag: string;
	image_id: string;
	state: string;
	update: boolean;
	update_to: string | null;
	unchecked: string | null;
}

export interface Alarm {
	key: string;
	severity: 'info' | 'warning' | 'critical';
	device: string;
	title: string;
	detail: string;
	opened: string;
	since: string | null;
	hidden: string[];
	acknowledged_by: string | null;
	acknowledged_note: string | null;
	acknowledged_at: string | null;
}

export interface Alarms {
	open: Alarm[];
	muted: Record<string, string>;
	channels: number;
}

export interface Finding {
	id: string;
	source: string;
	summary: string;
	score: number | null;
	severity: 'critical' | 'high' | 'medium' | 'low' | 'unknown';
	published: string | null;
	url: string;
	aliases: string[];
	fixed_in: string | null;
	exploited: boolean;
	confidence: 'exact' | 'upstream';
}

export interface Vulns {
	checked: string;
	items: { where: string; product: string; findings: Finding[] }[];
	unknown: { where: string; product: string }[];
	not_looked_up?: string[];
	errors: Record<string, string>;
	kev?: string | null;
}

export interface WgClient {
	id: string;
	name: string;
	enabled: boolean;
	address: string | null;
	created: string | null;
	expires: string | null;
	last_handshake: string | null;
	rx: number | null;
	tx: number | null;
}

export interface OvpnClient {
	name: string;
	status: 'valid' | 'revoked' | 'expired' | 'unknown';
	expires: string | null;
	serial: string;
}

export interface Vpn {
	wireguard: { clients: WgClient[]; error?: string; insecure?: boolean } | null;
	openvpn: {
		clients: OvpnClient[];
		error?: string;
		crl_to_server?: boolean;
		crl?: { pushed?: string; next_update?: string | null; error?: string | null };
	} | null;
}

export interface SetupHost {
	role: 'pki' | 'server';
	device: string;
	name: string;
	host: string;
	user: string;
	login: string | null;
	checks: { name: string; ok: boolean; detail: string }[];
	can_install: boolean;
	script: string;
}

export interface Setup {
	mode: 'restricted' | 'direct';
	vpn_key: string;
	hosts: SetupHost[];
}

export interface Access {
	user: string;
	port: number;
	hub_ip: string | null;
	key: string;
	checks: { name: string; ok: boolean; detail: string }[];
	scripts: { existing: string | null; dedicated: string };
}

export interface Health {
	ok: boolean;
	version: string;
	config_error: string | null;
	ping_refused: string | null;
	locked: boolean;
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

/** Something that happened: a check went up or down, or a service's software changed. */
export type Change =
	| { kind: 'state'; time: string; device: string; check: string; up: boolean; detail: string }
	| { kind: 'version'; time: string; device: string; check: string; from: string; to: string };

/** The hub answered 401: nobody is signed in (or the session ended). */
export class SignedOut extends Error {}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
	const write = method !== 'GET';
	const response = await fetch(`/api${path}`, {
		method,
		// Every write carries this header; the hub refuses writes without it (see auth_api.py).
		headers: write ? { 'X-Kastellan': '1', 'Content-Type': 'application/json' } : undefined,
		body: body === undefined ? undefined : JSON.stringify(body)
	});
	if (response.status === 401) throw new SignedOut((await detail(response)) ?? 'sign in first');
	if (!response.ok) throw new Error((await detail(response)) ?? `${path}: HTTP ${response.status}`);
	return (await response.json()) as T;
}

async function detail(response: Response): Promise<string | undefined> {
	try {
		return (await response.json()).detail;
	} catch {
		return undefined;
	}
}

function get<T>(path: string, params: Record<string, string | number | undefined> = {}) {
	const query = new URLSearchParams();
	for (const [k, v] of Object.entries(params)) if (v !== undefined) query.set(k, String(v));
	return call<T>('GET', `${path}${query.size ? `?${query}` : ''}`);
}

export interface Me {
	signin: boolean; // false: sign-in is off, everyone is an admin
	user: string | null;
	role: 'admin' | 'observer' | null;
}

export const api = {
	health: () => get<Health>('/health'),
	devices: () => get<Device[]>('/devices'),
	series: (hours: number, buckets: number, device?: string) =>
		get<Series>('/series', { hours, buckets, device }),
	changes: (hours = 24, device?: string, limit = 100) =>
		get<Change[]>('/changes', { hours, device, limit }),
	me: () => get<Me>('/auth/me'),
	alarms: () => get<Alarms>('/alarms'),
	vpn: () => get<Vpn>('/vpn'),
	wgCreate: (name: string, days: number | null) =>
		call<{ id: string }>('POST', '/vpn/wireguard', { name, days }),
	wgEnable: (id: string, on: boolean) =>
		call<{ enabled: boolean }>('POST', `/vpn/wireguard/${id}/${on ? 'enable' : 'disable'}`),
	wgDelete: (id: string) => call<{ deleted: boolean }>('DELETE', `/vpn/wireguard/${id}`),
	ovpnIssue: (name: string, days: number | null) =>
		call<{ name: string }>('POST', '/vpn/openvpn', { name, days }),
	ovpnRevoke: (name: string) =>
		call<{ revoked: boolean; crl_pushed: boolean; error?: string }>(
			'POST',
			`/vpn/openvpn/${encodeURIComponent(name)}/revoke`
		),
	ovpnPushCrl: () => call<{ crl: unknown }>('POST', '/vpn/openvpn/crl'),
	ovpnSetup: () => get<Setup>('/vpn/openvpn/setup'),
	access: (device: string) => get<Access>(`/devices/${encodeURIComponent(device)}/access`),
	setUpAccess: (
		device: string,
		body: {
			login: string;
			password: string;
			user: string;
			create: boolean;
			sudo: boolean;
			port: number;
		}
	) =>
		call<{ output: string; config: string }>(
			'POST',
			`/devices/${encodeURIComponent(device)}/access/setup`,
			body
		),
	ovpnInstall: (role: string) =>
		call<{ output: string }>('POST', `/vpn/openvpn/setup/${role}/install`),
	acknowledge: (key: string, note = '') =>
		call<Alarm>('POST', `/alarms/${encodeURIComponent(key)}/ack`, { note }),
	mute: (device: string, minutes: number) =>
		call<{ muted_until: string | null }>('POST', `/devices/${encodeURIComponent(device)}/mute`, {
			minutes
		}),
	login: (user: string, password: string) =>
		call<{ user: string; role: string }>('POST', '/auth/login', { user, password }),
	logout: () => call<{ ok: boolean }>('POST', '/auth/logout'),
	sshKey: () => get<{ public_key: string }>('/ssh/key'),
	trustHost: (device: string, fingerprint: string) =>
		call<{ state: string }>('POST', `/devices/${encodeURIComponent(device)}/ssh/trust`, {
			fingerprint
		}),
	desktopTicket: (
		device: string,
		credentials: { username: string; password: string; domain: string }
	) =>
		call<{ ticket: string }>(
			'POST',
			`/devices/${encodeURIComponent(device)}/desktop/ticket`,
			credentials
		),
	forgetHost: (device: string) =>
		call<{ state: string }>('POST', `/devices/${encodeURIComponent(device)}/ssh/forget`)
};
