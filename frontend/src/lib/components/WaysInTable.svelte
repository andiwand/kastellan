<script lang="ts">
	import type { FritzReport } from '#lib/api.js';
	import { waysIn, waysInUnknown, type WayIn } from '#lib/router.js';
	import Table, { type Column } from './ui/Table.svelte';
	import StateDot from './ui/StateDot.svelte';

	/** The ways in from the internet a router opens: its enabled port forwards, remote access to
	 * it, MyFRITZ! (a FRITZ!Box). A dot marks what is open to the internet; the words stay plain. */
	let { fritz }: { fritz: FritzReport } = $props();

	const rows = $derived(waysIn(fritz));
	const columns: Column<WayIn>[] = [
		{ key: 'kind', label: 'Way in', cell: kindCell },
		{ key: 'what', label: 'To', mono: true, grow: true, value: (w) => w.what },
		{ key: 'name', label: 'Name', priority: 2, value: (w) => w.name }
	];
</script>

{#snippet kindCell(w: WayIn)}
	<span class="inline-flex items-center gap-1.5">
		{#if w.warn}<StateDot tone="warning" label="Open to the internet" size="size-1.5" />{/if}
		{w.kind}
	</span>
{/snippet}

<Table
	{columns}
	{rows}
	key={(w) => w.key}
	label="Ways in from the internet"
	empty={waysInUnknown(fritz) ? 'Some access settings could not be read.' : 'None.'}
/>
