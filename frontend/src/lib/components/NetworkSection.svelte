<script lang="ts">
	import Section from './ui/Section.svelte';
	import type { Device } from '#lib/api.js';
	import DhcpPool from './DhcpPool.svelte';
	import FritzWireguard from './FritzWireguard.svelte';
	import RouterClientsTable from './RouterClientsTable.svelte';

	/** What a router does for the network: the addresses it hands out (DHCP, OpenWrt or
	 * FRITZ!Box), its WireGuard connections, and who is connected through it. Its internet line
	 * and the ways in are RouterInternet's. */
	let { device }: { device: Device } = $props();

	const fritz = $derived(device.fritz);
	const dhcp = $derived(device.router?.dhcp ?? fritz?.dhcp ?? null);
	let showClients = $state(false);
</script>

{#if dhcp || fritz?.wireguard || fritz?.clients.length}
	<Section title="Network">
		{#if dhcp || fritz?.wireguard}
			<div class="grid gap-3 md:grid-cols-2">
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
