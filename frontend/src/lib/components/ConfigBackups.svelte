<script lang="ts">
	import { more, type DeviceBackups } from '#lib/api.js';
	import BackupDiffDialog from './BackupDiffDialog.svelte';
	import { store } from '#lib/state.svelte.js';
	import { ago, bytes, dayClock } from '#lib/format.js';
	import Download from '@lucide/svelte/icons/download';
	import GitCompare from '@lucide/svelte/icons/git-compare';
	import Lock from '@lucide/svelte/icons/lock';
	import { configBackupState } from '#lib/states.js';
	import type { BackupVersion } from '#lib/api.js';
	import Section from './ui/Section.svelte';
	import Table, { type Column } from './ui/Table.svelte';
	import StateBadge from './ui/StateBadge.svelte';
	import EmptyState from './ui/EmptyState.svelte';

	/** A device's configuration backups (devbackups.py): its state, then a table of versions,
	 * each once ("the same until"), with what changed since the one before (secrets masked) and
	 * the download to restore it on its row. Admins. */
	let { device }: { device: string } = $props();

	let data = $state<DeviceBackups | null>(null);
	let error = $state<string | null>(null);
	let busy = $state(false);
	let note = $state<string | null>(null);
	let diff = $state<{ older: string; newer: string } | null>(null);

	async function load() {
		try {
			data = await more.backupsOf(device);
			error = null;
		} catch (e) {
			error = (e as Error).message;
		}
	}
	$effect(() => {
		void device;
		load();
		const t = setInterval(load, 60_000);
		return () => clearInterval(t);
	});

	async function now() {
		busy = true;
		note =
			data?.kind === 'fritzbox-export'
				? 'If the FRITZ!Box asks for confirmation, press a button on it now.'
				: null;
		try {
			const res = await more.backupNow(device);
			data = res;
			note = res.new ? 'A new version.' : 'The same as the newest version: nothing new kept.';
		} catch (e) {
			note = (e as Error).message;
		} finally {
			busy = false;
		}
	}

	function compare(older: string, newer: string) {
		diff = { older, newer };
	}

	const signedIn = $derived(!!store.me?.user);
	const KIND: Record<string, string> = {
		'openwrt-sysupgrade':
			'OpenWrt backup (sysupgrade -b): restore it in LuCI, System → Backup / Flash',
		'fritzbox-export':
			'Extract the ZIP, then restore the .export in FRITZ!OS → System → Backup → Restore. Use the password in export-password.txt.'
	};

	type Row = BackupVersion & { older: string | null };
	const rows = $derived(
		(data?.versions ?? []).map((v, i, all) => ({ ...v, older: all[i + 1]?.id ?? null }))
	);
	const columns: Column<Row>[] = [
		{ key: 'taken', label: 'Taken', value: (v) => dayClock(v.taken, store.now) },
		{
			key: 'files',
			label: 'Files',
			priority: 2,
			grow: true,
			value: (v) => `${v.files} ${v.files === 1 ? 'file' : 'files'}, ${bytes(v.size)}`
		},
		{
			key: 'same',
			label: 'The same until',
			priority: 3,
			title: 'When it was last read and found unchanged',
			value: (v) => (v.checked !== v.taken ? ago(v.checked, store.now) : null)
		},
		{ key: 'by', label: 'By', priority: 3, value: (v) => v.by },
		{ key: 'actions', label: '', align: 'right', cell: versionActions }
	];
</script>

{#snippet versionActions(v: Row)}
	{#if signedIn}
		<span class="inline-flex items-center gap-3 text-meta">
			{#if v.older}
				<button
					class="inline-flex items-center gap-1 text-muted hover:text-text"
					onclick={() => compare(v.older!, v.id)}
				>
					<GitCompare class="size-3.5" /> what changed
				</button>
			{/if}
			<a
				class="inline-flex items-center gap-1 text-muted hover:text-text"
				href={more.backupDownload(device, v.id)}
				download
				title="Everything, passwords and keys too"
			>
				<Download class="size-3.5" /> download
			</a>
		</span>
	{/if}
{/snippet}

{#if data && (data.kind || data.versions.length)}
	<Section title="Configuration backups" count={data.versions.length || null}>
		{#snippet meta()}
			<span class="inline-flex flex-wrap items-center gap-x-1.5">
				<StateBadge state={configBackupState(data!.status)} quiet />
				{#if data!.status.error}
					<span>{data!.status.error}</span>
				{:else if data!.status.ok}
					taken {ago(data!.status.ok, store.now)}, every {data!.every_hours} h
				{:else}
					every {data!.every_hours} h
				{/if}
			</span>
		{/snippet}
		{#snippet actions()}
			{#if data!.kind && store.me?.role === 'admin'}
				<button class="btn btn-sm" disabled={busy} onclick={now}>
					{busy ? 'Backing up…' : 'Back up now'}
				</button>
			{/if}
		{/snippet}
		{#if data.kind}<p class="mb-2 text-meta text-muted">{KIND[data.kind] ?? data.kind}</p>{/if}
		{#if note}<p class="mb-2 text-meta text-muted">{note}</p>{/if}
		{#if data.versions.length}
			<Table {columns} {rows} key={(v) => v.id} label="Configuration backup versions" />
			<p class="mt-2 flex items-center gap-1.5 text-meta text-faint">
				<Lock class="size-3" /> Kept encrypted; a version the same as the one before isn't kept twice.
			</p>
		{:else if !data.status.error}
			<EmptyState text="No version yet: the first is taken within minutes." />
		{/if}
		{#if error}<div class="mt-2"><EmptyState kind="error" text={error} onretry={load} /></div>{/if}
	</Section>
{/if}

{#if diff}
	{@const d = diff}
	<BackupDiffDialog
		load={() => more.backupDiff(device, d.older, d.newer)}
		onclose={() => (diff = null)}
	/>
{/if}
