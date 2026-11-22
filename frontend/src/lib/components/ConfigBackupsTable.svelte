<script lang="ts">
	import { more, type DeviceBackupSummary } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { ago, dayClock } from '#lib/format.js';
	import { configBackupState } from '#lib/states.js';
	import KindIcon from './KindIcon.svelte';
	import Table, { type Column } from './ui/Table.svelte';
	import StateBadge from './ui/StateBadge.svelte';

	/** The devices' configuration backups Kastellan takes itself (devbackups.py), a row per
	 * device: its state, the kind of backup, the newest version and since when it is unchanged,
	 * how many versions are kept; "Back up now" and its versions (on the device's page) on the
	 * row. `device` keeps one device's row. Phones keep the device and its state. */
	let {
		configs,
		device,
		onchanged
	}: {
		configs: DeviceBackupSummary[];
		device?: string;
		onchanged?: () => void | Promise<void>; // a backup was taken: read again
	} = $props();

	const rows = $derived(configs.filter((c) => !device || c.device === device));
	let busy = $state<string | null>(null);
	let notes = $state<Record<string, string>>({});

	async function now(id: string) {
		busy = id;
		try {
			const res = await more.backupNow(id);
			notes[id] = res.new ? 'new version kept' : 'unchanged';
			await onchanged?.();
		} catch (e) {
			notes[id] = (e as Error).message;
		} finally {
			busy = null;
		}
	}

	const name = (id: string) => store.byId.get(id)?.name ?? id;
	const kind = (id: string) => store.byId.get(id)?.kind ?? 'other';
	const KIND: Record<string, string> = {
		'openwrt-sysupgrade': 'OpenWrt',
		'fritzbox-export': 'FRITZ!Box export'
	};

	const columns: Column<DeviceBackupSummary>[] = [
		{ key: 'device', label: 'Device', grow: true, cell: deviceCell },
		{ key: 'state', label: 'State', cell: configState, width: '8rem' },
		{
			key: 'kind',
			label: 'Kind',
			priority: 3,
			value: (c) => (c.kind ? (KIND[c.kind] ?? c.kind) : 'no login any more')
		},
		{
			key: 'newest',
			label: 'Newest',
			priority: 2,
			value: (c) => (c.latest ? dayClock(c.latest.taken, store.now) : null)
		},
		{
			key: 'same',
			label: 'The same until',
			priority: 3,
			title: 'When it was last read and found unchanged',
			value: (c) =>
				c.latest && c.latest.checked !== c.latest.taken ? ago(c.latest.checked, store.now) : null
		},
		{ key: 'versions', label: 'Versions', numeric: true, priority: 3, value: (c) => c.versions },
		{ key: 'actions', label: '', align: 'right', priority: 2, cell: configActions }
	];
</script>

{#snippet deviceCell(c: DeviceBackupSummary)}
	<a href="/devices/{c.device}" class="flex min-w-0 items-center gap-2 font-medium hover:underline">
		<KindIcon kind={kind(c.device)} class="size-4 shrink-0 text-muted" />
		<span class="truncate">{name(c.device)}</span>
	</a>
	<!-- What happened, on its own line: a long message never pushes the buttons around. -->
	{#if busy === c.device && c.kind === 'fritzbox-export'}
		<p class="mt-0.5 truncate text-meta">
			If FRITZ!OS asks, press a button on the FRITZ!Box now (within 90 s).
		</p>
	{:else if c.status.error}
		<p class="mt-0.5 truncate text-meta text-muted" title={c.status.error}>{c.status.error}</p>
	{:else if notes[c.device]}
		<p class="mt-0.5 truncate text-meta text-muted">{notes[c.device]}</p>
	{/if}
{/snippet}
{#snippet configState(c: DeviceBackupSummary)}
	<StateBadge state={configBackupState(c.status, !!c.latest)} />
{/snippet}
{#snippet configActions(c: DeviceBackupSummary)}
	<span class="inline-flex gap-2">
		{#if c.kind}
			<button class="btn btn-sm" disabled={busy !== null} onclick={() => now(c.device)}>
				{busy === c.device ? 'Backing up…' : 'Back up now'}
			</button>
		{/if}
		<a class="btn btn-sm" href="/devices/{c.device}#config-backups">Versions</a>
	</span>
{/snippet}

{#snippet belowRow(c: DeviceBackupSummary)}
	<div class="flex flex-wrap justify-end gap-2 pb-3 sm:hidden">{@render configActions(c)}</div>
{/snippet}

<Table
	{columns}
	{rows}
	key={(c) => c.device}
	below={belowRow}
	label="Device configurations"
	empty="No configuration backups."
/>
