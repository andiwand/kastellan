<script lang="ts">
	import GroupsView from '#lib/components/GroupsView.svelte';
	import Tabs from '#lib/components/Tabs.svelte';
	import { store } from '#lib/state.svelte.js';
	import { statusOf, type Status } from '#lib/status.js';
	import { tree } from '#lib/tree.js';
	import DeviceList from '#lib/components/DeviceList.svelte';
	import NetworkMap from '#lib/components/NetworkMap.svelte';
	import Search from '@lucide/svelte/icons/search';
	import List from '@lucide/svelte/icons/list';
	import Workflow from '@lucide/svelte/icons/workflow';
	import Plus from '@lucide/svelte/icons/plus';
	import DeviceEditor from '#lib/components/DeviceEditor.svelte';
	import Discovered from '#lib/components/Discovered.svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';

	type Filter = 'all' | 'problems' | 'online' | 'offline';
	let query = $state('');
	let filter = $state<Filter>('all');
	let input: HTMLInputElement | undefined = $state();
	let adding = $state(false);

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
	const shown = (d: (typeof devices)[number]) => {
		const q = query.trim().toLowerCase();
		const text = `${d.name} ${d.host} ${d.kind} ${d.site_name} ${d.tags.join(' ')} ${d.checks.map((c) => c.name).join(' ')}`;
		return matches(statusOf(d), filter) && (!q || text.toLowerCase().includes(q));
	};
	// By site when devices are at more than one (home first), each with its own tree.
	const sections = $derived.by(() => {
		const sites = [...new Set(devices.map((d) => d.site))];
		if (sites.length < 2)
			return [{ site: '', name: '', rows: tree(devices).filter((r) => shown(r.device)) }];
		const name = (site: string) => devices.find((d) => d.site === site)?.site_name ?? site;
		return sites
			.sort((a, b) => Number(b === 'home') - Number(a === 'home') || name(a).localeCompare(name(b)))
			.map((site) => ({
				site,
				name: name(site),
				rows: tree(devices.filter((d) => d.site === site)).filter((r) => shown(r.device))
			}))
			.filter((s) => s.rows.length);
	});
	// The graph keeps every device and fades the ones a search or filter leaves out.
	const matching = $derived(
		filter === 'all' && !query.trim() ? null : new Set(devices.filter(shown).map((d) => d.id))
	);

	// List or graph, kept in the address so a link or a reload keeps it.
	type View = 'list' | 'graph';
	// Devices, or their groups (an admin's tab; /groups leads here).
	const section = $derived(
		page.url.searchParams.get('section') === 'groups' && store.me?.role === 'admin'
			? 'groups'
			: 'devices'
	);
	const view = $derived<View>(page.url.searchParams.get('view') === 'graph' ? 'graph' : 'list');
	function show(v: View) {
		const url = new URL(page.url.href);
		if (v === 'list') url.searchParams.delete('view');
		else url.searchParams.set('view', v);
		goto(url, { replace: true, reset: false });
	}
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

<svelte:head><title>{section === 'groups' ? 'Groups' : 'Devices'} · Kastellan</title></svelte:head>
<svelte:window onkeydown={key} />

<div class="flex flex-wrap items-end justify-between gap-4">
	<div>
		<h1 class="text-2xl font-semibold tracking-tight">Devices</h1>
		<p class="mt-1 text-sm text-muted">
			Everything Kastellan watches, as a list or by network: what is at home, what is outside, and
			what can be reached from the internet.
		</p>
	</div>
	{#if store.me?.role === 'admin' && !adding && section === 'devices'}
		<button
			onclick={() => (adding = true)}
			disabled={store.editBlocked !== null}
			title={store.editBlocked ?? 'Add a device to config.yaml'}
			class="inline-flex h-9 items-center gap-2 rounded-xl bg-accent px-3.5 text-sm font-medium text-accent-ink shadow-card transition-opacity hover:opacity-90 disabled:opacity-50"
		>
			<Plus class="size-4" /> Add device
		</button>
	{/if}
</div>

{#if store.me?.role === 'admin'}
	<div class="mt-5">
		<Tabs
			tabs={[
				{ id: 'devices', label: 'Devices' },
				{ id: 'groups', label: 'Groups' }
			]}
			current={section}
			onselect={(t) => goto(t === 'groups' ? '?section=groups' : '?', { replace: true })}
		/>
	</div>
{/if}

{#if section === 'groups'}
	<div class="mt-6"><GroupsView /></div>
{:else}
	{#if adding}
		<h2 class="mt-6 mb-3 text-sm font-semibold">Add a device</h2>
		<DeviceEditor onclose={() => (adding = false)} />
	{/if}

	<div class="mt-6 flex flex-wrap items-center gap-3">
		<label class="relative w-full sm:w-auto sm:max-w-sm sm:flex-1">
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
		<Tabs
			label="Show"
			class="flex-wrap"
			tabs={filters.map((f) => ({ ...f, count: counts[f.id] }))}
			current={filter}
			onselect={(f) => (filter = f)}
		/>
		<Tabs
			label="View"
			class="sm:ml-auto"
			tabs={[
				{ id: 'list' as const, label: 'List', icon: List },
				{ id: 'graph' as const, label: 'Networks', icon: Workflow }
			]}
			current={view}
			onselect={show}
		/>
	</div>

	{#if view === 'graph'}
		<div class="mt-4">
			{#if store.devices === null}
				<div class="card h-96 animate-pulse"></div>
			{:else}
				<NetworkMap {devices} {matching} />
			{/if}
		</div>
	{:else}
		<div class="mt-4">
			<DeviceList
				{sections}
				{flat}
				empty={store.devices === null ? 'Loading…' : 'No device matches.'}
			/>
		</div>
	{/if}
	{#if store.me?.role === 'admin'}<Discovered />{/if}
{/if}
