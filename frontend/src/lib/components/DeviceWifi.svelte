<script lang="ts">
	import { more, type Device, type WifiOverview } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { issuesIn } from '#lib/attention.js';
	import { openFindings } from '#lib/wifi.js';
	import Section from './ui/Section.svelte';
	import EmptyState from './ui/EmptyState.svelte';
	import WifiFindings from './WifiFindings.svelte';
	import AccessPointsTable from './AccessPointsTable.svelte';
	import WifiClientsTable from './WifiClientsTable.svelte';
	import WifiNetworksTable from './WifiNetworksTable.svelte';
	import RoamingInfo from './RoamingInfo.svelte';

	/** A device's Wi-Fi on its page: the Wi-Fi page's findings, access points (its radios, and a
	 * FRITZ!Box's mesh repeaters), clients and networks, kept to this device; then how clients
	 * roam here (usteer). Refreshed every minute and after a radio changed.
	 * Names, passwords and channel plans are changed on the Wi-Fi page. Those who may only look see
	 * what a phone nearby would: no clients. */
	let { device }: { device: Device } = $props();

	const admin = $derived(store.me?.role === 'admin');
	let data = $state<WifiOverview | null>(null);
	let error = $state<string | null>(null);
	let latest = 0;
	async function load() {
		const mine = ++latest;
		try {
			const answer = await more.wifiOverview();
			if (mine === latest) [data, error] = [answer, null];
		} catch (e) {
			if (mine === latest) error = (e as Error).message;
		}
	}
	// The page is keyed on the device: this runs once per device (and when one signs in).
	$effect(() => {
		load();
		const timer = setInterval(load, 60_000);
		return () => {
			clearInterval(timer);
			latest++;
		};
	});

	const issues = $derived(issuesIn(store.attention, 'wifi'));
	const anyFindings = $derived(
		issues.some((i) => i.device === device.id) ||
			!!openFindings(
				data?.findings ?? [],
				issues.map((i) => i.key),
				device.id
			).length
	);
	const clients = $derived((data?.clients ?? []).filter((c) => c.device === device.id));
	const networks = $derived((data?.networks ?? []).filter((n) => n.devices.includes(device.id)));
</script>

{#snippet toWifi()}
	<a class="hover:text-text" href="/wifi">Names, passwords and channels: Wi-Fi page</a>
{/snippet}

<Section title="Wi-Fi" id="wifi" actions={toWifi}>
	{#if error && !data}
		<EmptyState kind="error" text={error} onretry={load} />
	{:else if !data}
		<div class="card h-32 animate-pulse"></div>
	{:else}
		{#if error}<p class="mb-3 text-meta text-muted">
				Could not refresh Wi-Fi: {error}. Showing the last reading.
			</p>{/if}
		{#if anyFindings}
			<div class="mb-4">
				<WifiFindings findings={data.findings ?? []} {issues} device={device.id} />
			</div>
		{/if}
		<AccessPointsTable
			radios={data.radios}
			networks={data.networks}
			device={device.id}
			onchanged={load}
			overTheAir={data.over_the_air}
		/>
		{#if data.over_the_air}
			<!-- no clients: only admins see who is on it -->
		{:else if clients.length}
			<h3 class="mt-6 mb-2 text-meta font-medium text-muted">
				Clients <span class="tabular ml-1 font-normal text-faint">{clients.length}</span>
			</h3>
			<WifiClientsTable clients={data.clients ?? []} device={device.id} />
		{:else}
			<p class="mt-3 text-meta text-muted">No clients on it now.</p>
		{/if}
		{#if networks.length}
			<h3 class="mt-6 mb-2 text-meta font-medium text-muted">
				Networks <span class="tabular ml-1 font-normal text-faint">{networks.length}</span>
			</h3>
			<WifiNetworksTable
				networks={data.networks}
				clients={data.clients ?? []}
				device={device.id}
				overTheAir={data.over_the_air}
			/>
		{/if}
		{#if device.router && admin}
			<div class="card mt-6 px-4 pb-4 empty:hidden"><RoamingInfo device={device.id} /></div>
		{/if}
	{/if}
</Section>
