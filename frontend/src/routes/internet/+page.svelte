<script lang="ts">
	import Tabs from '#lib/components/Tabs.svelte';
	import { more, type Bucket, type InternetView } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { bitrate, bytes, dayClock, duration, kbitrate } from '#lib/format.js';
	import LatencyChart from '#lib/components/LatencyChart.svelte';
	import UptimeStrip from '#lib/components/UptimeStrip.svelte';
	import Gauge from '@lucide/svelte/icons/gauge';

	/** The internet connection (internet.py): how it is now, where it fails when it does, its
	 * history (outages, availability by day and month: the record for the provider), what goes
	 * through it, and speed tests. */
	const RANGES = [
		{ hours: 1, label: '1 h' },
		{ hours: 24, label: '24 h' },
		{ hours: 168, label: '7 d' },
		{ hours: 720, label: '30 d' }
	];
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
			testNote = `↓ ${bitrate(r.down)} · ↑ ${bitrate(r.up)} · latency ${r.ms_idle ?? '–'} ms idle, ${r.ms_loaded ?? '–'} ms under load`;
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
	const STATE: Record<string, [string, string]> = {
		up: ['var(--good-text)', 'Online'],
		degraded: ['var(--warning-text)', 'Slow or losing packets'],
		down: ['var(--critical-text)', 'Down'],
		unknown: ['var(--muted)', 'Unknown']
	};
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
</script>

<svelte:head><title>Internet · Kastellan</title></svelte:head>

<div class="flex flex-wrap items-end justify-between gap-3">
	<div>
		<h1 class="text-2xl font-semibold tracking-tight">Internet</h1>
		<p class="mt-1 max-w-2xl text-sm text-muted">
			Connectivity, outages and speed measured from the hub. Through
			{#if router}<a class="text-text hover:underline" href="/devices/{router.id}">
					{router.name}
				</a>{:else}the hub's gateway{/if}.
		</p>
	</div>
	<Tabs
		small
		label="Range"
		tabs={RANGES.map((r) => ({ id: r.hours, label: r.label }))}
		current={hours}
		onselect={(h) => (hours = h)}
	/>
</div>

{#if error}<p class="mt-4 text-sm" style:color="var(--critical-text)">{error}</p>{/if}

{#if view === null}
	<div class="card mt-6 h-48 animate-pulse"></div>
{:else if !view.enabled}
	<div class="card mt-6 p-5 text-sm text-muted">
		Off (<code>internet: enabled: false</code> in config.yaml).
	</div>
{:else}
	<!-- Now -->
	<section class="card mt-6 p-5">
		<div class="flex flex-wrap items-baseline gap-x-4 gap-y-1">
			<span class="text-lg font-semibold" style:color={STATE[now?.state ?? 'unknown'][0]}>
				{STATE[now?.state ?? 'unknown'][1]}
			</span>
			{#if now?.state === 'down' && now.why}<span class="text-sm">{now.why}</span>{/if}
			{#if view.outage}
				<span class="text-sm text-muted">for {duration(view.outage.seconds)}</span>
			{/if}
			{#if now?.note}<span class="text-sm text-muted">{now.note}</span>{/if}
		</div>
		<dl class="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-3 lg:grid-cols-6">
			<div>
				<dt class="text-xs text-muted">Latency</dt>
				<dd class="tabular">{now?.ms != null ? `${now.ms} ms` : '–'}</dd>
			</div>
			<div>
				<dt class="text-xs text-muted">Jitter · loss</dt>
				<dd class="tabular">
					{now?.jitter != null ? `${now.jitter} ms` : '–'} · {pct(now?.loss, 0)}
				</dd>
			</div>
			<div>
				<dt class="text-xs text-muted">IPv6</dt>
				<dd class="tabular">
					{now?.v6_ms != null ? `${now.v6_ms} ms` : now?.v6_loss === 1 ? 'no answer' : 'not here'}
				</dd>
			</div>
			<div>
				<dt class="text-xs text-muted">Router · provider · DNS</dt>
				<dd class="tabular">
					{now?.router_ms != null ? `${now.router_ms} ms` : '–'} · {now?.hop_ms != null
						? `${now.hop_ms} ms`
						: '–'} · {now?.dns_ms != null ? `${now.dns_ms} ms` : '–'}
				</dd>
			</div>
			<div>
				<dt class="text-xs text-muted">In use now</dt>
				<dd class="tabular">↓ {bitrate(now?.down)} · ↑ {bitrate(now?.up)}</dd>
			</div>
			<div>
				<dt class="text-xs text-muted">Public address</dt>
				<dd class="truncate font-mono text-xs" title={view.line.address_v6 ?? ''}>
					{view.line.address ?? '–'}
				</dd>
			</div>
		</dl>
		{#if view.route?.length}
			<p class="mt-3 text-xs text-muted">
				The way out:
				{#each view.route as h, i (i)}
					{#if i}<span class="text-faint"> → </span>{/if}<span
						class="font-mono"
						style:color={view.hops?.includes(h.address) ? 'var(--text)' : undefined}
						title="{h.ms} ms{view.hops?.includes(h.address)
							? ' · the provider’s, checked with every look'
							: ''}"
					>
						{h.address}
					</span>
				{/each}
			</p>
		{/if}
		{#if view.line.connected_for || view.line.dsl}
			<p class="mt-3 text-xs text-muted">
				{#if view.line.access}{view.line.access}{/if}
				{#if view.line.connected_for}· connected for {duration(view.line.connected_for)}{/if}
				{#if view.line.dsl}· synced at ↓ {kbitrate(view.line.dsl.down)} · ↑ {kbitrate(
						view.line.dsl.up
					)}{/if}
			</p>
		{/if}
	</section>

	<!-- History -->
	{#if start}
		<section class="card mt-6 p-5">
			<h2 class="text-sm font-semibold">Online</h2>
			<div class="mt-3"><UptimeStrip buckets={uptime} {start} {step} label="Internet" /></div>
			<h2 class="mt-6 text-sm font-semibold">Latency</h2>
			<div class="mt-2"><LatencyChart buckets={latency} {start} {step} height={160} /></div>
			{#if hasRate}
				<h2 class="mt-6 text-sm font-semibold">
					Download in use <span class="font-normal text-muted">(Mbit/s)</span>
				</h2>
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
		</section>
	{/if}

	<div class="mt-6 grid gap-6 lg:grid-cols-2">
		<section class="card p-5">
			<h2 class="text-sm font-semibold">Outages</h2>
			{#if view.outages.length}
				<ul class="mt-3 divide-y divide-line text-sm">
					{#each view.outages as o (o.start)}
						<li class="flex flex-wrap items-baseline gap-x-3 py-1.5">
							<span class="tabular">{dayClock(new Date(o.start * 1000))}</span>
							<span class="tabular text-muted">
								{o.end ? duration(Math.round(o.seconds)) : 'going on'}
							</span>
							<span class="text-xs text-muted">{o.why}</span>
						</li>
					{/each}
				</ul>
			{:else}
				<p class="mt-3 text-sm text-muted">None recorded.</p>
			{/if}
			{#if view.events.filter((e) => e.kind === 'reconnect' || e.kind === 'address').length}
				<h3 class="mt-5 text-xs font-medium text-muted">The line</h3>
				<ul class="mt-1 text-sm">
					{#each view.events.filter((e) => e.kind === 'reconnect' || e.kind === 'address') as e (e.t)}
						<li>
							<span class="tabular text-muted">{dayClock(new Date(e.t * 1000))}</span>
							{e.text}
						</li>
					{/each}
				</ul>
			{/if}
		</section>

		<section class="card p-5">
			<h2 class="text-sm font-semibold">Availability</h2>
			{#if view.months.length}
				<ul class="mt-3 text-sm">
					{#each [...view.months].reverse() as m (m.month)}
						<li class="flex justify-between py-0.5">
							<span>
								{new Date(m.month + '-01').toLocaleDateString([], {
									month: 'long',
									year: 'numeric'
								})}
							</span>
							<span class="tabular">
								{pct(m.availability, 3)}
								<span class="text-xs text-muted">· down {duration(m.down_seconds)}</span>
							</span>
						</li>
					{/each}
				</ul>
			{/if}
			{#if view.days.length}
				<h3 class="mt-5 text-xs font-medium text-muted">Last days</h3>
				<div class="mt-2 flex items-end gap-0.5" style="height: 48px">
					{#each view.days.slice(-31) as d (d.day)}
						{@const a = d.availability ?? 1}
						<div
							class="flex-1 rounded-sm"
							style:height="{Math.max(8, a * 100)}%"
							style:background={a >= 0.999
								? 'var(--good)'
								: a >= 0.99
									? 'var(--warning)'
									: 'var(--critical)'}
							title="{d.day}: {pct(d.availability, 2)}, down {duration(d.down_seconds)}{d.ms
								? `, ${d.ms} ms`
								: ''}"
						></div>
					{/each}
				</div>
			{/if}
		</section>
	</div>

	<!-- Who uses the line -->
	{#if view.clients && (view.clients.rows.length || view.clients.routers.length)}
		<section class="card mt-6 p-5">
			<h2 class="text-sm font-semibold">Who uses the line</h2>
			<p class="mt-1 text-xs text-muted">
				From nlbwmon on {view.clients.routers.map(name).join(', ')}: now (since its last read) and
				this accounting period.
			</p>
			{#each Object.entries(view.clients.errors) as [rid, e] (rid)}
				<p class="mt-1 text-xs" style:color="var(--warning-text)">{name(rid)}: {e}</p>
			{/each}
			{#if view.clients.rows.length}
				<table class="mt-3 w-full text-sm">
					<thead class="text-left text-xs text-muted">
						<tr>
							<th class="font-medium">Device</th>
							<th class="font-medium">Now ↓ · ↑</th>
							<th class="font-medium">Period ↓ · ↑</th>
						</tr>
					</thead>
					<tbody class="tabular">
						{#each view.clients.rows.slice(0, 15) as c (c.router + c.mac)}
							<tr class="border-t border-line">
								<td class="py-1">
									{c.name ?? c.mac}
									{#if c.name}<span class="font-mono text-xs text-faint">{c.mac}</span>{/if}
								</td>
								<td>{bitrate(c.rx_rate)} · {bitrate(c.tx_rate)}</td>
								<td>{bytes(c.rx)} · {bytes(c.tx)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{/if}
		</section>
	{/if}

	<!-- Speed -->
	<section class="card mt-6 p-5">
		<div class="flex flex-wrap items-baseline justify-between gap-2">
			<h2 class="text-sm font-semibold">Speed</h2>
			{#if store.me?.role === 'admin' && !store.health?.demo}
				<button class="btn btn-sm" disabled={testing || view.speedtest.testing} onclick={test}>
					<Gauge class="size-3.5" />
					{testing || view.speedtest.testing ? 'Measuring (half a minute)…' : 'Test now'}
				</button>
			{/if}
		</div>
		<p class="mt-1 text-xs text-muted">
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
		{#if testNote}<p class="mt-2 text-sm">{testNote}</p>{/if}
		{#if view.speedtest.error}<p class="mt-2 text-xs" style:color="var(--warning-text)">
				Last test: {view.speedtest.error}
			</p>{/if}
		{#if view.speedtests.length}
			<table class="mt-3 w-full text-sm">
				<thead class="text-left text-xs text-muted">
					<tr>
						<th class="font-medium">When</th><th class="font-medium">Down</th><th
							class="font-medium"
						>
							Up
						</th><th class="font-medium">Latency idle → loaded</th>
					</tr>
				</thead>
				<tbody class="tabular">
					{#each view.speedtests.slice(0, 10) as t (t.t)}
						{@const low =
							view.speedtest.booked_down &&
							t.down != null &&
							t.down < view.speedtest.booked_down * 5e5}
						<tr class="border-t border-line">
							<td class="py-1">{dayClock(new Date(t.t * 1000))}</td>
							<td style:color={low ? 'var(--warning-text)' : undefined}>{bitrate(t.down)}</td>
							<td>{bitrate(t.up)}</td>
							<td>
								{t.ms_idle ?? '–'} → {t.ms_loaded ?? '–'} ms
								{#if t.ms_idle && t.ms_loaded && t.ms_loaded > t.ms_idle * 4}<span
										class="text-xs"
										style:color="var(--warning-text)"
									>
										bufferbloat
									</span>{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</section>
{/if}
