<script lang="ts">
	import { page } from '$app/state';
	import { api, type Change, type Series } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { statusOf, uptimeOf } from '#lib/status.js';
	import { ago, dayClock, ms, percent, stateFor } from '#lib/format.js';
	import KindIcon from '#lib/components/KindIcon.svelte';
	import StatusBadge from '#lib/components/StatusBadge.svelte';
	import UptimeStrip from '#lib/components/UptimeStrip.svelte';
	import LatencyChart from '#lib/components/LatencyChart.svelte';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import SquareTerminal from '@lucide/svelte/icons/square-terminal';
	import MonitorPlay from '@lucide/svelte/icons/monitor-play';
	import Bell from '@lucide/svelte/icons/bell';
	import BellOff from '@lucide/svelte/icons/bell-off';
	import Copy from '@lucide/svelte/icons/copy';
	import Check from '@lucide/svelte/icons/check';
	import ActivityItem from '#lib/components/ActivityItem.svelte';
	import ServicesTable from '#lib/components/ServicesTable.svelte';
	import ContainersTable from '#lib/components/ContainersTable.svelte';
	import VulnsList from '#lib/components/VulnsList.svelte';
	import AccessSetup from '#lib/components/AccessSetup.svelte';

	const id = $derived(page.params.id ?? '');
	const device = $derived(store.byId.get(id));
	const status = $derived(device ? statusOf(device) : 'waiting');
	const parent = $derived(device?.parent ? store.byId.get(device.parent) : undefined);
	const children = $derived((store.devices ?? []).filter((d) => d.parent === id));
	const webUi = $derived(
		device?.checks.find((c) => c.type === 'http')?.target as string | undefined
	);

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
	<header class="mt-4 flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
		<div class="flex items-start gap-4">
			<div class="card grid size-14 shrink-0 place-items-center rounded-2xl text-muted">
				<KindIcon kind={device.kind} class="size-6" />
			</div>
			<div class="min-w-0">
				<div class="flex flex-wrap items-center gap-x-3 gap-y-1.5">
					<h1 class="text-2xl font-semibold tracking-tight">{device.name}</h1>
					<StatusBadge {status} />
				</div>
				<div class="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted">
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
					<span>{kindLabel[device.kind] ?? device.kind}</span>
					{#if parent}
						<span class="text-faint">·</span>
						<span>
							via <a href="/devices/{parent.id}" class="text-text hover:underline">{parent.name}</a>
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
		<div class="flex shrink-0 flex-wrap gap-2 self-start">
			{#if device.ssh && store.me?.role === 'admin' && store.me.user}
				<a
					href="/devices/{device.id}/terminal"
					class="inline-flex h-9 items-center gap-2 rounded-xl border border-line bg-surface px-3.5 text-sm font-medium shadow-card transition-colors hover:border-line-strong"
				>
					<SquareTerminal class="size-4 text-muted" /> Terminal
				</a>
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
		</div>
	</header>

	<!-- Range -->
	<div class="mt-8 flex items-center justify-between">
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
	</div>

	{#if store.me?.role === 'admin'}
		<AccessSetup device={device.id} canSetUp={!!store.me.user} />
	{/if}

	{#if device.vulns}
		<h2 class="mt-8 mb-3 text-sm font-semibold">Vulnerabilities</h2>
		<VulnsList vulns={device.vulns} exposed={device.exposed} />
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
					from WUD, {ago(device.containers_checked, store.now)}
				{/if}
			</p>
		</div>
		{#if device.containers}<ContainersTable
				containers={device.containers}
				stale={!!device.containers_error}
			/>{/if}
	{/if}

	<div class="mt-8 grid gap-8 xl:grid-cols-[1fr_20rem]">
		<section>
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
		</section>

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
