<script lang="ts">
	import type { HostReport } from '#lib/api.js';
	import DiskActivity from './DiskActivity.svelte';
	import VolumesTable from './VolumesTable.svelte';
	import DisksTable from './DisksTable.svelte';
	import Section from './ui/Section.svelte';
	import Table, { type Column } from './ui/Table.svelte';
	import StateDot from './ui/StateDot.svelte';

	/** A Linux host's storage and hardware, from its collector: its volumes (how full, when full
	 * at the rate they fill), its RAID arrays and disks (health from SMART, temperature), its
	 * hottest sensors, and its disks over time (DiskActivity). The fullest volume is also among
	 * the vitals. */
	let {
		device,
		report,
		forecast = {}
	}: { device: string; report: HostReport; forecast?: Record<string, number> } = $props();

	type Sensor = { key: string; sensor: string; celsius: number; limit: number | null };
	const hot = $derived(
		[...(report.temperatures ?? [])]
			.sort((a, b) => b.celsius - a.celsius)
			.slice(0, 6)
			.map((t, i) => ({ ...t, key: `${i}:${t.sensor}` }))
	);
	const hasDisks = $derived(!!(report.raid?.length || report.smart?.disks.length));
	const sensorColumns: Column<Sensor>[] = [
		{ key: 'sensor', label: 'Sensor', grow: true, value: (t) => t.sensor },
		{ key: 'celsius', label: 'Temperature', numeric: true, cell: celsiusCell },
		{
			key: 'limit',
			label: 'Limit',
			numeric: true,
			priority: 2,
			value: (t) => (t.limit != null ? `${t.limit.toFixed(0)} °C` : null)
		}
	];
</script>

{#snippet celsiusCell(t: Sensor)}
	<span class="inline-flex items-center gap-1.5">
		{#if t.limit != null && t.celsius >= t.limit}<StateDot
				tone="warning"
				label="At its limit"
				size="size-1.5"
			/>{/if}
		{t.celsius.toFixed(0)} °C
	</span>
{/snippet}

{#if report.filesystems?.length || hasDisks || hot.length || report.activity?.length}
	<Section title="Storage and hardware" id="storage">
		{#if report.filesystems?.length}
			<h3 class="mb-2 text-meta font-medium text-muted">Volumes</h3>
			<VolumesTable {report} {forecast} />
		{/if}
		{#if hasDisks}
			<h3 class="mt-5 mb-2 text-meta font-medium text-muted">Disks</h3>
			<DisksTable {report} />
		{/if}
		{#if report.smart && !report.smart.available}
			<p class="mt-2 text-meta text-muted">Disk health needs smartmontools on the host.</p>
		{/if}
		{#if hot.length}
			<h3 class="mt-5 mb-2 text-meta font-medium text-muted">Temperatures</h3>
			<Table columns={sensorColumns} rows={hot} key={(t) => t.key} label="Temperatures" />
		{/if}
		<DiskActivity {device} />
	</Section>
{/if}
