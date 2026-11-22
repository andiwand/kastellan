<script lang="ts">
	import { onMount } from 'svelte';
	import {
		more,
		type ChannelPlan,
		type WifiNetwork,
		type WifiOverview,
		type WifiRadio
	} from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { ago, bandLabel, dayClock } from '#lib/format.js';
	import JobDialog from '#lib/components/JobDialog.svelte';
	import FritzMesh from '#lib/components/FritzMesh.svelte';
	import Radar from '@lucide/svelte/icons/radar';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import KeyRound from '@lucide/svelte/icons/key-round';
	import Users from '@lucide/svelte/icons/users';

	/** Wi-Fi by network, not by device (wifi.py): each network name once with the access points
	 * that broadcast it; the radios side by side; what they hear; a channel plan from that. */
	let data = $state<WifiOverview | null>(null);
	let error = $state<string | null>(null);
	let plan = $state<ChannelPlan | null>(null);
	let planError = $state<string | null>(null);
	let dfs = $state(false);
	let chosen = $state<Set<string>>(new Set());
	let job = $state<
		| { kind: 'change'; net: WifiNetwork }
		| { kind: 'add'; net: WifiNetwork }
		| { kind: 'remove'; net: WifiNetwork }
		| { kind: 'scan' }
		| { kind: 'apply' }
		| null
	>(null);
	let editing = $state<string | null>(null); // a radio being changed: device/radio
	let radioNote = $state<string | null>(null);

	// The forms' fields.
	let newName = $state('');
	let newKey = $state('');
	let encryption = $state('');
	let enabled = $state<'' | 'on' | 'off'>('');
	let targets = $state<Set<string>>(new Set());
	let channel = $state('auto');
	let htmode = $state('');
	let country = $state('');
	let showAllClients = $state(false);

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

	async function suggest() {
		planError = null;
		try {
			plan = await more.wifiPlan(dfs);
			chosen = new Set(plan.radios.filter((r) => r.change).map((r) => `${r.device}/${r.radio}`));
		} catch (e) {
			planError = (e as Error).message;
		}
	}

	async function saveRadio(r: WifiRadio) {
		radioNote = null;
		try {
			await more.wifiRadio({
				device: r.device,
				radio: r.radio,
				channel,
				...(htmode ? { htmode } : {}),
				...(country ? { country } : {})
			});
			editing = null;
			radioNote = `${name(r.device)}: changed (the list updates within two minutes).`;
		} catch (e) {
			radioNote = (e as Error).message;
		}
	}

	function open(kind: 'change' | 'add' | 'remove', net: WifiNetwork) {
		newName = '';
		newKey = '';
		encryption = '';
		enabled = '';
		targets = new Set();
		job = { kind, net };
	}

	const name = (id: string) => store.byId.get(id)?.name ?? id;
	const ENCRYPTIONS = [
		['sae-mixed', 'WPA2/WPA3'],
		['sae', 'WPA3'],
		['psk2', 'WPA2'],
		['psk-mixed', 'WPA/WPA2 (old devices)']
	];
	const signedIn = $derived(!!store.me?.user && !store.health?.demo);
	// OpenWrt radios that don't broadcast a network yet: where it can be added.
	const openwrtRadios = $derived((data?.radios ?? []).filter((r) => r.kind === 'openwrt'));
	const widths = (r: WifiRadio) => {
		const mode = r.htmode?.match(/^(HT|VHT|HE|EHT)/)?.[0];
		if (!mode) return r.htmode ? [r.htmode] : [];
		return (r.band === '2g' || mode === 'HT' ? [20, 40] : [20, 40, 80, 160]).map(
			(width) => `${mode}${width}`
		);
	};
	const channelsOf = (r: WifiRadio) =>
		r.allowed ??
		(r.band === '2g'
			? [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
			: [36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140]);
	const loudest = (list: { signal: number | null }[]) =>
		Math.max(...list.map((n) => n.signal ?? -100));
	const SCAN_CHANNELS: Record<string, number[]> = {
		'2g': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13],
		'5g': [
			36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 149,
			153, 157, 161
		]
	};
	const input =
		'h-9 w-full rounded-lg border border-line bg-surface px-3 text-sm outline-none focus:border-accent';
	const label = 'flex flex-col gap-1 text-xs font-medium text-muted';
</script>

<svelte:head><title>Wi-Fi · Kastellan</title></svelte:head>

<h1 class="text-2xl font-semibold tracking-tight">Wi-Fi</h1>
<p class="mt-1 max-w-2xl text-sm text-muted">
	Your Wi-Fi by network: the same name on several access points is one network. From every access
	point Kastellan has a login on (OpenWrt with “Change its wireless configuration”, a FRITZ!Box).
	Passwords are compared by fingerprint, never shown.
</p>
{#if error}<p class="mt-4 text-sm" style:color="var(--critical-text)">{error}</p>{/if}
{#if data && Object.keys(data.errors).length}
	<div class="card mt-4 p-3 text-xs">
		{#each Object.entries(data.errors) as [id, e] (id)}
			<p>
				<a class="font-medium hover:underline" href="/devices/{id}?tab=setup">{name(id)}</a>:
				<span style:color="var(--warning-text)">{e}</span>
			</p>
		{/each}
	</div>
{/if}

{#if data === null}
	<div class="card mt-6 h-48 animate-pulse"></div>
{:else}
	<!-- Networks -->
	<h2 class="mt-8 mb-3 text-sm font-semibold">Networks</h2>
	{#if !data.networks.length}
		<div class="card px-5 py-6 text-sm text-muted">
			No networks known. Kastellan reads Wi-Fi from OpenWrt devices (Setup → OpenWrt login, “Change
			its wireless configuration”) and FRITZ!Boxes (Setup → FRITZ!Box login).
		</div>
	{/if}
	<div class="grid gap-4 md:grid-cols-2">
		{#each data.networks as net (net.ssid)}
			{@const clients = net.on.reduce((n, e) => n + (e.clients ?? 0), 0)}
			<div class="card flex flex-col p-4">
				<div class="flex flex-wrap items-baseline gap-2">
					<h3 class="text-base font-semibold">{net.ssid}</h3>
					{#if net.guest}<span class="rounded-md bg-surface-2 px-1.5 py-0.5 text-xs">
							guest
						</span>{/if}
					<span class="text-xs text-muted">{net.bands.map(bandLabel).join(' · ')}</span>
				</div>
				<ul class="mt-2 space-y-0.5 text-sm">
					{#each net.devices as d (d)}
						{@const entries = net.on.filter((e) => e.device === d)}
						<li class="flex flex-wrap items-baseline gap-x-2">
							<a class="hover:underline" href="/devices/{d}">{name(d)}</a>
							<span class="text-xs text-muted">
								{entries.map((e) => bandLabel(e.band) || e.radio).join(', ')}
								{#if entries[0]?.kind === 'fritzbox'}· FRITZ!Box{/if}
							</span>
							{#if net.off.includes(d)}<span class="text-xs text-faint">off</span>{/if}
						</li>
					{/each}
				</ul>
				<p class="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs">
					<span
						class="inline-flex items-center gap-1"
						style:color={net.same_key == null
							? 'var(--muted)'
							: net.same_key
								? 'var(--good-text)'
								: 'var(--warning-text)'}
					>
						<KeyRound class="size-3" />
						{net.same_key == null
							? 'password comparison unavailable'
							: net.same_key
								? 'one password'
								: 'the passwords differ'}
					</span>
					{#if !net.same_encryption}<span style:color="var(--warning-text)">
							the encryption differs
						</span>{/if}
					{#if clients}<span class="inline-flex items-center gap-1 text-muted">
							<Users class="size-3" />{clients} on the FRITZ!Box
						</span>{/if}
				</p>
				{#if signedIn}
					<div class="mt-auto flex flex-wrap gap-2 pt-3">
						<button class="btn btn-sm" onclick={() => open('change', net)}>Change</button>
						{#if openwrtRadios.length}
							<button class="btn btn-sm" onclick={() => open('add', net)}>Add to…</button>
						{/if}
						{#if net.on.some((e) => e.kind === 'openwrt')}
							<button class="btn btn-sm" onclick={() => open('remove', net)}>Remove from…</button>
						{/if}
					</div>
				{/if}
			</div>
		{/each}
	</div>

	<!-- FRITZ!Mesh: it sets itself up between AVM devices; shown as the boxes report it. -->
	{#each (store.devices ?? []).filter((d) => d.fritz?.mesh?.members.length) as d (d.id)}
		<h2 class="mt-8 mb-3 text-sm font-semibold">FRITZ!Mesh · {d.name}</h2>
		<div class="card p-4"><FritzMesh mesh={d.fritz!.mesh!} /></div>
	{/each}

	<!-- Radios -->
	<div class="mt-10 mb-3 flex flex-wrap items-baseline justify-between gap-2">
		<h2 class="text-sm font-semibold">Radios</h2>
		{#if signedIn}
			<button class="btn btn-sm" onclick={() => (job = { kind: 'scan' })}>
				<Radar class="size-3.5" /> Scan now
			</button>
		{/if}
	</div>
	{#if radioNote}<p class="mb-2 text-xs text-muted">{radioNote}</p>{/if}
	{#if data.country_note}
		<p class="mb-2 text-xs" style:color="var(--warning-text)">
			Country: {data.country_note}. It decides which channels and how much power are allowed.
		</p>
	{:else if data.countries?.length}
		<p class="mb-2 text-xs text-muted">Country: {data.countries.join(', ')}.</p>
	{/if}
	<div class="card overflow-x-auto">
		<table class="w-full min-w-[40rem] text-sm">
			<thead class="text-left text-xs text-muted">
				<tr class="border-b border-line">
					<th class="px-4 py-2 font-medium">Access point</th>
					<th class="font-medium">Band</th>
					<th class="font-medium">Channel</th>
					<th class="font-medium">Width</th>
					<th class="font-medium">Clients</th>
					<th class="font-medium">Heard</th>
					<th class="font-medium">Busy</th>
					<th></th>
				</tr>
			</thead>
			<tbody>
				{#each data.radios as r (`${r.device}/${r.radio}`)}
					{@const key = `${r.device}/${r.radio}`}
					<tr class="border-b border-line last:border-0">
						<td class="px-4 py-2">
							<a class="font-medium hover:underline" href="/devices/{r.device}">{name(r.device)}</a>
							<span class="font-mono text-xs text-faint">{r.radio}</span>
						</td>
						<td>{bandLabel(r.band)}</td>
						<td class="tabular">{r.channel ?? 'auto'}</td>
						<td class="tabular">{r.width} MHz</td>
						<td class="tabular">{r.clients ?? '–'}</td>
						<td class="text-xs">
							{#if r.scanned}
								{r.neighbours} nearby{#each Object.entries(r.hears) as [k, s] (k)}
									<br /><span class="text-muted">{name(k.split('/')[0])} {s} dBm</span>
								{/each}
							{:else}<span class="text-faint">not scanned</span>{/if}
						</td>
						<td class="tabular text-xs">{r.busy != null ? `${r.busy} %` : '–'}</td>
						<td class="pr-4 text-right">
							{#if signedIn && editing !== key}
								<button
									class="text-xs text-muted underline hover:text-text"
									onclick={() => {
										editing = key;
										channel = String(r.channel ?? 'auto');
										htmode = '';
										country = '';
									}}
								>
									change
								</button>
							{/if}
						</td>
					</tr>
					{#if editing === key}
						<tr class="border-b border-line bg-surface-2">
							<td colspan="8" class="px-4 py-3">
								<div class="flex flex-wrap items-end gap-3">
									<label class={label}>
										Channel
										<select class="{input} w-28" bind:value={channel}>
											<option value="auto">automatic</option>
											{#each channelsOf(r) as c (c)}<option value={String(c)}>{c}</option>{/each}
										</select>
									</label>
									{#if r.kind === 'openwrt'}
										<label class={label}>
											Width
											<select class="{input} w-32" bind:value={htmode}>
												<option value="">as it is</option>
												{#each widths(r) as w (w)}<option value={w}>
														{w.replace('HE', '')} MHz
													</option>{/each}
											</select>
										</label>
									{/if}
									{#if r.kind === 'openwrt'}
										<label class={label}>
											Country
											<input
												class="{input} w-20 uppercase"
												maxlength="2"
												placeholder={r.country ?? '–'}
												bind:value={country}
												oninput={() => (country = country.toUpperCase())}
											/>
										</label>
									{/if}
									<button class="btn btn-primary btn-sm" onclick={() => saveRadio(r)}>Apply</button>
									<button class="btn btn-sm" onclick={() => (editing = null)}>Cancel</button>
									<span class="text-xs text-muted">
										{r.kind === 'openwrt'
											? 'Applied with a rollback: undone if the radio doesn’t come back.'
											: 'The FRITZ!Box applies it at once.'}
									</span>
								</div>
							</td>
						</tr>
					{/if}
				{/each}
			</tbody>
		</table>
	</div>

	<!-- Clients -->
	{#if data.clients?.length}
		{@const troubled = data.clients.filter((c) => c.issues.length)}
		<div class="mt-10 mb-3 flex flex-wrap items-baseline justify-between gap-2">
			<h2 class="text-sm font-semibold">Clients</h2>
			<span class="text-xs text-muted">
				{data.clients.length} connected · {troubled.length} with a weak or slow link
			</span>
		</div>
		<div class="card overflow-x-auto">
			<table class="w-full min-w-[40rem] text-sm">
				<thead class="text-left text-xs text-muted">
					<tr class="border-b border-line">
						<th class="px-4 py-2 font-medium">Client</th>
						<th class="font-medium">At</th>
						<th class="font-medium">Signal</th>
						<th class="font-medium">Rate to · from it</th>
						<th class="pr-4 font-medium">Worth a look</th>
					</tr>
				</thead>
				<tbody>
					{#each showAllClients ? data.clients : data.clients.slice(0, 12) as c, i (i)}
						<tr class="border-b border-line last:border-0">
							<td class="px-4 py-1.5">
								{c.name ?? c.mac ?? '?'}
								{#if c.name}<span class="font-mono text-xs text-faint">{c.mac}</span>{/if}
							</td>
							<td class="text-xs">
								{name(c.device)}
								<span class="text-faint">{bandLabel(c.band) || c.radio}</span>
							</td>
							<td class="tabular text-xs">
								{#if c.signal == null}–{:else if c.kind === 'fritzbox'}{c.signal} %{:else}{c.signal}
									dBm{#if c.snr != null}<span class="text-faint"> · SNR {c.snr}</span>{/if}{/if}
							</td>
							<td class="tabular text-xs">
								{#if c.kind === 'fritzbox'}{c.speed ?? '–'} Mbit/s{:else}
									{#if c.tx || c.rx}{c.tx?.mbit ?? '–'} · {c.rx?.mbit ?? '–'} Mbit/s{:else}–{/if}
									{#if c.tx?.std}<span class="text-faint">
											{c.tx.std.toUpperCase()}{c.tx.mhz ? ` ${c.tx.mhz} MHz` : ''}{c.tx.nss
												? ` ${c.tx.nss}×`
												: ''}
										</span>{/if}
								{/if}
							</td>
							<td
								class="pr-4 text-xs"
								style:color={c.issues.length ? 'var(--warning-text)' : undefined}
							>
								{c.issues.join('; ') || '–'}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
			{#if data.clients.length > 12}
				<button
					class="w-full border-t border-line py-2 text-xs text-muted hover:text-text"
					onclick={() => (showAllClients = !showAllClients)}
				>
					{showAllClients ? 'Fewer' : `All ${data.clients.length}`}
				</button>
			{/if}
		</div>
	{/if}

	<!-- What the radios hear -->
	{#if Object.keys(data.scans).length}
		<h2 class="mt-10 mb-3 text-sm font-semibold">What the radios hear</h2>
		<div class="grid gap-4 md:grid-cols-2">
			{#each Object.entries(data.scans) as [key, scan] (key)}
				{@const chans = SCAN_CHANNELS[scan.band] ?? []}
				{@const maxCount = Math.max(
					1,
					...chans.map((c) => scan.neighbours[String(c)]?.length ?? 0)
				)}
				<div class="card min-w-0 p-4">
					<p class="text-sm font-medium">
						{name(key.split('/')[0])}
						<span class="text-xs font-normal text-muted">
							{bandLabel(scan.band)} · on {scan.channel} · {ago(
								new Date(scan.t * 1000).toISOString(),
								store.now
							)}
						</span>
					</p>
					<div class="mt-3 flex items-end gap-0.5" style="height: 64px">
						{#each chans as c (c)}
							{@const nets = scan.neighbours[String(c)] ?? []}
							<div
								class="flex h-full flex-1 flex-col items-center justify-end"
								title="{c}: {nets.length
									? nets.map((n) => `${n.ssid} ${n.signal} dBm`).join(', ')
									: 'nobody'}"
							>
								<div
									class="w-full rounded-sm"
									style:height="{nets.length ? Math.max(10, (nets.length / maxCount) * 100) : 3}%"
									style:background={c === scan.channel
										? 'var(--accent)'
										: nets.length && loudest(nets) > -65
											? 'var(--warning)'
											: 'var(--line-strong)'}
								></div>
							</div>
						{/each}
					</div>
					<div class="mt-1 flex gap-0.5 text-[9px] text-faint">
						{#each chans as c (c)}<span class="min-w-0 flex-1 text-center">{c}</span>{/each}
					</div>
					{#if scan.survey.length}
						<p class="mt-2 text-xs text-muted">
							Busy: {scan.survey
								.slice(0, 8)
								.map((s) => `${s.mhz} MHz ${s.busy} %`)
								.join(' · ')}
						</p>
					{/if}
				</div>
			{/each}
		</div>
	{/if}

	<!-- Channel plan -->
	<div class="mt-10 mb-3 flex flex-wrap items-baseline justify-between gap-2">
		<h2 class="text-sm font-semibold">Channel plan</h2>
		<div class="flex items-center gap-3">
			<label class="flex items-center gap-1.5 text-xs text-muted">
				<input type="checkbox" bind:checked={dfs} onchange={() => plan && suggest()} /> radar channels
				(DFS)
			</label>
			<button class="btn btn-sm" onclick={suggest}><Sparkles class="size-3.5" /> Suggest</button>
		</div>
	</div>
	<p class="max-w-3xl text-xs text-muted">
		{data.scan_at
			? `The radios scan every day at ${data.scan_at} (Settings → Wi-Fi scans) and when asked.`
			: 'The radios scan only when asked (a daily scan can be set under Settings → Wi-Fi scans).'}
		Scan before suggesting channels. Changes require approval; OpenWrt can roll back an unreachable access
		point. FRITZ!Box changes have no automatic rollback.
	</p>
	{#if planError}<p class="mt-2 text-sm" style:color="var(--critical-text)">{planError}</p>{/if}
	{#if plan}
		{#if !plan.scanned}
			<p class="mt-2 text-sm" style:color="var(--warning-text)">
				No recent scan. Radios without known allowed channels keep their current channel.
			</p>
		{/if}
		<div class="card mt-3 divide-y divide-line">
			{#each plan.radios as r (`${r.device}/${r.radio}`)}
				{@const key = `${r.device}/${r.radio}`}
				<label class="flex gap-3 px-4 py-3 text-sm {r.change ? '' : 'opacity-70'}">
					<input
						type="checkbox"
						class="mt-1"
						disabled={!r.change}
						checked={chosen.has(key)}
						onchange={(e) => {
							const next = new Set(chosen);
							if ((e.currentTarget as HTMLInputElement).checked) next.add(key);
							else next.delete(key);
							chosen = next;
						}}
					/>
					<span class="min-w-0">
						<span class="font-medium">{name(r.device)}</span>
						<span class="text-xs text-muted">{bandLabel(r.band)}</span>
						<span class="tabular ml-1">
							{r.current ?? 'auto'} → <b>{r.channel}</b>{r.htmode
								? ` (${r.htmode.replace('HE', '')} MHz)`
								: ''}
						</span>
						<span class="block text-xs text-muted">{r.reason}</span>
					</span>
				</label>
			{/each}
		</div>
		<p class="mt-2 text-xs text-muted">
			Disturbance (lower is better): now {plan.before}, with the plan {plan.after}.
		</p>
		{#each plan.hints as h (h)}<p class="mt-1 text-xs" style:color="var(--warning-text)">
				{h}
			</p>{/each}
		{#if signedIn && chosen.size}
			<button class="btn btn-primary btn-sm mt-3" onclick={() => (job = { kind: 'apply' })}>
				Apply {chosen.size}
				{chosen.size === 1 ? 'change' : 'changes'}
			</button>
		{/if}
	{/if}
	{#if data.plans.length}
		<h3 class="mt-6 text-xs font-medium text-muted">Applied before</h3>
		<ul class="mt-1 text-xs">
			{#each [...data.plans].reverse() as p (p.t)}
				<li>
					{dayClock(new Date(p.t * 1000))} by {p.by}: {Object.entries(p.outcome)
						.map(([k, v]) => `${name(k.split('/')[0])} ${v}`)
						.join(', ')}
					{#if p.before != null}· disturbance {p.before} → {p.after} (expected){/if}
				</li>
			{/each}
		</ul>
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
			is. Clients may notice a pause of a second or two.
		</p>
	</JobDialog>
{:else if job?.kind === 'apply' && plan}
	{@const p = plan}
	<JobDialog
		title="Apply the channel plan"
		path="/wifi/plan/apply"
		action="Apply"
		body={() => ({
			radios: p.radios
				.filter((r) => chosen.has(`${r.device}/${r.radio}`))
				.map((r) => ({
					device: r.device,
					radio: r.radio,
					channel: String(r.channel),
					...(r.htmode ? { htmode: r.htmode } : {})
				})),
			before: p.before,
			after: p.after
		})}
		onclose={() => (job = null)}
		ondone={load}
	>
		<p>
			{chosen.size}
			{chosen.size === 1 ? 'radio moves' : 'radios move'}, one access point at a time. Clients
			briefly reconnect. OpenWrt uses rollback; FRITZ!Box does not.
		</p>
	</JobDialog>
{/if}
