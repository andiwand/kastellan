<script lang="ts">
	import { onMount, tick } from 'svelte';
	import { more, type WifiFinding, type WifiNetwork, type WifiOverview } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { issuesIn } from '#lib/attention.js';
	import { ago, bandLabel } from '#lib/format.js';
	import { count } from '#lib/glossary.js';
	import { accessPoints, busiest, openFindings } from '#lib/wifi.js';
	import PageHeader from '#lib/components/ui/PageHeader.svelte';
	import Section from '#lib/components/ui/Section.svelte';
	import Tile from '#lib/components/ui/Tile.svelte';
	import EmptyState from '#lib/components/ui/EmptyState.svelte';
	import JobDialog from '#lib/components/JobDialog.svelte';
	import WifiFindings from '#lib/components/WifiFindings.svelte';
	import WifiClientsTable from '#lib/components/WifiClientsTable.svelte';
	import AccessPointsTable from '#lib/components/AccessPointsTable.svelte';
	import WifiNetworksTable from '#lib/components/WifiNetworksTable.svelte';
	import ChannelChart from '#lib/components/ChannelChart.svelte';
	import ChannelPlan from '#lib/components/ChannelPlan.svelte';
	import Radar from '@lucide/svelte/icons/radar';

	/** Wi-Fi by network, not by device (wifi.py): is it fine (the summary and the findings, each
	 * with its fix), who is on it and how well (clients), the access points and their radios,
	 * the networks, how busy the channels are, and a channel plan from that. */
	let data = $state<WifiOverview | null>(null);
	let error = $state<string | null>(null);
	let job = $state<
		| { kind: 'change'; net: WifiNetwork }
		| { kind: 'add'; net: WifiNetwork }
		| { kind: 'remove'; net: WifiNetwork }
		| { kind: 'scan' }
		| null
	>(null);
	let clientFilter = $state<'all' | 'weak'>('all');
	let planner = $state<ReturnType<typeof ChannelPlan>>();

	// The forms' fields.
	let newName = $state('');
	let newKey = $state('');
	let encryption = $state('');
	let enabled = $state<'' | 'on' | 'off'>('');
	let targets = $state<Set<string>>(new Set());

	async function load() {
		try {
			data = await more.wifiOverview();
			error = null;
		} catch (e) {
			error = (e as Error).message;
		}
	}
	onMount(() => {
		load();
		const t = setInterval(load, 60_000);
		return () => clearInterval(t);
	});

	function open(kind: 'change' | 'add' | 'remove', net: WifiNetwork) {
		newName = '';
		newKey = '';
		encryption = '';
		enabled = '';
		targets = new Set();
		job = { kind, net };
	}
	async function go(id: string) {
		await tick();
		document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
	}
	function act(f: WifiFinding) {
		const a = f.action;
		if (!a) return;
		if (a.kind === 'plan') {
			go('channel-plan');
			planner?.suggest();
		} else if (a.kind === 'clients') {
			clientFilter = 'weak';
			go('clients');
		} else if (a.kind === 'scan') job = { kind: 'scan' };
		else if (a.kind === 'radios') go('access-points');
		else if (a.kind === 'network') {
			const net = data?.networks.find((n) => n.ssid === a.ssid);
			if (net) open('change', net);
		}
	}

	const name = (id: string) => store.byId.get(id)?.name ?? id;
	const canChange = $derived(store.editBlocked === null);
	const issues = $derived(issuesIn(store.attention, 'wifi'));
	const findings = $derived(
		openFindings(
			data?.findings ?? [],
			issues.map((i) => i.key)
		)
	);
	const clients = $derived(data?.clients ?? []);
	const weak = $derived(clients.filter((c) => c.issues.length).length);
	const aps = $derived(
		data ? accessPoints(data.radios, data.networks, store.devices ?? [], name) : []
	);
	const apCount = $derived(new Set(aps.map((a) => a.device ?? a.key)).size);
	const top = $derived(data ? busiest(data.scans) : null);
	const bands = $derived(
		['2g', '5g'].filter(
			(b) =>
				data?.radios.some((r) => r.band === b) ||
				Object.values(data?.scans ?? {}).some((s) => s.band === b)
		)
	);
	const newestScan = $derived(
		Math.max(0, ...Object.values(data?.scans ?? {}).map((s) => s.t ?? 0))
	);
	const openwrtRadios = $derived((data?.radios ?? []).filter((r) => r.kind === 'openwrt'));
	const ENCRYPTIONS = [
		['sae-mixed', 'WPA2/WPA3'],
		['sae', 'WPA3'],
		['psk2', 'WPA2'],
		['psk-mixed', 'WPA/WPA2 (old devices)']
	];
	const input =
		'h-9 w-full rounded-lg border border-line bg-surface px-3 text-sm outline-none focus:border-accent';
	const label = 'flex flex-col gap-1 text-xs font-medium text-muted';
</script>

<svelte:head><title>Wi-Fi · Kastellan</title></svelte:head>

<PageHeader
	title="Wi-Fi"
	summary={data?.over_the_air
		? 'Networks and access points as a phone nearby would see them. Admins see the clients too.'
		: 'Networks, access points and clients from every OpenWrt device and FRITZ!Box.'}
>
	{#snippet actions()}
		<button
			class="btn btn-sm"
			disabled={!canChange || !!data?.scanning.length}
			title={canChange
				? 'Every radio listens for its neighbours'
				: `Can't scan here: ${store.editBlocked}`}
			onclick={() => (job = { kind: 'scan' })}
		>
			<Radar class="size-3.5" />
			{data?.scanning.length ? 'Scanning…' : 'Scan now'}
		</button>
	{/snippet}
</PageHeader>

{#if error && !data}
	<EmptyState kind="error" text={error} onretry={load} />
{:else if data === null}
	<div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
		{#each [0, 1, 2, 3] as i (i)}<div class="card h-24 animate-pulse"></div>{/each}
	</div>
{:else}
	<div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
		<Tile
			label="Networks"
			value={data.networks.length}
			sub={data.networks.some((n) => n.guest)
				? `${count(data.networks.filter((n) => n.guest).length, 'guest network')}`
				: undefined}
			href="#networks"
		/>
		<Tile
			label="Access points"
			value={apCount}
			sub={count(data.radios.length, 'radio')}
			href="#access-points"
		/>
		{#if !data.over_the_air}<Tile
				label="Clients"
				value={clients.length}
				state={weak ? { word: `${weak} weak`, tone: 'warning' } : undefined}
				sub={weak ? 'with a weak or slow link' : 'none with a weak link'}
				href="#clients"
			/>{/if}
		<Tile
			label="Busiest channel"
			value={top?.channel ?? null}
			sub={top ? `${top.busy} % busy · ${bandLabel(top.band)}` : 'not scanned yet'}
			href="#channels"
		/>
	</div>

	<Section title="Findings" count={issues.length + findings.length} id="findings">
		{#if issues.length || findings.length}
			<WifiFindings findings={data.findings ?? []} {issues} onaction={act} />
		{:else}
			<EmptyState text="Nothing to look at: the networks, channels and roaming are in order." />
		{/if}
	</Section>

	{#if clients.length}
		<Section title="Clients" count={clients.length} id="clients" meta="OpenWrt and FRITZ!Box">
			<WifiClientsTable {clients} bind:filter={clientFilter} />
		</Section>
	{/if}

	<Section
		title="Access points"
		count={apCount}
		id="access-points"
		meta={data.countries?.length ? `Country ${data.countries.join(', ')}` : undefined}
	>
		<AccessPointsTable
			radios={data.radios}
			networks={data.networks}
			onchanged={load}
			overTheAir={data.over_the_air}
		/>
	</Section>

	<Section
		title="Networks"
		count={data.networks.length}
		id="networks"
		meta={data.over_the_air ? undefined : 'Passwords are compared by fingerprint, never shown'}
	>
		<WifiNetworksTable
			networks={data.networks}
			{clients}
			onaction={canChange ? open : undefined}
			overTheAir={data.over_the_air}
		/>
	</Section>

	{#if bands.length}
		<Section
			title="Channel use"
			id="channels"
			meta={(newestScan
				? `Scanned ${ago(new Date(newestScan * 1000).toISOString(), store.now)}`
				: 'Not scanned yet') +
				(data.scan_at ? ` · daily at ${data.scan_at}` : ' · only when asked')}
		>
			{#if Object.keys(data.scans).length}
				<div class="grid gap-4 xl:grid-cols-2">
					{#each bands as band (band)}
						<ChannelChart {band} scans={data.scans} radios={data.radios} />
					{/each}
				</div>
			{:else}
				<EmptyState text="No scan in the last week: scan to see how busy each channel is." />
			{/if}
		</Section>
	{/if}

	{#if data.radios.length && !data.over_the_air}
		<Section
			title="Channel plan"
			id="channel-plan"
			meta="A suggestion from the newest scans; nothing changes until applied"
		>
			<ChannelPlan bind:this={planner} plans={data.plans} onapplied={load} />
		</Section>
	{/if}
{/if}

{#if job?.kind === 'change'}
	{@const net = job.net}
	<JobDialog
		title="Change “{net.ssid}”"
		path="/wifi/networks/change"
		action="Change everywhere"
		body={() => {
			const b = {
				ssid: net.ssid,
				new_ssid: newName.trim() || undefined,
				key: newKey || undefined,
				encryption: encryption || undefined,
				enabled: enabled === '' ? undefined : enabled === 'on'
			};
			return b.new_ssid || b.key || b.encryption || b.enabled !== undefined ? b : null;
		}}
		onclose={() => (job = null)}
		ondone={load}
	>
		<p class="text-muted">
			On {net.devices.map(name).join(', ')}, one after the other. Leave a field empty to keep it.
		</p>
		<label class={label}>
			New name <input class={input} bind:value={newName} maxlength="32" />
		</label>
		<label class={label}>
			New password
			<input class={input} type="password" bind:value={newKey} autocomplete="new-password" />
		</label>
		<label class={label}>
			Encryption
			<select class={input} bind:value={encryption}>
				<option value="">as it is</option>
				{#each ENCRYPTIONS as [v, l] (v)}<option value={v}>{l}</option>{/each}
			</select>
		</label>
		<label class={label}>
			On or off
			<select class={input} bind:value={enabled}>
				<option value="">as it is</option>
				<option value="on">on</option>
				<option value="off">off</option>
			</select>
		</label>
		<p class="text-xs text-muted">
			Devices connected to it need the new name or password too; you may lose the page if you are on
			this network.
		</p>
	</JobDialog>
{:else if job?.kind === 'add'}
	{@const net = job.net}
	<JobDialog
		title="Add “{net.ssid}” to more access points"
		path="/wifi/networks/add"
		action="Add"
		body={() =>
			targets.size && (newKey.length >= 8 || encryption === 'none')
				? {
						ssid: net.ssid,
						key: newKey,
						encryption: encryption || 'sae-mixed',
						targets: [...targets].map((t) => ({ device: t.split('/')[0], radio: t.split('/')[1] }))
					}
				: null}
		onclose={() => (job = null)}
		ondone={load}
	>
		<p class="text-muted">On which radios (OpenWrt):</p>
		{#each openwrtRadios as r (`${r.device}/${r.radio}`)}
			{@const key = `${r.device}/${r.radio}`}
			{@const has = net.on.some((e) => e.device === r.device && e.radio === r.radio)}
			<label class="flex items-center gap-2 {has ? 'opacity-50' : ''}">
				<input
					type="checkbox"
					disabled={has}
					checked={targets.has(key)}
					onchange={(e) => {
						const next = new Set(targets);
						if ((e.currentTarget as HTMLInputElement).checked) next.add(key);
						else next.delete(key);
						targets = next;
					}}
				/>
				{name(r.device)} <span class="text-xs text-muted">{bandLabel(r.band)} ({r.radio})</span>
				{#if has}<span class="text-xs text-faint">has it</span>{/if}
			</label>
		{/each}
		<label class={label}>
			Its password (the same as on the others)
			<input class={input} type="password" bind:value={newKey} autocomplete="off" />
		</label>
		<label class={label}>
			Encryption
			<select class={input} bind:value={encryption}>
				{#each ENCRYPTIONS as [v, l] (v)}<option value={v}>{l}</option>{/each}
			</select>
		</label>
	</JobDialog>
{:else if job?.kind === 'remove'}
	{@const net = job.net}
	<JobDialog
		title="Remove “{net.ssid}”"
		path="/wifi/networks/remove"
		action="Remove"
		body={() =>
			targets.size
				? {
						ssid: net.ssid,
						targets: [...targets].map((t) => ({ device: t.split('/')[0], radio: t.split('/')[1] }))
					}
				: null}
		onclose={() => (job = null)}
		ondone={load}
	>
		<p class="text-muted">From which access points (OpenWrt; switch a FRITZ!Box's off instead):</p>
		{#each net.on.filter((e) => e.kind === 'openwrt') as e (`${e.device}/${e.section}`)}
			{@const key = `${e.device}/${e.section}`}
			<label class="flex items-center gap-2">
				<input
					type="checkbox"
					checked={targets.has(key)}
					onchange={(ev) => {
						const next = new Set(targets);
						if ((ev.currentTarget as HTMLInputElement).checked) next.add(key);
						else next.delete(key);
						targets = next;
					}}
				/>
				{name(e.device)}
				<span class="text-xs text-muted">{bandLabel(e.band)} ({e.section})</span>
			</label>
		{/each}
	</JobDialog>
{:else if job?.kind === 'scan'}
	<JobDialog
		title="Scan for Wi-Fi networks"
		path="/wifi/scan"
		action="Scan"
		body={() => ({})}
		onclose={() => (job = null)}
		ondone={load}
	>
		<p>
			Every OpenWrt radio whose login may scan listens for its neighbours and how busy each channel
			is; FRITZ!Boxes say what they heard. Clients may notice a pause of a second or two.
		</p>
	</JobDialog>
{/if}
