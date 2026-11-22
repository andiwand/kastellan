<script lang="ts">
	import Tabs from '#lib/components/Tabs.svelte';
	import { store } from '#lib/state.svelte.js';
	import { ago } from '#lib/format.js';
	import { runner, type Job } from '#lib/runner.svelte.js';
	import type { Container, Device } from '#lib/api.js';
	import { sameTagUpdates } from '#lib/updates.js';
	import ContainersTable from '#lib/components/ContainersTable.svelte';
	import ReadAgain from '#lib/components/ReadAgain.svelte';
	import PageHeader from '#lib/components/ui/PageHeader.svelte';
	import Section from '#lib/components/ui/Section.svelte';
	import EmptyState from '#lib/components/ui/EmptyState.svelte';
	import StateBadge from '#lib/components/ui/StateBadge.svelte';
	import { count } from '#lib/glossary.js';
	import Search from '@lucide/svelte/icons/search';
	import VmsTable from '#lib/components/VmsTable.svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';

	/** Containers & VMs, a tab each (/vms redirects to the second).
	 * Containers: every container on every host (Docker, through the collector or WUD), grouped
	 * by host: what runs, what is stopped, what has a newer image; started, stopped, restarted
	 * and updated where the host's actor allows it, through the same queue as the Updates page.
	 * Virtual machines: every VM on every host (libvirt/KVM, through the host's collector);
	 * started, shut down, restarted or forced off where the host's actor allows it. */
	type Tab = 'containers' | 'vms';
	type Filter = 'all' | 'running' | 'stopped' | 'updates';
	let filter = $state<Filter>('all');
	let query = $state('');
	let asking = $state(false);

	const admin = $derived(store.me?.role === 'admin' && !!store.me.user);
	const hosts = $derived((store.devices ?? []).filter((d) => d.containers?.length));
	const vmHosts = $derived((store.devices ?? []).filter((d) => d.vms?.length));
	// VMs when asked for, or when there are VMs and no containers.
	const tab = $derived<Tab>(
		page.url.searchParams.get('tab') === 'vms' || (!hosts.length && vmHosts.length)
			? 'vms'
			: 'containers'
	);
	const vmCount = $derived(vmHosts.reduce((n, d) => n + (d.vms?.length ?? 0), 0));
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
	// "Update all": a newer build of the tag each runs (another tag is one at a time, asked first,
	// in its row), on hosts with an actor, not said to be not needed, not already queued.
	const updatable = $derived(
		hosts.filter(canAct).flatMap((d) =>
			sameTagUpdates(d.containers ?? [])
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

<svelte:head>
	<title>{tab === 'vms' ? 'Virtual machines' : 'Containers'} · Kastellan</title>
</svelte:head>

<PageHeader
	title="Containers & VMs"
	summary={tab === 'vms'
		? 'The virtual machines on every host (libvirt, KVM), as each host’s collector reports them.'
		: 'The Docker containers on every host: what runs, what is stopped, and what has a newer image.'}
>
	{#snippet tabs()}
		<Tabs
			label="Containers or virtual machines"
			tabs={[
				{ id: 'containers', label: 'Containers', count: all.length },
				{ id: 'vms', label: 'Virtual machines', count: vmCount }
			]}
			current={tab}
			onselect={(t: Tab) => goto(t === 'vms' ? '?tab=vms' : '?', { replace: true })}
		/>
	{/snippet}
	{#snippet actions()}
		{#if tab === 'containers' && updatable.length}
			{#if asking}
				<span class="flex flex-wrap items-center gap-2 text-xs">
					Update {count(updatable.length, 'container')}? One after another on each host, hosts side
					by side.
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
	{/snippet}
	{#snippet controls()}
		{#if tab === 'containers' && hosts.length}
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
			<Tabs
				label="Show"
				tabs={FILTERS.map((f) => ({ ...f, count: counts[f.id] }))}
				current={filter}
				onselect={(f) => (filter = f)}
			/>
		{/if}
	{/snippet}
</PageHeader>

{#if store.devices === null}
	<div class="card h-48 animate-pulse"></div>
{:else if tab === 'vms'}
	{#each vmHosts as d, i (d.id)}
		<Section
			title={d.name}
			href="/devices/{d.id}"
			count={d.vms?.length}
			first={i === 0}
			id="host-{d.id}"
		>
			{#snippet meta()}
				{#if d.host_checked}<span>
						from its collector, {ago(d.host_checked, store.now)} · <ReadAgain device={d.id} />
					</span>{/if}
			{/snippet}
			<VmsTable device={d.id} vms={d.vms ?? []} canAct={!!d.act && admin} />
		</Section>
	{:else}
		<EmptyState
			text="No virtual machines known yet. Kastellan sees a host's VMs through its collector (set it up on the host's page); the host needs libvirt (virsh)."
		/>
	{/each}
{:else if hosts.length === 0}
	<EmptyState
		text="No containers known yet. Kastellan sees a host's containers through its collector (set it up on the host's page) or through WUD."
	/>
{:else}
	{#each shown as h, i (h.device.id)}
		<Section
			title={h.device.name}
			href="/devices/{h.device.id}"
			first={i === 0}
			id="host-{h.device.id}"
		>
			{#snippet meta()}
				<span class="inline-flex flex-wrap items-center gap-x-1.5">
					{#if h.device.containers_error}
						<StateBadge word="Not read" tone="warning" quiet />
						<span>{h.device.containers_error}</span>
					{:else if h.device.containers_checked}
						from {h.device.host_report?.docker ? 'the collector' : 'WUD'}, {ago(
							h.device.containers_checked,
							store.now
						)}
					{/if}
					{#if h.device.collect && h.device.host_report?.docker}· <ReadAgain
							device={h.device.id}
						/>{/if}
				</span>
			{/snippet}
			<ContainersTable
				device={h.device.id}
				containers={h.containers}
				stale={!!h.device.containers_error}
				canAct={canAct(h.device)}
			/>
		</Section>
	{:else}
		<EmptyState text="Nothing matches." />
	{/each}
{/if}
