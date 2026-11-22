<script lang="ts">
	import { onMount } from 'svelte';
	import { api, more, type Backup, type DeviceBackupSummary } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { ago, dayClock } from '#lib/format.js';
	import { backupState, configBackupState } from '#lib/states.js';
	import KindIcon from '#lib/components/KindIcon.svelte';
	import HubBackupCard from '#lib/components/HubBackupCard.svelte';
	import PageHeader from '#lib/components/ui/PageHeader.svelte';
	import Section from '#lib/components/ui/Section.svelte';
	import Table, { type Column } from '#lib/components/ui/Table.svelte';
	import StateBadge from '#lib/components/ui/StateBadge.svelte';
	import EmptyState from '#lib/components/ui/EmptyState.svelte';

	/** Every backup in one place: the jobs that report in (backups.py) and the devices'
	 * configurations Kastellan keeps itself (devbackups.py). */
	let jobs = $state<Backup[] | null>(null);
	let configs = $state<DeviceBackupSummary[] | null>(null);
	let error = $state<string | null>(null);
	let busy = $state<string | null>(null);
	let notes = $state<Record<string, string>>({});

	async function load() {
		try {
			[jobs, configs] = await Promise.all([api.backups(), more.deviceBackups()]);
			error = null;
		} catch (e) {
			error = (e as Error).message;
		}
	}
	onMount(() => {
		load();
		const t = setInterval(load, 60_000);
		return () => clearInterval(t);
	});

	async function now(id: string) {
		busy = id;
		try {
			const res = await more.backupNow(id);
			notes[id] = res.new ? 'new version kept' : 'unchanged';
			await load();
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

	const configColumns: Column<DeviceBackupSummary>[] = [
		{ key: 'device', label: 'Device', cell: deviceCell },
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
		{ key: 'actions', label: '', align: 'right', cell: configActions }
	];
	const jobColumns: Column<Backup>[] = [
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
			grow: true,
			value: (b) =>
				b.failed ? `${ago(b.failed, store.now)}${b.message ? `: ${b.message}` : ''}` : null
		}
	];
</script>

{#snippet deviceCell(c: DeviceBackupSummary)}
	<a href="/devices/{c.device}" class="flex items-center gap-2 font-medium hover:underline">
		<KindIcon kind={kind(c.device)} class="size-4 shrink-0 text-muted" />
		{name(c.device)}
	</a>
	<!-- What happened, on its own line: a long message never pushes the buttons around. -->
	{#if busy === c.device && c.kind === 'fritzbox-export'}
		<p class="mt-0.5 text-meta">
			If FRITZ!OS asks, press a button on the FRITZ!Box now (within 90 s).
		</p>
	{:else if c.status.error}
		<p class="mt-0.5 text-meta text-muted">{c.status.error}</p>
	{:else if notes[c.device]}
		<p class="mt-0.5 text-meta text-muted">{notes[c.device]}</p>
	{/if}
{/snippet}
{#snippet configState(c: DeviceBackupSummary)}
	<StateBadge state={configBackupState(c.status)} />
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
{#snippet jobName(b: Backup)}
	<span class="font-medium">{b.name}</span>
	<a href="/devices/{b.device}" class="block text-meta text-muted hover:underline">
		{name(b.device)}
	</a>
{/snippet}
{#snippet jobState(b: Backup)}
	<StateBadge state={backupState[b.state]} />
{/snippet}

<svelte:head><title>Backups · Kastellan</title></svelte:head>

<PageHeader
	title="Backups"
	summary="Hub backups, device configurations and reports from your backup jobs."
/>
{#if error}<EmptyState kind="error" text={error} onretry={load} />{/if}

<HubBackupCard />

<Section title="Device configurations" count={configs?.length}>
	{#if configs === null}
		<div class="card h-32 animate-pulse"></div>
	{:else if !configs.length}
		<EmptyState
			text="None yet. Kastellan backs up OpenWrt devices whose login may (tick “Download its configuration backup” in the device's Setup) and FRITZ!Boxes with Kastellan's login."
		/>
	{:else}
		<Table
			columns={configColumns}
			rows={configs}
			key={(c) => c.device}
			label="Device configurations"
		/>
	{/if}
</Section>

<Section title="Backup jobs" count={jobs?.length}>
	{#if jobs === null}
		<div class="card h-24 animate-pulse"></div>
	{:else if !jobs.length}
		<EmptyState
			text="None. A backup job (restic, borg, a script) can tell Kastellan when it ran: add it under Settings → Backups, then make its token on the device's page."
		/>
	{:else}
		<Table columns={jobColumns} rows={jobs} key={(b) => b.id} label="Backup jobs" />
	{/if}
</Section>
