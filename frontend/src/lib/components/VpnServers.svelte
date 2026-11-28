<script lang="ts">
	import type { VpnServer } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { ago, bitrate } from '#lib/format.js';
	import { vpnServerState } from '#lib/states.js';
	import Section from './ui/Section.svelte';
	import Table, { type Column } from './ui/Table.svelte';
	import StateBadge from './ui/StateBadge.svelte';
	import EmptyState from './ui/EmptyState.svelte';

	/** Every VPN server Kastellan sees: OpenVPN and WireGuard on the routers, a FRITZ!Box's
	 * WireGuard, VPN containers on the hosts. Whether each runs, how it listens, who is
	 * connected now, and which managed VPN it serves. */
	let { servers, names }: { servers: VpnServer[]; names: Record<string, string> } = $props();

	const KIND: Record<string, string> = {
		openvpn: 'OpenVPN',
		wireguard: 'WireGuard',
		ipsec: 'IPsec',
		tailscale: 'Tailscale',
		zerotier: 'ZeroTier',
		nebula: 'Nebula',
		softether: 'SoftEther'
	};
	const SOURCE: Record<string, string> = {
		openwrt: 'OpenWrt',
		fritzbox: 'FRITZ!Box',
		container: 'container',
		'wg-easy': 'wg-easy'
	};
	const deviceName = (id: string | null) =>
		id ? (store.devices?.find((d) => d.id === id)?.name ?? id) : null;
	function listen(s: VpnServer) {
		const parts = [];
		if (s.port) parts.push(String(s.port));
		if (s.proto) parts.push(s.proto.replace(/-server$/, '').replace(/^tcp4?6?$/, 'tcp'));
		if (s.dev) parts.push(s.dev.replace(/\d+$/, ''));
		return parts.join(' / ');
	}

	/** Bytes per second as bits: kbit/s below a megabit, where Mbit/s would read 0.0. */
	function speed(bytes: number) {
		const bits = bytes * 8;
		return bits < 1e6 ? `${Math.round(bits / 1e3)} kbit/s` : bitrate(bits);
	}
	// The server's view: rx is what clients send (their upload), tx what they get.
	const TRAFFIC = 'down: to the clients, up: from them (between the last two reads)';

	const columns: Column<VpnServer>[] = [
		{ key: 'server', label: 'Server', cell: server },
		{ key: 'state', label: 'State', cell: stateCell, width: '8rem' },
		{ key: 'where', label: 'Where', priority: 2, wrap: true, cell: where },
		{ key: 'listens', label: 'Listens', priority: 3, value: (s) => listen(s) },
		{ key: 'clients', label: 'Connected now', priority: 2, cell: connected }
	];
</script>

{#snippet server(s: VpnServer)}
	<span class="font-medium">{s.name ?? '–'}</span>
	<span class="text-muted">· {KIND[s.kind] ?? s.kind}</span>
	{#if s.vpn}
		<a class="block text-meta text-accent hover:underline" href="?tab=clients#vpn-{s.vpn}">
			manages: {names[s.vpn] ?? s.vpn}
		</a>
	{/if}
	{#if s.image}<span class="block font-mono text-meta text-faint">{s.image}</span>{/if}
{/snippet}
{#snippet stateCell(s: VpnServer)}
	<StateBadge state={vpnServerState(s)} />
	{#if s.checked && !s.checked.startsWith('x')}
		<span class="block text-meta text-faint">read {ago(s.checked)}</span>
	{/if}
{/snippet}
{#snippet where(s: VpnServer)}
	{#if s.device}
		<a class="hover:underline" href="/devices/{s.device}">{deviceName(s.device)}</a>
	{:else}
		{s.where ?? '–'}
	{/if}
	<span class="block text-meta text-faint">{SOURCE[s.source] ?? s.source}</span>
{/snippet}
{#snippet connected(s: VpnServer)}
	{#if s.clients == null}
		<span class="text-faint">not read</span>
	{:else if !s.clients.length}
		<span class="text-muted">nobody</span>
	{:else}
		{#each s.clients as c, j (j)}
			<span class="block">
				{c.name}{#if c.address}<span class="font-mono text-faint"> · {c.address}</span>{/if}
				{#if c.since}<span class="text-faint"> · {ago(c.since)}</span>{/if}
				{#if c.tx_rate != null && c.rx_rate != null}
					<span class="text-faint tabular-nums" title={TRAFFIC}>
						· ↓ {speed(c.tx_rate)} ↑ {speed(c.rx_rate)}
					</span>
				{/if}
			</span>
		{/each}
	{/if}
	{#if s.tx_rate != null && s.rx_rate != null && (s.clients?.length ?? 0) > 1}
		<span class="block text-meta text-faint tabular-nums" title={TRAFFIC}>
			all: ↓ {speed(s.tx_rate)} ↑ {speed(s.rx_rate)}
		</span>
	{/if}
{/snippet}

<Section title="Servers" count={servers.length} meta="seen on the network" first>
	{#if !servers.length}
		<EmptyState
			text="None seen. Kastellan finds OpenVPN servers through an OpenWrt login with the openvpn ability, WireGuard interfaces on OpenWrt, a FRITZ!Box's WireGuard and VPN containers on hosts with a collector."
		/>
	{:else}
		<Table
			{columns}
			rows={servers}
			key={(s) =>
				`${s.device ?? s.where ?? ''}/${s.kind}/${s.name ?? ''}/${s.port ?? ''}/${s.image ?? ''}`}
			label="VPN servers"
		/>
	{/if}
</Section>
