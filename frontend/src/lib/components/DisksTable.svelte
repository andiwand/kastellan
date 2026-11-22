<script lang="ts">
	import type { HostReport } from '#lib/api.js';
	import { disksOf, type DiskRow } from '#lib/storage.js';
	import Table, { type Column } from './ui/Table.svelte';
	import StateBadge from './ui/StateBadge.svelte';

	/** A host's RAID arrays and disks (its collector: mdstat, SMART): one State each (Healthy,
	 * Wear, Failing, Degraded, Not read) with what it can't say (asleep, a resync), the disk's
	 * temperature and how long it has been on. Phones keep the disk and its state. */
	let { report }: { report: HostReport } = $props();

	const rows = $derived(disksOf(report));
	const columns: Column<DiskRow>[] = [
		{ key: 'name', label: 'Disk', grow: true, cell: nameCell },
		{ key: 'state', label: 'State', cell: stateCell, width: '8rem' },
		{
			key: 'temperature',
			label: 'Temperature',
			numeric: true,
			priority: 2,
			value: (d) => (d.temperature != null ? `${d.temperature} °C` : null)
		},
		{
			key: 'age',
			label: 'On for',
			numeric: true,
			priority: 3,
			title: 'Its power-on hours (SMART)',
			value: (d) => (d.years != null ? `${d.years} years` : null)
		}
	];
</script>

{#snippet nameCell(d: DiskRow)}
	<span class="block truncate">
		<span class="font-mono">{d.name}</span>
		{#if d.status}<span class="ml-1 font-mono text-meta text-faint">{d.status}</span>{/if}
	</span>
	{#if d.what || d.note}
		<span class="block truncate text-meta text-muted" title={d.note ?? d.what}>
			{[d.what, d.note].filter(Boolean).join(' · ')}
		</span>
	{/if}
{/snippet}

{#snippet stateCell(d: DiskRow)}
	<StateBadge state={d.state} />
{/snippet}

<Table {columns} {rows} key={(d) => d.key} label="Disks" empty="No disks read." />
