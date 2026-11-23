<script lang="ts">
	import {
		KEEP,
		type MaintenanceWindowForm,
		type NotifyChannelForm,
		type Settings
	} from '#lib/api.js';
	import { settings, copy } from '#lib/settings.svelte.js';
	import { store } from '#lib/state.svelte.js';
	import SettingsCard from './SettingsCard.svelte';
	import SecretInput from './SecretInput.svelte';
	import { onMount } from 'svelte';
	import Plus from '@lucide/svelte/icons/plus';
	import Trash2 from '@lucide/svelte/icons/trash-2';

	/** The global settings as forms, each saved on its own into config.yaml (comments outside
	 * that part stay). Devices, groups and the VPN have their own pages. */
	type S = Settings['sections'];
	let general = $state<S['general']>({ check_interval: 60, retention_days: 30, heartbeat: null });
	let names = $state(''); // config.yaml's hosts, comma-separated in the form
	let alarms = $state<S['alarms']['alarms']>({
		down_after: 180,
		cert_days: 14,
		disk_warning: 85,
		disk_critical: 95,
		disk_temperature: 55
	});
	let notify = $state<NotifyChannelForm[]>([]);
	let vulns = $state<S['vulns']['vulns']>({ enabled: true, every_hours: 6, nvd_api_key: null });
	let networks = $state<{ id: string; name: string; cidr: string }[]>([]);
	let sites = $state<{ id: string; name: string }[]>([]);
	let users = $state<
		{ name: string; role: 'admin' | 'observer'; password: string; old: boolean }[]
	>([]);

	// Planned maintenance: weekly or once; times as the inputs want them (local, no zone).
	type Window = {
		name: string;
		devices: string[];
		groups: string[];
		sites: string[];
		weekly: boolean;
		days: string[];
		at: string;
		minutes: number;
		start: string;
		end: string;
	};
	let windows = $state<Window[]>([]);
	const DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
	const pad = (n: number) => String(n).padStart(2, '0');
	function localInput(iso: string | null | undefined): string {
		if (!iso) return '';
		const d = new Date(iso);
		return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
	}
	const toWindow = (w: MaintenanceWindowForm): Window => ({
		name: w.name ?? '',
		devices: w.devices ?? [],
		groups: w.groups ?? [],
		sites: w.sites ?? [],
		weekly: !w.start,
		days: w.days ?? [],
		at: w.at ?? '03:00',
		minutes: w.minutes ?? 60,
		start: localInput(w.start),
		end: localInput(w.end)
	});
	const fromWindow = (w: Window): MaintenanceWindowForm => ({
		name: w.name || undefined,
		devices: w.devices,
		groups: w.groups,
		sites: w.sites,
		...(w.weekly
			? { days: DAYS.filter((d) => w.days.includes(d)), at: w.at, minutes: w.minutes }
			: { start: new Date(w.start).toISOString(), end: new Date(w.end).toISOString() })
	});
	const windowOk = (w: Window) =>
		(w.devices.length || w.groups.length || w.sites.length) > 0 &&
		(w.weekly ? w.days.length > 0 && !!w.at && w.minutes > 0 : !!w.start && w.end > w.start);
	const toggle = (list: string[], x: string) => {
		const i = list.indexOf(x);
		if (i < 0) list.push(x);
		else list.splice(i, 1);
	};

	let backups = $state<{ id: string; name: string; device: string; within_hours: number }[]>([]);
	let logs = $state({
		days: 14,
		max_lines: 1_000_000,
		syslog: false,
		port: 5514,
		tls: false,
		tlsPort: 6514
	});
	let devBackups = $state({ every_hours: 24, keep: 60 });
	let wifiScan = $state({ on: true, at: '04:00' });
	let hubBackup = $state({
		every_hours: 24,
		keep: 30,
		history: true,
		logs: false,
		targets: [] as { path: string; device: string }[]
	});
	let net = $state({
		enabled: true,
		router: '',
		targets: '1.1.1.1, 9.9.9.9, 8.8.8.8',
		speed: false,
		every_hours: 24,
		booked_down: '' as string | number,
		booked_up: '' as string | number,
		iperf3: ''
	});

	// What was loaded, to tell what changed (as JSON).
	let base = $state<Record<string, string>>({});
	const json = (v: unknown) => JSON.stringify($state.snapshot(v));

	/** Fill the forms from what was loaded; a form with unsaved changes keeps them. */
	function fill(s: S) {
		const clean = (k: string, v: unknown) => !base[k] || json(v) === base[k] || saved.has(k);
		const next: Record<string, string> = { ...base };
		if (clean('general', general)) {
			general = copy(s.general);
			next.general = json(general);
		}
		if (clean('names', names)) {
			names = (s.names?.hosts ?? []).join(', ');
			next.names = json(names);
		}
		if (clean('alarms', alarms)) {
			alarms = copy(s.alarms.alarms);
			next.alarms = json(alarms);
		}
		if (clean('notify', notify)) {
			notify = copy(s.notify.notify);
			next.notify = json(notify);
		}
		if (clean('vulns', vulns)) {
			vulns = copy(s.vulns.vulns);
			next.vulns = json(vulns);
		}
		if (clean('networks', networks)) {
			networks = Object.entries(s.networks.networks ?? {}).map(([id, n]) => ({
				id,
				name: n.name ?? '',
				cidr: n.cidr ?? ''
			}));
			next.networks = json(networks);
		}
		if (clean('sites', sites)) {
			sites = Object.entries(s.sites.sites ?? {}).map(([id, name]) => ({ id, name }));
			next.sites = json(sites);
		}
		if (clean('maintenance', windows)) {
			windows = (s.maintenance.maintenance ?? []).map(toWindow);
			next.maintenance = json(windows);
		}
		if (clean('backups', backups)) {
			backups = Object.entries(s.backups.backups ?? {}).map(([id, b]) => ({
				id,
				name: b.name && b.name !== id ? b.name : '',
				device: b.device,
				within_hours: b.within_hours ?? 26
			}));
			next.backups = json(backups);
		}
		if (clean('logs', logs)) {
			const l = s.logs?.logs ?? {};
			logs = {
				days: l.days ?? 14,
				max_lines: l.max_lines ?? 1_000_000,
				syslog: l.syslog?.enabled ?? false,
				port: l.syslog?.port ?? 5514,
				tls: l.syslog_tls?.enabled ?? false,
				tlsPort: l.syslog_tls?.port ?? 6514
			};
			next.logs = json(logs);
		}
		if (clean('device_backups', devBackups)) {
			const b = s.device_backups?.device_backups ?? {};
			devBackups = { every_hours: b.every_hours ?? 24, keep: b.keep ?? 60 };
			next.device_backups = json(devBackups);
		}
		if (clean('hub_backup', hubBackup)) {
			const h = s.hub_backup?.hub_backup ?? {};
			hubBackup = {
				every_hours: h.every_hours ?? 24,
				keep: h.keep ?? 30,
				history: h.history ?? true,
				logs: h.logs ?? false,
				targets: (h.targets ?? []).map((t) => ({ path: t.path, device: t.device ?? '' }))
			};
			next.hub_backup = json(hubBackup);
		}
		if (clean('wifi', wifiScan)) {
			const w = s.wifi?.wifi;
			const at = w && 'scan_at' in w ? w.scan_at : '04:00';
			wifiScan = { on: at !== null, at: at ?? '04:00' };
			next.wifi = json(wifiScan);
		}
		if (clean('internet', net)) {
			const i = s.internet?.internet ?? {};
			net = {
				enabled: i.enabled ?? true,
				router: i.router ?? '',
				targets: (i.targets ?? ['1.1.1.1', '9.9.9.9', '8.8.8.8']).join(', '),
				speed: i.speedtest?.enabled ?? false,
				every_hours: i.speedtest?.every_hours ?? 24,
				booked_down: i.speedtest?.booked_down ?? '',
				booked_up: i.speedtest?.booked_up ?? '',
				iperf3: i.speedtest?.iperf3 ?? ''
			};
			next.internet = json(net);
		}
		if (clean('users', users)) {
			users = Object.entries(s.users.users ?? {}).map(([name, u]) => ({
				name,
				role: u.role,
				password: '',
				old: true
			}));
			next.users = json(users);
		}
		base = next;
		saved.clear();
	}
	// The form just saved is filled again from the file, even though it differed from before.
	const saved = new Set<string>();
	function save(section: keyof S, value: object) {
		saved.add(section);
		return settings.save(section, value).catch((e) => {
			saved.delete(section);
			throw e;
		});
	}

	onMount(() => settings.load()); // fresh: the file may have changed elsewhere
	let shown = '';
	$effect(() => {
		const d = settings.data;
		if (d && d.version !== shown) {
			shown = d.version;
			fill(d.sections);
		}
	});

	const KINDS = ['ntfy', 'gotify', 'telegram', 'email', 'webhook', 'push'] as const;
	type Kind = (typeof KINDS)[number];
	const kindOf = (c: NotifyChannelForm): Kind =>
		KINDS.find((k) => c[k] != null && c[k] !== '') ?? 'ntfy';
	function setKind(c: NotifyChannelForm, kind: Kind) {
		const target = c[kindOf(c)];
		for (const k of KINDS) c[k] = null;
		// Another kind is another channel: none of the old one's secrets go along.
		c[kind] =
			kind === 'telegram' || kind === 'push' || target === KEEP ? '' : (target as string) || '';
		c.token = null;
		c.smtp = null;
	}
	// What this channel was when the page got it (by its id), if anything.
	const original = (c: NotifyChannelForm) =>
		c._id == null
			? undefined
			: (JSON.parse(base.notify ?? '[]') as NotifyChannelForm[]).find((o) => o._id === c._id);
	// Its token stays only while it is the same channel: the same kind and address.
	function tokenStored(c: NotifyChannelForm): boolean {
		const o = original(c);
		const k = kindOf(c);
		return !!o && o.token === KEEP && kindOf(o) === k && o[k] === c[k];
	}
	const TARGET: Record<Kind, [string, string]> = {
		ntfy: ['Topic URL', 'https://ntfy.sh/my-home'],
		gotify: ['Server', 'https://gotify.example.org'],
		telegram: ['Chat id', '123456789'],
		email: ['To', 'me@example.org'],
		webhook: ['URL', 'https://example.org/hook'],
		push: ['User (their browsers with notifications on; * for everyone)', 'ada']
	};
	const TOKEN: Partial<Record<Kind, string>> = {
		ntfy: 'Access token (optional)',
		gotify: 'App token',
		telegram: 'Bot token'
	};

	// Only what the backend wants: empty strings as nothing.
	const clean = (o: object) =>
		Object.fromEntries(Object.entries(o).map(([k, v]) => [k, v === '' ? null : v]));
	const me = $derived(store.me?.user ?? '');
	const nameOk = (n: string) => /^[a-z0-9][a-z0-9_.-]{0,31}$/.test(n);
	const unique = (names: string[]) => new Set(names).size === names.length;
</script>

{#if settings.error}
	<p class="mt-6 text-sm" style:color="var(--critical-text)">{settings.error}</p>
{:else if !settings.data || !base.general}
	<p class="mt-6 text-sm text-muted">Loading…</p>
{:else}
	<div class="mt-6 flex flex-col gap-6">
		<SettingsCard
			title="Checking"
			description="How often devices are checked, how long history is kept, and a dead man's switch: a URL Kastellan calls every minute (healthchecks.io, say), so you hear about it when Kastellan itself stops."
			dirty={json(general) !== base.general}
			reset={() => (general = JSON.parse(base.general))}
			save={() => save('general', clean(general))}
		>
			<div class="grid gap-4 sm:grid-cols-2">
				<label class="field">
					Check every (seconds)
					<input class="input" type="number" min="5" bind:value={general.check_interval} />
				</label>
				<label class="field">
					Keep history (days)
					<input class="input" type="number" min="1" bind:value={general.retention_days} />
				</label>
			</div>
			<label class="field">
				Heartbeat URL
				<input
					class="input"
					type="url"
					placeholder="https://hc-ping.com/…"
					bind:value={general.heartbeat}
				/>
			</label>
			<label class="choice text-sm">
				<input
					type="checkbox"
					checked={general.check_releases ?? true}
					onchange={(e) => (general.check_releases = e.currentTarget.checked)}
				/>
				Tell me when a newer Kastellan is out (asks GitHub once a day; sends nothing about this hub)
			</label>
		</SettingsCard>

		<SettingsCard
			title="Names"
			description="The names you open Kastellan by, besides its IP addresses and localhost. It refuses requests for any other name, so a web page on someone else's name can't reach it (DNS rebinding). The names in KASTELLAN_HOSTS are taken in once, at the first start; from then on they are set here."
			dirty={json(names) !== base.names}
			reset={() => (names = JSON.parse(base.names))}
			save={() =>
				save('names', {
					hosts: names
						.split(/[\s,]+/)
						.map((n) => n.trim())
						.filter(Boolean)
				})}
		>
			<label class="field">
				Names (comma-separated)
				<input class="input font-mono" placeholder="kastellan.lan" bind:value={names} />
			</label>
		</SettingsCard>

		<SettingsCard
			title="Internet"
			description="Router and outside targets for connectivity checks. Speed tests use bandwidth and are off by default."
			dirty={json(net) !== base.internet}
			reset={() => (net = JSON.parse(base.internet))}
			save={() =>
				save('internet', {
					internet: {
						...settings.data?.sections.internet.internet,
						enabled: net.enabled,
						router: net.router || null,
						targets: net.targets
							.split(/[\s,]+/)
							.map((t) => t.trim())
							.filter(Boolean),
						speedtest: {
							...settings.data?.sections.internet.internet?.speedtest,
							enabled: net.speed,
							every_hours: net.every_hours,
							booked_down: net.booked_down === '' ? null : Number(net.booked_down),
							booked_up: net.booked_up === '' ? null : Number(net.booked_up),
							iperf3: net.iperf3.trim() || null
						}
					}
				})}
		>
			<label class="choice text-sm">
				<input type="checkbox" bind:checked={net.enabled} /> Check the internet connection
			</label>
			<div class="grid gap-4 sm:grid-cols-2">
				<label class="field">
					Router (whose line it is)
					<select class="input" bind:value={net.router}>
						<option value="">find it (the gateway, the FRITZ!Box)</option>
						{#each (store.devices ?? []).filter((d) => d.kind === 'router') as d (d.id)}
							<option value={d.id}>{d.name}</option>
						{/each}
					</select>
				</label>
				<label class="field">
					Addresses to ping
					<input class="input" bind:value={net.targets} />
				</label>
			</div>
			<label class="choice text-sm">
				<input type="checkbox" bind:checked={net.speed} /> Scheduled speed tests (deferred while the line
				is busy)
			</label>
			<div class="grid gap-4 sm:grid-cols-3">
				<label class="field">
					Every (hours)
					<input class="input" type="number" min="1" bind:value={net.every_hours} />
				</label>
				<label class="field">
					Booked download (Mbit/s)
					<input class="input" type="number" min="1" bind:value={net.booked_down} />
				</label>
				<label class="field">
					Booked upload (Mbit/s)
					<input class="input" type="number" min="1" bind:value={net.booked_up} />
				</label>
			</div>
			<label class="field">
				iperf3 server (instead of the HTTP test; host or host:port)
				<input class="input" placeholder="none" bind:value={net.iperf3} />
			</label>
		</SettingsCard>

		<SettingsCard
			title="Wi-Fi scans"
			description="Daily OpenWrt scans for channel suggestions. Clients may briefly pause; choose a quiet hour."
			dirty={json(wifiScan) !== base.wifi}
			reset={() => (wifiScan = JSON.parse(base.wifi))}
			save={() => save('wifi', { wifi: { scan_at: wifiScan.on ? wifiScan.at : null } })}
		>
			<label class="choice text-sm">
				<input type="checkbox" bind:checked={wifiScan.on} /> Scan every day
			</label>
			{#if wifiScan.on}
				<label class="field sm:w-48">
					At (the hub's time)
					<input class="input" type="time" bind:value={wifiScan.at} />
				</label>
			{/if}
		</SettingsCard>

		<SettingsCard
			title="Logs"
			description="Log retention and optional UDP syslog. Syslog is unencrypted and forgeable; known-device messages are marked unverified. Publish the UDP port in Docker."
			dirty={json(logs) !== base.logs}
			reset={() => (logs = JSON.parse(base.logs))}
			save={() =>
				save('logs', {
					logs: {
						days: logs.days,
						max_lines: logs.max_lines,
						syslog: { enabled: logs.syslog, port: logs.port },
						syslog_tls: { enabled: logs.tls, port: logs.tlsPort }
					}
				})}
		>
			<div class="grid gap-4 sm:grid-cols-2">
				<label class="field">
					Keep (days)
					<input class="input" type="number" min="1" max="365" bind:value={logs.days} />
				</label>
				<label class="field">
					At most (lines)
					<input class="input" type="number" min="10000" step="10000" bind:value={logs.max_lines} />
				</label>
			</div>
			<label class="choice text-sm">
				<input type="checkbox" bind:checked={logs.syslog} /> Take syslog on an open UDP port (unencrypted)
			</label>
			{#if logs.syslog}
				<label class="field sm:w-48">
					Port
					<input class="input" type="number" min="1" max="65535" bind:value={logs.port} />
				</label>
			{/if}
			<label class="choice text-sm">
				<input type="checkbox" bind:checked={logs.tls} /> Take syslog over TLS (encrypted; each device
				proves itself with a certificate Kastellan issues it, on its Setup tab)
			</label>
			{#if logs.tls}
				<label class="field sm:w-48">
					TLS port
					<input class="input" type="number" min="1" max="65535" bind:value={logs.tlsPort} />
				</label>
			{/if}
		</SettingsCard>

		<SettingsCard
			title="Kastellan's own backup"
			description="Copy backups to a mounted folder or an SFTP device. Each passphrase gets a separate kastellan-… subfolder. Create the passphrase on the Backups page."
			dirty={json(hubBackup) !== base.hub_backup}
			reset={() => (hubBackup = JSON.parse(base.hub_backup))}
			save={() =>
				save('hub_backup', {
					hub_backup: {
						...hubBackup,
						targets: hubBackup.targets.map((t) => ({ path: t.path, device: t.device || null }))
					}
				})}
		>
			<div class="grid gap-4 sm:grid-cols-2">
				<label class="field">
					Every (hours)
					<input class="input" type="number" min="1" bind:value={hubBackup.every_hours} />
				</label>
				<label class="field">
					Keep (versions)
					<input class="input" type="number" min="1" max="1000" bind:value={hubBackup.keep} />
				</label>
			</div>
			<label class="choice text-sm">
				<input type="checkbox" bind:checked={hubBackup.history} /> With check and vitals history
			</label>
			<label class="choice text-sm">
				<input type="checkbox" bind:checked={hubBackup.logs} /> With the central logs (large)
			</label>
			{#each hubBackup.targets as t, i (i)}
				<div class="grid grid-cols-[minmax(0,1fr)_auto] items-end gap-2 sm:flex sm:flex-wrap">
					<label class="field col-span-2">
						On
						<select class="input" bind:value={t.device}>
							<option value="">this hub (a mounted folder)</option>
							{#each (store.devices ?? []).filter((d) => d.ssh) as d (d.id)}
								<option value={d.id}>{d.name} (SFTP)</option>
							{/each}
						</select>
					</label>
					<label class="field min-w-0 sm:min-w-64 sm:flex-1">
						Folder
						<input class="input" placeholder="/backup/kastellan" bind:value={t.path} />
					</label>
					<button
						class="btn btn-sm mb-0.5"
						type="button"
						onclick={() => hubBackup.targets.splice(i, 1)}
						aria-label="Remove"
					>
						<Trash2 class="size-3.5" />
					</button>
				</div>
			{/each}
			<button
				class="btn btn-sm self-start"
				type="button"
				disabled={hubBackup.targets.length >= 8}
				onclick={() => hubBackup.targets.push({ path: '', device: '' })}
			>
				<Plus class="size-3.5" /> A place for copies
			</button>
		</SettingsCard>

		<SettingsCard
			title="Device configuration backups"
			description="Encrypted OpenWrt and FRITZ!Box configuration backups, with version comparisons."
			dirty={json(devBackups) !== base.device_backups}
			reset={() => (devBackups = JSON.parse(base.device_backups))}
			save={() => save('device_backups', { device_backups: devBackups })}
		>
			<div class="grid gap-4 sm:grid-cols-2">
				<label class="field">
					Every (hours)
					<input class="input" type="number" min="1" bind:value={devBackups.every_hours} />
				</label>
				<label class="field">
					Keep (versions per device)
					<input class="input" type="number" min="1" max="1000" bind:value={devBackups.keep} />
				</label>
			</div>
		</SettingsCard>

		<SettingsCard
			title="Alarms"
			description="When a problem becomes an alarm. An alarm nobody acknowledged can be sent again now and then, until someone does."
			dirty={json(alarms) !== base.alarms}
			reset={() => (alarms = JSON.parse(base.alarms))}
			save={() =>
				save('alarms', {
					alarms: {
						...alarms,
						remind_hours: alarms.remind_hours || 0,
						disk_days: alarms.disk_days ?? 14
					}
				})}
		>
			<div class="grid gap-4 sm:grid-cols-2">
				<label class="field">
					Down for (seconds) before an alarm
					<input class="input" type="number" min="30" bind:value={alarms.down_after} />
				</label>
				<label class="field">
					Certificates ending within (days)
					<input class="input" type="number" min="1" max="365" bind:value={alarms.cert_days} />
				</label>
				<label class="field">
					A disk this full: warning (%)
					<input class="input" type="number" min="50" max="99" bind:value={alarms.disk_warning} />
				</label>
				<label class="field">
					…critical (%)
					<input class="input" type="number" min="50" max="100" bind:value={alarms.disk_critical} />
				</label>
				<label class="field">
					A disk this warm (°C)
					<input
						class="input"
						type="number"
						min="30"
						max="90"
						bind:value={alarms.disk_temperature}
					/>
				</label>
				<label class="field">
					A disk full within (days, at its rate; 0: never)
					<input class="input" type="number" min="0" max="365" bind:value={alarms.disk_days} />
				</label>
				<label class="field">
					Say an alarm again after (hours; 0: never)
					<input
						class="input"
						type="number"
						min="0"
						max="168"
						bind:value={alarms.remind_hours}
						placeholder="0"
					/>
				</label>
			</div>
		</SettingsCard>

		<SettingsCard
			title="Notifications"
			description="Where alarms go. Each channel gets alarms from its level up; in its quiet hours, only critical ones."
			dirty={json(notify) !== base.notify}
			reset={() => (notify = JSON.parse(base.notify))}
			save={() => save('notify', { notify: notify.map(clean) })}
		>
			{#each notify as channel, i (channel)}
				{@const kind = kindOf(channel)}
				<fieldset class="flex flex-col gap-3 rounded-xl border border-line p-4">
					<div class="flex items-end gap-3">
						<label class="field w-40">
							Kind
							<select
								class="input"
								value={kind}
								onchange={(e) => setKind(channel, e.currentTarget.value as Kind)}
							>
								<option value="ntfy">ntfy</option>
								<option value="gotify">Gotify</option>
								<option value="telegram">Telegram</option>
								<option value="email">E-mail</option>
								<option value="webhook">Webhook</option>
								<option value="push">Push (browsers)</option>
							</select>
						</label>
						<label class="field flex-1">
							{TARGET[kind][0]}
							{#if kind === 'webhook' && original(channel)?.webhook === KEEP}
								<SecretInput
									bind:value={channel.webhook}
									stored={kindOf(original(channel)!) === 'webhook'}
									optional={false}
								/>
							{:else}
								<input
									class="input"
									placeholder={TARGET[kind][1]}
									value={channel[kind] ?? ''}
									oninput={(e) => (channel[kind] = e.currentTarget.value)}
								/>
							{/if}
						</label>
						<button
							type="button"
							class="btn btn-sm mb-0.5 px-2"
							aria-label="Remove this channel"
							onclick={() => notify.splice(i, 1)}
						>
							<Trash2 class="size-4 text-muted" />
						</button>
					</div>
					{#if TOKEN[kind]}
						<label class="field">
							{TOKEN[kind]}
							<SecretInput bind:value={channel.token} stored={tokenStored(channel)} />
						</label>
					{/if}
					{#if kind === 'email'}
						<div class="grid gap-3 sm:grid-cols-2">
							<label class="field">
								Mail server
								<input
									class="input"
									placeholder="smtps://user:pass@mail.example.org"
									bind:value={channel.smtp}
								/>
								{#if channel.smtp?.includes(':__keep__@')}<span class="hint">
										the password stays hidden (__keep__) unless you type a new one
									</span>{/if}
							</label>
							<label class="field">
								From
								<input
									class="input"
									placeholder="kastellan@example.org"
									bind:value={channel.sender}
								/>
							</label>
						</div>
					{/if}
					<div class="grid gap-3 sm:grid-cols-2">
						<label class="field">
							From level
							<select class="input" bind:value={channel.min}>
								<option value="info">info</option>
								<option value="warning">warning</option>
								<option value="critical">critical</option>
							</select>
						</label>
						<label class="field">
							Quiet hours
							<input class="input" placeholder="22:00-07:00" bind:value={channel.quiet} />
						</label>
					</div>
				</fieldset>
			{/each}
			<div>
				<button
					type="button"
					class="btn btn-sm"
					onclick={() => notify.push({ ntfy: '', min: 'warning' })}
				>
					<Plus class="size-3.5" /> Add a channel
				</button>
			</div>
		</SettingsCard>

		<SettingsCard
			title="Vulnerabilities"
			description="Known vulnerabilities in the software, firmware and containers Kastellan sees (NVD, OSV, CISA KEV)."
			dirty={json(vulns) !== base.vulns}
			reset={() => (vulns = JSON.parse(base.vulns))}
			save={() => save('vulns', { vulns: clean(vulns) })}
		>
			<label class="choice text-sm">
				<input type="checkbox" bind:checked={vulns.enabled} /> Look them up
			</label>
			<div class="grid gap-4 sm:grid-cols-2">
				<label class="field">
					Every (hours)
					<input class="input" type="number" min="1" step="0.5" bind:value={vulns.every_hours} />
				</label>
				<label class="field">
					NVD API key (optional, faster)
					<SecretInput
						bind:value={vulns.nvd_api_key}
						stored={JSON.parse(base.vulns ?? '{}').nvd_api_key === KEEP}
					/>
				</label>
			</div>
		</SettingsCard>

		<SettingsCard
			title="Networks"
			description="Names for your networks: a device whose address is in a network's range is in it. Without any, Kastellan works them out by itself."
			dirty={json(networks) !== base.networks}
			canSave={networks.every((n) => nameOk(n.id)) && unique(networks.map((n) => n.id))}
			reset={() => (networks = JSON.parse(base.networks))}
			save={() =>
				save('networks', {
					networks: Object.fromEntries(
						networks.map((n) => [n.id, { name: n.name, cidr: n.cidr || null }])
					)
				})}
		>
			{#each networks as n, i (i)}
				<div class="grid grid-cols-[1fr_1.5fr_1.5fr_auto] items-end gap-3">
					<label class="field">
						<span class={i ? 'sr-only' : ''}>Id</span>
						<input class="input font-mono" bind:value={n.id} placeholder="home" />
					</label>
					<label class="field">
						<span class={i ? 'sr-only' : ''}>Name</span>
						<input class="input" bind:value={n.name} placeholder="Home" />
					</label>
					<label class="field">
						<span class={i ? 'sr-only' : ''}>Range</span>
						<input class="input font-mono" bind:value={n.cidr} placeholder="192.168.1.0/24" />
					</label>
					<button
						type="button"
						class="btn btn-sm mb-0.5 px-2"
						aria-label="Remove this network"
						onclick={() => networks.splice(i, 1)}
					>
						<Trash2 class="size-4 text-muted" />
					</button>
				</div>
			{/each}
			<div>
				<button
					type="button"
					class="btn btn-sm"
					onclick={() => networks.push({ id: '', name: '', cidr: '' })}
				>
					<Plus class="size-3.5" /> Add a network
				</button>
			</div>
		</SettingsCard>

		<SettingsCard
			title="Sites"
			description="Names for where devices are (a device's site: home, hetzner, ...). With devices at more than one site, the list is by site, and alarms say where a device away from home is."
			dirty={json(sites) !== base.sites}
			canSave={sites.every((s) => nameOk(s.id)) && unique(sites.map((s) => s.id))}
			reset={() => (sites = JSON.parse(base.sites))}
			save={() => save('sites', { sites: Object.fromEntries(sites.map((s) => [s.id, s.name])) })}
		>
			{#each sites as s, i (i)}
				<div class="grid grid-cols-[1fr_2fr_auto] items-end gap-3">
					<label class="field">
						<span class={i ? 'sr-only' : ''}>Id</span>
						<input class="input font-mono" bind:value={s.id} placeholder="hetzner" />
					</label>
					<label class="field">
						<span class={i ? 'sr-only' : ''}>Name</span>
						<input class="input" bind:value={s.name} placeholder="Hetzner Cloud" />
					</label>
					<button
						type="button"
						class="btn btn-sm mb-0.5 px-2"
						aria-label="Remove this site"
						onclick={() => sites.splice(i, 1)}
					>
						<Trash2 class="size-4 text-muted" />
					</button>
				</div>
			{/each}
			<div>
				<button type="button" class="btn btn-sm" onclick={() => sites.push({ id: '', name: '' })}>
					<Plus class="size-3.5" /> Add a site
				</button>
			</div>
		</SettingsCard>

		<SettingsCard
			title="Maintenance"
			description="Hold notifications during planned work. Weekly times use the hub’s timezone; one-off times use yours. Problems still present are sent when maintenance ends."
			dirty={json(windows) !== base.maintenance}
			canSave={windows.every(windowOk)}
			reset={() => (windows = JSON.parse(base.maintenance))}
			save={() => save('maintenance', { maintenance: windows.map(fromWindow) })}
		>
			{#each windows as w, i (i)}
				{@const groupIds = Object.keys(settings.data?.sections.groups.groups ?? {})}
				{@const siteIds = [
					...new Set([
						'home',
						...Object.keys(settings.data?.sections.sites.sites ?? {}),
						...(store.devices ?? []).map((d) => d.site)
					])
				]}
				<fieldset class="flex flex-col gap-3 rounded-xl border border-line p-3">
					<div class="flex items-end gap-3">
						<label class="field flex-1">
							Name
							<input class="input" bind:value={w.name} placeholder="Patch night" />
						</label>
						<button
							type="button"
							class="btn btn-sm mb-0.5 px-2"
							aria-label="Remove this window"
							onclick={() => windows.splice(i, 1)}
						>
							<Trash2 class="size-4 text-muted" />
						</button>
					</div>
					<div class="flex flex-wrap gap-x-4 gap-y-1 text-sm">
						<label class="choice">
							<input type="radio" bind:group={w.weekly} value={true} /> Every week
						</label>
						<label class="choice">
							<input type="radio" bind:group={w.weekly} value={false} /> Once
						</label>
					</div>
					{#if w.weekly}
						<div class="flex flex-wrap gap-1.5">
							{#each DAYS as d (d)}
								<button
									type="button"
									class="btn btn-sm capitalize {w.days.includes(d) ? 'btn-primary' : ''}"
									aria-pressed={w.days.includes(d)}
									onclick={() => toggle(w.days, d)}
								>
									{d}
								</button>
							{/each}
						</div>
						<div class="grid gap-3 sm:grid-cols-2">
							<label class="field">At <input class="input" type="time" bind:value={w.at} /></label>
							<label class="field">
								For (minutes)
								<input class="input" type="number" min="1" max="10080" bind:value={w.minutes} />
							</label>
						</div>
					{:else}
						<div class="grid gap-3 sm:grid-cols-2">
							<label class="field">
								From <input class="input" type="datetime-local" bind:value={w.start} />
							</label>
							<label class="field">
								Until <input class="input" type="datetime-local" bind:value={w.end} />
							</label>
						</div>
					{/if}
					<div class="flex flex-col gap-2 text-xs text-muted">
						{#if groupIds.length}
							<div class="flex flex-wrap items-center gap-x-3 gap-y-1">
								Groups:
								{#each groupIds as g (g)}
									<label class="choice text-text">
										<input
											type="checkbox"
											checked={w.groups.includes(g)}
											onchange={() => toggle(w.groups, g)}
										/>
										{settings.data?.sections.groups.groups[g]?.name || g}
									</label>
								{/each}
							</div>
						{/if}
						{#if siteIds.length > 1}
							<div class="flex flex-wrap items-center gap-x-3 gap-y-1">
								Sites:
								{#each siteIds as site (site)}
									<label class="choice text-text">
										<input
											type="checkbox"
											checked={w.sites.includes(site)}
											onchange={() => toggle(w.sites, site)}
										/>
										{settings.data?.sections.sites.sites[site] || site}
									</label>
								{/each}
							</div>
						{/if}
						<label class="field">
							Devices
							<select class="input h-24" multiple bind:value={w.devices}>
								{#each store.devices ?? [] as d (d.id)}
									<option value={d.id}>{d.name}</option>
								{/each}
							</select>
						</label>
					</div>
				</fieldset>
			{/each}
			<div>
				<button
					type="button"
					class="btn btn-sm"
					onclick={() => windows.push(toWindow({ days: ['sun'] }))}
				>
					<Plus class="size-3.5" /> Add a window
				</button>
			</div>
		</SettingsCard>

		<SettingsCard
			title="Backups"
			description="Backups that report in: their job calls Kastellan when done (the command is on the device's page, with a token for it). No success within the hours given, or a failure since the last one, is an alarm."
			dirty={json(backups) !== base.backups}
			canSave={backups.every((b) => nameOk(b.id) && b.device && b.within_hours > 0) &&
				unique(backups.map((b) => b.id))}
			reset={() => (backups = JSON.parse(base.backups))}
			save={() =>
				save('backups', {
					backups: Object.fromEntries(
						backups.map((b) => [
							b.id,
							{ name: b.name || undefined, device: b.device, within_hours: b.within_hours }
						])
					)
				})}
		>
			{#each backups as b, i (i)}
				<div class="grid grid-cols-2 items-end gap-3 sm:grid-cols-[1fr_1.5fr_1fr_7rem_auto]">
					<label class="field">
						<span class={i ? 'sm:sr-only' : ''}>Id</span>
						<input class="input font-mono" bind:value={b.id} placeholder="nas-b2" />
					</label>
					<label class="field">
						<span class={i ? 'sm:sr-only' : ''}>Name</span>
						<input class="input" bind:value={b.name} placeholder="NAS to B2" />
					</label>
					<label class="field">
						<span class={i ? 'sm:sr-only' : ''}>Device</span>
						<select class="input" bind:value={b.device}>
							{#each store.devices ?? [] as d (d.id)}
								<option value={d.id}>{d.name}</option>
							{/each}
						</select>
					</label>
					<label class="field">
						<span class={i ? 'sm:sr-only' : ''}>Within (h)</span>
						<input class="input" type="number" min="1" bind:value={b.within_hours} />
					</label>
					<button
						type="button"
						class="btn btn-sm mb-0.5 px-2"
						aria-label="Remove this backup"
						onclick={() => backups.splice(i, 1)}
					>
						<Trash2 class="size-4 text-muted" />
					</button>
				</div>
			{/each}
			<div>
				<button
					type="button"
					class="btn btn-sm"
					onclick={() =>
						backups.push({
							id: '',
							name: '',
							device: store.devices?.[0]?.id ?? '',
							within_hours: 26
						})}
				>
					<Plus class="size-3.5" /> Add a backup
				</button>
			</div>
		</SettingsCard>

		<SettingsCard
			title="Users"
			description="Who may sign in. Admins change things; observers see everything and change nothing. Passwords are stored as hashes."
			dirty={json(users) !== base.users}
			canSave={users.every((u) => nameOk(u.name) && (u.old || u.password.length >= 10)) &&
				unique(users.map((u) => u.name))}
			reset={() => (users = JSON.parse(base.users))}
			save={() =>
				save('users', {
					users: Object.fromEntries(
						users.map((u) => [u.name, { role: u.role, password: u.password || null }])
					)
				})}
		>
			{#each users as u, i (i)}
				<div class="grid grid-cols-[1fr_1fr_1.5fr_auto] items-end gap-3">
					<label class="field">
						<span class={i ? 'sr-only' : ''}>Name</span>
						<input class="input font-mono" bind:value={u.name} readonly={u.old} />
					</label>
					<label class="field">
						<span class={i ? 'sr-only' : ''}>Role</span>
						<select class="input" bind:value={u.role}>
							<option value="admin">admin</option>
							<option value="observer">observer</option>
						</select>
					</label>
					<label class="field">
						<span class={i ? 'sr-only' : ''}>Password</span>
						<input
							class="input"
							type="password"
							autocomplete="new-password"
							placeholder={u.old ? 'empty keeps it' : 'a password'}
							bind:value={u.password}
						/>
					</label>
					<button
						type="button"
						class="btn btn-sm mb-0.5 px-2"
						aria-label="Remove this user"
						disabled={u.name === me}
						title={u.name === me ? "That's you" : ''}
						onclick={() => users.splice(i, 1)}
					>
						<Trash2 class="size-4 text-muted" />
					</button>
				</div>
			{/each}
			<div>
				<button
					type="button"
					class="btn btn-sm"
					onclick={() => users.push({ name: '', role: 'observer', password: '', old: false })}
				>
					<Plus class="size-3.5" /> Add a user
				</button>
			</div>
		</SettingsCard>
	</div>
{/if}
