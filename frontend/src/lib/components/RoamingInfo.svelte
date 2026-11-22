<script lang="ts">
	import { more, type RoamingView } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { bandOfFrequency, duration } from '#lib/format.js';
	import { onMount } from 'svelte';

	/** How clients roam at this access point, as usteer here sees it (the `roaming` ability):
	 * its own networks and the other access points it hears of, roams away and here, its clients
	 * with their signal, its thresholds. Shown only when read. */
	let { device }: { device: string } = $props();
	let view = $state<RoamingView | null>(null);
	onMount(async () => {
		try {
			view = await more.deviceRoaming(device);
		} catch {
			view = null;
		}
	});
	const name = (id: string | null) => (id ? (store.byId.get(id)?.name ?? id) : null);
	const LABELS: Record<string, string> = {
		min_snr: 'kick below SNR',
		min_connect_snr: 'refuse below SNR',
		roam_scan_snr: 'look around below SNR',
		roam_trigger_snr: 'steer below SNR',
		signal_diff_threshold: 'better by (dB)',
		band_steering_threshold: 'band steering',
		load_kick_enabled: 'kick when busy',
		assoc_steering: 'steer at connect',
		probe_steering: 'steer at probe'
	};
</script>

{#if view?.read}
	<h3 class="mt-5 text-xs font-medium text-muted">Roaming (usteer)</h3>
	{#if view.error}
		<p class="mt-1 text-xs" style:color="var(--warning-text)">{view.error}</p>
	{/if}
	<ul class="mt-2 space-y-1 text-sm">
		{#each view.nodes ?? [] as n (n.node)}
			<li class="flex flex-wrap items-baseline gap-x-3">
				<span class="font-medium">
					{n.own ? 'Here' : (name(n.device) ?? n.host)}
				</span>
				<span class="text-xs text-muted">{n.ssid ?? n.iface} · {bandOfFrequency(n.freq)}</span>
				<span class="tabular text-xs text-muted">
					{n.clients ?? 0} clients{#if n.load != null}
						· {n.load} % busy{/if}
				</span>
				{#if n.own && (n.roam_source != null || n.roam_target != null)}
					<span class="tabular text-xs text-faint">
						{n.roam_source ?? 0} roamed away · {n.roam_target ?? 0} came here
					</span>
				{/if}
			</li>
		{/each}
	</ul>
	{#if view.clients?.length}
		<ul class="mt-2 grid gap-x-6 text-xs sm:grid-cols-2">
			{#each view.clients as c (c.mac)}
				<li class="flex justify-between gap-2 py-0.5">
					<span class="truncate">{c.name ?? c.mac}</span>
					<span
						class="tabular"
						style:color={c.signal != null && c.signal < -75 ? 'var(--warning-text)' : undefined}
					>
						{c.signal ?? '–'} dBm{#if c.connected != null}<span class="text-faint">
								· {duration(c.connected / 1000)}
							</span>{/if}
					</span>
				</li>
			{/each}
		</ul>
	{/if}
	{#if view.config && Object.keys(view.config).length}
		<p class="mt-2 text-xs text-faint">
			{Object.entries(view.config)
				.map(([k, v]) => `${LABELS[k] ?? k}: ${v === true ? 'on' : v === false ? 'off' : v}`)
				.join(' · ')}
		</p>
	{/if}
{/if}
