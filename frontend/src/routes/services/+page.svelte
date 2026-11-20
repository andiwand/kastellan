<script lang="ts">
	import { store } from '#lib/state.svelte.js';
	import { api, type Vpn } from '#lib/api.js';
	import { onMount } from 'svelte';
	import KeyRound from '@lucide/svelte/icons/key-round';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import ServicesTable from '#lib/components/ServicesTable.svelte';
	import Search from '@lucide/svelte/icons/search';

	// The VPNs: their clients are managed here in Kastellan (the VPN page).
	let vpn = $state<Vpn | null>(null);
	onMount(async () => {
		try {
			vpn = await api.vpn();
		} catch {
			vpn = null; // not shown then; the VPN page says why
		}
	});
	const vpns = $derived(
		vpn
			? [
					vpn.wireguard && {
						name: 'WireGuard',
						via: 'wg-easy',
						clients: vpn.wireguard.clients.length,
						error: vpn.wireguard.error
					},
					vpn.openvpn && {
						name: 'OpenVPN',
						via: 'easy-rsa',
						clients: vpn.openvpn.clients.length,
						error: vpn.openvpn.error
					}
				].filter((v) => !!v)
			: []
	);
	const admin = $derived(store.me?.role === 'admin' && !!store.me.user);

	type Filter = 'all' | 'problems' | 'versioned';
	let query = $state('');
	let filter = $state<Filter>('all');

	// Every port and web check on every device; ping only tells whether the device is there.
	const all = $derived(
		(store.devices ?? []).flatMap((device) =>
			device.checks.filter((c) => c.type !== 'ping').map((check) => ({ device, check }))
		)
	);
	const problem = (r: (typeof all)[number]) => r.check.up === false && r.device.always_on;
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

{#if vpns.length || (vpn && admin)}
	<div class="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
		{#each vpns as v (v.name)}
			<a
				href="/vpn"
				class="card flex items-center gap-3 px-4 py-3 transition-colors hover:border-line-strong"
			>
				<KeyRound class="size-4 shrink-0 text-muted" />
				<span class="min-w-0 flex-1">
					<span class="block text-sm font-medium">
						{v.name} <span class="text-faint">· VPN</span>
					</span>
					<span class="block truncate text-xs text-muted">
						{#if v.error}<span style:color="var(--warning-text)">{v.error}</span>{:else}{v.clients}
							{v.clients === 1 ? 'client' : 'clients'} · {v.via}{/if}
					</span>
				</span>
				<ChevronRight class="size-4 text-faint" />
			</a>
		{:else}
			<a
				href="/vpn"
				class="card flex items-center gap-3 border-dashed px-4 py-3 text-sm text-muted hover:text-text"
			>
				<KeyRound class="size-4 shrink-0" /> Set up the VPN's clients here
				<ChevronRight class="ml-auto size-4 text-faint" />
			</a>
		{/each}
	</div>
{/if}

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
