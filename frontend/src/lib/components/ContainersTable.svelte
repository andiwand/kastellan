<script lang="ts">
	import type { Container } from '#lib/api.js';
	import { runner, type Job } from '#lib/runner.svelte.js';
	import JobStatus from './JobStatus.svelte';
	import ArrowUpCircle from '@lucide/svelte/icons/circle-arrow-up';
	import CircleHelp from '@lucide/svelte/icons/circle-help';
	import Check from '@lucide/svelte/icons/check';

	/** A host's containers: what runs, and whether a newer image exists (or why that's unknown).
	 * With `canAct`, a signed-in admin can update or restart them through the host's actor (which
	 * acts only on what the host allows), through the same queue as the Updates page. */
	let {
		device,
		containers,
		stale = false,
		canAct = false
	}: { device: string; containers: Container[]; stale?: boolean; canAct?: boolean } = $props();

	let asking = $state<string | null>(null); // a job's key (or "all"): asked once before it runs

	const update = (c: Container): Job => ({ device, kind: 'container', name: c.name });
	const restart = (c: Container): Job => ({ device, kind: 'restart', name: c.name });
	const keyOf = (j: Job) => `${j.kind}:${'name' in j ? j.name : ''}`;
	const busy = (j: Job) => ['queued', 'running'].includes(runner.state(j)?.state ?? '');
	// The actor pulls the tag a container runs again: a newer build, not a new tag.
	const updatable = $derived(containers.filter((c) => c.update && c.update_to === 'newer build'));
	const waiting = $derived(updatable.filter((c) => !busy(update(c))));

	function go(jobs: Job[]) {
		runner.add(jobs);
		asking = null;
	}

	const order = (c: Container) => (c.update ? 0 : c.unchecked ? 1 : 2);
	const sorted = $derived(
		[...containers].sort((a, b) => order(a) - order(b) || a.name.localeCompare(b.name))
	);
</script>

{#if canAct && waiting.length > 1}
	<div class="mb-3 flex flex-wrap items-center gap-2 text-sm">
		{#if asking === 'all'}
			<span class="text-xs">
				Update {waiting.map((c) => c.name).join(', ')}? One after another, each recreated with its
				new image (the old one comes back if that fails).
			</span>
			<button class="btn btn-sm btn-primary" onclick={() => go(waiting.map(update))}>
				Update {waiting.length}
			</button>
			<button class="btn btn-sm" onclick={() => (asking = null)}>Not now</button>
		{:else}
			<button class="btn btn-sm" onclick={() => (asking = 'all')}>
				Update all ({waiting.length})
			</button>
		{/if}
	</div>
{/if}
<div class="card overflow-hidden" class:opacity-60={stale}>
	<div
		class="hidden grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_6rem_minmax(0,1.2fr)] gap-4 border-b border-line px-5 py-2.5 text-xs font-medium text-muted md:grid"
	>
		<span>Container</span><span>Image</span><span>State</span><span>Update</span>
	</div>
	<ul class="divide-y divide-line">
		{#each sorted as c (c.key)}
			<li
				class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 px-5 py-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_6rem_minmax(0,1.2fr)]"
			>
				<p class="truncate text-sm font-medium">{c.name}</p>
				<p
					class="col-span-2 row-start-2 truncate font-mono text-xs text-muted md:col-span-1 md:row-start-auto"
					title="{c.image}:{c.tag} ({c.image_id})"
				>
					{c.image}<span class="text-faint">:</span><span class="text-text">{c.tag}</span>
				</p>
				<span
					class="hidden text-xs md:block"
					style:color={c.state === 'running' ? 'var(--good-text)' : 'var(--muted)'}
				>
					{c.state}
				</span>
				<span class="flex items-center gap-1.5 text-xs">
					{#if c.update}
						<ArrowUpCircle class="size-3.5 shrink-0 text-accent" />
						<span class="font-medium text-accent">{c.update_to ?? 'newer image'}</span>
					{:else if c.unchecked}
						<CircleHelp class="size-3.5 shrink-0 text-faint" />
						<span class="truncate text-muted" title={c.unchecked}>can't check: {c.unchecked}</span>
					{:else if stale}
						<span class="text-muted">was up to date</span>
					{:else}
						<Check class="size-3.5 shrink-0" style="color: var(--good-text)" />
						<span class="text-muted">up to date</span>
					{/if}
					{#if c.state !== 'running'}
						<span class="ml-auto text-faint md:hidden">{c.state}</span>
					{/if}
					{#if canAct}
						<span class="ml-auto flex shrink-0 gap-1">
							{#if updatable.includes(c)}
								<button
									class="btn btn-sm"
									disabled={busy(update(c))}
									onclick={() => (asking = keyOf(update(c)))}
									title="Pull its image again and recreate it with the same settings"
								>
									Update
								</button>
							{/if}
							<button
								class="btn btn-sm text-muted"
								disabled={busy(restart(c))}
								onclick={() => (asking = keyOf(restart(c)))}
							>
								Restart
							</button>
						</span>
					{/if}
				</span>
				{#each [update(c), restart(c)] as j (keyOf(j))}
					{#if asking === keyOf(j)}
						<p class="col-span-full flex flex-wrap items-center gap-2 text-xs">
							{j.kind === 'container'
								? `Update ${c.name}? It is recreated with the new image (the old one comes back if that fails).`
								: `Restart ${c.name}?`}
							<button class="btn btn-sm btn-primary" onclick={() => go([j])}>
								{j.kind === 'container' ? 'Update' : 'Restart'}
							</button>
							<button class="btn btn-sm" onclick={() => (asking = null)}>Not now</button>
						</p>
					{/if}
					{#if runner.state(j)}<div class="col-span-full">
							<JobStatus job={runner.state(j)} />
						</div>{/if}
				{/each}
			</li>
		{/each}
	</ul>
</div>
