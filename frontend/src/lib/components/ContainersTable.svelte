<script lang="ts">
	import type { CheckStatus, Container } from '#lib/api.js';
	import { serves } from '#lib/ownServices.js';
	import { runner, type Job } from '#lib/runner.svelte.js';
	import JobStatus from './JobStatus.svelte';
	import LogsDialog from './LogsDialog.svelte';
	import { store } from '#lib/state.svelte.js';
	import { containerActions, type ContainerAction } from '#lib/actions.js';
	import { containerAsk, sameTagUpdates, tagOf, updateTarget } from '#lib/updates.js';
	import ArrowUpCircle from '@lucide/svelte/icons/circle-arrow-up';
	import CircleHelp from '@lucide/svelte/icons/circle-help';
	import Check from '@lucide/svelte/icons/check';
	import Table, { type Column } from './ui/Table.svelte';
	import StateBadge from './ui/StateBadge.svelte';
	import { containerState } from '#lib/states.js';

	/** A host's containers: what runs, which checks it serves, and whether a newer image exists
	 * (or why that's unknown). With `canAct`, a signed-in admin can update, restart, stop or start
	 * them on their row through the host's actor (which acts only on what the host allows),
	 * through the same queue as the Updates page. The same table on /containers (one per host)
	 * and the host's page. Phones keep the container, its state and its actions (under the row). */
	let {
		device,
		containers,
		checks = [],
		stale = false,
		canAct = false
	}: {
		device: string;
		containers: Container[];
		checks?: CheckStatus[]; // the device's: a container answering one names it
		stale?: boolean;
		canAct?: boolean;
	} = $props();

	let asking = $state<string | null>(null); // a job's key (or "all"): asked once before it runs
	let logsOf = $state<string | null>(null); // the container whose log is open

	const update = (c: Container, to = c.update_to): Job => ({
		device,
		kind: 'container',
		name: c.name,
		...(tagOf(to) ? { tag: tagOf(to) } : {})
	});
	const ask = (c: Container, to: string) =>
		containerAsk({
			name: c.name,
			from: c.tag,
			to,
			compose: c.compose
		});
	const restart = (c: Container): Job => ({ device, kind: 'restart', name: c.name });
	type Action = { job: Job; label: string; ask: string; title?: string; primary?: boolean };
	/** What can be done to it now (only what can work from its state: actions.ts), each with its
	 * button and its question; the update to a new build of its tag stands out. */
	function actions(c: Container): Action[] {
		const all: Record<ContainerAction, Action> = {
			update: {
				job: update(c),
				label: tagOf(c.update_to) ? `Update to ${c.update_to}` : 'Update',
				ask: ask(c, updateTarget(c.update_to)),
				primary: !tagOf(c.update_to),
				title: tagOf(c.update_to)
					? `Pull ${c.image}:${c.update_to} and recreate it with the same settings`
					: 'Pull its image again and recreate it with the same settings'
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
		const out = containerActions(c.state, !!c.update && !c.unchecked, !!c.previous).map(
			(a) => all[a]
		);
		// A new major version: the newest of the one it runs is offered too.
		if (c.update && !c.unchecked && c.update_within && tagOf(c.update_within))
			out.splice(1, 0, {
				job: update(c, c.update_within),
				label: `Update to ${c.update_within}`,
				ask: ask(c, c.update_within),
				title: `The newest within the major version it runs`
			});
		return out;
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
	const keyOf = (j: Job) =>
		`${j.kind}:${'name' in j ? j.name : ''}${j.kind === 'container' && j.tag ? `:${j.tag}` : ''}`;
	const busy = (j: Job) => ['queued', 'running'].includes(runner.state(j)?.state ?? '');
	// "Update all": new builds of the tags they run, not already queued.
	const waiting = $derived(sameTagUpdates(containers).filter((c) => !busy(update(c))));
	const askingOf = (c: Container) => actions(c).find((a) => asking === keyOf(a.job));

	function go(jobs: Job[]) {
		runner.add(jobs);
		asking = null;
	}

	// Updates first, then those whose update can't be checked, then the rest; by name.
	const order = (c: Container) => (c.update ? 0 : c.unchecked ? 1 : 2);
	const sorted = $derived(
		[...containers].sort((a, b) => order(a) - order(b) || a.name.localeCompare(b.name))
	);
	const servesOf = (c: Container) =>
		serves(c, checks)
			.map((k) => k.name)
			.join(', ');

	const columns: Column<Container>[] = $derived([
		{ key: 'name', label: 'Container', grow: true, cell: nameCell },
		{ key: 'image', label: 'Image', priority: 2, cell: imageCell },
		{ key: 'state', label: 'State', cell: stateCell, width: '7.5rem' },
		{ key: 'update', label: 'Update', priority: 2, wrap: true, width: '16rem', cell: updateCell },
		...(canAct
			? [
					{
						key: 'actions',
						label: '',
						priority: 3,
						align: 'right',
						cell: actionsCell
					} satisfies Column<Container>
				]
			: [])
	]);
</script>

{#snippet nameCell(c: Container)}
	<span class="block truncate font-medium">{c.name}</span>
	{#if servesOf(c)}<span class="block truncate text-meta text-muted">
			serves {servesOf(c)}
		</span>{/if}
	<!-- Phones: the image's tag and its update, under the name. -->
	<span class="block truncate text-meta text-muted sm:hidden">
		<span class="font-mono">{c.tag}</span>{#if c.update && !c.unchecked}
			· update to {updateTarget(c.update_to)}{/if}
	</span>
{/snippet}

{#snippet imageCell(c: Container)}
	<span
		class="block max-w-[16rem] truncate font-mono text-meta text-muted"
		title="{c.image}:{c.tag} ({c.image_id})"
	>
		{c.image}<span class="text-faint">:</span><span class="text-text">{c.tag}</span>
	</span>
{/snippet}

{#snippet stateCell(c: Container)}
	<StateBadge state={containerState(c.state)} />
{/snippet}

{#snippet updateCell(c: Container)}
	<span class="inline-flex min-w-0 flex-wrap items-center gap-1.5 text-meta">
		{#if c.update}
			<ArrowUpCircle class="size-3.5 shrink-0 text-accent" />
			<span class="font-medium" class:opacity-60={c.skipped}>{updateTarget(c.update_to)}</span>
			{#if c.skipped}<span class="text-muted" title="Said to be not needed on Updates">
					· not needed
				</span>{/if}
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
			<span class="min-w-0 text-muted" title="Why its update can't be checked">{c.unchecked}</span>
		{:else if stale}
			<span class="text-muted">was up to date</span>
		{:else}
			<Check class="size-3.5 shrink-0 text-faint" />
			<span class="text-muted">up to date</span>
		{/if}
	</span>
{/snippet}

{#snippet buttons(c: Container)}
	{#each actions(c) as a (a.label)}
		<button
			class="btn btn-sm {a.primary ? '' : 'text-muted'}"
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
{/snippet}

{#snippet actionsCell(c: Container)}
	<span class="inline-flex justify-end gap-1">{@render buttons(c)}</span>
{/snippet}

<!-- Under the row: the actions on narrower screens, the question before one runs, its progress. -->
{#snippet belowRow(c: Container)}
	{@const a = askingOf(c)}
	{@const last = lastOf(c)}
	{#if canAct}
		<div class="flex flex-wrap justify-end gap-1 pb-3 lg:hidden">{@render buttons(c)}</div>
	{/if}
	{#if a}
		<p class="flex flex-wrap items-center gap-2 pb-3 text-meta">
			{a.ask}
			<button class="btn btn-sm btn-primary" onclick={() => go([a.job])}>{a.label}</button>
			<button class="btn btn-sm" onclick={() => (asking = null)}>Not now</button>
		</p>
	{/if}
	{#if last}
		<div class="pb-3"><JobStatus job={last.state} dismiss={() => runner.dismiss(last.job)} /></div>
	{/if}
{/snippet}

{#if canAct && waiting.length > 1}
	<div class="mb-3 flex flex-wrap items-center gap-2 text-sm">
		{#if asking === 'all'}
			<span class="text-meta">
				Update {waiting.map((c) => c.name).join(', ')}? One after another, each recreated with its
				new image (the old one comes back if that fails).
			</span>
			<button class="btn btn-sm btn-primary" onclick={() => go(waiting.map((c) => update(c)))}>
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
<div class:opacity-60={stale}>
	<Table
		{columns}
		rows={sorted}
		key={(c) => c.key}
		below={belowRow}
		hasBelow={(c) => canAct || !!askingOf(c) || !!lastOf(c)}
		label="Containers"
		empty="No containers."
	/>
</div>

{#if logsOf}
	<LogsDialog {device} what={{ container: logsOf }} onclose={() => (logsOf = null)} />
{/if}
