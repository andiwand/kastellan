export type CheckState = 'unknown' | 'up' | 'partial' | 'down';

export interface CheckStatus {
	name: string;
	type: 'ping' | 'tcp' | 'http' | 'dns';
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
	site: string; // where it is (an id; "home" when not set)
	site_name: string;
	parent: string | null;
	tags: string[];
	always_on: boolean;
	ssh: { user: string; port: number } | null;
	ssh_port: number | null; // where it answers SSH: its ssh:, or an SSH check (a terminal is offered)
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
	web_url: string | null; // its web interface (scheme://host:port), if it has one
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
	host_forecast?: Record<string, number> | null; // days until full, per mount (disks.py)
	/** libvirt's virtual machines on the host, from its collector (null: it can't tell). */
	vms?: Vm[] | null;
	runs_on?: RunsOn | null;
	/** A UPS (kind ups): what its host reads of it. */
	ups?: UpsReading | null;
	powers?: string[]; // a UPS: the devices it powers
	powered_by?: { ups: string; state: string } | null; // the UPS a device hangs on, and its state
	reads_ups?: { device: string; state: string }[]; // a host: the UPSes it reads
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
	uninstall: string; // the commands that take it off the host again
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
	push?: string | null; // a user's browsers (web push), * for everyone's
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
		general: {
			check_interval: number;
			retention_days: number;
			heartbeat: string | null;
			check_releases?: boolean;
		};
		alarms: {
			alarms: {
				down_after: number;
				cert_days: number;
				disk_warning: number;
				disk_critical: number;
				disk_temperature: number;
				remind_hours?: number;
				disk_days?: number;
			};
		};
		notify: { notify: NotifyChannelForm[] };
		vulns: { vulns: { enabled: boolean; every_hours: number; nvd_api_key: string | null } };
		networks: { networks: Record<string, { name: string; cidr: string | null }> };
		groups: { groups: Record<string, { name: string; devices: string[] }> };
		sites: { sites: Record<string, string> };
		users: { users: Record<string, { role: 'admin' | 'observer'; password?: string }> };
		maintenance: { maintenance: MaintenanceWindowForm[] };
		backups: {
			backups: Record<string, { name?: string; device: string; within_hours?: number }>;
		};
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
/** A group's SSH files side by side (sshsync.py): a column per device and user. */
export type SshFile = 'authorized_keys' | 'known_hosts';
export interface SshColumn {
	id: string; // "device/user"
	device: string;
	user: string;
	error?: string; // no such user, can't read it, unreachable, ...
	path?: string;
	exists?: boolean;
	version?: string;
	login?: boolean; // the user Kastellan logs in as there
}
export interface SshRow {
	key: string;
	type: string;
	label: string; // a key's comment, or a known host's hosts ("hashed")
	line: string; // the line as most columns have it
	fingerprint: string;
	kastellan: string | null; // one of Kastellan's own keys: never removed here
	on: Record<string, 'same' | 'differs'>;
	conflict: boolean; // known_hosts: different keys for one host
}
export interface GroupSsh {
	file: SshFile;
	users: string[];
	columns: SshColumn[];
	no_ssh: string[];
	rows: SshRow[];
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
	uninstall: string; // the commands that take it off the host again
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

/** A virtual machine on a host (libvirt), as its collector reports it. */
export interface Vm {
	name: string;
	uuid: string | null;
	state: string; // running, shut off, paused, ...
	autostart: boolean;
	persistent: boolean;
	vcpus: number | null;
	memory: number | null; // bytes
	memory_used: number | null;
	disks: { target: string; source: string }[];
	device?: string | null; // the configured device that runs in it (its runs_on), if any
}

/** A UPS as its host reads it (apcupsd or NUT). `state` is the flag that matters most. */
export interface UpsReading {
	source: 'apcupsd' | 'nut';
	name: string | null;
	model: string | null;
	serial: string | null;
	firmware: string | null;
	status: string;
	flags: string[];
	state: UpsState;
	charge: number | null; // percent
	runtime: number | null; // seconds
	load: number | null; // percent
	input_voltage: number | null;
	battery_voltage: number | null;
	nominal_power: number | null; // watts
	last_transfer: string | null; // why it last went to battery
	on_battery_since: string | null;
	off_battery_since: string | null;
	transfers: number | null;
	battery_date: string | null;
	checked: string | null;
}
export type UpsState =
	'online' | 'on_battery' | 'low_battery' | 'replace_battery' | 'no_contact' | 'unknown';

/** Where a device runs as a virtual machine (config.yaml's runs_on), and the VM's state as its
 * host reports it ("" when not reported). */
export interface RunsOn {
	host: string;
	vm: string;
	state?: string;
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
	previous?: string | null; // the image kept from before its last update (actor), short id
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
	held?: boolean; // opened during maintenance: not sent (yet)
}

/** A device in maintenance now (maintenance.py). */
export interface InMaintenance {
	until: string;
	reason: string;
	planned: boolean; // from config.yaml; else started here, and can be ended here
}

/** A planned window, with its current or next times. */
export interface MaintenanceWindowInfo {
	index: number;
	name: string;
	devices: string[];
	start: string | null;
	end: string | null;
	now: boolean;
}

/** A planned window as config.yaml has it. */
export interface MaintenanceWindowForm {
	name?: string;
	devices?: string[];
	groups?: string[];
	sites?: string[];
	start?: string | null;
	end?: string | null;
	days?: string[];
	at?: string | null;
	minutes?: number | null;
}

/** A backup that reports in (backups.py). */
export interface Backup {
	id: string;
	name: string;
	device: string;
	within_hours: number;
	ok: string | null; // when it last said it succeeded
	failed: string | null;
	message: string;
	since: string;
	token: boolean; // a token was made for its job
	state: 'ok' | 'failed' | 'late' | 'waiting';
}

export interface Alarms {
	open: Alarm[];
	muted: Record<string, string>;
	maintenance?: Record<string, InMaintenance>;
	windows?: MaintenanceWindowInfo[];
	channels: number;
	heartbeat?: { on: boolean; last: string | null; error: string | null };
	limits?: { disk_warning: number; disk_critical: number; disk_days?: number };
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
	/** Connected now (from the server's status, read through its OpenWrt login). */
	connected?: {
		address: string | null;
		since: string | null;
		rx: number | null;
		tx: number | null;
	} | null;
	last_seen?: string | null;
}

/** Rotating the VPN key: the steps per host ("<vpn>:pki", "<vpn>:server"), as for the hub key. */
export interface VpnKey {
	fingerprint: string;
	next: string | null;
	rotation: {
		started: string;
		stage: 'adding' | 'switching' | 'removing' | 'done';
		finished: string | null;
		devices: Record<
			string,
			{
				added: boolean;
				removed: boolean;
				error: string | null;
				target: string | null;
				note: string | null; // e.g. how many other key lines it removed
				forgotten: boolean; // given up on: the old key may still be trusted there
			}
		>;
	} | null;
}

/** A VPN's settings, as config.yaml's `vpns:` has them (what its form edits). */
export type VpnSettings =
	| { kind: 'wireguard'; name: string; wg_easy: string; user: string; password_secret: string }
	| {
			kind: 'openvpn';
			name: string;
			mode: 'restricted' | 'direct';
			pki: string;
			server: string | null;
			days: number;
			[k: string]: unknown;
	  };

/** One VPN Kastellan manages the clients of (GET /api/vpn: all of them). */
export interface Vpn {
	id: string;
	kind: 'wireguard' | 'openvpn';
	name: string;
	error?: string;
	settings?: VpnSettings; // admins
	// WireGuard
	clients: (WgClient | OvpnClient)[];
	insecure?: boolean;
	password_set?: boolean;
	// OpenVPN
	mode?: 'restricted' | 'direct';
	crl_to_server?: boolean;
	crl?: { pushed?: string; next_update?: string | null; error?: string | null };
	/** Whether Kastellan sees who is connected: through the server's OpenWrt login. */
	seen?: {
		how: 'openwrt' | 'not-allowed' | 'no-openwrt';
		device?: string | null;
		checked: string | null;
		error: string | null;
	};
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
	/** OpenWrt: one login (root), no sudo; only the first way applies. */
	openwrt: boolean;
	port: number;
	hub_ip: string | null;
	key: string;
	checks: { name: string; ok: boolean; detail: string }[];
	host_key: {
		state: 'trusted' | 'unknown' | 'changed' | 'unreachable';
		fingerprint: string | null;
	};
	scripts: { existing: string | null; dedicated: string };
}

export interface Health {
	ok: boolean;
	version: string;
	config_error: string | null;
	ping_refused: string | null;
	locked: boolean;
	demo: boolean;
	/** Device web pages through the hub at <device>.<web_domain>; null: not set up. */
	web_domain: string | null;
	/** The key rings (vault.py): absent, locked, partly open, open. */
	vault: VaultState;
	/** Locked rings Kastellan uses by itself: what needs their secrets waits. */
	vault_waiting: string[];
	/** A newer Kastellan, if one is out (releases.py). */
	newer?: { version: string; url: string } | null;
}

export type VaultState = 'absent' | 'locked' | 'partly' | 'unlocked' | 'damaged';
export type VaultLevel = 'convenient' | 'balanced' | 'strict';

/** A way a ring opens: an admin's password, a key file, an environment variable, a key in
 * config.yaml; `ready`: set up (for password: some admin has a slot). */
export interface RingWay {
	way: 'password' | 'keyfile' | 'env' | 'config';
	source: string;
	ready: boolean;
	users: string[];
}

export interface Ring {
	id: string;
	name: string;
	open: boolean;
	entries: number;
	ways: RingWay[];
	recovery: boolean;
	stays_unlocked: string; // lock | signed_in | idle:<minutes>
	background: boolean; // Kastellan may use its entries by itself
	problem: string | null; // why it didn't open by itself
	builtin: boolean;
}

export interface Vault {
	state: VaultState;
	damaged: string | null; // why vault.json can't be read
	level: VaultLevel;
	rings: Ring[];
	files: string[];
	entries: VaultEntry[];
}

export interface VaultEntry {
	id: string;
	ring: string;
	kind: 'password' | 'token' | 'ssh-key' | 'other';
	name: string;
	user: string;
	notes: string;
	created: number;
	changed: number;
	public: string; // an SSH key's public half
	devices: string[]; // a login's devices: a terminal sends it only there
	used_by: string[];
	own: boolean; // Kastellan's own key: changed by rotating it
}

export interface VaultForm {
	ring: string;
	kind: VaultEntry['kind'];
	name: string;
	user: string;
	notes: string;
	secret: string | null; // null on a change: kept
	devices?: string[]; // a login's devices; left out: kept
	password?: string; // typed again: needed to change its devices
}

export type UnlockItem = 'password' | 'config' | { keyfile: string } | { env: string };

export interface RingChoice {
	unlock: UnlockItem[] | null; // null: as the level says
	stays_unlocked: string | null;
	background: boolean | null;
	make_keyfile: boolean;
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
	type: 'ping' | 'tcp' | 'http' | 'dns';
	target: string | number | null;
	name: string;
	expect?: number | string[] | null; // http: a status; dns: addresses among the answers
	server?: string | null; // dns: the resolver to ask
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
	site?: string; // empty: home
	runs_on?: { host: string; vm: string } | null; // a virtual machine on another device
	ups?: { via: string; name?: string | null } | null; // a UPS: which host reads it
	powered_by?: string | null; // the UPS it hangs on
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

// When the person last did something here. A request made while nobody has for a minute is the
// page refreshing on its own: marked, so it doesn't keep key rings open ("while an admin uses
// the app", vault.py).
let lastInput = Date.now();
if (typeof window !== 'undefined')
	for (const event of ['pointerdown', 'keydown', 'focus', 'visibilitychange'])
		window.addEventListener(event, () => (lastInput = Date.now()), { passive: true });

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
	const write = method !== 'GET';
	const headers: Record<string, string> = {};
	// Every write carries this header; the hub refuses writes without it (see auth_api.py).
	if (write) Object.assign(headers, { 'X-Kastellan': '1', 'Content-Type': 'application/json' });
	if (Date.now() - lastInput > 60_000) headers['X-Kastellan-Auto'] = '1';
	const response = await fetch(`/api${path}`, {
		method,
		headers,
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
			else if (line !== 'KASTELLAN-ALIVE') onLine(line); // the hub's "still working"
		}
	}
	return outcome;
}

/** What the actor answers when it is done (the ability's JSON). */
export interface ActResult {
	container?: string;
	image?: string;
	updated?: boolean;
	from?: string;
	to?: string;
	why?: string;
	restarted?: boolean;
	started?: boolean;
	stopped?: boolean;
	running?: boolean;
	vm?: string;
	state?: string; // a VM's state after it acted
	forced?: boolean;
	reboot?: string | boolean;
	upgraded?: number;
	packages?: string[];
	seconds?: number;
}

/** One action through a host's actor, followed as it goes: each step it reports, then its
 * answer (or why it failed, thrown). Older scripts report no steps; it works all the same. */
export async function actStream(
	device: string,
	ability: string,
	name: string | null, // the container's, or the VM's for vm-* abilities
	onStep: (text: string) => void
): Promise<ActResult> {
	const outcome = (await streamSetup(
		`/devices/${encodeURIComponent(device)}/act/${ability}/stream`,
		ability.startsWith('vm-') ? { vm: name } : { container: name },
		(line) => {
			if (line.startsWith('KASTELLAN-STEP ')) onStep(line.slice(15));
		}
	)) as SetupOutcome & { result?: ActResult };
	if (!outcome.ok) throw new Error(outcome.error ?? 'it failed');
	return outcome.result ?? {};
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
	vpn: () => get<{ vpns: Vpn[] }>('/vpn'),
	putVpn: (vid: string, vpn: Partial<VpnSettings>, version: string, isNew: boolean) =>
		call<{ id: string; version: string }>('PUT', `/vpns/${vid}`, { vpn, version, new: isNew }),
	deleteVpn: (vid: string, version: string) =>
		call<{ version: string }>('DELETE', `/vpns/${vid}?version=${encodeURIComponent(version)}`),
	wgCreate: (vid: string, name: string, days: number | null) =>
		call<{ id: string }>('POST', `/vpns/${vid}/wireguard`, { name, days }),
	wgEnable: (vid: string, id: string, on: boolean) =>
		call<{ enabled: boolean }>('POST', `/vpns/${vid}/wireguard/${id}/${on ? 'enable' : 'disable'}`),
	wgDelete: (vid: string, id: string) =>
		call<{ deleted: boolean }>('DELETE', `/vpns/${vid}/wireguard/${id}`),
	ovpnIssue: (vid: string, name: string, days: number | null) =>
		call<{ name: string }>('POST', `/vpns/${vid}/openvpn`, { name, days }),
	ovpnRevoke: (vid: string, name: string) =>
		call<{ revoked: boolean; crl_pushed: boolean; error?: string }>(
			'POST',
			`/vpns/${vid}/openvpn/${encodeURIComponent(name)}/revoke`
		),
	ovpnPushCrl: (vid: string) => call<{ crl: unknown }>('POST', `/vpns/${vid}/openvpn/crl`),
	ovpnSetup: (vid: string) => get<Setup>(`/vpns/${vid}/openvpn/setup`),
	vpnKey: () => get<VpnKey>('/vpn/key'),
	vpnKeyRotate: (step: 'start' | 'finish' | 'cancel', anyway = false) =>
		call<VpnKey>('POST', `/vpn/key/rotate/${step}`, step === 'finish' ? { anyway } : undefined),
	vpnKeyForget: (host: string) => call<VpnKey>('POST', '/vpn/key/rotate/forget', { host }),
	access: (device: string, login?: string) =>
		get<Access>(
			`/devices/${encodeURIComponent(device)}/access${login ? `?login=${encodeURIComponent(login)}` : ''}`
		),
	ovpnInstall: (vid: string, role: string) =>
		call<{ output: string }>('POST', `/vpns/${vid}/openvpn/setup/${role}/install`),
	config: () => get<{ text: string; version: string; error: string | null }>('/config'),
	checkConfig: (text: string) =>
		call<{ ok: boolean; error: string | null }>('POST', '/config/check', { text }),
	saveConfig: (text: string, version: string) =>
		call<{ version: string }>('PUT', '/config', { text, version }),
	deviceForm: (id: string) => get<DeviceForm>(`/devices/${encodeURIComponent(id)}/form`),
	/** Which devices a UPS powers (each one's powered_by), at the file's version. */
	setPowered: (id: string, devices: string[], version: string) =>
		call<{ version: string }>('PUT', `/devices/${encodeURIComponent(id)}/powered`, {
			devices,
			version
		}),
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
	actScript: (
		id: string,
		abilities: string[],
		containers: string[],
		vms: string[] = [],
		allowed?: Record<string, string[]>
	) =>
		call<ActSetup>('POST', `/devices/${encodeURIComponent(id)}/act/script`, {
			abilities,
			containers,
			vms,
			allowed
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
	collectNow: (id: string) =>
		call<{ checked: string | null }>('POST', `/devices/${encodeURIComponent(id)}/collect/now`),
	backups: () => get<Backup[]>('/backups'),
	backupToken: (id: string) =>
		call<{ token: string }>('POST', `/backups/${encodeURIComponent(id)}/token`),
	/** The last lines of a container's log or a systemd unit's journal, through the actor. */
	logs: (id: string, what: { container: string } | { unit: string }) =>
		call<{ lines: string[]; running?: boolean; state?: string }>(
			'POST',
			`/devices/${encodeURIComponent(id)}/act/${'unit' in what ? 'unit-logs' : 'container-logs'}`,
			what
		),
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
		get<{ id: string; name: string; devices: string[]; openwrt: string[]; ssh: string[] }[]>(
			'/groups'
		),
	groupSsh: (id: string, file: SshFile, users: string[]) => {
		const query = new URLSearchParams({ file });
		for (const u of users) query.append('users', u);
		return call<GroupSsh>('GET', `/groups/${encodeURIComponent(id)}/ssh?${query}`);
	},
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
	push: () =>
		get<{
			public_key: string;
			subscriptions: { endpoint: string; label: string; created: number }[];
			channel: boolean; // a push: channel in config.yaml reaches this person
		}>('/push'),
	pushSubscribe: (subscription: PushSubscriptionJSON, label: string) =>
		call<{ subscribed: boolean }>('POST', '/push/subscribe', { ...subscription, label }),
	pushUnsubscribe: (endpoint: string) =>
		call<{ removed: boolean }>('POST', '/push/unsubscribe', { endpoint }),
	pushTest: () => call<{ browsers: number }>('POST', '/push/test'),
	vault: () => get<Vault>('/vault'),
	vaultCreate: (password: string) =>
		call<{ recovery: Record<string, string>; taken: string[]; notes: string[] }>(
			'POST',
			'/vault/create',
			{
				password
			}
		),
	vaultUnlock: (how: { password?: string; recovery?: string; ring?: string }) =>
		call<{ state: VaultState; opened: string[] }>('POST', '/vault/unlock', how),
	vaultLock: (ring?: string) => call<{ state: VaultState }>('POST', '/vault/lock', { ring }),
	vaultRecovery: (ring: string, password: string) =>
		call<{ recovery: string }>('POST', `/vault/rings/${ring}/recovery`, { password }),
	vaultAddRing: (id: string, name: string, password: string) =>
		call<{ recovery: string }>('POST', '/vault/rings', { id, name, password }),
	vaultRemoveRing: (id: string) => call<{ ring: string }>('DELETE', `/vault/rings/${id}`),
	vaultSettings: (password: string, level: VaultLevel, rings: Record<string, RingChoice>) =>
		call<{ notes: string[]; state: VaultState }>('PUT', '/vault/settings', {
			password,
			level,
			rings
		}),
	vaultAdd: (form: VaultForm) => call<{ id: string }>('POST', '/vault/entries', form),
	vaultChange: (id: string, form: VaultForm) =>
		call<{ id: string }>('PUT', `/vault/entries/${id}`, form),
	vaultDelete: (id: string) => call<{ id: string }>('DELETE', `/vault/entries/${id}`),
	vaultReveal: (id: string, password: string) =>
		call<{ secret: string }>('POST', `/vault/entries/${id}/reveal`, { password }),
	maintenance: (kind: 'devices' | 'groups', id: string, minutes: number, reason = '') =>
		call<{ devices: string[]; until: string | null }>(
			'POST',
			`/${kind}/${encodeURIComponent(id)}/maintenance`,
			{ minutes, reason }
		),
	mute: (device: string, minutes: number) =>
		call<{ muted_until: string | null }>('POST', `/devices/${encodeURIComponent(device)}/mute`, {
			minutes
		}),
	login: (user: string, password: string) =>
		call<{ user: string; role: string }>('POST', '/auth/login', { user, password }),
	logout: () => call<{ ok: boolean }>('POST', '/auth/logout'),
	sshKey: () => get<{ public_key: string }>('/ssh/key'),
	trustHost: (device: string, fingerprint: string, port?: number) =>
		call<{ state: string }>('POST', `/devices/${encodeURIComponent(device)}/ssh/trust`, {
			fingerprint,
			port
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
	forgetHost: (device: string, port?: number) =>
		call<{ state: string }>(
			'POST',
			`/devices/${encodeURIComponent(device)}/ssh/forget${port ? `?port=${port}` : ''}`
		)
};
