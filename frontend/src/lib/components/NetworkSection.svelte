<script lang="ts">
	import StateBadge from './ui/StateBadge.svelte';
	import Section from './ui/Section.svelte';
	import KeyValues, { type KeyValue } from './ui/KeyValues.svelte';
	import type { Device } from '#lib/api.js';
	import { bitrate, duration, kbitrate } from '#lib/format.js';
	import DhcpPool from './DhcpPool.svelte';
	import FritzWireguard from './FritzWireguard.svelte';
	import WaysInTable from './WaysInTable.svelte';
	import RouterClientsTable from './RouterClientsTable.svelte';

	/** What a router does for the network: its internet line and the ways in from the internet
	 * (a FRITZ!Box), the addresses it hands out (DHCP, OpenWrt or FRITZ!Box), its WireGuard
	 * connections, and who is connected through it. `internet={false}` leaves the line and the
	 * ways in out (a page that shows them on their own, RouterInternet). */
	let { device, internet = true }: { device: Device; internet?: boolean } = $props();

	const fritz = $derived(device.fritz);
	const net = $derived(internet ? (fritz?.internet ?? null) : null);
	const dsl = $derived(net?.dsl);
	const dhcp = $derived(device.router?.dhcp ?? fritz?.dhcp ?? null);
	let showClients = $state(false);

	const db = (v: number | null | undefined) => (v != null ? `${(v / 10).toFixed(1)} dB` : '–');
	const line = $derived<KeyValue[]>(
		net
			? [
					{ label: 'Connection', content: connection },
					{ label: 'Public address', value: net.address, mono: true, hint: net.address_v6 ?? '' },
					{
						label: `${net.access ?? 'Line'} in use`,
						value: `↓ ${bitrate(net.rate?.down)} · ↑ ${bitrate(net.rate?.up)}`
					},
					...(dsl
						? [
								{
									label: 'DSL line',
									value: `↓ ${kbitrate(dsl.down)} of ${kbitrate(dsl.max_down)} · ↑ ${kbitrate(dsl.up)} of ${kbitrate(dsl.max_up)}`
								},
								{
									label: 'Noise margin',
									value: `↓ ${db(dsl.snr_down)} · ↑ ${db(dsl.snr_up)}`,
									hint:
										dsl.crc != null
											? `${dsl.crc.toLocaleString()} CRC errors, ${dsl.errored_seconds ?? 0} errored s`
											: ''
								}
							]
						: [])
				]
			: []
	);
</script>

{#snippet connection()}
	<span class="inline-flex flex-wrap items-center justify-end gap-x-1.5">
		<StateBadge
			word={net?.connection ?? 'Unknown'}
			tone={net?.connection === 'Connected' ? 'good' : 'critical'}
			quiet
		/>
		{#if net?.connected_for}<span class="text-meta text-muted">
				for {duration(net.connected_for)}
			</span>{/if}
	</span>
{/snippet}

{#if net || dhcp || fritz?.wireguard || fritz?.clients.length}
	<Section title="Network">
		{#if net && fritz}
			<h3 class="mb-2 text-meta font-medium text-muted">Internet</h3>
			<KeyValues items={line} />
			<h3 class="mt-5 mb-2 text-meta font-medium text-muted">Ways in from the internet</h3>
			<WaysInTable {fritz} />
		{/if}
		{#if dhcp || fritz?.wireguard}
			<div class="mt-5 grid gap-3 md:grid-cols-2">
				{#if dhcp}
					<div class="card p-4 text-sm">
						<h3 class="text-meta font-medium text-muted">Addresses it hands out (DHCP)</h3>
						<DhcpPool pool={dhcp} />
					</div>
				{/if}
				{#if fritz?.wireguard}
					<div class="card p-4 text-sm">
						<h3 class="text-meta font-medium text-muted">WireGuard</h3>
						<FritzWireguard wg={fritz.wireguard} />
					</div>
				{/if}
			</div>
		{/if}
		{#if fritz?.clients.length}
			<h3 class="mt-5 mb-2 flex items-baseline gap-2 text-meta font-medium text-muted">
				Connected <span class="tabular text-faint">{fritz.clients.length}</span>
				<button
					class="font-normal underline hover:text-text"
					onclick={() => (showClients = !showClients)}
				>
					{showClients ? 'hide' : 'show'}
				</button>
			</h3>
			{#if showClients}<RouterClientsTable clients={fritz.clients} />{/if}
		{/if}
	</Section>
{/if}
