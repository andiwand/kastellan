<script lang="ts">
	import { onMount } from 'svelte';
	import { api, more, type Backup, type DeviceBackupSummary } from '#lib/api.js';
	import HubBackupCard from '#lib/components/HubBackupCard.svelte';
	import BackupJobsTable from '#lib/components/BackupJobsTable.svelte';
	import ConfigBackupsTable from '#lib/components/ConfigBackupsTable.svelte';
	import PageHeader from '#lib/components/ui/PageHeader.svelte';
	import Section from '#lib/components/ui/Section.svelte';
	import EmptyState from '#lib/components/ui/EmptyState.svelte';

	/** Every backup in one place: the jobs that report in (backups.py) and the devices'
	 * configurations Kastellan keeps itself (devbackups.py). */
	let jobs = $state<Backup[] | null>(null);
	let configs = $state<DeviceBackupSummary[] | null>(null);
	let error = $state<string | null>(null);

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
</script>

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
		<ConfigBackupsTable {configs} onchanged={load} />
	{/if}
</Section>

<Section title="Backup jobs" count={jobs?.length}>
	{#if jobs === null}
		<div class="card h-24 animate-pulse"></div>
	{:else if !jobs.length}
		<EmptyState
			text="None. A backup job (restic, borg, a script) can tell Kastellan when it ran: add it under Settings → Backups, then make its token on its row here."
		/>
	{:else}
		<BackupJobsTable {jobs} onchanged={load} />
	{/if}
</Section>
