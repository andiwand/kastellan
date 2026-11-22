<script lang="ts">
	import type { FritzReport } from '#lib/api.js';
	import { bitrate, duration, kbitrate } from '#lib/format.js';
	import KeyValues, { type KeyValue } from './ui/KeyValues.svelte';
	import StateBadge from './ui/StateBadge.svelte';
	import WaysInTable from './WaysInTable.svelte';

	/** A FRITZ!Box's internet connection, as it says: connected or not and for how long, its
	 * public address, the line's speed in use and (DSL) what it synced at, its noise margin and
	 * errors; then the ways in from the internet (port forwards, remote access, MyFRITZ!:
	 * WaysInTable). */
	let { fritz }: { fritz: FritzReport } = $props();

	const net = $derived(fritz.internet);
	const dsl = $derived(net.dsl);
	const db = (v: number | null | undefined) => (v != null ? (v / 10).toFixed(1) : '–');
	const items = $derived.by((): KeyValue[] => {
		const list: KeyValue[] = [
			{ label: 'Connection', content: connection },
			{
				label: 'Public address',
				value: net.address,
				mono: true,
				hint: net.address_v6 ?? undefined
			},
			{
				label: `${net.access ?? 'Line'} in use`,
				value: `↓ ${bitrate(net.rate?.down)} · ↑ ${bitrate(net.rate?.up)}`
			}
		];
		if (dsl) {
			list.push(
				{
					label: 'DSL synced at',
					value: `↓ ${kbitrate(dsl.down)} · ↑ ${kbitrate(dsl.up)}`,
					hint: `of ↓ ${kbitrate(dsl.max_down)} · ↑ ${kbitrate(dsl.max_up)} possible`
				},
				{
					label: 'Noise margin',
					value: `↓ ${db(dsl.snr_down)} dB · ↑ ${db(dsl.snr_up)} dB`,
					hint:
						dsl.crc != null
							? `${dsl.crc.toLocaleString()} CRC errors, ${dsl.errored_seconds ?? 0} errored s`
							: undefined
				}
			);
		}
		if (net.last_error) list.push({ label: 'Last error', value: net.last_error });
		return list;
	});
</script>

{#snippet connection()}
	<StateBadge
		word={net.connection ?? 'Unknown'}
		tone={net.connection === 'Connected' ? 'good' : net.connection ? 'critical' : 'neutral'}
		quiet
	/>
	{#if net.connected_for}<span class="block text-meta text-faint">
			for {duration(net.connected_for)}
		</span>{/if}
{/snippet}

<KeyValues {items} columns={2} />
<h3 class="mt-5 mb-2 text-meta font-medium text-muted">Ways in from the internet</h3>
<WaysInTable {fritz} />
