<script lang="ts">
	import { more, type Device } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { bandLabel } from '#lib/format.js';
	import FritzMesh from './FritzMesh.svelte';
	import RoamingInfo from './RoamingInfo.svelte';

	/** A device's Wi-Fi, whatever makes it: a card per OpenWrt radio (its networks and who is on
	 * them) or per FRITZ!Box network; roaming (usteer) and the FRITZ!Mesh below. Names,
	 * passwords and channels are changed on the Wi-Fi page (or under Setup). */
	let { device, canChange = false }: { device: Device; canChange?: boolean } = $props();

	const ENC: Record<string, string> = {
		'11i': 'WPA2',
		WPAand11i: 'WPA/WPA2',
		'11iandWPA3': 'WPA2/WPA3',
		WPA3: 'WPA3',
		None: 'open'
	};
	const radios = $derived(device.router?.radios ?? []);
	const fritz = $derived(device.fritz?.wifi ?? []);
	const wps = $derived(fritz.some((w) => w.wps));
	const clientsOf = (r: (typeof radios)[number]) =>
		r.interfaces.reduce((n, i) => n + i.clients.length, 0);
	let note = $state<{ ok: boolean; text: string } | null>(null);

	async function wpsOff() {
		try {
			await more.fritzWpsOff(device.id);
			note = { ok: true, text: 'WPS is off; it shows within a minute.' };
		} catch (e) {
			note = { ok: false, text: (e as Error).message };
		}
	}
</script>

{#if radios.length || fritz.length}
	<section class="mt-8">
		<div class="flex flex-wrap items-baseline justify-between gap-2">
			<h2 class="text-sm font-semibold">Wi-Fi</h2>
			{#if store.me?.role === 'admin'}
				<a class="text-xs text-muted hover:text-text" href="/wifi">
					Names, passwords and channels: on the Wi-Fi page
				</a>
			{/if}
		</div>
		<div class="mt-3 grid gap-3 md:grid-cols-2">
			{#each radios as radio (radio.name)}
				{@const n = clientsOf(radio)}
				<div class="card p-4" class:opacity-60={radio.disabled}>
					<div class="flex items-baseline justify-between gap-2">
						<p class="text-sm font-medium">
							{bandLabel(radio.band) || radio.name}
							<span class="font-normal text-muted">
								· channel {radio.channel ?? 'auto'}{radio.htmode ? ` · ${radio.htmode}` : ''}
							</span>
						</p>
						<span class="tabular text-xs text-muted">
							{radio.disabled ? 'off' : `${n} ${n === 1 ? 'client' : 'clients'}`}
						</span>
					</div>
					<ul class="mt-2 space-y-2">
						{#each radio.interfaces as iface (iface.section)}
							<li class="text-sm">
								<span class="font-medium">{iface.ssid ?? iface.section}</span>
								<span class="text-xs text-muted">{iface.mode}</span>
								{#if iface.clients.length}
									<ul class="mt-1 space-y-0.5 text-xs">
										{#each iface.clients as c (c.mac)}
											<li class="flex justify-between gap-3">
												<span class="truncate">{c.name ?? c.mac}</span>
												{#if c.signal != null}<span class="tabular shrink-0 text-muted">
														{c.signal} dBm
													</span>{/if}
											</li>
										{/each}
									</ul>
								{/if}
							</li>
						{/each}
					</ul>
					<p class="mt-2 font-mono text-[11px] text-faint">{radio.name}</p>
				</div>
			{/each}
			{#each fritz as w (w.index)}
				<div class="card p-4" class:opacity-60={!w.enabled}>
					<div class="flex items-baseline justify-between gap-2">
						<p class="truncate text-sm font-medium">
							{w.ssid ?? `network ${w.index}`}
							{#if w.guest}<span class="text-xs font-normal text-muted">· guest</span>{/if}
						</p>
						<span class="tabular shrink-0 text-xs text-muted">
							{!w.enabled ? 'off' : `${w.clients} ${w.clients === 1 ? 'client' : 'clients'}`}
						</span>
					</div>
					<p class="mt-1 text-xs text-muted">
						{bandLabel(w.band)} · channel {w.channel || 'auto'} · {ENC[w.encryption ?? ''] ??
							w.encryption}
					</p>
					{#if w.associated?.length}
						<ul class="mt-2 space-y-0.5 text-xs">
							{#each w.associated as a, i (a.mac ?? i)}
								<li class="flex justify-between gap-3">
									<span class="truncate">{a.address ?? a.mac}</span>
									<span class="tabular shrink-0 text-muted">
										{a.signal != null ? `${a.signal} %` : ''}{a.speed ? ` · ${a.speed} Mbit/s` : ''}
									</span>
								</li>
							{/each}
						</ul>
					{/if}
				</div>
			{/each}
		</div>
		{#if wps}
			<p class="mt-3 text-xs" style:color="var(--warning-text)">
				WPS is on (a button or a PIN lets devices in without the password).
				{#if canChange}<button class="ml-1 underline" onclick={wpsOff}>Turn it off</button>{/if}
			</p>
		{/if}
		{#if note}<p
				class="mt-2 text-xs"
				style:color={note.ok ? 'var(--good-text)' : 'var(--critical-text)'}
			>
				{note.text}
			</p>{/if}
		{#if device.router && store.me?.role === 'admin'}
			<div class="card mt-3 px-4 pb-4 empty:hidden"><RoamingInfo device={device.id} /></div>
		{/if}
		{#if device.fritz?.mesh?.members.length}
			<div class="card mt-3 p-4">
				<h3 class="text-xs font-medium text-muted">FRITZ!Mesh</h3>
				<FritzMesh mesh={device.fritz.mesh} />
			</div>
		{/if}
	</section>
{/if}
