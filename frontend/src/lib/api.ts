export type CheckState = 'unknown' | 'up' | 'partial' | 'down';

export interface CheckStatus {
	name: string;
	type: 'ping' | 'tcp' | 'http';
	target: string | number | null;
	exposed?: boolean; // reachable from the internet (this service)
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
	/** Kastellan's scripts on the host older than this hub's ("collector", "actor"). */
	outdated_scripts: string[];
	vulns: Vulns | null;
	exposed: boolean;
	address: string | null; // its host, resolved
	networks: NetworkRef[]; // several: it sits on the boundary between them
	network_assumed: boolean; // its name didn't resolve: taken to be in the hub's network
	public_ways: PublicWay[]; // checks that reach it by a public name or address
	collect?: boolean; // the collector reads it (collect: in config.yaml)
	act?: boolean; // an actor is set up there (act: in config.yaml)
	openwrt?: boolean; // an OpenWrt device Kastellan reads through its API
	router?: RouterReport | null;
	router_checked?: string | null;
	router_error?: string | null;
	host_report?: HostReport | null;
	host_checked?: string | null;
	host_error?: string | null;
	state: CheckState;
	checks: CheckStatus[];
}

/** What the collector reports about a host (host/collector.py). Every part may be missing. */
export interface HostReport {
	errors?: Record<string, string>;
	system?: {
		hostname: string;
		kernel: string;
		uptime: number;
		load: number[];
		cpus: number;
		memory: { total: number; available: number };
		swap: { total: number; free: number };
	};
	filesystems?: {
		mount: string;
		device: string;
		type: string;
		total: number;
		used: number;
		available: number;
	}[];
	raid?: {
		name: string;
		active: boolean;
		level: string;
		devices: string[];
		failed: string[];
		status: string;
		progress: { what: string; percent: number } | null;
	}[];
	smart?: {
		available: boolean;
		disks: {
			disk: string;
			model?: string | null;
			passed?: boolean | null;
			temperature?: number | null;
			power_on_hours?: number | null;
			reallocated?: number | null;
			pending?: number | null;
			uncorrectable?: number | null;
			nvme_used_percent?: number | null;
			nvme_media_errors?: number | null;
			asleep?: boolean;
			error?: string | null;
		}[];
	};
	temperatures?: { sensor: string; celsius: number; limit: number | null }[];
	services?: { failed: string[] };
	updates?: {
		reboot: boolean;
		reboot_for?: string[];
		available: number | null;
		security: number | null;
	};
	docker?: unknown[] | null; // its containers (they show in the device's container list)
}

export interface ActSetup {
	configured: boolean;
	allowed: Record<string, string[]> | null; // what the host allows now (ability -> containers)
	error: string | null;
	abilities: Record<string, string>; // every ability Kastellan knows, described
	port: number;
	script: string; // the commands for the choice asked about
}

/** An OpenWrt device's state (openwrt/client.py). */
export interface RouterReport {
	board: {
		model: string | null;
		hostname: string | null;
		board_name: string | null;
		release: { version: string | null; revision: string | null; target: string | null };
		kernel: string | null;
	};
	system: {
		uptime: number | null;
		load: number[];
		memory: { total?: number; available?: number; free?: number };
	};
	interfaces?: { name: string; up: boolean; proto: string; device: string; ipv4: string[] }[];
	radios?: {
		name: string;
		up: boolean;
		disabled: boolean;
		band: string | null;
		channel: string | number | null;
		htmode: string | null;
		interfaces: {
			section: string;
			ifname: string | null;
			ssid: string | null;
			mode: string | null;
			clients: { mac: string; name: string | null; signal: number | null }[];
		}[];
	}[];
	services?: { name: string; enabled: boolean; running: boolean }[];
	services_allowed?: string[];
	packages?: string[] | null;
	packages_checked?: number | null;
	firmware?: {
		current: string;
		series: string;
		supported: boolean;
		newest_in_series: string | null;
		next_step: string | null;
		stable: string;
		oldstable: string;
	} | null;
	errors?: Record<string, string>;
}

export interface OpenWrtSetup {
	configured: boolean;
	allowed: string[] | null; // the abilities the device grants now
	abilities: Record<string, string>;
	port: number;
	script: string;
}

export interface WifiSettings {
	radios: {
		section: string;
		channel: string | null;
		htmode: string | null;
		txpower: string | null;
		disabled: string | null;
		country: string | null;
		band: string | null;
	}[];
	interfaces: {
		section: string;
		ssid: string | null;
		encryption: string | null;
		disabled: string | null;
		hidden: string | null;
		key_set: boolean;
		device: string | null;
		mode: string | null;
		network: string | string[] | null;
	}[];
}

/** config.yaml's settings as forms (config_api.py); secrets come as KEEP, never their value. */
export const KEEP = '__keep__';
export interface NotifyChannelForm {
	ntfy?: string | null;
	webhook?: string | null;
	gotify?: string | null;
	telegram?: string | number | null;
	email?: string | null;
	smtp?: string | null;
	sender?: string | null;
	token?: string | null;
	min: 'info' | 'warning' | 'critical';
	quiet?: string | null;
	_id?: number; // which channel it was when loaded: its secrets go back to it only
}
export interface Settings {
	version: string;
	secrets: Record<string, boolean>;
	sections: {
		general: { check_interval: number; retention_days: number; heartbeat: string | null };
		alarms: {
			alarms: {
				down_after: number;
				cert_days: number;
				disk_warning: number;
				disk_critical: number;
				disk_temperature: number;
			};
		};
		notify: { notify: NotifyChannelForm[] };
		vulns: { vulns: { enabled: boolean; every_hours: number; nvd_api_key: string | null } };
		vpn: {
			vpn: {
				wireguard: { wg_easy: string; user: string; password_secret: string } | null;
				openvpn: {
					mode: 'restricted' | 'direct';
					pki: string;
					server: string | null;
					days: number;
					[k: string]: unknown;
				} | null;
			};
		};
		networks: { networks: Record<string, { name: string; cidr: string | null }> };
		groups: { groups: Record<string, { name: string; devices: string[] }> };
		users: { users: Record<string, { role: 'admin' | 'observer'; password?: string }> };
	};
}

/** A group of OpenWrt devices, lined up (openwrt/sync.py). Passwords only as fingerprints. */
export interface GroupEntry {
	device: string;
	section: string;
	radio: string | null;
	band: string | null;
	encryption: string | null;
	key: string | null;
	disabled: boolean;
}
export interface GroupSurvey {
	id: string;
	name: string;
	members: string[];
	errors: Record<string, string>;
	radios: Record<string, { section: string; band: string | null; channel: string | null }[]>;
	can_mesh: Record<string, boolean | null>;
	networks: {
		ssid: string;
		on: GroupEntry[];
		devices: string[];
		missing: string[];
		same_key: boolean;
		same_encryption: boolean;
	}[];
	mesh: {
		mesh_id: string;
		on: GroupEntry[];
		devices: string[];
		missing: string[];
		same_key: boolean;
	}[];
}

export interface CollectSetup {
	configured: boolean;
	state: 'off' | 'waiting' | 'reading' | 'failing';
	error: string | null;
	port: number;
	hub_ip: string | null;
	user: string;
	script: string;
}

export interface NetworkRef {
	id: string; // "internet" for the internet
	name: string;
	cidr: string | null;
	local: boolean;
}

export interface PublicWay {
	check: string;
	target: string | number | null;
	address: string | null; // where the public name led
}

export interface Networks {
	hub: NetworkRef | null; // where Kastellan runs
	hub_address: string | null;
	gateway: string | null;
	named: { id: string; name: string; cidr: string | null }[];
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
	heartbeat?: { on: boolean; last: string | null; error: string | null };
	limits?: { disk_warning: number; disk_critical: number };
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

/** A check as config.yaml has it (the forms). */
export interface CheckForm {
	type: 'ping' | 'tcp' | 'http';
	target: string | number | null;
	name: string;
	expect?: number | null;
	verify?: boolean;
	tls?: boolean | null;
	interval?: number | null;
	timeout?: number | null;
	exposed?: boolean; // this service is reachable from the internet
}

/** A device as config.yaml has it: what the form edits; the rest of it stays as it is. */
export interface DeviceForm {
	id?: string;
	name: string;
	host: string;
	kind: string;
	tags: string[];
	always_on: boolean | null; // null: by kind
	exposed: boolean;
	checks: CheckForm[];
	version?: string; // the file's, when the form was filled: saving refuses if it changed
}

/** A key allowed to log in on a device (one line of an authorized_keys). */
export interface AuthorizedKey {
	user: string;
	file: string;
	type: string;
	fingerprint: string;
	comment: string;
	options: string;
	kastellan: string | null; // one of Kastellan's own keys: which
}

export interface RotationStep {
	added: boolean;
	removed: boolean;
	error: string | null;
	current: boolean; // done for the login the device has now
}

export interface Rotation {
	started: string;
	stage: 'adding' | 'removing' | 'done';
	finished: string | null;
	devices: Record<string, RotationStep>;
	missing: string[]; // devices the new key isn't on (for their login now)
}

export interface HubKeys {
	public_key: string;
	fingerprint: string;
	made: string;
	next: string | null;
	rotation: Rotation | null;
	devices: string[]; // with an ssh: login
}

/** The day as a person would tell it (stats.activity on the hub). */
export type ActivityEntry =
	| {
			kind: 'outage';
			device: string;
			check: string;
			start: string;
			end: string | null;
			detail: string;
	  }
	| { kind: 'blips'; device: string; check: string; count: number; last: string }
	| { kind: 'together'; start: string; end: string | null; devices: string[]; count: number }
	| { kind: 'online'; device: string; start: string | null; end: string | null }
	| { kind: 'version'; device: string; check: string; time: string; from: string; to: string };

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

export interface SetupOutcome {
	ok: boolean;
	error?: string;
	config?: string;
	exit?: number;
}

/** Run a setup on a device and follow its output line by line (the setup dialog). Errors before
 * it starts (a refused login, an untrusted host key) throw; the outcome is the last line. */
export async function streamSetup(
	path: string,
	body: object,
	onLine: (line: string) => void
): Promise<SetupOutcome> {
	const response = await fetch(`/api${path}`, {
		method: 'POST',
		headers: { 'X-Kastellan': '1', 'Content-Type': 'application/json' },
		body: JSON.stringify(body)
	});
	if (response.status === 401 && !(await response.clone().text()).includes('refused'))
		throw new SignedOut('sign in first');
	if (!response.ok || !response.body)
		throw new Error((await detail(response)) ?? `HTTP ${response.status}`);
	const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
	let buffer = '';
	let outcome: SetupOutcome = { ok: false, error: 'the connection ended early' };
	for (;;) {
		const { value, done } = await reader.read();
		if (done) break;
		buffer += value;
		let nl: number;
		while ((nl = buffer.indexOf('\n')) >= 0) {
			const line = buffer.slice(0, nl);
			buffer = buffer.slice(nl + 1);
			if (line.startsWith('KASTELLAN-DONE ')) outcome = JSON.parse(line.slice(15));
			else onLine(line);
		}
	}
	return outcome;
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
	ovpnInstall: (role: string) =>
		call<{ output: string }>('POST', `/vpn/openvpn/setup/${role}/install`),
	config: () => get<{ text: string; version: string; error: string | null }>('/config'),
	checkConfig: (text: string) =>
		call<{ ok: boolean; error: string | null }>('POST', '/config/check', { text }),
	saveConfig: (text: string, version: string) =>
		call<{ version: string }>('PUT', '/config', { text, version }),
	deviceForm: (id: string) => get<DeviceForm>(`/devices/${encodeURIComponent(id)}/form`),
	deviceYaml: (id: string) =>
		get<{ text: string; version: string }>(`/devices/${encodeURIComponent(id)}/yaml`),
	saveDeviceYaml: (id: string, text: string, version: string) =>
		call<{ version: string }>('PUT', `/devices/${encodeURIComponent(id)}/yaml`, { text, version }),
	addDevice: (form: DeviceForm & { id: string }) => call<{ id: string }>('POST', '/devices', form),
	changeDevice: (id: string, form: DeviceForm) =>
		call<{ id: string }>('PUT', `/devices/${encodeURIComponent(id)}`, form),
	deleteDevice: (id: string, version?: string) =>
		call<{ id: string }>(
			'DELETE',
			`/devices/${encodeURIComponent(id)}${version ? `?version=${encodeURIComponent(version)}` : ''}`
		),
	networks: () => get<Networks>('/networks'),
	activity: (hours = 24) => get<ActivityEntry[]>('/activity', { hours }),
	collectSetup: (id: string) => get<CollectSetup>(`/devices/${encodeURIComponent(id)}/collect`),
	collectTest: (id: string) =>
		call<{ ok: boolean; errors: Record<string, string> }>(
			'POST',
			`/devices/${encodeURIComponent(id)}/collect/test`
		),
	actScript: (id: string, abilities: string[], containers: string[]) =>
		call<ActSetup>('POST', `/devices/${encodeURIComponent(id)}/act/script`, {
			abilities,
			containers
		}),
	act: (id: string, ability: string, container?: string) =>
		call<{
			updated?: boolean;
			from?: string;
			to?: string;
			why?: string;
			reboot?: string | boolean;
			upgraded?: number;
		}>('POST', `/devices/${encodeURIComponent(id)}/act/${ability}`, {
			container: container ?? null
		}),
	openwrtScript: (id: string, abilities: string[], services: string[] = []) =>
		call<OpenWrtSetup>('POST', `/devices/${encodeURIComponent(id)}/openwrt/script`, {
			abilities,
			services
		}),
	openwrtService: (id: string, name: string, action: string) =>
		call<{ output: string }>(
			'POST',
			`/devices/${encodeURIComponent(id)}/openwrt/services/${encodeURIComponent(name)}`,
			{ action }
		),
	openwrtReboot: (id: string) =>
		call<{ rebooting: boolean }>('POST', `/devices/${encodeURIComponent(id)}/openwrt/reboot`),
	wifi: (id: string) => get<WifiSettings>(`/devices/${encodeURIComponent(id)}/openwrt/wifi`),
	setWifi: (
		id: string,
		changes: Record<string, Record<string, string | number | boolean | null>>
	) => call<WifiSettings>('PUT', `/devices/${encodeURIComponent(id)}/openwrt/wifi`, { changes }),
	groups: () =>
		get<{ id: string; name: string; devices: string[]; openwrt: string[] }[]>('/groups'),
	settings: () => get<Settings>('/settings'),
	putSettings: (section: keyof Settings['sections'], value: object, version: string) =>
		call<{ version: string }>('PUT', `/settings/${section}`, { value, version }),
	putSecret: (name: string, value: string) =>
		call<{ set: boolean }>('PUT', `/secrets/${encodeURIComponent(name)}`, { value }),
	group: (id: string) => get<GroupSurvey>(`/groups/${encodeURIComponent(id)}`),
	hubKeys: () => get<HubKeys>('/ssh/keys'),
	deviceKeys: (id: string) =>
		get<{ everyone: boolean; login: string; keys: AuthorizedKey[] }>(
			`/devices/${encodeURIComponent(id)}/keys`
		),
	removeKey: (id: string, key: { user: string; file: string; fingerprint: string }) =>
		call<{ removed: number }>('POST', `/devices/${encodeURIComponent(id)}/keys/remove`, key),
	rotateStart: () => call<{ rotation: Rotation | null }>('POST', '/ssh/rotate/start'),
	rotateFinish: (anyway = false) =>
		call<{ rotation: Rotation | null }>('POST', '/ssh/rotate/finish', { anyway }),
	rotateCancel: () => call<{ rotation: null }>('POST', '/ssh/rotate/cancel'),
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
