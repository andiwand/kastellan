<script lang="ts">
	import type { FritzReport } from '#lib/api.js';
	import Table, { type Column } from './ui/Table.svelte';

	/** Who is connected through a router (a FRITZ!Box's host list): name, address, by cable or
	 * Wi-Fi, its link speed, guest. Phones keep the name and the address. */
	let { clients }: { clients: FritzReport['clients'] } = $props();

	type Row = FritzReport['clients'][number] & { key: string };
	const rows = $derived(
		clients
			.map((c, i) => ({ ...c, key: `${c.mac ?? ''}/${i}` }))
			.sort((a, b) => (a.name ?? a.mac ?? '').localeCompare(b.name ?? b.mac ?? ''))
	);
	const columns: Column<Row>[] = [
		{ key: 'name', label: 'Client', grow: true, cell: nameCell },
		{ key: 'address', label: 'Address', mono: true, value: (c) => c.address },
		{
			key: 'via',
			label: 'Via',
			priority: 2,
			value: (c) =>
				[c.via === '802.11' ? 'Wi-Fi' : c.via, c.guest ? 'guest' : null].filter(Boolean).join(' · ')
		},
		{
			key: 'speed',
			label: 'Speed',
			priority: 3,
			numeric: true,
			value: (c) => (c.speed ? `${c.speed} Mbit/s` : null)
		}
	];
</script>

{#snippet nameCell(c: Row)}
	<span class="block truncate">{c.name ?? c.mac ?? '?'}</span>
	{#if c.name && c.mac}<span class="block truncate font-mono text-meta text-muted">
			{c.mac}
		</span>{/if}
{/snippet}

<Table {columns} {rows} key={(c) => c.key} label="Connected clients" empty="None connected." />
