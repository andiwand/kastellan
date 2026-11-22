<script lang="ts">
	import type { HostReport } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { bytes } from '#lib/format.js';
	import { diskLimits, toneColor } from '#lib/vitals.js';
	import { fullInText, volumesOf, type Volume } from '#lib/storage.js';
	import Table, { type Column } from './ui/Table.svelte';
	import StateDot from './ui/StateDot.svelte';

	/** A host's volumes (its filesystems, from its collector): how full (a bar in the disk alarms'
	 * colours), what is free, and when it is full at the rate it filled. Phones keep the mount,
	 * how full and what is free. */
	let { report, forecast = {} }: { report: HostReport; forecast?: Record<string, number> } =
		$props();

	const rows = $derived(
		volumesOf(report, forecast, diskLimits(store.alarms), store.alarms?.limits?.disk_days ?? 14)
	);
	const columns: Column<Volume>[] = [
		{ key: 'mount', label: 'Volume', grow: true, cell: mountCell },
		{ key: 'used', label: 'Used', cell: usedCell, width: '10rem' },
		{ key: 'free', label: 'Free', numeric: true, value: (v) => bytes(v.free) },
		{
			key: 'size',
			label: 'Size',
			numeric: true,
			priority: 3,
			value: (v) => bytes(v.used + v.free)
		},
		{
			key: 'full',
			label: 'Full',
			priority: 2,
			title: 'At the rate it filled over the last days',
			cell: fullCell
		}
	];
</script>

{#snippet mountCell(v: Volume)}
	<span class="block truncate font-mono" title={v.device}>{v.mount}</span>
	<span class="block truncate text-meta text-muted">{v.device} · {v.type}</span>
{/snippet}

{#snippet usedCell(v: Volume)}
	<span class="flex items-center gap-2">
		<span class="h-1 w-16 overflow-hidden rounded-full bg-[var(--empty)]">
			<span
				class="block h-full rounded-full"
				style:width="{v.percent}%"
				style:background={toneColor[v.tone]}
			></span>
		</span>
		<span class="tabular">{v.percent.toFixed(0)} %</span>
	</span>
{/snippet}

{#snippet fullCell(v: Volume)}
	{#if v.fullIn != null}
		<span class="inline-flex items-center gap-1.5">
			{#if v.soon}<StateDot tone="warning" label="Full soon" size="size-1.5" />{/if}
			{fullInText(v.fullIn)}
		</span>
	{:else}<span class="text-faint">–</span>{/if}
{/snippet}

<Table {columns} {rows} key={(v) => v.mount} label="Volumes" empty="No volumes." />
