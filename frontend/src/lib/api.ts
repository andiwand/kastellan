export type CheckState = 'unknown' | 'up' | 'partial' | 'down';

export interface CheckStatus {
	name: string;
	type: 'ping' | 'tcp' | 'http' | 'dns' | 'dhcp';
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
	/** Abilities Kastellan offers that its login there hasn't been given (Updates). */
	new_abilities?: { actor?: string[]; openwrt?: string[] };
	syslog_cert?: boolean; // it has a certificate to send syslog over TLS
	fritzbox?: boolean; // a FRITZ!Box Kastellan reads through TR-064 with its own user
	fritz?: FritzReport | null;
	fritz_checked?: string | null;
	fritz_error?: string | null;
	host_report?: HostReport | null;
	host_forecast?: Record<string, number> | null; // days until full, per mount (disks.py)
	vitals?: Vitals | null; // CPU, memory, storage, temperature now (vitals.py)
	updates_skipped?: ('firmware' | 'reboot' | 'packages')[]; // said to be not needed (skipped.py)
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

/** A router's DHCP server (openwrt/client.py, fritzbox/client.py). */
export interface DhcpPool {
	server?: boolean;
	pools:
		| {
				interface: string;
				start: number | string | null;
				end?: string | null;
				size: number | null;
		  }[]
		| null;
	size: number | null;
	used: number;
	leases: {
		mac: string | null;
		name: string | null;
		address: string | null;
		expires: number | null;
	}[];
}

/** An OpenWrt device's state (openwrt/client.py). */
export interface RouterReport {
	dhcp?: DhcpPool | null;
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
	/** Why it isn't asked for package updates (a small router: opkg would run it out of memory). */
	packages_skipped?: string | null;
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
		logs: {
			logs: {
				days?: number;
				max_lines?: number;
				syslog?: { enabled?: boolean; port?: number };
				syslog_tls?: { enabled?: boolean; port?: number };
			} | null;
		};
		device_backups: { device_backups: { every_hours?: number; keep?: number } | null };
		wifi: { wifi: { scan_at?: string | null } | null };
		hub_backup: {
			hub_backup: {
				every_hours?: number;
				keep?: number;
				history?: boolean;
				logs?: boolean;
				targets?: { path: string; device?: string | null }[];
			} | null;
		};
		internet: {
			internet: {
				enabled?: boolean;
				router?: string | null;
				targets?: string[];
				speedtest?: {
					enabled?: boolean;
					every_hours?: number;
					booked_down?: number | null;
					booked_up?: number | null;
					iperf3?: string | null;
				};
			} | null;
		};
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
	roaming?: GroupRoaming;
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
	/** When update_to is a new major version: the newest tag within the running one. */
	update_within?: string | null;
	unchecked: string | null;
	previous?: string | null; // the image kept from before its last update (actor), short id
	compose?: string | null; // its compose file (or project), when compose made it
	skipped?: boolean; // its update offer was said to be not needed (Updates leaves it out)
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
	/** upstream: a distribution may have fixed it; patch: the patch level is unknown (e.g. OpenSSH 10.6 for 10.6p2) */
	confidence: 'exact' | 'upstream' | 'patch';
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
	fingerprint: string | null; // null while the key ring is locked and no copy is known
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

/** A sign-in or an attempt (signins.py), from the logs or the hub's audit log. */
export interface SignIn {
	verified: boolean;
	ts: number;
	device: string;
	service: 'ssh' | 'vpn' | 'router' | 'hub';
	program: string;
	outcome: 'ok' | 'failed' | 'attempt';
	user: string | null;
	address: string | null;
	outside: boolean;
	via: string | null;
	line: number | null;
	own: boolean;
}
export interface SignInSource {
	address: string;
	outside: boolean;
	ok: number;
	failed: number;
	attempts: number;
	first: number;
	last: number;
	users: string[];
	services: string[];
	devices: string[];
	via: string | null;
	flags: ('repeated' | 'outside-ok' | 'new-outside')[];
}
export interface SignIns {
	hours: number;
	own: string[];
	summary: {
		by_service: Record<string, { ok: number; failed: number; attempt: number }>;
		outside_addresses: number;
		outside_ok: number;
	};
	sources: SignInSource[];
	events: SignIn[];
	total: number;
}

/** A VPN server Kastellan sees (vpn_servers.py): on a router, a FRITZ!Box or in a container. */
export interface VpnServer {
	device: string | null;
	source: 'openwrt' | 'fritzbox' | 'container' | 'wg-easy';
	kind: string; // openvpn, wireguard, ipsec, tailscale, ...
	name: string | null;
	port?: number | null;
	proto?: string | null;
	dev?: string | null;
	running: boolean | null;
	stale?: boolean;
	state?: string;
	image?: string;
	where?: string | null;
	addresses?: string[] | null;
	uptime?: number | null;
	clients: { name: string; since: string | null; address?: string; active?: unknown }[] | null;
	vpn: string | null; // the managed VPN it serves
	error?: string | null;
	checked: string | null;
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

/** A device's vitals now: percentages of what is used, with what they are of (vitals.py). */
export interface Vitals {
	cpu: number | null; // percent busy, mean since the reading before
	load: number | null; // 1-minute load average (where there is no CPU percentage)
	cores: number | null;
	memory: number | null; // percent
	memory_used: number | null; // bytes
	memory_total: number | null;
	memory_share: number | null; // percent, where only that is known (a FRITZ!Box)
	storage: number | null; // percent, of the fullest filesystem
	storage_used: number | null;
	storage_total: number | null;
	storage_mount: string | null;
	temperature: number | null; // °C, the hottest sensor
	checked: string;
}

export type VitalField = 'cpu' | 'memory' | 'storage' | 'temperature';

/** One part of Kastellan's data folder (footprint.py): its size, rate and where it levels off. */
export interface DataStore {
	name: string;
	path: string;
	used: number; // bytes
	per_day: number | null; // bytes a day (null: not known yet)
	kept: string; // in words: "14 days", "60 versions", "everything"
	steady: number | null; // bytes once it levels off (null: it doesn't)
	days_to_steady: number | null; // until then (0: it has)
}

/** What Kastellan costs the machine it runs on (GET /api/hub/resources). */
export interface HubResources {
	time: number;
	hub: { cpu: number | null; memory: number | null; memory_is_peak: boolean }; // cpu: % of one core
	machine: {
		cores: number | null;
		cpu: number | null; // all cores, 0-100
		memory_total: number | null;
		memory_available: number | null;
	};
	disk: { path: string; total: number | null; free: number | null };
	stores: DataStore[];
	forecast: {
		used: number;
		per_day: number;
		steady: number;
		growing_per_day: number; // what never levels off
		days_to_steady: number | null;
		days_until_full: number | null;
	};
}

export interface VitalsSeries {
	start: string;
	step_seconds: number;
	devices: Record<string, Record<VitalField, (number | null)[]>>;
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
	type: 'ping' | 'tcp' | 'http' | 'dns' | 'dhcp';
	target: string | number | null;
	name: string;
	expect?: number | string[] | null; // http: a status; dns: addresses among the answers
	contains?: string | null; // http: text the page must show
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
	target: string | null; // the login it was done for (user@host:port)
	forgotten: boolean; // given up on: the old key may still be trusted there
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
		const d = (await response.json()).detail;
		if (d === undefined || d === null || typeof d === 'string') return d ?? undefined;
		// FastAPI's validation errors: [{loc: [...], msg: "..."}, ...]
		if (Array.isArray(d))
			return d
				.map((e) => {
					const where = Array.isArray(e?.loc)
						? e.loc.filter((p: unknown) => p !== 'body').join('.')
						: '';
					return where ? `${where}: ${e?.msg}` : String(e?.msg ?? JSON.stringify(e));
				})
				.join('; ');
		return JSON.stringify(d);
	} catch {
		return undefined;
	}
}

function get<T>(path: string, params: Record<string, string | number | undefined> = {}) {
	const query = new URLSearchParams();
	for (const [k, v] of Object.entries(params)) if (v !== undefined) query.set(k, String(v));
	return call<T>('GET', `${path}${query.size ? `?${query}` : ''}`);
}

/** A device's path in the API. */
const dev = (id: string) => `/devices/${encodeURIComponent(id)}`;

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
	from_tag?: string; // an update to another tag: the tags
	to_tag?: string;
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
	onStep: (text: string) => void,
	tag?: string // update-container: another tag of its image (one the hub found)
): Promise<ActResult> {
	const outcome = (await streamSetup(
		`${dev(device)}/act/${ability}/stream`,
		ability.startsWith('vm-') ? { vm: name } : { container: name, ...(tag ? { tag } : {}) },
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

// --- Central logs (logs.py) -----------------------------------------------------------------

export interface LogLine {
	id: number;
	ts: number; // seconds since the epoch
	device: string;
	source: string; // syslog, kernel, fritzbox, ...
	program: string;
	level: number; // 0 emerg .. 7 debug
	msg: string;
	verified: boolean; // false: came in on the open syslog port
}

/** An alarm opened or resolved, or an info event, among the log lines (alarms.log). */
export interface LogMark {
	ts: number;
	what: 'opened' | 'resolved' | 'event';
	device: string | null;
	title: string;
	severity: string | null;
	key: string | null;
}

export interface LogFilter {
	device?: string[];
	group?: string[];
	source?: string[];
	program?: string[];
	level?: number;
	since?: number;
	until?: number;
	q?: string;
	before?: number;
	after?: number;
	limit?: number;
}

export interface LogFacets {
	devices: Record<string, number>;
	sources: Record<string, number>;
	programs: Record<string, number>;
	total: number;
}

export interface LogSources {
	devices: Record<
		string,
		{ how: string; ok?: string | null; error?: string | null; denied?: boolean; new?: number }
	>;
	syslog: {
		enabled: boolean;
		listening: boolean;
		port: number | null;
		error: string | null;
		received: number;
		refused: number;
	};
	syslog_tls: {
		enabled: boolean;
		listening: boolean;
		port: number | null;
		error: string | null;
		received: number;
		refused: number;
	};
	days: number;
	max_lines: number;
}

// --- Device configuration backups (devbackups.py) ------------------------------------------

export interface BackupVersion {
	id: string;
	kind: string;
	taken: string;
	checked: string; // last seen the same
	files: number;
	size: number;
	by: string | null;
}

export interface BackupStatus {
	tried?: string;
	ok?: string;
	error?: string | null;
	denied?: boolean;
	new?: boolean;
}

export interface DeviceBackups {
	device: string;
	kind: string | null;
	versions: BackupVersion[];
	status: BackupStatus;
	every_hours: number;
}

export interface DeviceBackupSummary {
	device: string;
	kind: string | null;
	versions: number;
	latest: BackupVersion | null;
	status: BackupStatus;
}

/** Kastellan's own backup (hubbackup.py): its key, versions, copies away from the hub, checks. */
export interface HubBackup {
	ready: boolean;
	legacy: boolean;
	keyring: boolean; // opens with the key rings (an admin's password, a recovery key)
	vault: boolean; // the hub has key rings
	made: string | null;
	status: {
		ok?: string;
		local_ok?: string;
		error?: string | null;
		running?: boolean;
		targets?: Record<string, { ok?: string; error?: string; written?: number; path?: string }>;
	};
	versions: {
		id: string;
		taken: string;
		checked: string;
		files: number;
		size: number;
		by: string | null;
	}[];
	every_hours: number;
	keep: number;
	targets: { path: string; device: string | null }[];
	checks: Record<string, string>;
	rings: string[];
}

/** A login in the People key ring a setup or the web interface may use here (never its secret). */
export interface StoredLogin {
	id: string;
	name: string;
	user: string;
}

export interface BackupDiff {
	added: string[];
	removed: string[];
	changed: {
		path: string;
		size: [number, number];
		secret?: boolean;
		binary?: boolean;
		only_secrets?: boolean;
		diff?: string[];
	}[];
}

// --- FRITZ!Box (fritzbox/client.py) --------------------------------------------------------

export interface FritzWifi {
	index: number;
	ssid: string | null;
	enabled: boolean;
	status: string | null;
	channel: number | null;
	possible_channels: number[];
	standard: string | null;
	encryption: string | null;
	band: '2g' | '5g' | '6g' | null;
	guest: boolean;
	clients: number;
	bssid: string | null;
	wps?: boolean;
	associated?: {
		mac: string | null;
		address: string | null;
		signal: number | null;
		speed: number | null;
	}[];
}

export interface FritzReport {
	model: string | null;
	firmware: string | null;
	uptime: number | null;
	update?: { available: boolean; version: string | null };
	internet: {
		access?: string;
		link?: string;
		max_up?: number | null;
		max_down?: number | null;
		connection?: string;
		connected_for?: number | null;
		address?: string | null;
		address_v6?: string | null;
		last_error?: string | null;
		dsl?: {
			status: string | null;
			up: number | null;
			down: number | null;
			max_up: number | null;
			max_down: number | null;
			snr_up: number | null;
			snr_down: number | null;
			crc?: number | null;
			fec?: number | null;
			errored_seconds?: number | null;
		};
		rate?: { down?: number; up?: number } | null;
	};
	wifi: FritzWifi[];
	clients: {
		name: string | null;
		mac: string | null;
		address: string | null;
		via: string | null;
		speed: number | null;
		guest: boolean;
	}[];
	forwards:
		| {
				enabled: boolean;
				protocol: string | null;
				port: number | null;
				to: string | null;
				to_port: number | null;
				name: string | null;
		  }[]
		| null;
	remote: { remote_access?: boolean; remote_port?: number | null; myfritz?: boolean };
	dhcp?: DhcpPool | null;
	mesh?: {
		members: {
			uid: string;
			name: string | null;
			model: string | null;
			role: string | null;
			mac: string | null;
			firmware: string | null;
		}[];
		links: {
			a: string;
			b: string;
			type: string | null;
			rate_rx: number | null;
			rate_tx: number | null;
			signal: number | null;
		}[];
	} | null;
	/** Read through the web interface's undocumented API: may be missing after a FRITZ!OS update. */
	wireguard?: {
		server: string | null;
		port: string | null;
		address: string | null;
		connections: {
			kind: 'device' | 'network';
			name: string;
			active: unknown;
			status: unknown;
			last_handshake: unknown;
			endpoint: unknown;
			networks: unknown;
		}[];
		error: string | null;
	};
	errors: Record<string, string>;
}

// --- The internet connection (internet.py) -------------------------------------------------

export interface InternetSample {
	t: number;
	state: 'up' | 'degraded' | 'down' | 'unknown';
	step: string | null;
	why?: string | null;
	router_ms: number | null;
	line: boolean | null;
	loss: number | null;
	ms: number | null;
	jitter: number | null;
	v6_loss: number | null;
	v6_ms: number | null;
	dns_ms: number | null;
	hop_ms?: number | null;
	hop_loss?: number | null;
	down: number | null;
	up: number | null;
	note: string | null;
}

export interface InternetOutage {
	start: number;
	end: number | null;
	seconds: number;
	step: string | null;
	why: string;
}

export interface SpeedTestResult {
	t: number;
	down: number | null;
	up: number | null;
	ms_idle: number | null;
	ms_loaded: number | null;
	server: string;
	by: string | null;
}

export interface InternetView {
	enabled: boolean;
	router: string | null;
	now: InternetSample | null;
	line: {
		address?: string | null;
		address_v6?: string | null;
		connected_for?: number | null;
		access?: string | null;
		max_down?: number | null;
		max_up?: number | null;
		dsl?: FritzReport['internet']['dsl'];
	};
	outage: InternetOutage | null;
	series: {
		t: number;
		state: string | null;
		ms: number | null;
		loss: number | null;
		down: number | null;
		up: number | null;
	}[];
	days: {
		day: string;
		availability: number | null;
		down_seconds: number;
		degraded_seconds: number;
		ms: number | null;
	}[];
	months: { month: string; availability: number | null; down_seconds: number }[];
	outages: InternetOutage[];
	events: { t: number; kind: string; text: string }[];
	speedtests: SpeedTestResult[];
	speedtest: {
		enabled: boolean;
		every_hours: number;
		booked_down: number | null;
		booked_up: number | null;
		server: string;
		how?: 'http' | 'iperf3';
		testing: boolean;
		error: string | null;
	};
	targets: string[];
	/** Who uses the line (nlbwmon on OpenWrt routers with the bandwidth ability). */
	clients?: {
		routers: string[];
		errors: Record<string, string>;
		rows: {
			router: string;
			mac: string;
			name: string | null;
			rx: number;
			tx: number;
			rx_rate: number | null;
			tx_rate: number | null;
		}[];
	};
	/** The way out (traceroute, hourly) and the provider's first routers that answer. */
	route?: { distance: number; address: string; ms: number }[];
	hops?: string[];
	route_checked?: number | null;
}

// --- Wi-Fi (wifi.py) ------------------------------------------------------------------------

export interface WifiNetworkEntry {
	device: string;
	section: string;
	radio: string;
	band: string | null;
	encryption: string | null;
	key: string | null; // a fingerprint
	disabled: boolean;
	kind: 'openwrt' | 'fritzbox';
	guest: boolean;
	clients?: number;
}

export interface WifiNetwork {
	ssid: string;
	on: WifiNetworkEntry[];
	devices: string[];
	bands: string[];
	same_key: boolean | null;
	same_encryption: boolean;
	off: string[];
	guest: boolean;
}

export interface WifiRadio {
	device: string;
	radio: string;
	kind: 'openwrt' | 'fritzbox';
	band: '2g' | '5g';
	channel: number | null;
	width: number;
	htmode: string | null;
	clients: number | null;
	neighbours: number;
	hears: Record<string, number>;
	busy: number | null;
	scanned: number | null;
	allowed: number[] | null;
	country?: string | null;
}

/** A Wi-Fi client and how its radio link is (wifi.diagnose). */
export interface WifiClient {
	device: string;
	radio: string;
	band: string | null;
	ssid: string | null;
	kind: 'openwrt' | 'fritzbox';
	mac: string | null;
	name?: string | null;
	address?: string | null;
	signal: number | null; // dBm (OpenWrt) or percent (FRITZ!Box)
	snr?: number | null;
	speed?: number | null; // FRITZ!Box: Mbit/s
	rx?: { mbit: number; mhz: number | null; std: string | null; nss: number | null } | null;
	tx?: { mbit: number; mhz: number | null; std: string | null; nss: number | null } | null;
	expected?: number | null;
	issues: string[];
}

/** What usteer on an access point knows (GET /api/devices/{id}/roaming). */
export interface RoamingView {
	read: boolean;
	why?: string;
	checked?: string;
	error?: string | null;
	nodes?: {
		node: string;
		own: boolean;
		host: string | null;
		device: string | null;
		iface: string;
		bssid: string | null;
		ssid: string | null;
		freq: number | null;
		clients: number | null;
		load: number | null;
		roam_source: number | null;
		roam_target: number | null;
	}[];
	clients?: {
		mac: string;
		name: string | null;
		node: string;
		signal: number | null;
		snr: number | null;
		connected: number | null;
	}[];
	config?: Record<string, number | boolean>;
}

/** A group's roaming check (roaming.py). */
export interface GroupRoaming {
	findings: { level: 'info' | 'warning'; text: string; fix: string; devices: string[] }[];
	members: Record<
		string,
		{
			usteer: 'running' | 'stopped' | 'missing' | 'unknown';
			sees: string[];
			misses: string[];
			clients: number;
			error: string | null;
			memory: number | null;
			free: number | null;
		}
	>;
	changes: Record<string, Record<string, Record<string, string>>>;
}

export interface WifiScanView {
	t: number;
	band: string;
	channel: number;
	neighbours: Record<string, { ssid: string; signal: number | null; width: number | null }[]>;
	survey: { mhz: number; busy: number; noise: number | null }[];
}

export interface WifiOverview {
	networks: WifiNetwork[];
	radios: WifiRadio[];
	errors: Record<string, string>;
	scans: Record<string, WifiScanView>;
	plans: {
		t: number;
		by: string;
		before: number | null;
		after: number | null;
		outcome: Record<string, string>;
	}[];
	scanning: string[];
	scan_at: string | null; // the daily scan's local time, or null (only on request)
	clients?: WifiClient[];
	countries?: string[];
	country_note?: string | null;
}

export interface ChannelPlan {
	radios: {
		device: string;
		radio: string;
		band: string;
		current: number | null;
		channel: number;
		width: number;
		htmode: string | null;
		reason: string;
		before: number;
		after: number;
		change: boolean;
	}[];
	before: number;
	after: number;
	dfs: boolean;
	scanned: boolean;
	hints: string[];
}

function logQuery(f: LogFilter): string {
	const q = new URLSearchParams();
	for (const [k, v] of Object.entries(f)) {
		if (v === undefined || v === '' || v === null) continue;
		if (Array.isArray(v)) for (const x of v) q.append(k, String(x));
		else q.set(k, String(v));
	}
	return q.size ? `?${q}` : '';
}

/** A device the routers see that Kastellan doesn't know (discovery.py). */
export interface Discovered {
	mac: string;
	name: string | null;
	address: string | null;
	routers: string[];
	wifi: boolean;
	via: string | null;
}

/** DNS and HTTPS for the site (web_setup.py). */
export interface WebSetupView {
	domain: string | null;
	zone?: string;
	dns_server?: string | null;
	address?: string | null;
	names?: {
		name: string;
		what: string;
		answers: string[];
		public: string[];
		same: boolean | null;
		ok: boolean;
	}[];
	certificates?: {
		name: string;
		certificate: {
			valid: boolean;
			error: string | null;
			expires: string | null;
			days_left: number | null;
			issuer: string | null;
		} | null;
	}[];
	routers?: {
		device: string;
		error?: string;
		dnsmasq?: boolean;
		rebind_ok?: boolean | null;
		forward_ok?: boolean | null;
		forwards?: string[];
		local?: string[];
	}[];
	commands?: string;
	checked: string | null;
}

export const more = {
	webSetup: (again = false) => get<WebSetupView>('/web/setup', again ? { again: 'true' } : {}),
	secrets: () =>
		get<{ name: string; set: boolean; used_by: string[]; missing: boolean }[]>('/secrets'),
	discovered: () => get<Discovered[]>('/discovered'),
	ignoreDiscovered: (mac: string) =>
		call<{ ok: boolean }>('POST', `/discovered/${encodeURIComponent(mac)}/ignore`),
	signins: (hours = 24) => get<SignIns>('/signins', { hours }),
	logs: (f: LogFilter) =>
		call<{ lines: LogLine[]; levels: string[]; marks?: LogMark[] }>('GET', `/logs${logQuery(f)}`),
	logFacets: (hours = 24) => get<LogFacets>('/logs/facets', { hours }),
	logSources: () => get<LogSources>('/logs/sources'),
	deviceUpdateNotNeeded: (id: string, kind: 'firmware' | 'reboot' | 'packages') =>
		call<{ skipped: string }>('POST', `${dev(id)}/updates/${kind}/not-needed`),
	updateNotNeeded: (id: string, key: string) =>
		call<{ skipped: string }>(
			'POST',
			`${dev(id)}/containers/${encodeURIComponent(key)}/not-needed`
		),
	notNeeded: (id: string, kind: 'actor' | 'openwrt') =>
		call<{ passed: string[] }>('POST', `${dev(id)}/abilities/${kind}/not-needed`),
	syslogState: (id: string) =>
		get<{
			issued: boolean;
			issued_at: string | null;
			enabled: boolean;
			port: number;
			listening: boolean;
		}>(`/logs/syslog/${encodeURIComponent(id)}`),
	syslogCertificate: (id: string) =>
		call<{ ca: string; cert: string; key: string; port: number }>(
			'POST',
			`/logs/syslog/${encodeURIComponent(id)}/certificate`
		),
	syslogRevoke: (id: string) =>
		call<{ revoked: number }>('DELETE', `/logs/syslog/${encodeURIComponent(id)}/certificate`),
	deviceBackups: () => get<DeviceBackupSummary[]>('/device-backups'),
	backupsOf: (id: string) => get<DeviceBackups>(`${dev(id)}/backups`),
	backupNow: (id: string) => call<DeviceBackups & { new: boolean }>('POST', `${dev(id)}/backups`),
	backupDiff: (id: string, older: string, newer: string) =>
		get<BackupDiff>(`${dev(id)}/backups/diff`, { older, newer }),
	backupDownload: (id: string, sid: string) => `/api${dev(id)}/backups/${sid}/download`,
	fritzCertificate: (id: string, port: number) =>
		call<{ fingerprint: string; pinned: string | null; port: number }>(
			'POST',
			`${dev(id)}/fritzbox/certificate`,
			{ port }
		),
	fritzSetup: (
		id: string,
		body: { user: string; password: string; fingerprint: string; port: number }
	) =>
		call<{ model: string | null; firmware: string | null }>(
			'POST',
			`${dev(id)}/fritzbox/setup`,
			body
		),
	fritzRemove: (id: string) => call<{ removed: boolean }>('DELETE', `${dev(id)}/fritzbox`),
	fritzReboot: (id: string) => call<{ done: boolean }>('POST', `${dev(id)}/fritzbox/reboot`),
	fritzFirmware: (id: string) => call<{ done: boolean }>('POST', `${dev(id)}/fritzbox/firmware`),
	fritzWpsOff: (id: string) => call<{ done: boolean }>('POST', `${dev(id)}/fritzbox/wps-off`),
	internet: (hours = 24) => get<InternetView>('/internet', { hours }),
	speedtest: () => call<SpeedTestResult>('POST', '/internet/speedtest'),
	wifiOverview: () => get<WifiOverview>('/wifi'),
	wifiPlan: (dfs = false) => get<ChannelPlan>('/wifi/plan', { dfs: String(dfs) }),
	wifiRadio: (body: {
		device: string;
		radio: string;
		channel?: string;
		htmode?: string;
		txpower?: number;
		country?: string;
	}) => call<{ done: boolean }>('PUT', '/wifi/radios', body),
	deviceRoaming: (id: string) => get<RoamingView>(`${dev(id)}/roaming`),
	/** The logins stored for a device, and whether the key ring that holds them is open. */
	storedLogins: (id: string) =>
		get<{ vault: boolean; ring_open: boolean; stored: StoredLogin[] }>(`${dev(id)}/stored-logins`),
	/** The hub signs in to the device's page with a stored login: a link that opens it so. */
	webSignIn: (id: string, entry: string, password: string) =>
		call<{ url: string }>('POST', `${dev(id)}/web/sign-in`, { entry, password }),
	hubBackup: () => get<HubBackup>('/hub-backup'),
	hubBackupNow: () => call<{ error?: string | null; new?: boolean }>('POST', '/hub-backup'),
	hubBackupPassphrase: (replace: boolean, mode: 'keyring' | 'passphrase') =>
		call<{ passphrase: string | null }>('POST', '/hub-backup/passphrase', { replace, mode }),
	/** Whether a passphrase, or a ring's recovery key, still opens the backup. */
	hubBackupCheck: (key: { passphrase: string } | { ring: string; recovery: string }) =>
		call<{ right: boolean }>('POST', '/hub-backup/check', key),
	hubBackupDiff: (older: string, newer: string) =>
		get<BackupDiff>('/hub-backup/diff', { older, newer })
};

export const api = {
	health: () => get<Health>('/health'),
	devices: () => get<Device[]>('/devices'),
	hubResources: () => get<HubResources>('/hub/resources'),
	vitals: (hours: number, buckets: number, device?: string) =>
		get<VitalsSeries>('/vitals', { hours, buckets, device }),
	series: (hours: number, buckets: number, device?: string) =>
		get<Series>('/series', { hours, buckets, device }),
	changes: (hours = 24, device?: string, limit = 100) =>
		get<Change[]>('/changes', { hours, device, limit }),
	me: () => get<Me>('/auth/me'),
	alarms: () => get<Alarms>('/alarms'),
	vpn: () => get<{ vpns: Vpn[]; servers: VpnServer[] }>('/vpn'),
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
		get<Access>(`${dev(device)}/access${login ? `?login=${encodeURIComponent(login)}` : ''}`),
	ovpnInstall: (vid: string, role: string) =>
		call<{ output: string }>('POST', `/vpns/${vid}/openvpn/setup/${role}/install`),
	config: () => get<{ text: string; version: string; error: string | null }>('/config'),
	checkConfig: (text: string) =>
		call<{ ok: boolean; error: string | null }>('POST', '/config/check', { text }),
	saveConfig: (text: string, version: string) =>
		call<{ version: string }>('PUT', '/config', { text, version }),
	deviceForm: (id: string) => get<DeviceForm>(`${dev(id)}/form`),
	/** Which devices a UPS powers (each one's powered_by), at the file's version. */
	setPowered: (id: string, devices: string[], version: string) =>
		call<{ version: string }>('PUT', `${dev(id)}/powered`, {
			devices,
			version
		}),
	deviceYaml: (id: string) => get<{ text: string; version: string }>(`${dev(id)}/yaml`),
	saveDeviceYaml: (id: string, text: string, version: string) =>
		call<{ version: string }>('PUT', `${dev(id)}/yaml`, { text, version }),
	addDevice: (form: DeviceForm & { id: string }) => call<{ id: string }>('POST', '/devices', form),
	changeDevice: (id: string, form: DeviceForm) => call<{ id: string }>('PUT', `${dev(id)}`, form),
	deleteDevice: (id: string, version?: string) =>
		call<{ id: string }>(
			'DELETE',
			`${dev(id)}${version ? `?version=${encodeURIComponent(version)}` : ''}`
		),
	networks: () => get<Networks>('/networks'),
	activity: (hours = 24) => get<ActivityEntry[]>('/activity', { hours }),
	collectSetup: (id: string) => get<CollectSetup>(`${dev(id)}/collect`),
	collectTest: (id: string) =>
		call<{ ok: boolean; errors: Record<string, string> }>('POST', `${dev(id)}/collect/test`),
	actScript: (
		id: string,
		abilities: string[],
		containers: string[],
		vms: string[] = [],
		allowed?: Record<string, string[]>
	) =>
		call<ActSetup>('POST', `${dev(id)}/act/script`, {
			abilities,
			containers,
			vms,
			allowed
		}),
	collectNow: (id: string) => call<{ checked: string | null }>('POST', `${dev(id)}/collect/now`),
	backups: () => get<Backup[]>('/backups'),
	backupToken: (id: string) =>
		call<{ token: string }>('POST', `/backups/${encodeURIComponent(id)}/token`),
	/** The last lines of a container's log or a systemd unit's journal, through the actor. */
	logs: (id: string, what: { container: string } | { unit: string }) =>
		call<{ lines: string[]; running?: boolean; state?: string }>(
			'POST',
			`${dev(id)}/act/${'unit' in what ? 'unit-logs' : 'container-logs'}`,
			what
		),
	openwrtScript: (id: string, abilities: string[], services: string[] = []) =>
		call<OpenWrtSetup>('POST', `${dev(id)}/openwrt/script`, {
			abilities,
			services
		}),
	openwrtService: (id: string, name: string, action: string) =>
		call<{ output: string }>('POST', `${dev(id)}/openwrt/services/${encodeURIComponent(name)}`, {
			action
		}),
	openwrtReboot: (id: string) => call<{ rebooting: boolean }>('POST', `${dev(id)}/openwrt/reboot`),
	wifi: (id: string) => get<WifiSettings>(`${dev(id)}/openwrt/wifi`),
	setWifi: (
		id: string,
		changes: Record<string, Record<string, string | number | boolean | null>>
	) => call<WifiSettings>('PUT', `${dev(id)}/openwrt/wifi`, { changes }),
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
		get<{ everyone: boolean; login: string; keys: AuthorizedKey[] }>(`${dev(id)}/keys`),
	removeKey: (id: string, key: { user: string; file: string; fingerprint: string }) =>
		call<{ removed: number }>('POST', `${dev(id)}/keys/remove`, key),
	rotateStart: () => call<{ rotation: Rotation | null }>('POST', '/ssh/rotate/start'),
	rotateFinish: (anyway = false) =>
		call<{ rotation: Rotation | null }>('POST', '/ssh/rotate/finish', { anyway }),
	rotateForget: (device: string) =>
		call<{ rotation: Rotation | null }>('POST', '/ssh/rotate/forget', { device }),
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
		call<{ muted_until: string | null }>('POST', `${dev(device)}/mute`, {
			minutes
		}),
	login: (user: string, password: string) =>
		call<{ user: string; role: string }>('POST', '/auth/login', { user, password }),
	logout: () => call<{ ok: boolean }>('POST', '/auth/logout'),
	sshKey: () => get<{ public_key: string }>('/ssh/key'),
	trustHost: (device: string, fingerprint: string, port?: number) =>
		call<{ state: string }>('POST', `${dev(device)}/ssh/trust`, {
			fingerprint,
			port
		}),
	desktopTicket: (
		device: string,
		credentials: { username: string; password: string; domain: string }
	) => call<{ ticket: string }>('POST', `${dev(device)}/desktop/ticket`, credentials),
	forgetHost: (device: string, port?: number) =>
		call<{ state: string }>('POST', `${dev(device)}/ssh/forget${port ? `?port=${port}` : ''}`)
};
