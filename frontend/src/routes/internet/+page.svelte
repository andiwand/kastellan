<script lang="ts">
	import RangePicker from '#lib/components/ui/RangePicker.svelte';
	import StateBadge from '#lib/components/ui/StateBadge.svelte';
	import StateDot from '#lib/components/ui/StateDot.svelte';
	import PageHeader from '#lib/components/ui/PageHeader.svelte';
	import Section from '#lib/components/ui/Section.svelte';
	import KeyValues, { type KeyValue } from '#lib/components/ui/KeyValues.svelte';
	import Table, { type Column } from '#lib/components/ui/Table.svelte';
	import EmptyState from '#lib/components/ui/EmptyState.svelte';
	import { internetState, toneColor } from '#lib/states.js';
	import { more, type Bucket, type InternetView, type SpeedTestResult } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { issuesIn } from '#lib/attention.js';
	import { diagnose } from '#lib/internet.js';
	import { bitrate, bytes, date, dayClock, duration, kbitrate, ms } from '#lib/format.js';
	import LatencyChart from '#lib/components/LatencyChart.svelte';
	import UptimeStrip from '#lib/components/UptimeStrip.svelte';
	import Gauge from '@lucide/svelte/icons/gauge';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';

	/** The internet connection (internet.py): where it is slow or down, in a sentence and along
	 * the way out, how it is now, its history (outages, availability by day and month: the
	 * record for the provider), what goes through it, and speed tests. */
	let hours = $state(24);
	let view = $state<InternetView | null>(null);
	let error = $state<string | null>(null);
	let testing = $state(false);
	let testNote = $state<string | null>(null);

	async function load() {
		try {
			view = await more.internet(hours);
			error = null;
		} catch (e) {
			error = (e as Error).message;
		}
	}
	$effect(() => {
		void hours;
		load();
		const t = setInterval(load, 30_000);
		return () => clearInterval(t);
	});

	async function test() {
		testing = true;
		testNote = null;
		try {
			const r = await more.speedtest();
			testNote = `↓ ${bitrate(r.down)} · ↑ ${bitrate(r.up)} · latency ${ms(r.ms_idle)} idle, ${ms(r.ms_loaded)} under load`;
			await load();
		} catch (e) {
			testNote = (e as Error).message;
		} finally {
			testing = false;
		}
	}

	const name = (id: string) => store.devices?.find((d) => d.id === id)?.name ?? id;
	const pct = (v: number | null | undefined, digits = 2) =>
		v == null ? '–' : `${(v * 100).toFixed(digits)} %`;
	const lineState = (s: string | undefined) =>
		internetState[(s ?? 'unknown') as keyof typeof internetState] ?? internetState.unknown;
	const step = $derived(view && view.series.length > 1 ? view.series[1].t - view.series[0].t : 60);
	const start = $derived(
		view?.series.length ? new Date(view.series[0].t * 1000).toISOString() : ''
	);
	const uptime = $derived<Bucket[]>(
		(view?.series ?? []).map((b) => ({
			uptime: b.state == null || b.state === 'unknown' ? null : b.state === 'down' ? 0 : 1,
			latency_ms: b.ms
		}))
	);
	const latency = $derived<Bucket[]>(
		(view?.series ?? []).map((b) => ({ uptime: null, latency_ms: b.ms }))
	);
	const down = $derived<Bucket[]>(
		(view?.series ?? []).map((b) => ({
			uptime: null,
			latency_ms: b.down == null ? null : b.down / 1e6
		}))
	);
	const hasRate = $derived(down.some((b) => b.latency_ms != null));
	const router = $derived(view?.router ? store.byId.get(view.router) : undefined);
	const now = $derived(view?.now);
	const wifiTones = $derived(issuesIn(store.attention, 'wifi').map((i) => i.tone));
	const d = $derived(view ? diagnose(view, wifiTones, router?.name ?? 'Router') : null);

	const facts = $derived<KeyValue[]>(
		view
			? [
					{ label: 'Latency', value: now?.ms != null ? ms(now.ms) : null },
					{ label: 'Jitter', value: now?.jitter != null ? ms(now.jitter) : null },
					{ label: 'Loss', value: now?.loss != null ? pct(now.loss, 0) : null },
					{
						label: 'IPv6',
						value:
							now?.v6_ms != null ? ms(now.v6_ms) : now?.v6_loss === 1 ? 'no answer' : 'not here'
					},
					{
						label: 'In use now',
						value: now?.down != null ? `↓ ${bitrate(now.down)} · ↑ ${bitrate(now.up)}` : null
					},
					{
						label: 'Public address',
						value: view.line.address,
						mono: true,
						hint: view.line.address_v6 ?? undefined
					},
					...(view.line.access || view.line.connected_for
						? [
								{
									label: 'Connection',
									value: [
										view.line.access,
										view.line.connected_for ? `for ${duration(view.line.connected_for)}` : null
									]
										.filter(Boolean)
										.join(' · ')
								}
							]
						: []),
					...(view.line.dsl
						? [
								{
									label: 'Synced at',
									value: `↓ ${kbitrate(view.line.dsl.down)} · ↑ ${kbitrate(view.line.dsl.up)}`
								}
							]
						: [])
				]
			: []
	);

	type Outage = InternetView['outages'][number];
	const outageColumns: Column<Outage>[] = [
		{ key: 'when', label: 'When', width: '9rem', value: (o) => dayClock(new Date(o.start * 1000)) },
		{
			key: 'long',
			label: 'How long',
			numeric: true,
			width: '6rem',
			value: (o) => (o.end ? duration(Math.round(o.seconds)) : 'going on')
		},
		{ key: 'why', label: 'Where', priority: 2, value: (o) => o.why }
	];

	type User = NonNullable<InternetView['clients']>['rows'][number];
	const userColumns: Column<User>[] = [
		{ key: 'name', label: 'Device', cell: userName },
		{
			key: 'now',
			label: 'Now ↓ · ↑',
			numeric: true,
			value: (c) => `${bitrate(c.rx_rate)} · ${bitrate(c.tx_rate)}`
		},
		{
			key: 'period',
			label: 'This period ↓ · ↑',
			numeric: true,
			priority: 2,
			value: (c) => `${bytes(c.rx)} · ${bytes(c.tx)}`
		}
	];

	const speedColumns = $derived<Column<SpeedTestResult>[]>([
		{ key: 'when', label: 'When', value: (t) => dayClock(new Date(t.t * 1000)) },
		{ key: 'down', label: 'Down', numeric: true, cell: speedDown },
		{ key: 'up', label: 'Up', numeric: true, value: (t) => bitrate(t.up) },
		{ key: 'ms', label: 'Latency idle → loaded', priority: 2, numeric: true, cell: speedMs }
	]);
	const lowSpeed = (t: SpeedTestResult) =>
		!!view?.speedtest.booked_down && t.down != null && t.down < view.speedtest.booked_down * 5e5;
	const bloat = (t: SpeedTestResult) => !!t.ms_idle && !!t.ms_loaded && t.ms_loaded > t.ms_idle * 4;
	const monthName = (m: string) =>
		new Date(m + '-01').toLocaleDateString('en', { month: 'long', year: 'numeric' });
</script>

{#snippet userName(c: User)}
	{c.name ?? c.mac}
	{#if c.name}<span class="ml-1 font-mono text-meta text-faint">{c.mac}</span>{/if}
{/snippet}

{#snippet speedDown(t: SpeedTestResult)}
	<span class="inline-flex items-center gap-1.5">
		{#if lowSpeed(t)}<StateDot
				tone="warning"
				label="Below half the booked speed"
				size="size-1.5"
			/>{/if}
		{bitrate(t.down)}
	</span>
{/snippet}

{#snippet speedMs(t: SpeedTestResult)}
	<span class="inline-flex items-center gap-1.5">
		{#if bloat(t)}<StateBadge word="Bufferbloat" tone="warning" quiet />{/if}
		{t.ms_idle ?? '–'} → {t.ms_loaded ?? '–'} ms
	</span>
{/snippet}

<svelte:head><title>Internet · Kastellan</title></svelte:head>

<PageHeader
	title="Internet"
	summary="Connectivity, outages and speed, measured from the hub through {router?.name ??
		'its gateway'}."
>
	{#snippet actions()}
		<RangePicker value={hours} onchange={(h) => (hours = h)} />
	{/snippet}
</PageHeader>

{#if error}<div class="mb-6"><EmptyState kind="error" text={error} onretry={load} /></div>{/if}

{#if view === null}
	{#if !error}<div class="card h-48 animate-pulse"></div>{/if}
{:else if !view.enabled}
	<EmptyState>
		Off (<code class="font-mono">internet: enabled: false</code> in config.yaml).
	</EmptyState>
{:else}
	<!-- Now: where the problem is, in a sentence and along the way out. -->
	<section class="card p-5" aria-label="Now">
		<div class="flex flex-wrap items-center gap-x-3 gap-y-2">
			<StateBadge state={lineState(now?.state)} pulse={now?.state === 'down'} />
			{#if view.outage}
				<span class="text-meta text-muted">for {duration(view.outage.seconds)}</span>
			{/if}
		</div>
		{#if d}
			<p class="mt-3 text-base leading-snug font-medium">{d.headline}</p>
			{#each d.notes as note (note)}<p class="mt-1 text-body text-muted">{note}</p>{/each}
			<p class="mt-1 text-body text-muted">
				{#if d.wifi}
					Wi-Fi has {d.wifi === 1 ? 'an issue' : `${d.wifi} issues`}: if a device is slow, it may be
					its Wi-Fi.
				{:else}
					Measured from the hub, which is wired: if only one device is slow, look at its Wi-Fi.
				{/if}
				{#if store.me?.role === 'admin'}
					<a class="inline-flex items-center gap-0.5 text-accent hover:underline" href="/wifi">
						Wi-Fi <ArrowRight class="size-3.5" />
					</a>
				{/if}
			</p>

			<ol class="mt-5 grid grid-cols-3 gap-x-2 gap-y-4 sm:grid-cols-6" aria-label="The way out">
				{#each d.hops as h (h.id)}
					<li class="min-w-0" title={h.title}>
						<span
							class="block h-1 rounded-full"
							style:background={h.seen || h.tone !== 'neutral' ? toneColor[h.tone].fill : undefined}
							style:border={h.seen || h.tone !== 'neutral'
								? undefined
								: '1px dashed var(--line-strong)'}
						></span>
						<span class="mt-2 block truncate text-meta text-muted">{h.label}</span>
						<span class="tabular block truncate text-cell font-medium">{h.value}</span>
					</li>
				{/each}
			</ol>
		{/if}
		{#if view.route?.length}
			<p class="mt-4 text-meta break-words text-muted">
				The way out:
				{#each view.route as h, i (i)}
					{#if i}<span class="text-faint"> → </span>{/if}<span
						class="font-mono {view.hops?.includes(h.address) ? 'text-text' : ''}"
						title="{h.ms} ms{view.hops?.includes(h.address)
							? ' · the provider’s, checked with every look'
							: ''}"
					>
						{h.address}
					</span>
				{/each}
			</p>
		{/if}
	</section>

	<div class="mt-4"><KeyValues items={facts} columns={3} /></div>

	<!-- History -->
	{#if start}
		<Section title="Over time">
			<div class="card p-5">
				<h3 class="text-meta font-medium text-muted">Online</h3>
				<div class="mt-2"><UptimeStrip buckets={uptime} {start} {step} label="Internet" /></div>
				<h3 class="mt-6 text-meta font-medium text-muted">Latency</h3>
				<div class="mt-2"><LatencyChart buckets={latency} {start} {step} height={160} /></div>
				{#if hasRate}
					<h3 class="mt-6 text-meta font-medium text-muted">Download in use (Mbit/s)</h3>
					<div class="mt-2">
						<LatencyChart
							buckets={down}
							{start}
							{step}
							height={140}
							label="Download in use over time"
							format={(v) => v.toFixed(v < 10 ? 1 : 0)}
						/>
					</div>
				{/if}
			</div>
		</Section>
	{/if}

	<div class="grid gap-x-6 lg:grid-cols-2">
		<Section title="Outages" count={view.outages.length}>
			<Table
				columns={outageColumns}
				rows={view.outages}
				key={(o) => String(o.start)}
				empty="None recorded."
				label="Outages"
			/>
			{@const lineEvents = view.events.filter(
				(e) => e.kind === 'reconnect' || e.kind === 'address'
			)}
			{#if lineEvents.length}
				<h3 class="mt-4 mb-1 text-meta font-medium text-muted">The line</h3>
				<ul class="text-cell">
					{#each lineEvents as e (e.t)}
						<li>
							<span class="tabular text-muted">{dayClock(new Date(e.t * 1000))}</span>
							{e.text}
						</li>
					{/each}
				</ul>
			{/if}
		</Section>

		<Section title="Availability">
			<div class="card p-5">
				{#if view.months.length}
					<ul class="text-cell">
						{#each [...view.months].reverse() as m (m.month)}
							<li class="flex justify-between gap-3 py-0.5">
								<span>{monthName(m.month)}</span>
								<span class="tabular">
									{pct(m.availability, 3)}
									<span class="text-meta text-muted">· down {duration(m.down_seconds)}</span>
								</span>
							</li>
						{/each}
					</ul>
				{/if}
				{#if view.days.length}
					<h3 class="mt-5 text-meta font-medium text-muted">Last days</h3>
					<div class="mt-2 flex h-10 items-end gap-0.5">
						{#each view.days.slice(-31) as day (day.day)}
							{@const a = day.availability ?? 1}
							<div
								class="flex-1 rounded-sm"
								style:height="{Math.max(10, a * 100)}%"
								style:background={a >= 0.999
									? 'var(--good-soft)'
									: a >= 0.99
										? 'var(--warning)'
										: 'var(--critical)'}
								title="{date(day.day)}: {pct(day.availability, 2)}, down {duration(
									day.down_seconds
								)}{day.ms ? `, ${ms(day.ms)}` : ''}"
							></div>
						{/each}
					</div>
				{/if}
			</div>
		</Section>
	</div>

	{#if view.clients && (view.clients.rows.length || view.clients.routers.length)}
		<Section
			title="Who uses the line"
			meta="from nlbwmon on {view.clients.routers
				.map(name)
				.join(', ')}: now, and this accounting period"
		>
			{#each Object.entries(view.clients.errors) as [rid, e] (rid)}
				<p class="mb-2 flex items-center gap-2 text-meta text-muted">
					<StateDot tone="warning" label="Not read" size="size-1.5" />{name(rid)}: {e}
				</p>
			{/each}
			{#if view.clients.rows.length}
				<Table
					columns={userColumns}
					rows={view.clients.rows.slice(0, 15)}
					key={(c) => c.router + c.mac}
					label="Who uses the line"
				/>
			{/if}
		</Section>
	{/if}

	<Section title="Speed">
		{#snippet actions()}
			{#if store.me?.role === 'admin' && !store.health?.demo}
				<button class="btn btn-sm" disabled={testing || view?.speedtest.testing} onclick={test}>
					<Gauge class="size-3.5" />
					{testing || view?.speedtest.testing ? 'Measuring (half a minute)…' : 'Test now'}
				</button>
			{/if}
		{/snippet}
		<p class="mb-3 max-w-3xl text-meta text-muted">
			{#if view.speedtest.how === 'iperf3'}A test measures against the iperf3 server {view.speedtest
					.server} for 8 seconds each way and the latency meanwhile (bufferbloat).{:else}A test
				downloads and uploads for up to 15 seconds each from {view.speedtest.server} and measures the
				latency meanwhile (bufferbloat).{/if}
			{view.speedtest.enabled
				? `On its own every ${view.speedtest.every_hours} h, never while the line is busy.`
				: 'Only when asked (turn on regular tests under internet: speedtest in config.yaml).'}
			{#if view.speedtest.booked_down}Booked: ↓ {view.speedtest.booked_down} Mbit/s{view.speedtest
					.booked_up
					? ` · ↑ ${view.speedtest.booked_up} Mbit/s`
					: ''}.{/if}
		</p>
		{#if testNote}<p class="mb-3 text-body">{testNote}</p>{/if}
		{#if view.speedtest.error}<p class="mb-3 flex items-center gap-2 text-meta text-muted">
				<StateDot tone="warning" label="Failed" size="size-1.5" />Last test: {view.speedtest.error}
			</p>{/if}
		{#if view.speedtests.length}
			<Table
				columns={speedColumns}
				rows={view.speedtests.slice(0, 10)}
				key={(t) => String(t.t)}
				label="Speed tests"
			/>
		{/if}
	</Section>
{/if}
