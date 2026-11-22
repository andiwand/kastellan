<script lang="ts">
	import { more, type WifiNetwork, type WifiRadio } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { ago } from '#lib/format.js';
	import { accessPoints, type AccessPoint } from '#lib/wifi.js';
	import Table, { type Column } from './ui/Table.svelte';
	import StateBadge from './ui/StateBadge.svelte';

	/** The access points: one row per radio (OpenWrt, FRITZ!Box) with its channel and width, its
	 * clients, how busy its channel is (a slim bar) and its networks; the FRITZ!Mesh repeaters a
	 * FRITZ!Box reports as rows of their own (with their link to the box). A radio's row opens to
	 * what it hears and, for a signed-in admin, its channel, width and country. `device` shows
	 * one device's radios (its device page; mesh repeaters then only on the box's). */
	let {
		radios,
		networks = [],
		device,
		onchanged,
		overTheAir = false
	}: {
		radios: WifiRadio[];
		networks?: WifiNetwork[];
		device?: string;
		overTheAir?: boolean; // only what a phone nearby sees: no client counts
		onchanged?: () => void; // after a radio was changed: read again
	} = $props();

	const name = (id: string) => store.byId.get(id)?.name ?? id;
	const rows = $derived(
		accessPoints(
			radios.filter((r) => !device || r.device === device),
			networks,
			(store.devices ?? []).filter((d) => !device || d.id === device),
			name
		)
	);
	const canChange = $derived(store.editBlocked === null);

	// The change form of the open radio (one at a time).
	let channel = $state('auto');
	let htmode = $state('');
	let country = $state('');
	let editing = $state<string | null>(null);
	let note = $state<{ key: string; ok: boolean; text: string } | null>(null);

	function edit(r: WifiRadio) {
		editing = `${r.device}/${r.radio}`;
		channel = String(r.channel ?? 'auto');
		htmode = '';
		country = '';
	}
	async function save(r: WifiRadio) {
		const key = `${r.device}/${r.radio}`;
		try {
			await more.wifiRadio({
				device: r.device,
				radio: r.radio,
				channel,
				...(htmode ? { htmode } : {}),
				...(country ? { country } : {})
			});
			editing = null;
			note = { key, ok: true, text: 'Changed; the list shows it within two minutes.' };
			onchanged?.();
		} catch (e) {
			note = { key, ok: false, text: (e as Error).message };
		}
	}
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
	const input =
		'h-9 rounded-lg border border-line bg-surface px-3 text-sm outline-none focus:border-accent';
	const label = 'flex flex-col gap-1 text-meta font-medium text-muted';

	const all: Column<AccessPoint>[] = [
		{ key: 'ap', label: 'Access point', grow: true, cell: apCell },
		{ key: 'channel', label: 'Channel', cell: channelCell, width: '7.5rem' },
		{
			key: 'clients',
			label: 'Clients',
			numeric: true,
			priority: 2,
			width: '4.5rem',
			value: (a) => a.clients
		},
		{
			key: 'busy',
			label: 'Busy',
			priority: 2,
			width: '8rem',
			title: 'How much of the time its channel was in use, at its newest scan',
			cell: busyCell
		},
		{
			key: 'networks',
			label: 'Networks',
			priority: 3,
			value: (a) => a.networks.join(', ') || null
		},
		{ key: 'state', label: 'State', width: '6.5rem', cell: stateCell }
	];
	const columns = $derived(overTheAir ? all.filter((c) => c.key !== 'clients') : all);
</script>

{#snippet apCell(a: AccessPoint)}
	<span class="block truncate font-medium max-sm:whitespace-normal" title={a.name}>
		{#if a.device}<a class="hover:underline" href="/devices/{encodeURIComponent(a.device)}">
				{a.name}
			</a>{:else}{a.name}{/if}
	</span>
	<span class="block truncate text-meta text-muted">{a.note}</span>
{/snippet}

{#snippet channelCell(a: AccessPoint)}
	{#if a.radio}
		<span class="tabular whitespace-nowrap">
			{a.channel ?? 'auto'}<span class="hidden text-muted sm:inline"> · {a.width} MHz</span>
		</span>
	{:else}<span class="text-faint">–</span>{/if}
{/snippet}

{#snippet busyCell(a: AccessPoint)}
	{#if a.busy != null}
		<span class="flex items-center gap-2" title="{a.busy} % busy">
			<span class="h-1.5 w-12 overflow-hidden rounded-full bg-surface-3">
				<span
					class="block h-full rounded-full"
					style:width="{Math.min(100, a.busy)}%"
					style:background={a.busy >= 60 ? 'var(--warning)' : 'var(--muted)'}
				></span>
			</span>
			<span class="tabular text-meta text-muted">{a.busy} %</span>
		</span>
	{:else}<span class="text-faint">–</span>{/if}
{/snippet}

{#snippet stateCell(a: AccessPoint)}<StateBadge state={a.state} quiet />{/snippet}

{#snippet details(a: AccessPoint)}
	{@const r = a.radio!}
	{@const key = `${r.device}/${r.radio}`}
	<dl class="grid gap-x-6 gap-y-1 text-meta sm:grid-cols-[auto_1fr]">
		<dt class="text-muted">Radio</dt>
		<dd class="font-mono">{r.radio}{r.htmode ? ` · ${r.htmode}` : ''}</dd>
		{#if a.networks.length}
			<dt class="text-muted lg:hidden">Networks</dt>
			<dd class="lg:hidden">{a.networks.join(', ')}</dd>
		{/if}
		<dt class="text-muted">Hears</dt>
		<dd>
			{#if r.scanned}
				{r.neighbours} networks nearby{#each Object.entries(r.hears) as [k, s] (k)}
					· {name(k.split('/')[0])} at {s} dBm{/each}
				<span class="text-faint">
					· scanned {ago(new Date(r.scanned * 1000).toISOString(), store.now)}
				</span>
			{:else}<span class="text-faint">not scanned</span>{/if}
		</dd>
		{#if r.country}
			<dt class="text-muted">Country</dt>
			<dd>{r.country}</dd>
		{/if}
	</dl>
	{#if note?.key === key}
		<p class="mt-2 text-meta" style:color={note.ok ? undefined : 'var(--critical-text)'}>
			{note.text}
		</p>
	{/if}
	{#if canChange && editing !== key}
		<button class="btn btn-sm mt-3" onclick={() => edit(r)}>Change channel</button>
	{:else if canChange}
		<div class="mt-3 flex flex-wrap items-end gap-3">
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
								{w.replace(/^[A-Z]+/, '')} MHz
							</option>{/each}
					</select>
				</label>
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
			<button class="btn btn-primary btn-sm" onclick={() => save(r)}>Apply</button>
			<button class="btn btn-sm" onclick={() => (editing = null)}>Cancel</button>
		</div>
		<p class="mt-2 text-meta text-muted">
			{r.kind === 'openwrt'
				? 'Applied with a rollback: undone if the radio doesn’t come back.'
				: 'The FRITZ!Box applies it at once, without a rollback.'}
		</p>
	{/if}
{/snippet}

<Table
	{columns}
	{rows}
	key={(a) => a.key}
	expand={details}
	expandable={(a) => !!a.radio}
	label="Access points"
	empty="No access points read."
/>
