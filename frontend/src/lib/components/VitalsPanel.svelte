<script lang="ts">
	import { onMount } from 'svelte';
	import { api, type Bucket, type VitalField, type Vitals, type VitalsSeries } from '#lib/api.js';
	import { ago } from '#lib/format.js';
	import { store } from '#lib/state.svelte.js';
	import {
		detailOf,
		fieldsOf,
		formatVital,
		LABEL,
		limitsOf,
		toneColor,
		toneOf
	} from '#lib/vitals.js';
	import LatencyChart from './LatencyChart.svelte';

	/** A device's vitals, whatever kind it is: a tile each for CPU, memory, storage and
	 * temperature (the value, what it is of, a slim bar, the last 24 hours as a line). A tile
	 * opens its chart over a day, a week or a month. */
	let { device, vitals }: { device: string; vitals: Vitals } = $props();

	const RANGES = [
		{ label: '24 hours', hours: 24, buckets: 96 },
		{ label: '7 days', hours: 168, buckets: 168 },
		{ label: '30 days', hours: 720, buckets: 180 }
	];
	let trend = $state<VitalsSeries | null>(null); // the last 24 hours, for the tiles' lines
	let open = $state<VitalField | null>(null);
	let range = $state(RANGES[0]);
	let chart = $state<VitalsSeries | null>(null);
	let chartError = $state<string | null>(null);

	const disk = $derived({
		warning: store.alarms?.limits?.disk_warning ?? 85,
		critical: store.alarms?.limits?.disk_critical ?? 95
	});
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
		const t = setInterval(loadTrend, 300_000); // one sample every two minutes at most
		return () => clearInterval(t);
	});
	$effect(() => {
		const field = open;
		const r = range;
		if (!field) return;
		chartError = null;
		api.vitals(r.hours, r.buckets, device).then(
			(s) => (chart = s),
			(e) => (chartError = (e as Error).message)
		);
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
			<p class="text-xs text-muted">{ago(vitals.checked)}</p>
		</div>
		<div class="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
			{#each fields as f (f)}
				{@const v = valueOf(f)}
				{@const tone = toneOf(v, limitsOf(f, disk))}
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
							<span
								class="text-[11px] font-medium"
								style:color={tone === 'warning' ? 'var(--warning-text)' : 'var(--critical-text)'}
							>
								{tone === 'warning' ? 'High' : 'Very high'}
							</span>
						{/if}
					</div>
					<div class="flex items-end justify-between gap-2">
						<span class="tabular text-xl leading-none font-semibold">
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
								class="h-7 w-20 shrink-0 overflow-visible"
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
			{@const values = seriesOf(chart, f)}
			<div class="card mt-3 p-4">
				<div class="flex flex-wrap items-center justify-between gap-2">
					<h3 class="text-sm font-medium">{LABEL[f]} over time</h3>
					<div class="flex gap-1 rounded-xl border border-line bg-surface p-1 shadow-card">
						{#each RANGES as r (r.label)}
							<button
								onclick={() => (range = r)}
								class="h-7 rounded-lg px-3 text-xs font-medium transition-colors {range === r
									? 'bg-surface-3 text-text'
									: 'text-muted hover:text-text'}"
							>
								{r.label}
							</button>
						{/each}
					</div>
				</div>
				{#if chartError}
					<p class="mt-3 text-sm" style:color="var(--critical-text)">{chartError}</p>
				{:else if chart && values.some((v) => v != null)}
					<div class="mt-3">
						<LatencyChart
							buckets={buckets(values)}
							start={chart.start}
							step={chart.step_seconds}
							format={(x) => formatVital(f, x)}
							fixedMax={f === 'temperature' ? undefined : 100}
							label="{LABEL[f]} over time"
						/>
					</div>
				{:else if chart}
					<p class="mt-3 text-sm text-muted">
						No history yet: Kastellan keeps a reading every two minutes from now on.
					</p>
				{/if}
			</div>
		{/if}
	</section>
{/if}
