<script lang="ts">
	import { store } from '#lib/state.svelte.js';
	import { ago } from '#lib/format.js';
	import { runner, type Job } from '#lib/runner.svelte.js';
	import type { Container, Device } from '#lib/api.js';
	import ContainersTable from '#lib/components/ContainersTable.svelte';
	import KindIcon from '#lib/components/KindIcon.svelte';
	import Search from '@lucide/svelte/icons/search';

	/** Every container on every host (Docker, through the collector or WUD), grouped by host:
	 * what runs, what is stopped, what has a newer image; started, stopped, restarted and updated
	 * where the host's actor allows it, through the same queue as the Updates page. */
	type Filter = 'all' | 'running' | 'stopped' | 'updates';
	let filter = $state<Filter>('all');
	let query = $state('');
	let asking = $state(false);

	const admin = $derived(store.me?.role === 'admin' && !!store.me.user);
	const hosts = $derived((store.devices ?? []).filter((d) => d.containers?.length));
	const matches = (c: Container) =>
		(filter === 'all' ||
			(filter === 'running' && c.state === 'running') ||
			(filter === 'stopped' && c.state !== 'running') ||
			(filter === 'updates' && c.update && !c.unchecked)) &&
		`${c.name} ${c.image}:${c.tag}`.toLowerCase().includes(query.trim().toLowerCase());
	const shown = $derived(
		hosts
			.map((d) => ({ device: d, containers: (d.containers ?? []).filter(matches) }))
			.filter((h) => h.containers.length)
	);
	const all = $derived(hosts.flatMap((d) => d.containers ?? []));
	const counts = $derived({
		all: all.length,
		running: all.filter((c) => c.state === 'running').length,
		stopped: all.filter((c) => c.state !== 'running').length,
		updates: all.filter((c) => c.update && !c.unchecked).length
	});
	const canAct = (d: Device) => !!d.act && admin;
	// Updates the actor can take: a newer build of the tag it runs (a new tag is chosen where the
	// container is set up), on hosts with an actor, not already queued.
	const updatable = $derived(
		hosts.filter(canAct).flatMap((d) =>
			(d.containers ?? [])
				.filter((c) => c.update && c.update_to === 'newer build')
				.map((c): Job => ({ device: d.id, kind: 'container', name: c.name }))
				.filter((j) => !['queued', 'running'].includes(runner.state(j)?.state ?? ''))
		)
	);
	const FILTERS: { id: Filter; label: string }[] = [
		{ id: 'all', label: 'All' },
		{ id: 'running', label: 'Running' },
		{ id: 'stopped', label: 'Stopped' },
		{ id: 'updates', label: 'With updates' }
	];
</script>

<svelte:head><title>Containers · Kastellan</title></svelte:head>

<div class="flex flex-wrap items-end justify-between gap-4">
	<div>
		<h1 class="text-2xl font-semibold tracking-tight">Containers</h1>
		<p class="mt-1 max-w-2xl text-sm text-muted">
			The Docker containers on every host: what runs, what is stopped, and what has a newer image.
			Kastellan acts only where a host's actor allows it.
		</p>
	</div>
	{#if updatable.length}
		{#if asking}
			<span class="flex flex-wrap items-center gap-2 text-xs">
				Update {updatable.length}
				{updatable.length === 1 ? 'container' : 'containers'}? One after another on each host, hosts
				side by side.
				<button
					class="btn btn-sm btn-primary"
					onclick={() => {
						runner.add(updatable);
						asking = false;
					}}
				>
					Update {updatable.length}
				</button>
				<button class="btn btn-sm" onclick={() => (asking = false)}>Not now</button>
			</span>
		{:else}
			<button class="btn btn-sm btn-primary" onclick={() => (asking = true)}>
				Update all ({updatable.length})
			</button>
		{/if}
	{/if}
</div>

{#if store.devices === null}
	<div class="card mt-6 h-48 animate-pulse"></div>
{:else if hosts.length === 0}
	<div class="card mt-6 px-5 py-8 text-sm text-muted">
		No containers known yet. Kastellan sees a host's containers through its collector (set it up on
		the host's page) or through WUD.
	</div>
{:else}
	<div class="mt-6 flex flex-wrap items-center gap-3">
		<label class="relative w-full sm:w-auto sm:max-w-sm sm:flex-1">
			<Search
				class="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-faint"
			/>
			<input
				bind:value={query}
				type="search"
				placeholder="Search name or image"
				class="h-10 w-full rounded-xl border border-line bg-surface pr-3 pl-9 text-sm shadow-card outline-none placeholder:text-faint focus:border-accent"
			/>
		</label>
		<div class="flex gap-1 rounded-xl border border-line bg-surface p-1 shadow-card">
			{#each FILTERS as f (f.id)}
				<button
					class="h-8 rounded-lg px-3 text-sm font-medium {filter === f.id
						? 'bg-surface-3 text-text'
						: 'text-muted hover:text-text'}"
					onclick={() => (filter = f.id)}
				>
					{f.label} <span class="tabular text-xs text-faint">{counts[f.id]}</span>
				</button>
			{/each}
		</div>
	</div>

	{#each shown as h (h.device.id)}
		<div class="mt-8 mb-3 flex flex-wrap items-baseline justify-between gap-2">
			<a href="/devices/{h.device.id}" class="flex items-center gap-2 text-sm font-semibold">
				<KindIcon kind={h.device.kind} class="size-4 text-muted" />
				<span class="hover:underline">{h.device.name}</span>
			</a>
			<p class="text-xs text-muted">
				{#if h.device.containers_error}
					<span style:color="var(--warning-text)">{h.device.containers_error}</span>
				{:else if h.device.containers_checked}
					from {h.device.host_report?.docker ? 'the collector' : 'WUD'}, {ago(
						h.device.containers_checked,
						store.now
					)}
				{/if}
			</p>
		</div>
		<ContainersTable
			device={h.device.id}
			containers={h.containers}
			stale={!!h.device.containers_error}
			canAct={canAct(h.device)}
		/>
	{:else}
		<p class="card mt-6 px-5 py-8 text-sm text-muted">Nothing matches.</p>
	{/each}
{/if}
