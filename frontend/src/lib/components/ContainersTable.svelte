<script lang="ts">
	import type { Container } from '#lib/api.js';
	import { runner, type Job } from '#lib/runner.svelte.js';
	import JobStatus from './JobStatus.svelte';
	import LogsDialog from './LogsDialog.svelte';
	import { store } from '#lib/state.svelte.js';
	import { containerActions, type ContainerAction } from '#lib/actions.js';
	import ArrowUpCircle from '@lucide/svelte/icons/circle-arrow-up';
	import CircleHelp from '@lucide/svelte/icons/circle-help';
	import Check from '@lucide/svelte/icons/check';

	/** A host's containers: what runs, and whether a newer image exists (or why that's unknown).
	 * With `canAct`, a signed-in admin can update, restart, stop or start them through the host's
	 * actor (which acts only on what the host allows), through the same queue as the Updates page. */
	let {
		device,
		containers,
		stale = false,
		canAct = false
	}: { device: string; containers: Container[]; stale?: boolean; canAct?: boolean } = $props();

	let asking = $state<string | null>(null); // a job's key (or "all"): asked once before it runs
	let logsOf = $state<string | null>(null); // the container whose log is open

	const update = (c: Container): Job => ({ device, kind: 'container', name: c.name });
	const restart = (c: Container): Job => ({ device, kind: 'restart', name: c.name });
	/** What can be done to it now (only what can work from its state: actions.ts), each with its
	 * button and its question. */
	function actions(c: Container): { job: Job; label: string; ask: string; title?: string }[] {
		const all: Record<ContainerAction, { job: Job; label: string; ask: string; title?: string }> = {
			update: {
				job: update(c),
				label: 'Update',
				ask: `Update ${c.name}? It is recreated with the new image (the old one comes back if that fails).`,
				title: 'Pull its image again and recreate it with the same settings'
			},
			rollback: {
				job: { device, kind: 'rollback', name: c.name },
				label: 'Go back',
				ask: `Go back to the image ${c.name} ran before its last update (${c.previous})? Stored data stays as it is; database migrations may need a separate restore.`,
				title: 'Recreate it with the image it ran before its last update'
			},
			start: {
				job: { device, kind: 'start', name: c.name },
				label: 'Start',
				ask: `Start ${c.name}?`
			},
			restart: { job: restart(c), label: 'Restart', ask: `Restart ${c.name}?` },
			stop: {
				job: { device, kind: 'stop', name: c.name },
				label: 'Stop',
				ask: `Stop ${c.name}? It stays stopped until started again (or until the host restarts, if it is set to restart).`
			}
		};
		return containerActions(c.state, updatable.includes(c), !!c.previous).map((a) => all[a]);
	}
	// What its row says: the latest of its jobs (runner.latest).
	const lastOf = (c: Container) =>
		runner.latest(
			[
				update(c),
				{ device, kind: 'rollback', name: c.name },
				restart(c),
				{ device, kind: 'start', name: c.name },
				{ device, kind: 'stop', name: c.name }
			],
			store.now
		);
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
						{#if c.update_within}<span
								class="text-muted"
								title="The newest within the major version it runs"
							>
								or {c.update_within}
							</span>{/if}
					{:else if c.unchecked === 'not checked yet'}
						<span class="text-muted" title="Its registry is being asked; it shows in a minute">
							checking…
						</span>
					{:else if c.unchecked}
						<CircleHelp class="size-3.5 shrink-0 text-faint" />
						<span class="min-w-0 text-muted" title="Why its update can't be checked">
							{c.unchecked}
						</span>
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
							{#each actions(c) as a (a.label)}
								<button
									class="btn btn-sm {a.label === 'Update' ? '' : 'text-muted'}"
									disabled={busy(a.job)}
									onclick={() => (asking = keyOf(a.job))}
									title={a.title ?? ''}
								>
									{a.label}
								</button>
							{/each}
							<button
								class="btn btn-sm text-muted"
								onclick={() => (logsOf = c.name)}
								title="The last lines of its log"
							>
								Logs
							</button>
						</span>
					{/if}
				</span>
				{#each actions(c) as a (a.label)}
					{#if asking === keyOf(a.job)}
						<p class="col-span-full flex flex-wrap items-center gap-2 text-xs">
							{a.ask}
							<button class="btn btn-sm btn-primary" onclick={() => go([a.job])}>{a.label}</button>
							<button class="btn btn-sm" onclick={() => (asking = null)}>Not now</button>
						</p>
					{/if}
				{/each}
				{#if lastOf(c)}
					{@const last = lastOf(c)!}
					<div class="col-span-full">
						<JobStatus job={last.state} dismiss={() => runner.dismiss(last.job)} />
					</div>{/if}
			</li>
		{/each}
	</ul>
</div>

{#if logsOf}
	<LogsDialog {device} what={{ container: logsOf }} onclose={() => (logsOf = null)} />
{/if}
