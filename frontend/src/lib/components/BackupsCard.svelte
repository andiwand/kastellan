<script lang="ts">
	import { api, type Backup } from '#lib/api.js';
	import { onMount } from 'svelte';
	import BackupJobsTable from './BackupJobsTable.svelte';
	import Section from './ui/Section.svelte';

	/** The backup jobs of a device that report in (backups.py): the /backups table, only this
	 * device's jobs; for admins the command its job runs when done (with a token made for it). */
	let { device }: { device: string } = $props();

	let all = $state<Backup[] | null>(null);
	const mine = $derived((all ?? []).filter((b) => b.device === device));

	async function load() {
		try {
			all = await api.backups();
		} catch {
			/* the card stays as it was */
		}
	}
	onMount(() => {
		load();
		const t = setInterval(load, 60_000);
		return () => clearInterval(t);
	});
</script>

{#if mine.length}
	<Section title="Backups" count={mine.length} id="backups">
		<BackupJobsTable jobs={mine} {device} onchanged={load} />
	</Section>
{/if}
