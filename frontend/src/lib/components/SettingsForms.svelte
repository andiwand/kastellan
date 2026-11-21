<script lang="ts">
	import { KEEP, type NotifyChannelForm, type Settings } from '#lib/api.js';
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
		</SettingsCard>

		<SettingsCard
			title="Alarms"
			description="When a problem becomes an alarm."
			dirty={json(alarms) !== base.alarms}
			reset={() => (alarms = JSON.parse(base.alarms))}
			save={() => save('alarms', { alarms })}
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
			title="Users"
			description="Who may sign in. Admins change things; observers see everything and change nothing. Passwords are stored as hashes; at least 10 characters."
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
							placeholder={u.old ? 'empty keeps it' : 'at least 10 characters'}
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
