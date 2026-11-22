<script lang="ts">
	import Tabs from '#lib/components/Tabs.svelte';
	import { store } from '#lib/state.svelte.js';
	import { checkStatusOf } from '#lib/status.js';
	import ServicesTable from '#lib/components/ServicesTable.svelte';
	import Search from '@lucide/svelte/icons/search';

	type Filter = 'all' | 'problems' | 'versioned';
	let query = $state('');
	let filter = $state<Filter>('all');

	// Every port and web check on every device; ping only tells whether the device is there.
	const all = $derived(
		(store.devices ?? []).flatMap((device) =>
			device.checks.filter((c) => c.type !== 'ping').map((check) => ({ device, check }))
		)
	);
	const problem = (r: (typeof all)[number]) => checkStatusOf(r.device, r.check) === 'down';
	const rows = $derived(
		all
			.filter((r) => {
				const q = query.trim().toLowerCase();
				const text = `${r.device.name} ${r.device.host} ${r.check.name} ${r.check.target} ${r.check.software ?? ''}`;
				const kept =
					filter === 'all' ||
					(filter === 'problems' && problem(r)) ||
					(filter === 'versioned' && !!r.check.software);
				return kept && (!q || text.toLowerCase().includes(q));
			})
			.sort(
				(a, b) =>
					Number(problem(b)) - Number(problem(a)) ||
					a.device.name.localeCompare(b.device.name) ||
					a.check.name.localeCompare(b.check.name)
			)
	);
	const counts = $derived({
		all: all.length,
		problems: all.filter(problem).length,
		versioned: all.filter((r) => r.check.software).length
	});
	const filters: { id: Filter; label: string }[] = [
		{ id: 'all', label: 'All' },
		{ id: 'problems', label: 'Problems' },
		{ id: 'versioned', label: 'With version' }
	];
</script>

<svelte:head><title>Services · Kastellan</title></svelte:head>

<h1 class="text-2xl font-semibold tracking-tight">Services</h1>
<p class="mt-1 text-sm text-muted">
	What runs where, which software it says it is, and how long it has been up.
</p>

<div class="mt-6 flex flex-wrap items-center gap-3">
	<label class="relative w-full sm:w-auto sm:max-w-sm sm:flex-1">
		<Search
			class="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-faint"
		/>
		<input
			bind:value={query}
			type="search"
			placeholder="Search device, service, software"
			class="h-10 w-full rounded-xl border border-line bg-surface pr-3 pl-9 text-sm shadow-card outline-none placeholder:text-faint focus:border-accent"
		/>
	</label>
	<Tabs
		label="Show"
		tabs={filters.map((f) => ({ ...f, count: counts[f.id] }))}
		current={filter}
		onselect={(f) => (filter = f)}
	/>
</div>

<div class="mt-4">
	{#if store.devices === null}
		<div class="card h-64 animate-pulse"></div>
	{:else if rows.length === 0}
		<div class="card px-5 py-10 text-center text-sm text-muted">
			{all.length
				? 'No service matches.'
				: 'No services yet: add a port or a web page on a device’s page.'}
		</div>
	{:else}
		<ServicesTable
			{rows}
			buckets={(device, check) => store.series?.devices[device]?.[check] ?? []}
			start={store.series?.start ?? null}
			step={store.series?.step_seconds ?? 0}
			showDevice
		/>
	{/if}
</div>
