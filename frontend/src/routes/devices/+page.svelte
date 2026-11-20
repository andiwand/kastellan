<script lang="ts">
	import { store } from '#lib/state.svelte.js';
	import { deviceBuckets, statusOf, uptimeOf, type Status } from '#lib/status.js';
	import { ms, percent } from '#lib/format.js';
	import { tree } from '#lib/tree.js';
	import KindIcon from '#lib/components/KindIcon.svelte';
	import StatusBadge from '#lib/components/StatusBadge.svelte';
	import UptimeStrip from '#lib/components/UptimeStrip.svelte';
	import Search from '@lucide/svelte/icons/search';

	type Filter = 'all' | 'problems' | 'online' | 'offline';
	let query = $state('');
	let filter = $state<Filter>('all');
	let input: HTMLInputElement | undefined = $state();

	const devices = $derived(store.devices ?? []);
	const matches = (status: Status, f: Filter) =>
		f === 'all' ||
		(f === 'problems' && (status === 'down' || status === 'degraded')) ||
		(f === 'online' && (status === 'up' || status === 'degraded')) ||
		(f === 'offline' && (status === 'offline' || status === 'down'));
	const counts = $derived(
		Object.fromEntries(
			(['all', 'problems', 'online', 'offline'] as Filter[]).map((f) => [
				f,
				devices.filter((d) => matches(statusOf(d), f)).length
			])
		) as Record<Filter, number>
	);
	const rows = $derived(
		tree(devices).filter(({ device: d }) => {
			const q = query.trim().toLowerCase();
			const text = `${d.name} ${d.host} ${d.kind} ${d.tags.join(' ')} ${d.checks.map((c) => c.name).join(' ')}`;
			return matches(statusOf(d), filter) && (!q || text.toLowerCase().includes(q));
		})
	);
	// Indent only when the whole tree shows; a filtered list is flat.
	const flat = $derived(filter !== 'all' || query.trim() !== '');

	function key(e: KeyboardEvent) {
		if (e.key === '/' && document.activeElement?.tagName !== 'INPUT') {
			e.preventDefault();
			input?.focus();
		}
	}
	const filters: { id: Filter; label: string }[] = [
		{ id: 'all', label: 'All' },
		{ id: 'problems', label: 'Problems' },
		{ id: 'online', label: 'Online' },
		{ id: 'offline', label: 'Offline' }
	];
</script>

<svelte:head><title>Devices · Kastellan</title></svelte:head>
<svelte:window onkeydown={key} />

<div class="flex flex-wrap items-end justify-between gap-4">
	<div>
		<h1 class="text-2xl font-semibold tracking-tight">Devices</h1>
		<p class="mt-1 text-sm text-muted">
			Everything Kastellan watches, in the order of the network.
		</p>
	</div>
</div>

<div class="mt-6 flex flex-col gap-3 sm:flex-row sm:items-center">
	<label class="relative flex-1 sm:max-w-sm">
		<Search
			class="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-faint"
		/>
		<input
			bind:this={input}
			bind:value={query}
			type="search"
			placeholder="Search name, host, tag, check"
			class="h-10 w-full rounded-xl border border-line bg-surface pr-10 pl-9 text-sm shadow-card outline-none placeholder:text-faint focus:border-accent"
		/>
		<kbd
			class="pointer-events-none absolute top-1/2 right-3 hidden -translate-y-1/2 rounded border border-line px-1.5 font-mono text-[11px] text-faint sm:block"
		>
			/
		</kbd>
	</label>
	<div class="flex gap-1 rounded-xl border border-line bg-surface p-1 shadow-card" role="tablist">
		{#each filters as f (f.id)}
			<button
				role="tab"
				aria-selected={filter === f.id}
				onclick={() => (filter = f.id)}
				class="flex h-8 items-center gap-1.5 rounded-lg px-3 text-sm font-medium transition-colors {filter ===
				f.id
					? 'bg-surface-3 text-text'
					: 'text-muted hover:text-text'}"
			>
				{f.label}
				<span class="tabular text-xs text-faint">{counts[f.id]}</span>
			</button>
		{/each}
	</div>
</div>

<div class="card mt-4 overflow-hidden">
	<div
		class="hidden grid-cols-[minmax(0,1.6fr)_7rem_minmax(0,1fr)_minmax(0,1.3fr)_4.5rem] gap-4 border-b border-line px-5 py-2.5 text-xs font-medium text-muted lg:grid"
	>
		<span>Device</span><span>Status</span><span>Checks</span><span>Last 24 hours</span><span
			class="text-right"
		>
			Ping
		</span>
	</div>
	{#if rows.length === 0}
		<p class="px-5 py-10 text-center text-sm text-muted">
			{store.devices === null ? 'Loading…' : 'No device matches.'}
		</p>
	{/if}
	<ul class="divide-y divide-line">
		{#each rows as { device, depth } (device.id)}
			{@const status = statusOf(device)}
			{@const buckets = deviceBuckets(store.series?.devices[device.id])}
			{@const ping = device.checks.find((c) => c.type === 'ping')}
			<li>
				<a
					href="/devices/{device.id}"
					class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-3 px-5 py-3.5 transition-colors hover:bg-surface-2/70 lg:grid-cols-[minmax(0,1.6fr)_7rem_minmax(0,1fr)_minmax(0,1.3fr)_4.5rem]"
				>
					<div
						class="flex min-w-0 items-center gap-3"
						style:padding-left="{flat ? 0 : depth * 1.25}rem"
					>
						{#if depth && !flat}<span class="-ml-3 h-px w-2 shrink-0 bg-line-strong"></span>{/if}
						<div class="grid size-8 shrink-0 place-items-center rounded-lg bg-surface-2 text-muted">
							<KindIcon kind={device.kind} />
						</div>
						<div class="min-w-0">
							<p class="truncate text-sm font-medium">{device.name}</p>
							<p class="truncate font-mono text-xs text-faint">{device.host}</p>
						</div>
					</div>
					<div><StatusBadge {status} /></div>
					<div class="col-span-2 flex flex-wrap gap-1 lg:col-span-1">
						{#each device.checks as check (check.name)}
							<span
								class="inline-flex items-center gap-1 rounded-md border border-line px-1.5 py-0.5 text-[11px] text-muted"
								title={check.detail || ''}
							>
								<span
									class="size-1.5 rounded-full"
									style:background={check.up == null
										? 'var(--offline)'
										: check.up
											? 'var(--good)'
											: device.always_on
												? 'var(--critical)'
												: 'var(--offline)'}
								></span>
								{check.name}
							</span>
						{/each}
					</div>
					<div class="col-span-2 flex items-center gap-3 lg:col-span-1">
						<div class="flex-1">
							{#if store.series && buckets.length}
								<UptimeStrip
									{buckets}
									start={store.series.start}
									step={store.series.step_seconds}
									alwaysOn={device.always_on}
									height="h-4"
								/>
							{/if}
						</div>
						<span class="tabular w-14 shrink-0 text-right text-xs text-muted">
							{percent(uptimeOf(buckets))}
						</span>
					</div>
					<span class="tabular hidden text-right text-sm lg:block">
						{ping?.up ? ms(ping.latency_ms) : '–'}
					</span>
				</a>
			</li>
		{/each}
	</ul>
</div>
