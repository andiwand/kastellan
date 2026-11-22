<script lang="ts">
	import RangePicker from './ui/RangePicker.svelte';
	import { onMount } from 'svelte';
	import { api, type Bucket, type VitalField, type Vitals, type VitalsSeries } from '#lib/api.js';
	import { ago } from '#lib/format.js';
	import { store } from '#lib/state.svelte.js';
	import {
		detailOf,
		diskLimits,
		fieldsOf,
		formatVital,
		LABEL,
		limitsOf,
		isStale,
		toneColor,
		toneOf,
		toneText
	} from '#lib/vitals.js';
	import LatencyChart from './LatencyChart.svelte';

	/** A device's vitals, whatever kind it is: a tile each for CPU, memory, storage and
	 * temperature (the value, what it is of, a slim bar, the last 24 hours as a line). A tile
	 * opens its chart over a day, a week or a month. */
	let { device, vitals }: { device: string; vitals: Vitals } = $props();

	const RANGES = [
		{ label: '24 h', hours: 24, buckets: 96 },
		{ label: '7 d', hours: 168, buckets: 168 },
		{ label: '30 d', hours: 720, buckets: 180 }
	];
	let trend = $state<VitalsSeries | null>(null); // the last 24 hours, for the tiles' lines
	let open = $state<VitalField | null>(null);
	let range = $state(RANGES[0].hours);
	// The open chart's readings (every field's) and the range they are of: a refresh keeps them
	// until the new ones arrive; another range shows "Loading…" instead of the last one's.
	let chart = $state<{ hours: number; series: VitalsSeries | null; error: string | null } | null>(
		null
	);
	const shown = $derived(chart?.hours === range ? chart : null);
	const opened = $derived(open !== null);
	let refresh = $state(0);
	const stale = $derived(isStale(vitals, store.now));

	const disk = $derived(diskLimits(store.alarms));
	const fields = $derived(fieldsOf(vitals));

	async function loadTrend() {
		try {
			trend = await api.vitals(24, 48, device);
		} catch {
			// the tiles show without their lines
		}
	}
	onMount(() => {
		loadTrend();
		const t = setInterval(() => {
			loadTrend();
			refresh++;
		}, 300_000); // one sample every two minutes at most
		return () => clearInterval(t);
	});
	$effect(() => {
		if (!opened) return;
		const r = RANGES.find((r) => r.hours === range)!;
		void refresh; // refresh an open chart along with the tiles
		let current = true;
		api.vitals(r.hours, r.buckets, device).then(
			(series) => {
				if (current) chart = { hours: r.hours, series, error: null };
			},
			(e) => {
				if (current) chart = { hours: r.hours, series: null, error: (e as Error).message };
			}
		);
		return () => {
			current = false;
		};
	});

	const valueOf = (f: VitalField) => vitals[f] as number | null;
	const seriesOf = (s: VitalsSeries | null, f: VitalField) => s?.devices[device]?.[f] ?? [];

	/** The 24 hours as an SVG path (0–100, or the range of temperatures), gaps as gaps. */
	function spark(values: (number | null)[], f: VitalField): string {
		const known = values.filter((v): v is number => v != null);
		if (known.length < 2) return '';
		const lo = f === 'temperature' ? Math.min(...known) - 2 : 0;
		const hi = f === 'temperature' ? Math.max(...known) + 2 : 100;
		const n = values.length;
		let d = '';
		let pen = false;
		values.forEach((v, i) => {
			if (v == null) {
				pen = false;
				return;
			}
			const x = (i / (n - 1)) * 100;
			const y = 100 - ((v - lo) / (hi - lo || 1)) * 100;
			d += `${pen ? 'L' : 'M'}${x.toFixed(2)},${y.toFixed(2)}`;
			pen = true;
		});
		return d;
	}
	const buckets = (values: (number | null)[]): Bucket[] =>
		values.map((v) => ({ uptime: v == null ? null : 1, latency_ms: v }));
</script>

{#if fields.length}
	<section class="mt-8">
		<div class="flex flex-wrap items-baseline justify-between gap-2">
			<h2 class="text-sm font-semibold">Vitals</h2>
			<p class="text-xs text-muted">
				{stale ? 'Old reading · ' : ''}{ago(vitals.checked, store.now)}
			</p>
		</div>
		<div class="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
			{#each fields as f (f)}
				{@const v = valueOf(f)}
				{@const tone = stale ? 'normal' : toneOf(v, limitsOf(f, disk))}
				{@const line = spark(seriesOf(trend, f), f)}
				<button
					type="button"
					class="card group flex min-w-0 flex-col gap-2 p-3.5 text-left transition-colors hover:bg-surface-2/60"
					style:box-shadow={open === f ? '0 0 0 1.5px var(--accent)' : undefined}
					aria-expanded={open === f}
					onclick={() => (open = open === f ? null : f)}
				>
					<div class="flex items-baseline justify-between gap-2">
						<span class="text-xs text-muted">{LABEL[f]}</span>
						{#if tone !== 'normal'}
							<span class="text-[11px] font-medium" style:color={toneText[tone]}>
								{tone === 'warning' ? 'High' : 'Very high'}
							</span>
						{/if}
					</div>
					<div class="flex items-end justify-between gap-2">
						<span class="tabular shrink-0 text-xl leading-none font-semibold whitespace-nowrap">
							{#if v == null && f === 'cpu'}
								<span class="text-base">load {vitals.load?.toFixed(2)}</span>
							{:else}
								{formatVital(f, v)}
							{/if}
						</span>
						{#if line}
							<svg
								viewBox="0 0 100 100"
								preserveAspectRatio="none"
								class="h-7 w-20 min-w-0 shrink overflow-visible"
								aria-hidden="true"
							>
								<path
									d={line}
									fill="none"
									stroke={toneColor[tone]}
									stroke-width="1.5"
									stroke-linejoin="round"
									stroke-linecap="round"
									vector-effect="non-scaling-stroke"
									opacity="0.85"
								/>
							</svg>
						{/if}
					</div>
					{#if f !== 'temperature' && v != null}
						<span class="h-1 overflow-hidden rounded-full bg-[var(--empty)]">
							<span
								class="block h-full rounded-full"
								style:width="{Math.max(2, Math.min(100, v))}%"
								style:background={toneColor[tone]}
							></span>
						</span>
					{/if}
					<span class="tabular truncate text-[11px] text-faint">
						{detailOf(f, vitals) || ' '}
					</span>
				</button>
			{/each}
		</div>

		{#if open}
			{@const f = open}
			{@const values = seriesOf(shown?.series ?? null, f)}
			<div class="card mt-3 p-4">
				<div class="flex flex-wrap items-center justify-between gap-2">
					<h3 class="text-sm font-medium">{LABEL[f]} over time</h3>
					<RangePicker
						value={range}
						options={RANGES.map((r) => r.hours)}
						onchange={(h) => (range = h)}
					/>
				</div>
				{#if shown?.error}
					<p class="mt-3 text-sm" style:color="var(--critical-text)">{shown.error}</p>
				{:else if shown?.series && values.some((v) => v != null)}
					<div class="mt-3">
						<LatencyChart
							buckets={buckets(values)}
							start={shown.series.start}
							step={shown.series.step_seconds}
							format={(x) => formatVital(f, x)}
							fixedMax={f === 'temperature' ? undefined : 100}
							fixedMin={f === 'temperature'
								? Math.min(0, ...values.filter((v): v is number => v != null))
								: 0}
							label="{LABEL[f]} over time"
						/>
					</div>
				{:else if shown}
					<p class="mt-3 text-sm text-muted">No readings in this period.</p>
				{:else}
					<p class="mt-3 text-sm text-muted">Loading…</p>
				{/if}
			</div>
		{/if}
	</section>
{/if}
