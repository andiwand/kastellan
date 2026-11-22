<script lang="ts">
	import type { Device } from '#lib/api.js';
	import { duration } from '#lib/format.js';
	import DhcpPool from './DhcpPool.svelte';
	import FritzWireguard from './FritzWireguard.svelte';

	/** What a router does for the network: its internet line and the ways in from the internet
	 * (a FRITZ!Box), the addresses it hands out (DHCP, OpenWrt or FRITZ!Box), its WireGuard
	 * connections, and who is connected through it. */
	let { device }: { device: Device } = $props();

	const fritz = $derived(device.fritz);
	const net = $derived(fritz?.internet ?? null);
	const dsl = $derived(net?.dsl);
	const dhcp = $derived(device.router?.dhcp ?? fritz?.dhcp ?? null);
	const forwards = $derived((fritz?.forwards ?? []).filter((f) => f.enabled));
	let showClients = $state(false);

	const mbit = (bits: number | null | undefined) =>
		bits == null
			? '–'
			: bits >= 1e9
				? `${(bits / 1e9).toFixed(1)} Gbit/s`
				: `${(bits / 1e6).toFixed(bits < 1e7 ? 1 : 0)} Mbit/s`;
</script>

{#if net || dhcp || fritz?.wireguard || fritz?.clients.length}
	<section class="mt-8">
		<h2 class="text-sm font-semibold">Network</h2>
		<div class="mt-3 grid gap-3 md:grid-cols-2">
			{#if net && fritz}
				<div class="card p-4 text-sm">
					<h3 class="text-xs font-medium text-muted">Internet</h3>
					<p class="mt-1">
						<span
							style:color={net.connection === 'Connected'
								? 'var(--good-text)'
								: 'var(--critical-text)'}
						>
							{net.connection === 'Connected' ? 'Connected' : (net.connection ?? 'unknown')}
						</span>
						{#if net.connected_for}<span class="text-xs text-muted">
								· for {duration(net.connected_for)}
							</span>{/if}
					</p>
					<dl class="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-xs">
						<dt class="text-muted">Public address</dt>
						<dd class="truncate font-mono" title={net.address_v6 ?? ''}>{net.address ?? '–'}</dd>
						<dt class="text-muted">{net.access ?? 'Line'} in use</dt>
						<dd class="tabular">↓ {mbit(net.rate?.down)} · ↑ {mbit(net.rate?.up)}</dd>
						{#if dsl}
							<dt class="text-muted">DSL line</dt>
							<dd class="tabular">
								↓ {mbit((dsl.down ?? 0) * 1000)} of {mbit((dsl.max_down ?? 0) * 1000)} · ↑ {mbit(
									(dsl.up ?? 0) * 1000
								)} of {mbit((dsl.max_up ?? 0) * 1000)}
							</dd>
							<dt class="text-muted">Noise margin</dt>
							<dd class="tabular">
								↓ {dsl.snr_down != null ? (dsl.snr_down / 10).toFixed(1) : '–'} dB · ↑
								{dsl.snr_up != null ? (dsl.snr_up / 10).toFixed(1) : '–'} dB
								{#if dsl.crc != null}<span class="text-muted">
										· {dsl.crc.toLocaleString()} CRC errors, {dsl.errored_seconds ?? 0} errored s
									</span>{/if}
							</dd>
						{/if}
					</dl>
				</div>
				<div class="card p-4 text-sm">
					<h3 class="text-xs font-medium text-muted">Ways in from the internet</h3>
					<ul class="mt-1 space-y-0.5">
						{#each forwards as f, i (i)}
							<li>
								<span style:color="var(--warning-text)">Port forward</span>
								<span class="font-mono text-xs">{f.protocol} {f.port} → {f.to}:{f.to_port}</span>
								{#if f.name}<span class="text-xs text-muted">({f.name})</span>{/if}
							</li>
						{/each}
						{#if fritz.remote.remote_access}
							<li style:color="var(--warning-text)">
								Remote access to the FRITZ!Box (HTTPS{fritz.remote.remote_port
									? `, port ${fritz.remote.remote_port}`
									: ''})
							</li>
						{/if}
						{#if fritz.remote.myfritz}<li class="text-muted">MyFRITZ! is on</li>{/if}
						{#if !forwards.length && !fritz.remote.remote_access && !fritz.remote.myfritz}
							<li class="text-muted">
								{fritz.forwards === null ||
								fritz.remote.remote_access == null ||
								fritz.remote.myfritz == null
									? 'Some access settings could not be read.'
									: 'None.'}
							</li>
						{/if}
					</ul>
				</div>
			{/if}
			{#if dhcp}
				<div class="card p-4 text-sm">
					<h3 class="text-xs font-medium text-muted">Addresses it hands out (DHCP)</h3>
					<DhcpPool pool={dhcp} />
				</div>
			{/if}
			{#if fritz?.wireguard}
				<div class="card p-4 text-sm">
					<h3 class="text-xs font-medium text-muted">WireGuard</h3>
					<FritzWireguard wg={fritz.wireguard} />
				</div>
			{/if}
		</div>
		{#if fritz?.clients.length}
			<div class="card mt-3 p-4 text-sm">
				<h3 class="text-xs font-medium text-muted">
					Connected · {fritz.clients.length}
					<button class="ml-1 font-normal underline" onclick={() => (showClients = !showClients)}>
						{showClients ? 'hide' : 'show'}
					</button>
				</h3>
				{#if showClients}
					<ul class="mt-2 grid gap-x-6 gap-y-0.5 sm:grid-cols-2">
						{#each fritz.clients as c, i (c.mac ?? i)}
							<li class="flex min-w-0 items-baseline gap-2">
								<span class="truncate">{c.name ?? c.mac}</span>
								<span class="shrink-0 font-mono text-xs text-muted">{c.address}</span>
								<span class="shrink-0 text-xs text-faint">
									{c.via === '802.11' ? 'Wi-Fi' : (c.via ?? '')}{c.speed
										? ` · ${c.speed} Mbit/s`
										: ''}{c.guest ? ' · guest' : ''}
								</span>
							</li>
						{/each}
					</ul>
				{/if}
			</div>
		{/if}
	</section>
{/if}
