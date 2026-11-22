<script lang="ts">
	import type { WifiClient, WifiNetwork } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { bandLabel } from '#lib/format.js';
	import { networkState } from '#lib/wifi.js';
	import Table, { type Column } from './ui/Table.svelte';
	import StateBadge from './ui/StateBadge.svelte';

	/** The Wi-Fi networks by name (wifi.lineup): the same name on several access points is one
	 * network. Its bands, encryption, the access points that carry it, its clients, whether the
	 * password is the same everywhere (compared by fingerprint), on or off. A row opens to each
	 * access point's part and, with `onaction` (the Wi-Fi page's dialogs), Change / Add to… /
	 * Remove from…. `device` shows the networks one device carries. */
	let {
		networks,
		clients = [],
		device,
		onaction,
		overTheAir = false
	}: {
		networks: WifiNetwork[];
		clients?: WifiClient[];
		device?: string;
		overTheAir?: boolean; // only what a phone nearby sees: no passwords, no clients
		onaction?: (kind: 'change' | 'add' | 'remove', net: WifiNetwork) => void;
	} = $props();

	const name = (id: string) => store.byId.get(id)?.name ?? id;
	const rows = $derived(networks.filter((n) => !device || n.devices.includes(device)));
	const canChange = $derived(store.editBlocked === null && !!onaction);
	const clientsOf = (n: WifiNetwork) =>
		clients.filter((c) => c.ssid === n.ssid && (!device || c.device === device)).length;
	const security = (n: WifiNetwork) =>
		(n.security ?? [...new Set(n.on.map((e) => e.security ?? e.encryption ?? '?'))]).join(' / ');
	const password = (n: WifiNetwork) =>
		n.on.length < 2 ? null : n.same_key == null ? 'not compared' : n.same_key ? 'one' : 'differ';

	const all: Column<WifiNetwork>[] = [
		{ key: 'ssid', label: 'Network', grow: true, cell: ssidCell },
		{ key: 'security', label: 'Encryption', priority: 2, value: security },
		{
			key: 'aps',
			label: 'Access points',
			priority: 3,
			value: (n) => n.devices.map(name).join(', ')
		},
		{
			key: 'password',
			label: 'Passwords',
			priority: 3,
			title: 'Compared by fingerprint, never shown',
			value: password
		},
		{ key: 'clients', label: 'Clients', numeric: true, width: '4.5rem', value: clientsOf },
		{ key: 'state', label: 'State', width: '7rem', cell: stateCell }
	];
	const columns = $derived(
		overTheAir ? all.filter((c) => c.key !== 'password' && c.key !== 'clients') : all
	);
</script>

{#snippet ssidCell(n: WifiNetwork)}
	<span class="flex min-w-0 items-center gap-1.5">
		<span class="truncate font-medium">{n.ssid}</span>
		{#if n.guest}<span class="rounded-md bg-surface-2 px-1.5 py-0.5 text-meta text-muted">
				guest
			</span>{/if}
	</span>
	<span class="block truncate text-meta text-muted">{n.bands.map(bandLabel).join(' · ')}</span>
{/snippet}

{#snippet stateCell(n: WifiNetwork)}<StateBadge state={networkState(n, device)} quiet />{/snippet}

{#snippet details(n: WifiNetwork)}
	<ul class="space-y-1 text-meta">
		{#each n.on.filter((e) => !device || e.device === device) as e (`${e.device}/${e.section}`)}
			<li class="flex flex-wrap gap-x-2">
				<a class="font-medium hover:underline" href="/devices/{encodeURIComponent(e.device)}">
					{name(e.device)}
				</a>
				<span class="text-muted">
					{[bandLabel(e.band) || e.radio, e.security ?? e.encryption, e.disabled ? 'off' : '']
						.filter(Boolean)
						.join(' · ')}
				</span>
				<span class="font-mono text-faint">{e.section}</span>
			</li>
		{/each}
	</ul>
	{#if canChange}
		<div class="mt-3 flex flex-wrap gap-2">
			<button class="btn btn-sm" onclick={() => onaction?.('change', n)}>Change</button>
			{#if store.devices?.some((d) => d.router?.radios?.length)}
				<button class="btn btn-sm" onclick={() => onaction?.('add', n)}>Add to…</button>
			{/if}
			{#if n.on.some((e) => e.kind === 'openwrt')}
				<button class="btn btn-sm" onclick={() => onaction?.('remove', n)}>Remove from…</button>
			{/if}
		</div>
	{/if}
{/snippet}

<Table
	{columns}
	{rows}
	key={(n) => n.ssid}
	expand={details}
	label="Wi-Fi networks"
	empty="No networks read. Kastellan reads Wi-Fi from OpenWrt devices (its login may change the wireless configuration) and FRITZ!Boxes."
/>
