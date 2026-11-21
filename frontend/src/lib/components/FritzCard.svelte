<script lang="ts">
	import DhcpPool from './DhcpPool.svelte';
	import { more, type FritzReport } from '#lib/api.js';
	import { ago, duration } from '#lib/format.js';
	import { store } from '#lib/state.svelte.js';
	import FritzMesh from './FritzMesh.svelte';
	import FritzWireguard from './FritzWireguard.svelte';

	/** A FRITZ!Box through TR-064 with Kastellan's user: the internet line, Wi-Fi, who is
	 * connected, and the ways in from the internet (port forwards, remote access, MyFRITZ!). */
	let {
		device,
		report,
		checked,
		error,
		canChange
	}: {
		device: string;
		report: FritzReport;
		checked: string | null;
		error: string | null;
		canChange: boolean;
	} = $props();

	let note = $state<{ ok: boolean; text: string } | null>(null);
	let showClients = $state(false);
	const net = $derived(report.internet ?? {});
	const dsl = $derived(net.dsl);
	const wps = $derived(report.wifi.some((w) => w.wps));
	const forwards = $derived((report.forwards ?? []).filter((f) => f.enabled));

	const mbit = (bits: number | null | undefined) =>
		bits == null
			? '–'
			: bits >= 1e9
				? `${(bits / 1e9).toFixed(1)} Gbit/s`
				: `${(bits / 1e6).toFixed(bits < 1e7 ? 1 : 0)} Mbit/s`;
	const BAND: Record<string, string> = { '2g': '2.4 GHz', '5g': '5 GHz', '6g': '6 GHz' };
	const ENC: Record<string, string> = {
		'11i': 'WPA2',
		WPAand11i: 'WPA/WPA2',
		'11iandWPA3': 'WPA2/WPA3',
		WPA3: 'WPA3',
		None: 'open'
	};

	async function wpsOff() {
		try {
			await more.fritzWpsOff(device);
			note = { ok: true, text: 'WPS is off; the card updates within a minute.' };
		} catch (e) {
			note = { ok: false, text: (e as Error).message };
		}
	}
</script>

<section class="card mt-8 p-5">
	<div class="flex flex-wrap items-baseline justify-between gap-2">
		<h2 class="text-sm font-semibold">FRITZ!Box</h2>
		<p class="text-xs text-muted">
			{#if error}<span style:color="var(--warning-text)">{error}</span> ·
			{/if}through TR-064{checked ? `, ${ago(checked, store.now)}` : ''}
		</p>
	</div>

	<dl class="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
		<div>
			<dt class="text-xs text-muted">Internet</dt>
			<dd>
				<span
					style:color={net.connection === 'Connected' ? 'var(--good-text)' : 'var(--critical-text)'}
				>
					{net.connection === 'Connected'
						? 'connected'
						: (net.connection ?? 'unknown').toLowerCase()}
				</span>
				{#if net.connected_for}<span class="text-xs text-muted">
						· {duration(net.connected_for)}
					</span>{/if}
			</dd>
		</div>
		<div>
			<dt class="text-xs text-muted">Public address</dt>
			<dd class="truncate font-mono text-xs" title={net.address_v6 ?? ''}>{net.address ?? '–'}</dd>
		</div>
		<div>
			<dt class="text-xs text-muted">{net.access ?? 'Line'} · in use now</dt>
			<dd class="tabular">
				↓ {mbit(net.rate?.down)} · ↑ {mbit(net.rate?.up)}
			</dd>
		</div>
		<div>
			<dt class="text-xs text-muted">FRITZ!OS</dt>
			<dd>
				{report.firmware ?? '–'}
				{#if report.update?.available}<span class="text-xs" style:color="var(--warning-text)">
						· {report.update.version ?? 'an update'} is out
					</span>{/if}
			</dd>
		</div>
	</dl>

	{#if dsl}
		<h3 class="mt-5 text-xs font-medium text-muted">DSL line</h3>
		<p class="mt-1 text-sm">
			<span class="tabular">
				↓ {mbit((dsl.down ?? 0) * 1000)} of {mbit((dsl.max_down ?? 0) * 1000)} possible · ↑ {mbit(
					(dsl.up ?? 0) * 1000
				)} of {mbit((dsl.max_up ?? 0) * 1000)}
			</span>
			<span class="text-xs text-muted">
				· noise margin ↓ {dsl.snr_down != null ? (dsl.snr_down / 10).toFixed(1) : '–'} dB, ↑
				{dsl.snr_up != null ? (dsl.snr_up / 10).toFixed(1) : '–'} dB
				{#if dsl.crc != null}· {dsl.crc.toLocaleString()} CRC errors, {dsl.errored_seconds ?? 0} errored
					seconds{/if}
			</span>
		</p>
	{/if}

	{#if report.wifi.length}
		<h3 class="mt-5 text-xs font-medium text-muted">Wi-Fi</h3>
		<ul class="mt-2 space-y-1 text-sm">
			{#each report.wifi as w (w.index)}
				<li class="flex flex-wrap items-baseline gap-x-2">
					<span class="font-medium">{w.ssid ?? `network ${w.index}`}</span>
					<span class="text-xs text-muted">
						{w.band ? BAND[w.band] : ''} · channel {w.channel || 'auto'} · {ENC[
							w.encryption ?? ''
						] ?? w.encryption}
						· {w.clients}
						{w.clients === 1 ? 'client' : 'clients'}{#if w.associated?.length}
							(signal {Math.min(...w.associated.map((a) => a.signal ?? 100))}–{Math.max(
								...w.associated.map((a) => a.signal ?? 0)
							)} %){/if}
						{#if w.guest}· guest network{/if}
					</span>
					{#if !w.enabled}<span class="text-xs text-faint">off</span>{/if}
				</li>
			{/each}
		</ul>
		{#if wps}
			<p class="mt-2 text-xs" style:color="var(--warning-text)">
				WPS is on (a button or a PIN lets devices in without the password).
				{#if canChange}<button class="ml-1 underline" onclick={wpsOff}>Turn it off</button>{/if}
			</p>
		{/if}
		<p class="mt-1 text-xs text-faint">Names, passwords and channels: on the Wi-Fi page.</p>
	{/if}

	{#if report.dhcp}
		<h3 class="mt-5 text-xs font-medium text-muted">DHCP</h3>
		<DhcpPool pool={report.dhcp} />
	{/if}

	{#if report.mesh?.members.length}
		<h3 class="mt-5 text-xs font-medium text-muted">FRITZ!Mesh</h3>
		<FritzMesh mesh={report.mesh} />
	{/if}
	{#if report.wireguard}
		<h3 class="mt-5 text-xs font-medium text-muted">WireGuard</h3>
		<FritzWireguard wg={report.wireguard} />
	{/if}

	<h3 class="mt-5 text-xs font-medium text-muted">Ways in from the internet</h3>
	<ul class="mt-1 space-y-0.5 text-sm">
		{#each forwards as f, i (i)}
			<li>
				<span style:color="var(--warning-text)">Port forward</span>
				<span class="font-mono text-xs">{f.protocol} {f.port} → {f.to}:{f.to_port}</span>
				{#if f.name}<span class="text-xs text-muted">({f.name})</span>{/if}
			</li>
		{/each}
		{#if report.remote.remote_access}
			<li style:color="var(--warning-text)">
				Remote access to the FRITZ!Box (HTTPS{report.remote.remote_port
					? `, port ${report.remote.remote_port}`
					: ''})
			</li>
		{/if}
		{#if report.remote.myfritz}<li class="text-muted">MyFRITZ! is on</li>{/if}
		{#if !forwards.length && !report.remote.remote_access && !report.remote.myfritz}
			<li class="text-muted">
				{report.forwards === null ||
				report.remote.remote_access == null ||
				report.remote.myfritz == null
					? 'Some access settings could not be read.'
					: 'None.'}
			</li>
		{/if}
	</ul>

	{#if report.clients.length}
		<h3 class="mt-5 text-xs font-medium text-muted">
			Connected · {report.clients.length}
			<button class="ml-1 font-normal underline" onclick={() => (showClients = !showClients)}>
				{showClients ? 'hide' : 'show'}
			</button>
		</h3>
		{#if showClients}
			<ul class="mt-2 grid gap-x-6 gap-y-0.5 text-sm sm:grid-cols-2">
				{#each report.clients as c, i (c.mac ?? i)}
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
	{/if}

	{#if Object.keys(report.errors ?? {}).length}
		<p class="mt-4 text-xs text-faint">
			Couldn't read: {Object.entries(report.errors)
				.map(([k, v]) => `${k} (${v})`)
				.join('; ')}
		</p>
	{/if}
	{#if note}
		<p class="mt-2 text-xs" style:color={note.ok ? 'var(--good-text)' : 'var(--critical-text)'}>
			{note.text}
		</p>
	{/if}
</section>
