<script lang="ts">
	import { api, type Backup } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { ago } from '#lib/format.js';
	import { backupState } from '#lib/states.js';
	import CodeBlock from './CodeBlock.svelte';
	import Table, { type Column } from './ui/Table.svelte';
	import StateBadge from './ui/StateBadge.svelte';

	/** Backup jobs that report in (backups.py): their state, last success, how often they are
	 * expected, the last failure. For admins, on its row: the command its job runs when done, with
	 * a token made for it (shown once). The same table on /backups and (with `device`, only that
	 * device's jobs, without the device line) on a device page. Phones keep the job and its state. */
	let {
		jobs,
		device,
		onchanged
	}: {
		jobs: Backup[];
		device?: string;
		onchanged?: () => void; // a token was made: read again
	} = $props();

	const rows = $derived(jobs.filter((b) => !device || b.device === device));
	const admin = $derived(store.me?.role === 'admin');
	const name = (id: string) => store.byId.get(id)?.name ?? id;

	let asking = $state<string | null>(null);
	let shown = $state<{ id: string; token: string } | null>(null);
	let error = $state<string | null>(null);

	async function makeToken(id: string) {
		error = null;
		try {
			shown = { id, token: (await api.backupToken(id)).token };
			asking = null;
			onchanged?.();
		} catch (e) {
			error = (e as Error).message;
		}
	}
	const command = (id: string, token: string) =>
		[
			`# At the end of the backup job, on success:`,
			`curl -fsS -m 30 -X POST -H "Authorization: Bearer ${token}" ${location.origin}/api/backups/${id}/ok`,
			`# Or when it failed (the reason as the body):`,
			`curl -fsS -m 30 -X POST -H "Authorization: Bearer ${token}" --data "why" ${location.origin}/api/backups/${id}/fail`,
			``,
			`# e.g. restic backup /data && curl ... /ok || curl ... /fail`
		].join('\n');

	const columns: Column<Backup>[] = $derived([
		{ key: 'name', label: 'Job', cell: jobName },
		{ key: 'state', label: 'State', cell: jobState, width: '8rem' },
		{
			key: 'ok',
			label: 'Last success',
			priority: 2,
			value: (b) => (b.ok ? ago(b.ok, store.now) : 'none yet')
		},
		{
			key: 'within',
			label: 'Expected',
			priority: 3,
			value: (b) => `every ${b.within_hours} h`
		},
		{
			key: 'message',
			label: 'Last failure',
			priority: 3,
			wrap: true,
			value: (b) =>
				b.failed ? `${ago(b.failed, store.now)}${b.message ? `: ${b.message}` : ''}` : null
		},
		...(admin
			? [
					{
						key: 'actions',
						label: '',
						align: 'right',
						priority: 2,
						cell: jobActions
					} satisfies Column<Backup>
				]
			: [])
	]);
</script>

{#snippet jobName(b: Backup)}
	<span class="block truncate font-medium" title={b.name}>{b.name}</span>
	{#if !device}
		<a href="/devices/{b.device}" class="block truncate text-meta text-muted hover:underline">
			{name(b.device)}
		</a>
	{/if}
	<!-- Phones: when it last worked, under the name. -->
	<span class="block truncate text-meta text-muted sm:hidden">
		{b.ok ? `last success ${ago(b.ok, store.now)}` : 'no success yet'}
	</span>
{/snippet}
{#snippet jobState(b: Backup)}
	<StateBadge state={backupState[b.state]} />
{/snippet}
{#snippet jobActions(b: Backup)}
	<button class="btn btn-sm text-muted" onclick={() => ((asking = b.id), (shown = null))}>
		{b.token ? 'New token' : 'How it reports in'}
	</button>
{/snippet}

<!-- Under the row: making its token, then the command with it (once). -->
{#snippet belowRow(b: Backup)}
	{#if admin}<div class="pb-3 sm:hidden">{@render jobActions(b)}</div>{/if}
	{#if asking === b.id}
		<p class="flex flex-wrap items-center gap-2 pb-3 text-meta">
			{b.token
				? 'A new token stops the one its job uses now. Make one?'
				: 'Its job calls Kastellan when done, with a token made for it (shown once). Make it?'}
			<button class="btn btn-sm btn-primary" onclick={() => makeToken(b.id)}>Make a token</button>
			<button class="btn btn-sm" onclick={() => (asking = null)}>Not now</button>
		</p>
		{#if error}<p class="pb-3 text-meta" style:color="var(--critical-text)">{error}</p>{/if}
	{/if}
	{#if shown?.id === b.id}
		<div class="pb-3">
			<p class="mb-1 text-meta text-muted">
				Put this at the end of the backup job. The token is shown only now.
			</p>
			<CodeBlock text={command(b.id, shown.token)} wrap />
		</div>
	{/if}
{/snippet}

<Table
	{columns}
	{rows}
	key={(b) => b.id}
	below={belowRow}
	hasBelow={(b) => admin || asking === b.id || shown?.id === b.id}
	label="Backup jobs"
	empty="No backup jobs."
/>
