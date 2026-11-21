<script lang="ts">
	const HOSTS = ['server', 'pc', 'laptop', 'other'];
	import { page } from '$app/state';
	import { api, more, type Change, type Series } from '#lib/api.js';
	import { scriptName } from '#lib/updates.js';
	import { store } from '#lib/state.svelte.js';
	import { statusColor, statusOf, upsLabel, upsStatus } from '#lib/status.js';
	import { ago, dayClock } from '#lib/format.js';
	import KindIcon from '#lib/components/KindIcon.svelte';
	import StatusBadge from '#lib/components/StatusBadge.svelte';
	import LatencyChart from '#lib/components/LatencyChart.svelte';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import SquareTerminal from '@lucide/svelte/icons/square-terminal';
	import MonitorPlay from '@lucide/svelte/icons/monitor-play';
	import Bell from '@lucide/svelte/icons/bell';
	import BellOff from '@lucide/svelte/icons/bell-off';
	import Wrench from '@lucide/svelte/icons/wrench';
	import MaintenanceButton from '#lib/components/MaintenanceButton.svelte';
	import Copy from '@lucide/svelte/icons/copy';
	import Check from '@lucide/svelte/icons/check';
	import ActivityItem from '#lib/components/ActivityItem.svelte';
	import ServicesTable from '#lib/components/ServicesTable.svelte';
	import ContainersTable from '#lib/components/ContainersTable.svelte';
	import VmsTable from '#lib/components/VmsTable.svelte';
	import UpsCard from '#lib/components/UpsCard.svelte';
	import { vmTone } from '#lib/actions.js';
	import VulnsList from '#lib/components/VulnsList.svelte';
	import AccessSetup from '#lib/components/AccessSetup.svelte';
	import DeviceEditor from '#lib/components/DeviceEditor.svelte';
	import ConfigFile from '#lib/components/ConfigFile.svelte';
	import Tabs from '#lib/components/Tabs.svelte';
	import { goto } from '$app/navigation';
	import DeviceKeys from '#lib/components/DeviceKeys.svelte';
	import CollectorSetup from '#lib/components/CollectorSetup.svelte';
	import ActorSetup from '#lib/components/ActorSetup.svelte';
	import HostHealth from '#lib/components/HostHealth.svelte';
	import BackupsCard from '#lib/components/BackupsCard.svelte';
	import ConfigBackups from '#lib/components/ConfigBackups.svelte';
	import ReadAgain from '#lib/components/ReadAgain.svelte';
	import RouterCard from '#lib/components/RouterCard.svelte';
	import FritzCard from '#lib/components/FritzCard.svelte';
	import FritzSetup from '#lib/components/FritzSetup.svelte';
	import SyslogSetup from '#lib/components/SyslogSetup.svelte';
	import OpenWrtSetup from '#lib/components/OpenWrtSetup.svelte';
	import RouterReboot from '#lib/components/RouterReboot.svelte';
	import HostReboot from '#lib/components/HostReboot.svelte';
	import WebLogin from '#lib/components/WebLogin.svelte';
	import SetupCard from '#lib/components/SetupCard.svelte';
	import WifiDialog from '#lib/components/WifiDialog.svelte';
	import FirmwareDialog from '#lib/components/FirmwareDialog.svelte';
	import CircleFadingArrowUp from '@lucide/svelte/icons/circle-fading-arrow-up';

	const id = $derived(page.params.id ?? '');
	const device = $derived(store.byId.get(id));
	const status = $derived(device ? statusOf(device) : 'waiting');
	const parent = $derived(device?.parent ? store.byId.get(device.parent) : undefined);
	const children = $derived((store.devices ?? []).filter((d) => d.parent === id));
	// Watching it (Status) is for everyone; setting it up (Setup) and changing its part of
	// config.yaml (Config: a form or the text) are for admins. Old links (?tab=edit|file) still
	// lead to the right place.
	type Tab = 'status' | 'setup' | 'config';
	const admin = $derived(store.me?.role === 'admin');
	const tab = $derived.by((): Tab => {
		const t = page.url.searchParams.get('tab');
		if (!admin) return 'status';
		// Setup opens doors on the device: one without an address of its own (a UPS read through
		// its host) has none.
		if (t === 'setup' && device?.host) return 'setup';
		return t === 'config' || t === 'edit' || t === 'file' ? 'config' : 'status';
	});
	const view = $derived(
		page.url.searchParams.get('view') === 'yaml' || page.url.searchParams.get('tab') === 'file'
			? 'yaml'
			: 'form'
	);
	function show(t: Tab, v: 'form' | 'yaml' = 'form') {
		const q =
			t === 'status' ? '?' : `?tab=${t}${t === 'config' && v === 'yaml' ? '&view=yaml' : ''}`;
		goto(q, { replaceState: true });
	}
	/** One action through the host's actor; what happened, in words. */
	// Through the hub on its own name (webproxy.py) for signed-in admins when web: is set: the
	// hub hands out a one-time ticket and sends the browser there; else the device directly.
	const webUi = $derived.by(() => {
		if (!device?.web_url) return undefined;
		if (store.health?.web_domain && store.me?.role === 'admin' && store.me.user)
			return `/api/devices/${encodeURIComponent(device.id)}/web`;
		return (device.checks.find((c) => c.type === 'http')?.target as string) ?? device.web_url;
	});

	const ranges = [
		{ hours: 1, buckets: 60, label: '1 h' },
		{ hours: 24, buckets: 96, label: '24 h' },
		{ hours: 24 * 7, buckets: 84, label: '7 d' }
	];
	let range = $state(ranges[1]);
	let series = $state<Series | null>(null);
	let changes = $state<Change[]>([]);
	let chosen = $state<string | null>(null);
	let copied = $state(false);
	let wifiOpen = $state(false);
	let firmwareOpen = $state(false);
	let fritzUpdate = $state<'asking' | 'started' | null>(null);
	let fritzNote = $state<string | null>(null);
	async function updateFritz() {
		try {
			await more.fritzFirmware(id);
			fritzUpdate = 'started';
		} catch (e) {
			fritzUpdate = null;
			fritzNote = (e as Error).message;
		}
	}

	let loadError = $state<string | null>(null);
	let latest = 0;
	async function load() {
		// Only the newest request may write: an older one finishing late must not win.
		const mine = ++latest;
		try {
			const [s, c] = await Promise.all([
				api.series(range.hours, range.buckets, id),
				api.changes(range.hours, id, 50)
			]);
			if (mine === latest) [series, changes, loadError] = [s, c, null];
		} catch (e) {
			if (mine === latest) loadError = e instanceof Error ? e.message : String(e);
		}
	}
	$effect(() => {
		void range;
		void id;
		load();
		const timer = setInterval(load, 30000);
		return () => clearInterval(timer);
	});

	const checkSeries = $derived(series?.devices[id] ?? {});
	const latencyCheck = $derived(
		chosen && device?.checks.some((c) => c.name === chosen)
			? chosen
			: (device?.checks.find((c) => c.type === 'ping')?.name ?? device?.checks[0]?.name ?? null)
	);

	async function copyHost() {
		if (!device) return;
		await navigator.clipboard.writeText(device.host);
		copied = true;
		setTimeout(() => (copied = false), 1500);
	}
	const kindLabel: Record<string, string> = {
		router: 'Router',
		'access-point': 'Access point',
		switch: 'Switch',
		server: 'Server',
		pc: 'PC',
		laptop: 'Laptop',
		printer: 'Printer',
		camera: 'Camera',
		ups: 'UPS',
		other: 'Device'
	};
</script>

<svelte:head><title>{device?.name ?? 'Device'} · Kastellan</title></svelte:head>

<a href="/devices" class="inline-flex items-center gap-1.5 text-sm text-muted hover:text-text">
	<ArrowLeft class="size-4" /> Devices
</a>

{#if store.devices && !device}
	<div class="card mt-6 px-6 py-10 text-center text-sm text-muted">
		There is no device <code class="font-mono">{id}</code> in config.yaml.
	</div>
{:else if device}
	<header class="mt-4 flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
		<div class="flex min-w-0 flex-1 items-start gap-4">
			<div class="card grid size-14 shrink-0 place-items-center rounded-2xl text-muted">
				<KindIcon kind={device.kind} class="size-6" />
			</div>
			<div class="min-w-0">
				<div class="flex flex-wrap items-center gap-x-3 gap-y-1.5">
					<h1 class="text-2xl font-semibold tracking-tight">{device.name}</h1>
					<StatusBadge {status} />
					{#if store.alarms?.maintenance?.[device.id]}
						{@const m = store.alarms.maintenance[device.id]}
						<span
							class="inline-flex items-center gap-1.5 rounded-full bg-surface-2 px-2.5 py-0.5 text-xs font-medium text-muted"
							title="Problems show but are sent only if still there when it ends"
						>
							<Wrench class="size-3.5" />
							{m.reason} · until {dayClock(m.until)}
						</span>
					{/if}
				</div>
				<div class="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted">
					{#if device.host}
						<button
							onclick={copyHost}
							class="inline-flex items-center gap-1.5 font-mono text-[13px] hover:text-text"
							title="Copy the address"
						>
							{device.host}
							{#if copied}<Check class="size-3.5" style="color: var(--good-text)" />{:else}<Copy
									class="size-3.5 text-faint"
								/>{/if}
						</button>
						<span class="text-faint">·</span>
					{/if}
					<span>{kindLabel[device.kind] ?? device.kind}</span>
					{#if parent}
						<span class="text-faint">·</span>
						<span>
							via <a href="/devices/{parent.id}" class="text-text hover:underline">{parent.name}</a>
						</span>
					{/if}
					{#if device.powered_by}
						{@const u = store.byId.get(device.powered_by.ups)}
						<span class="text-faint">·</span>
						<span>
							on <a class="text-text hover:underline" href="/devices/{device.powered_by.ups}">
								{u?.name ?? device.powered_by.ups}
							</a>
							<span style:color={statusColor[upsStatus[device.powered_by.state] ?? 'waiting'].text}>
								· {upsLabel[device.powered_by.state] ?? device.powered_by.state}
							</span>
						</span>
					{/if}
					{#each device.reads_ups ?? [] as r (r.device)}
						<span class="text-faint">·</span>
						<span>
							UPS <a class="text-text hover:underline" href="/devices/{r.device}">
								{store.byId.get(r.device)?.name ?? r.device}
							</a>
							<span style:color={statusColor[upsStatus[r.state] ?? 'waiting'].text}>
								· {upsLabel[r.state] ?? r.state}
							</span>
						</span>
					{/each}
					{#if device.runs_on}
						{@const host = store.byId.get(device.runs_on.host)}
						<span class="text-faint">·</span>
						<span>
							virtual machine on <a
								class="text-text hover:underline"
								href="/devices/{device.runs_on.host}"
							>
								{host?.name ?? device.runs_on.host}
							</a>
							<span style:color={vmTone(device.runs_on.state ?? '')}>
								· {device.runs_on.state || 'not reported'}
							</span>
						</span>
					{/if}
					{#if device.info?.model}
						<span class="text-faint">·</span>
						<span>{device.info.model}</span>
					{/if}
					{#if device.info?.firmware}
						<span class="text-faint">·</span>
						<span title="Reported by the device">{device.info.firmware}</span>
					{/if}
					{#each device.tags as tag (tag)}
						<span class="rounded-md bg-surface-2 px-1.5 py-0.5 text-xs">{tag}</span>
					{/each}
				</div>
			</div>
		</div>
		<!-- The actions wrap within at most about half the width: the name keeps the rest. -->
		<div class="flex flex-wrap gap-2 self-start lg:max-w-[55%] lg:justify-end">
			<!-- Getting to it, acting on it, then its alarms. -->
			{#if device.ssh_port && store.me?.role === 'admin' && store.me.user}
				<a
					href="/devices/{device.id}/terminal"
					class="inline-flex h-9 items-center gap-2 rounded-xl border border-line bg-surface px-3.5 text-sm font-medium shadow-card transition-colors hover:border-line-strong"
				>
					<SquareTerminal class="size-4 text-muted" /> Terminal
				</a>
			{/if}
			{#if device.desktop && store.me?.role === 'admin' && store.me.user}
				<a
					href="/devices/{device.id}/desktop"
					class="inline-flex h-9 items-center gap-2 rounded-xl border border-line bg-surface px-3.5 text-sm font-medium shadow-card transition-colors hover:border-line-strong"
				>
					<MonitorPlay class="size-4 text-muted" /> Desktop
				</a>
			{/if}
			{#if webUi}
				<a
					href={webUi}
					target="_blank"
					rel="noopener noreferrer"
					class="inline-flex h-9 shrink-0 items-center gap-2 self-start rounded-xl border border-line bg-surface px-3.5 text-sm font-medium shadow-card transition-colors hover:border-line-strong"
				>
					Open web interface <ExternalLink class="size-3.5 text-muted" />
				</a>
			{/if}
			{#if webUi && store.me?.role === 'admin' && store.me.user}
				{#key device.id}<WebLogin device={device.id} proxied={!!store.health?.web_domain} />{/key}
			{/if}
			{#if device.fritzbox && device.fritz && store.me?.role === 'admin' && store.me.user}
				<RouterReboot device={device.id} kind="fritzbox" />
				{#if device.fritz.update?.available}
					<button
						class="inline-flex h-9 items-center gap-2 rounded-xl border border-line bg-surface px-3.5 text-sm font-medium shadow-card transition-colors hover:border-line-strong"
						title="Install {device.fritz.update.version ?? 'the update FRITZ!OS offers'}"
						onclick={() => (fritzUpdate = 'asking')}
					>
						<CircleFadingArrowUp class="size-4 text-muted" />
						{fritzUpdate === 'asking'
							? 'Install it?'
							: fritzUpdate === 'started'
								? 'Updating…'
								: 'Update firmware'}
					</button>
					{#if fritzUpdate === 'asking'}
						<span class="inline-flex h-9 items-center gap-2 text-sm">
							<button class="underline" style:color="var(--critical-text)" onclick={updateFritz}>
								Yes, it restarts
							</button>
							<button class="underline" onclick={() => (fritzUpdate = null)}>No</button>
						</span>
					{/if}
				{/if}
				{#if fritzNote}<span class="self-center text-xs" style:color="var(--critical-text)">
						{fritzNote}
					</span>{/if}
			{/if}
			{#if device.act && store.me?.role === 'admin' && store.me.user}
				<HostReboot device={device.id} wanted={!!device.host_report?.updates?.reboot} />
			{/if}
			{#if device.openwrt && device.router && store.me?.role === 'admin' && store.me.user}
				<RouterReboot device={device.id} />
				{#if device.router.firmware?.next_step}
					<button
						class="inline-flex h-9 items-center gap-2 rounded-xl border border-line bg-surface px-3.5 text-sm font-medium shadow-card transition-colors hover:border-line-strong"
						title="To OpenWrt {device.router.firmware.next_step}"
						onclick={() => (firmwareOpen = true)}
					>
						<CircleFadingArrowUp class="size-4 text-muted" /> Update firmware
					</button>
				{/if}
				{#if firmwareOpen && device.router.firmware}
					<FirmwareDialog
						device={device.id}
						advice={device.router.firmware}
						model={device.router.board.model}
						onclose={() => (firmwareOpen = false)}
					/>
				{/if}
			{/if}
			{#if store.me?.role === 'admin'}
				{@const mutedUntil = store.alarms?.muted[device.id]}
				<button
					onclick={async () => {
						await api.mute(device.id, mutedUntil ? 0 : 60);
						store.refreshHistory();
					}}
					class="inline-flex h-9 items-center gap-2 rounded-xl border border-line bg-surface px-3.5 text-sm font-medium shadow-card transition-colors hover:border-line-strong"
					title={mutedUntil ? `No alarms until ${dayClock(mutedUntil)}` : 'No alarms for an hour'}
				>
					{#if mutedUntil}<BellOff class="size-4 text-muted" /> Muted{:else}<Bell
							class="size-4 text-muted"
						/> Mute 1 h{/if}
				</button>
				<MaintenanceButton kind="devices" id={device.id} devices={[device.id]} />
			{/if}
		</div>
	</header>

	{#if admin}
		<div class="mt-6">
			<Tabs
				tabs={[
					{ id: 'status', label: 'Status' },
					...(device.host ? [{ id: 'setup' as const, label: 'Setup' }] : []),
					{ id: 'config', label: 'Config' }
				]}
				current={tab}
				onselect={(t) => show(t)}
			/>
		</div>
	{/if}

	{#if tab === 'config'}
		{#if store.editBlocked !== null}
			<div class="card mt-6 p-5 text-sm text-muted">
				Its config can't be changed from here: {store.editBlocked}.
			</div>
		{:else}
			<div class="mt-6 flex flex-wrap items-center gap-3">
				<Tabs
					tabs={[
						{ id: 'form', label: 'Form' },
						{ id: 'yaml', label: 'YAML', mono: true }
					]}
					current={view}
					onselect={(v) => show('config', v)}
				/>
				<p class="text-xs text-muted">
					{view === 'form'
						? 'Name, address, kind, tags and services; the rest stays as it is in config.yaml.'
						: 'Its part of config.yaml, comments and all.'}
				</p>
			</div>
			<!-- Keyed: another device's page must never keep (and save) this one's form or text. -->
			{#key device.id}
				{#if view === 'form'}
					<div class="mt-4"><DeviceEditor id={device.id} onclose={() => show('status')} /></div>
				{:else}
					<div class="mt-4"><ConfigFile device={device.id} /></div>
				{/if}
			{/key}
		{/if}
	{:else if tab === 'setup'}
		{#if store.me}
			<p class="mt-6 mb-4 max-w-2xl text-sm text-muted">
				How Kastellan reaches this device and what it may do there. Each part is set up once, with
				your login (used for that one connection) or with commands to run yourself.
			</p>
			{#if device.outdated_scripts.length}
				<p class="mb-3 text-sm" style:color="var(--warning-text)">
					Kastellan's {device.outdated_scripts.map(scriptName).join(' and ')} here {device
						.outdated_scripts.length === 1
						? 'is'
						: 'are'} outdated. Renew the host or OpenWrt setup below.
					{#if device.outdated_scripts.some((n) => n.startsWith('vpn-'))}
						Renew VPN helpers in <a class="underline" href="/vpn?tab=setup">VPN → Setup</a>.
					{/if}
				</p>
			{/if}
			{@const hostLike = !device.openwrt && HOSTS.includes(device.kind)}
			{@const fritzish = !!device.fritzbox || device.driver === 'fritzbox'}
			{@const parts = [
				{
					id: 'openwrt',
					shown:
						!!device.openwrt ||
						(!fritzish && ['router', 'access-point', 'switch'].includes(device.kind)),
					set: !!device.openwrt
				},
				{
					id: 'fritzbox',
					shown: fritzish || (!device.openwrt && ['router', 'access-point'].includes(device.kind)),
					set: !!device.fritzbox
				},
				{ id: 'wifi', shown: !!device.openwrt && !!device.router?.radios?.length, set: true },
				{ id: 'collector', shown: !!device.collect || hostLike, set: !!device.collect },
				{ id: 'actor', shown: !!device.act || hostLike, set: !!device.act },
				{ id: 'access', shown: true, set: !!device.ssh },
				{ id: 'syslog', shown: !device.openwrt && !fritzish, set: !!device.syslog_cert },
				{ id: 'keys', shown: !!device.ssh, set: true }
			].filter((p) => p.shown)}
			<!-- Keyed: going from one device's page to another's starts these afresh. -->
			{#key device.id}
				{#each [{ title: 'Set up', set: true }, { title: 'Can be set up', set: false }] as group (group.title)}
					{@const these = parts.filter((p) => p.set === group.set)}
					{#if these.length}
						<h2 class="mt-6 mb-3 text-sm font-semibold">{group.title}</h2>
						<div class="grid auto-rows-fr gap-4 sm:grid-cols-2 xl:grid-cols-3">
							{#each these as p (p.id)}
								{#if p.id === 'openwrt'}
									<OpenWrtSetup
										device={device.id}
										canInstall={!!store.me.user}
										services={(device.router?.services ?? []).map((s) => s.name)}
										allowedServices={device.router?.services_allowed ?? []}
									/>
								{:else if p.id === 'fritzbox'}
									<FritzSetup device={device.id} canSetUp={!!store.me.user} />
								{:else if p.id === 'wifi'}
									{@const names = (device.router?.radios ?? []).flatMap((r) =>
										r.interfaces.map((i) => i.ssid ?? i.section)
									)}
									<SetupCard
										title="Wi-Fi"
										about="Network names, passwords, radios on or off, channels. Applied with a rollback: undone if the device can't be reached afterwards."
										stage="set"
										note={[...new Set(names)].join(', ')}
										button="Change"
										onclick={() => (wifiOpen = true)}
									/>
								{:else if p.id === 'syslog'}
									<SyslogSetup device={device.id} canSetUp={!!store.me.user} />
								{:else if p.id === 'collector'}
									<CollectorSetup device={device.id} canInstall={!!store.me.user} />
								{:else if p.id === 'actor'}
									<ActorSetup
										device={device.id}
										containers={(device.containers ?? []).map((c) => c.name)}
										canInstall={!!store.me.user}
									/>
								{:else if p.id === 'access'}
									<AccessSetup device={device.id} canSetUp={!!store.me.user} />
								{:else}
									<DeviceKeys device={device.id} canChange={!!store.me.user} />
								{/if}
							{/each}
						</div>
					{/if}
				{/each}
			{/key}
			{#if wifiOpen}
				<WifiDialog device={device.id} onclose={() => (wifiOpen = false)} />
			{/if}
		{/if}
	{:else}
		{#if device.kind === 'ups'}
			<UpsCard {device} canChange={store.editBlocked === null && store.me?.role === 'admin'} />
		{/if}
		<!-- Range (a UPS read through its host has no services of its own) -->
		{#if device.checks.length}<div class="mt-8 flex items-center justify-between">
				<h2 class="text-sm font-semibold">Services</h2>
				<div class="flex gap-1 rounded-xl border border-line bg-surface p-1 shadow-card">
					{#each ranges as r (r.label)}
						<button
							onclick={() => (range = r)}
							class="h-7 rounded-lg px-3 text-xs font-medium transition-colors {range === r
								? 'bg-surface-3 text-text'
								: 'text-muted hover:text-text'}"
						>
							{r.label}
						</button>
					{/each}
				</div>
			</div>

			{#if loadError}
				<p class="mt-3 text-sm" style:color="var(--critical-text)">
					Cannot load the history: {loadError}. Showing what was loaded before.
				</p>
			{/if}

			<div class="mt-3">
				<ServicesTable
					rows={device.checks.map((check) => ({ device: device!, check }))}
					buckets={(_, name) => checkSeries[name] ?? []}
					start={series?.start ?? null}
					step={series?.step_seconds ?? 0}
					rangeLabel={range.label}
				/>
			</div>{/if}

		{#if device.router}
			<RouterCard
				device={device.id}
				report={device.router}
				checked={device.router_checked ?? null}
				error={device.router_error ?? null}
				canChange={store.me?.role === 'admin' && !!store.me.user}
			/>
		{:else if device.openwrt && device.router_error}
			<p class="card mt-8 p-4 text-sm" style:color="var(--warning-text)">
				Kastellan can't read this OpenWrt device: {device.router_error}
			</p>
		{/if}
		{#if device.fritz}
			<FritzCard
				device={device.id}
				report={device.fritz}
				checked={device.fritz_checked ?? null}
				error={device.fritz_error ?? null}
				canChange={store.me?.role === 'admin' && !!store.me.user}
			/>
		{:else if device.fritzbox && device.fritz_error}
			<p class="card mt-8 p-4 text-sm" style:color="var(--warning-text)">
				Kastellan can't read this FRITZ!Box: {device.fritz_error}
			</p>
		{/if}

		<BackupsCard device={device.id} />
		{#if admin}<div id="config-backups"><ConfigBackups device={device.id} /></div>{/if}
		{#if device.host_report}
			<HostHealth
				report={device.host_report}
				checked={device.host_checked ?? null}
				error={device.host_error ?? null}
				forecast={device.host_forecast ?? {}}
				device={device.id}
				canAct={!!device.act && store.me?.role === 'admin' && !!store.me.user}
			/>
		{:else if device.collect && device.host_error}
			<p class="card mt-8 p-4 text-sm" style:color="var(--warning-text)">
				The collector doesn't answer: {device.host_error}
			</p>
		{/if}

		{#if device.containers || device.containers_error}
			<div class="mt-8 mb-3 flex flex-wrap items-baseline justify-between gap-2">
				<h2 class="text-sm font-semibold">
					Containers
					{#if device.containers}<span class="tabular ml-1 font-normal text-faint">
							{device.containers.length}
						</span>{/if}
				</h2>
				<p class="text-xs text-muted">
					{#if device.containers_error}
						<span style:color="var(--warning-text)">{device.containers_error}</span>
						{#if device.containers_checked}
							· last read {ago(device.containers_checked, store.now)}
						{/if}
					{:else}
						from {device.host_report?.docker ? 'the collector' : 'WUD'}, {ago(
							device.containers_checked,
							store.now
						)}
					{/if}
					{#if device.collect && device.host_report?.docker}· <ReadAgain device={device.id} />{/if}
				</p>
			</div>
			{#if device.containers}<ContainersTable
					device={device.id}
					containers={device.containers}
					stale={!!device.containers_error}
					canAct={!!device.act && store.me?.role === 'admin' && !!store.me.user}
				/>{/if}
		{/if}

		{#if device.runs_on}
			{@const host = store.byId.get(device.runs_on.host)}
			{@const vm = host?.vms?.find((v) => v.name === device.runs_on?.vm)}
			{#if host && vm}
				<h2 class="mt-8 mb-3 text-sm font-semibold">
					Its virtual machine <span class="font-normal text-faint">on {host.name}</span>
				</h2>
				<VmsTable
					device={host.id}
					vms={[vm]}
					canAct={!!host.act && store.me?.role === 'admin' && !!store.me.user}
				/>
			{/if}
		{/if}

		{#if device.vms?.length}
			<h2 class="mt-8 mb-3 text-sm font-semibold">
				Virtual machines <span class="tabular ml-1 font-normal text-faint">
					{device.vms.length}
				</span>
			</h2>
			<VmsTable
				device={device.id}
				vms={device.vms}
				canAct={!!device.act && store.me?.role === 'admin' && !!store.me.user}
			/>
		{/if}

		{#if device.vulns}
			<h2 class="mt-8 mb-3 text-sm font-semibold">Vulnerabilities</h2>
			<VulnsList vulns={device.vulns} exposed={device.exposed} />
		{/if}

		<!-- Nothing checked (a UPS read through its host): no response time, only its activity. -->
		<div
			class="mt-8 grid min-w-0 grid-cols-1 gap-8 {device.checks.length
				? 'xl:grid-cols-[minmax(0,1fr)_20rem]'
				: ''}"
		>
			{#if device.checks.length}<section class="min-w-0">
					<div class="mb-3 flex flex-wrap items-center justify-between gap-2">
						<h2 class="text-sm font-semibold">Response time</h2>
						{#if device.checks.length > 1}
							<div class="flex flex-wrap gap-1">
								{#each device.checks as check (check.name)}
									<button
										onclick={() => (chosen = check.name)}
										class="h-7 rounded-lg border px-2.5 text-xs font-medium transition-colors {latencyCheck ===
										check.name
											? 'border-accent bg-accent-soft text-accent'
											: 'border-line text-muted hover:text-text'}"
									>
										{check.name}
									</button>
								{/each}
							</div>
						{/if}
					</div>
					<div class="card px-2 pt-4 pb-1">
						{#if series && latencyCheck && checkSeries[latencyCheck]}
							<LatencyChart
								buckets={checkSeries[latencyCheck]}
								start={series.start}
								step={series.step_seconds}
								height={220}
							/>
						{:else}
							<div class="h-[220px] animate-pulse rounded-lg bg-surface-2"></div>
						{/if}
					</div>
				</section>{/if}

			<section>
				<h2 class="mb-3 text-sm font-semibold">Activity</h2>
				<div class="card p-2">
					{#if changes.length === 0}
						<p class="px-3 py-6 text-center text-sm text-muted">
							No changes in the last {range.label}.
						</p>
					{:else}
						<ol class="max-h-[22rem] overflow-y-auto">
							{#each changes as change (`${change.kind}/${change.time}/${change.check}`)}
								<li><ActivityItem {change} {device} /></li>
							{/each}
						</ol>
					{/if}
				</div>

				{#if children.length}
					<h2 class="mt-8 mb-3 text-sm font-semibold">Connected through it</h2>
					<div class="card divide-y divide-line">
						{#each children as child (child.id)}
							<a
								href="/devices/{child.id}"
								class="flex items-center gap-3 px-4 py-2.5 text-sm transition-colors hover:bg-surface-2/70"
							>
								<KindIcon kind={child.kind} class="size-4 text-muted" />
								<span class="flex-1 truncate">{child.name}</span>
								<StatusBadge status={statusOf(child)} />
							</a>
						{/each}
					</div>
				{/if}
			</section>
		</div>
	{/if}
{/if}
