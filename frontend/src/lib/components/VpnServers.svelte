<script lang="ts">
	import type { VpnServer } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { ago } from '#lib/format.js';

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
	function state(s: VpnServer): { text: string; color: string } {
		if (s.error) return { text: 'can’t be read', color: 'var(--warning-text)' };
		if (s.running === true && s.stale)
			return { text: 'runs, status old', color: 'var(--warning-text)' };
		if (s.running === true) return { text: 'running', color: 'var(--good-text)' };
		if (s.running === false)
			return {
				text: s.state && s.state !== 'running' ? s.state : 'stopped',
				color: 'var(--critical-text)'
			};
		return { text: 'unknown', color: 'var(--muted)' };
	}
	function listen(s: VpnServer) {
		const parts = [];
		if (s.port) parts.push(String(s.port));
		if (s.proto) parts.push(s.proto.replace(/-server$/, '').replace(/^tcp4?6?$/, 'tcp'));
		if (s.dev) parts.push(s.dev.replace(/\d+$/, ''));
		return parts.join(' / ');
	}
</script>

<section class="card mt-5 overflow-hidden">
	<div class="flex flex-wrap items-baseline justify-between gap-2 px-4 pt-4">
		<h2 class="text-sm font-semibold">Servers</h2>
		<span class="text-xs text-faint">
			{servers.length}
			{servers.length === 1 ? 'server' : 'servers'} seen on the network
		</span>
	</div>
	{#if !servers.length}
		<p class="px-4 pt-2 pb-4 text-xs text-muted">
			None seen. Kastellan finds OpenVPN servers through an OpenWrt login with the openvpn ability,
			WireGuard interfaces on OpenWrt, a FRITZ!Box's WireGuard and VPN containers on hosts with a
			collector.
		</p>
	{:else}
		<div class="mt-2 overflow-x-auto">
			<table class="w-full min-w-[40rem] text-sm">
				<thead>
					<tr class="text-left text-xs text-faint">
						<th class="px-4 py-1.5 font-medium">Server</th>
						<th class="px-2 py-1.5 font-medium">Where</th>
						<th class="px-2 py-1.5 font-medium">Listens</th>
						<th class="px-2 py-1.5 font-medium">State</th>
						<th class="px-4 py-1.5 font-medium">Connected now</th>
					</tr>
				</thead>
				<tbody>
					{#each servers as s, i (i)}
						{@const st = state(s)}
						<tr class="border-t border-line align-top">
							<td class="px-4 py-2">
								<span class="font-medium">{s.name ?? '–'}</span>
								<span class="text-xs text-muted">· {KIND[s.kind] ?? s.kind}</span>
								{#if s.vpn}
									<a class="block text-xs text-accent hover:underline" href="#vpn-{s.vpn}">
										manages: {names[s.vpn] ?? s.vpn}
									</a>
								{/if}
								{#if s.image}<span class="block text-xs text-faint">{s.image}</span>{/if}
							</td>
							<td class="px-2 py-2 text-xs">
								{#if s.device}
									<a class="hover:underline" href="/devices/{s.device}">{deviceName(s.device)}</a>
								{:else if s.where}
									{s.where}
								{:else}
									–
								{/if}
								<span class="block text-faint">{SOURCE[s.source] ?? s.source}</span>
							</td>
							<td class="tabular px-2 py-2 text-xs">{listen(s) || '–'}</td>
							<td class="px-2 py-2 text-xs">
								<span style:color={st.color} title={s.error ?? undefined}>{st.text}</span>
								{#if s.checked && !s.checked.startsWith('x')}
									<span class="block text-faint">read {ago(s.checked)}</span>
								{/if}
							</td>
							<td class="px-4 py-2 text-xs">
								{#if s.clients == null}
									<span class="text-faint">not read</span>
								{:else if !s.clients.length}
									<span class="text-muted">nobody</span>
								{:else}
									{#each s.clients as c, j (j)}
										<span class="block">
											{c.name}{#if c.address}<span class="text-faint"> · {c.address}</span>{/if}
											{#if c.since}<span class="text-faint"> · {ago(c.since)}</span>{/if}
										</span>
									{/each}
								{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</section>
