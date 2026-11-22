<script lang="ts">
	import type { WifiClient } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { bandLabel } from '#lib/format.js';
	import { count } from '#lib/glossary.js';
	import { clientMatches, signalState, signalText } from '#lib/wifi.js';
	import Table, { type Column } from './ui/Table.svelte';
	import StateDot from './ui/StateDot.svelte';
	import Tabs from './Tabs.svelte';
	import Search from '@lucide/svelte/icons/search';

	/** Every Wi-Fi client, OpenWrt's and the FRITZ!Box's (GET /api/wifi `clients`): where it is,
	 * its signal in words (Good, Fair, Weak) before the measure, its rate and what is off. With a
	 * search, a filter for weak or slow links and one for the access point; grouped by access
	 * point (≈ the room). `device` shows one access point's clients (its device page). Phones
	 * keep the client and its signal. */
	let {
		clients,
		device,
		controls = true,
		filter = $bindable('all'),
		query = $bindable('')
	}: {
		clients: WifiClient[];
		device?: string;
		controls?: boolean; // the search and filters (shown when there are more than a few)
		filter?: 'all' | 'weak';
		query?: string;
	} = $props();

	type Row = WifiClient & { key: string; ap: string };
	let at = $state('');
	const name = (id: string) => store.byId.get(id)?.name ?? id;
	const mine = $derived(
		clients
			.filter((c) => !device || c.device === device)
			.map((c, i) => ({ ...c, key: `${c.device}/${c.mac ?? i}/${i}`, ap: name(c.device) }))
	);
	const aps = $derived([...new Set(mine.map((c) => c.device))].sort());
	const weak = $derived(mine.filter((c) => c.issues.length).length);
	const showControls = $derived(controls && mine.length > 5);
	const grouped = $derived(!device && !at && aps.length > 1);
	// Worst first; grouped by access point when there are several.
	const rows = $derived(
		mine
			.filter(
				(c) =>
					(filter === 'all' || !showControls || c.issues.length) &&
					(!at || c.device === at) &&
					clientMatches(c, query, c.ap)
			)
			.sort(
				(a, b) =>
					(grouped ? a.ap.localeCompare(b.ap) : 0) ||
					b.issues.length - a.issues.length ||
					(a.signal ?? 0) - (b.signal ?? 0)
			)
	);

	const rate = (c: WifiClient) =>
		c.kind === 'fritzbox'
			? c.speed
				? `${c.speed} Mbit/s`
				: null
			: c.tx || c.rx
				? `${c.tx?.mbit ?? '–'} · ${c.rx?.mbit ?? '–'} Mbit/s`
				: null;

	const columns: Column<Row>[] = $derived([
		{ key: 'client', label: 'Client', grow: true, cell: clientCell },
		{
			key: 'ap',
			label: device ? 'Network' : 'Access point',
			priority: 2,
			cell: apCell
		},
		{ key: 'signal', label: 'Signal', cell: signalCell, width: '9.5rem' },
		{
			key: 'rate',
			label: 'Rate',
			priority: 3,
			numeric: true,
			title: 'To the client · from it (OpenWrt); the link speed (FRITZ!Box)',
			value: rate
		},
		{ key: 'issues', label: 'Worth a look', priority: 2, wrap: true, cell: issuesCell }
	]);
</script>

{#snippet clientCell(c: Row)}
	<span class="block truncate font-medium">{c.name ?? c.mac ?? '?'}</span>
	<span class="block truncate text-meta text-muted">
		{#if !device}<span class="sm:hidden">{c.ap} · </span>{/if}<span class="font-mono">
			{c.address ?? (c.name ? c.mac : '') ?? ''}
		</span>
	</span>
{/snippet}

{#snippet apCell(c: Row)}
	{#if !device}<span class="block truncate">{c.ap}</span>{/if}
	<span class="block truncate {device ? '' : 'text-meta text-muted'}">
		{[c.ssid, bandLabel(c.band) || c.radio].filter(Boolean).join(' · ')}
	</span>
{/snippet}

{#snippet signalCell(c: Row)}
	{@const s = signalState(c)}
	{#if s}
		<span class="inline-flex items-center gap-1.5 whitespace-nowrap">
			<StateDot tone={s.tone} label={s.word} />
			{s.word}
			<span class="tabular text-meta text-muted">{signalText(c)}</span>
		</span>
	{:else}<span class="text-faint">–</span>{/if}
{/snippet}

{#snippet issuesCell(c: Row)}
	{#if c.issues.length}<span class="text-meta text-muted">{c.issues.join('; ')}</span>
	{:else}<span class="text-faint">–</span>{/if}
{/snippet}

{#if showControls}
	<div class="mb-3 flex flex-wrap items-center gap-2">
		<label class="relative w-full sm:w-64">
			<Search
				class="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-faint"
			/>
			<input
				bind:value={query}
				type="search"
				placeholder="Find a client"
				aria-label="Find a client"
				class="h-9 w-full rounded-lg border border-line bg-surface pr-3 pl-9 text-sm outline-none placeholder:text-faint focus:border-accent"
			/>
		</label>
		<Tabs
			small
			label="Show"
			current={filter}
			onselect={(id) => (filter = id)}
			tabs={[
				{ id: 'all', label: 'All', count: mine.length },
				{ id: 'weak', label: 'Weak or slow', count: weak }
			]}
		/>
		{#if aps.length > 1}
			<select
				bind:value={at}
				aria-label="Access point"
				class="h-9 rounded-lg border border-line bg-surface px-2 text-sm outline-none focus:border-accent"
			>
				<option value="">Every access point</option>
				{#each aps as id (id)}<option value={id}>{name(id)}</option>{/each}
			</select>
		{/if}
	</div>
{/if}
<Table
	{columns}
	{rows}
	key={(c) => c.key}
	group={grouped ? (c) => c.ap : undefined}
	label="Wi-Fi clients"
	empty={query || filter !== 'all' || at
		? 'No client matches.'
		: count(0, 'client') + ' connected.'}
/>
