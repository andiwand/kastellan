<script lang="ts">
	import { onMount } from 'svelte';
	import { api, type Bucket, type DiskSeries } from '#lib/api.js';
	import { clock } from '#lib/format.js';
	import LatencyChart from './LatencyChart.svelte';
	import Tabs from './Tabs.svelte';

	/** A host's disks over the day: one strip each, a bar per slice: grey while it slept, the
	 * accent while it spun, stronger the busier it was; now, its state and warmth. A disk opens
	 * its charts (busy, asleep, temperature) over a day, a week or a month. */
	let { device }: { device: string } = $props();

	const RANGES = [
		{ label: '24 h', hours: 24, buckets: 96 },
		{ label: '7 d', hours: 168, buckets: 168 },
		{ label: '30 d', hours: 720, buckets: 180 }
	];
	type Field = 'busy' | 'asleep' | 'temperature';
	const FIELDS: { id: Field; label: string }[] = [
		{ id: 'busy', label: 'Busy' },
		{ id: 'asleep', label: 'Asleep' },
		{ id: 'temperature', label: 'Temperature' }
	];
	let day = $state<DiskSeries | null>(null);
	let open = $state<string | null>(null);
	let field = $state<Field>('busy');
	let range = $state(RANGES[0].hours);
	let chart = $state<{ hours: number; series: DiskSeries } | null>(null);
	let error = $state<string | null>(null);
	let refresh = $state(0);

	async function loadDay() {
		try {
			day = await api.disks(device, 24, 96);
		} catch {
			// the section shows without it
		}
	}
	onMount(() => {
		loadDay();
		const t = setInterval(() => {
			loadDay();
			refresh++;
		}, 300_000);
		return () => clearInterval(t);
	});
	$effect(() => {
		const r = RANGES.find((x) => x.hours === range)!;
		void refresh;
		chart = null;
		error = null;
		if (!open) return;
		let current = true;
		api.disks(device, r.hours, r.buckets).then(
			(series) => {
				if (current) chart = { hours: r.hours, series };
			},
			(e) => {
				if (current) error = (e as Error).message;
			}
		);
		return () => {
			current = false;
		};
	});

	const names = $derived(Object.keys(day?.disks ?? {}).sort());
	const last = (values: (number | null)[]) => values.at(-1) ?? null;
	function shade(busy: number | null, asleep: number | null): string {
		if (busy == null && asleep == null) return 'var(--empty)';
		if (asleep != null && asleep >= 50) return 'var(--line-strong)';
		const strength = Math.round(30 + Math.min(70, (busy ?? 0) * 1.4));
		return `color-mix(in srgb, var(--accent) ${strength}%, var(--empty))`;
	}
	const slice = (i: number) => {
		if (!day) return '';
		const t0 = Date.parse(day.start) + i * day.step_seconds * 1000;
		return `${clock(new Date(t0))}–${clock(new Date(t0 + day.step_seconds * 1000))}`;
	};
	const buckets = (values: (number | null)[]): Bucket[] =>
		values.map((v) => ({ uptime: v == null ? null : 1, latency_ms: v }));
	const format = (f: Field) => (v: number) =>
		f === 'temperature' ? `${v.toFixed(0)} °C` : `${v.toFixed(0)} %`;
</script>

{#if names.length && day}
	<div class="card mt-3 p-4">
		<h3 class="text-xs font-medium text-muted">Disks over the last 24 hours</h3>
		<ul class="mt-2 space-y-2">
			{#each names as name (name)}
				{@const d = day.disks[name]}
				{@const asleepNow = last(d.asleep)}
				{@const busyNow = last(d.busy)}
				{@const warm = last(d.temperature)}
				<li>
					<button
						type="button"
						class="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-3 rounded-md text-left text-sm hover:bg-surface-2/60 sm:grid-cols-[4.5rem_minmax(0,1fr)_8rem]"
						aria-expanded={open === name}
						onclick={() => (open = open === name ? null : name)}
					>
						<span class="font-mono text-xs">{name}</span>
						<span
							class="col-span-2 row-start-2 flex h-4 gap-px sm:col-span-1 sm:row-start-auto"
							aria-label="{name} over the day"
						>
							{#each d.busy as b, i (i)}
								<span
									class="flex-1 rounded-[1px]"
									style:background={shade(b, d.asleep[i])}
									title="{slice(i)}: {d.asleep[i] != null && d.asleep[i]! >= 50
										? 'asleep'
										: b != null
											? `busy ${b.toFixed(0)} %`
											: 'not read'}{d.temperature[i] != null ? ` · ${d.temperature[i]} °C` : ''}"
								></span>
							{/each}
						</span>
						<span class="tabular text-right text-xs text-muted">
							{asleepNow != null && asleepNow >= 50
								? 'asleep'
								: busyNow != null
									? `busy ${busyNow.toFixed(0)} %`
									: '–'}{warm != null && !(asleepNow != null && asleepNow >= 50)
								? ` · ${warm.toFixed(0)} °C`
								: ''}
						</span>
					</button>
				</li>
			{/each}
		</ul>
		<p class="mt-2 text-xs text-faint">
			Grey: asleep (spun down); blue: spinning, stronger the busier. A disk shows its charts.
		</p>

		{#if open}
			{@const values = chart?.series.disks[open]?.[field] ?? []}
			<div class="mt-4 border-t border-line pt-4">
				<div class="flex flex-wrap items-center justify-between gap-2">
					<Tabs small label="What" tabs={FIELDS} current={field} onselect={(f) => (field = f)} />
					<Tabs
						small
						label="Range"
						tabs={RANGES.map((r) => ({ id: r.hours, label: r.label }))}
						current={range}
						onselect={(h) => (range = h)}
					/>
				</div>
				{#if error}
					<p class="mt-3 text-sm text-muted">Cannot read disk history: {error}</p>
				{:else if chart && chart.hours === range && values.some((v) => v != null)}
					<div class="mt-3">
						<LatencyChart
							buckets={buckets(values)}
							start={chart.series.start}
							step={chart.series.step_seconds}
							format={format(field)}
							fixedMax={field === 'temperature' ? undefined : 100}
							fixedMin={field === 'temperature'
								? Math.min(0, ...values.filter((v): v is number => v != null))
								: 0}
							label="{open}: {field}"
						/>
					</div>
				{:else if chart && chart.hours === range}
					<p class="mt-3 text-sm text-muted">No readings in this period.</p>
				{:else}
					<p class="mt-3 text-sm text-muted">Loading…</p>
				{/if}
			</div>
		{/if}
	</div>
{/if}
