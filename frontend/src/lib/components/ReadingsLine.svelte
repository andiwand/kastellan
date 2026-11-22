<script lang="ts">
	import type { Device } from '#lib/api.js';
	import { ago, duration } from '#lib/format.js';
	import { store } from '#lib/state.svelte.js';
	import ReadAgain from './ReadAgain.svelte';

	/** Where the device page's readings come from and how fresh they are (OpenWrt's API, TR-064,
	 * the collector), how long it has been up, and what couldn't be read. */
	let { device }: { device: Device } = $props();

	const sources = $derived(
		[
			device.openwrt || device.router
				? { name: "OpenWrt's API", at: device.router_checked, error: device.router_error }
				: null,
			device.fritzbox || device.fritz
				? { name: 'TR-064', at: device.fritz_checked, error: device.fritz_error }
				: null,
			device.collect || device.host_report
				? { name: 'the collector', at: device.host_checked, error: device.host_error }
				: null
		].filter((s) => s !== null)
	);
	const uptime = $derived(
		device.router?.system.uptime ?? device.fritz?.uptime ?? device.host_report?.system?.uptime
	);
	const unread = $derived(
		Object.entries({
			...(device.router?.errors ?? {}),
			...(device.fritz?.errors ?? {}),
			...(device.host_report?.errors ?? {})
		})
	);
	const kernel = $derived(device.host_report?.system?.kernel);
</script>

{#if sources.length}
	<p class="mt-4 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
		{#if uptime != null}<span>Up {duration(uptime)}</span><span class="text-faint">·</span>{/if}
		{#if kernel}<span class="font-mono" title="Kernel">{kernel}</span><span class="text-faint">
				·
			</span>{/if}
		{#each sources as s, i (s.name)}
			<span>
				{#if s.error}<span style:color="var(--warning-text)">{s.name}: {s.error}</span>{:else}read
					through {s.name}{s.at ? `, ${ago(s.at, store.now)}` : ''}{/if}
			</span>
			{#if s.name === 'the collector'}<ReadAgain device={device.id} />{/if}
			{#if i < sources.length - 1}<span class="text-faint">·</span>{/if}
		{/each}
		{#if unread.length}
			<span class="text-faint">·</span>
			<span class="text-faint">
				couldn't read {unread.map(([k, v]) => `${k} (${v})`).join('; ')}
			</span>
		{/if}
	</p>
{/if}
