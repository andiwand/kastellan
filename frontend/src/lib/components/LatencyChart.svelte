<script lang="ts">
	import type { Bucket } from '#lib/api.js';
	import { clock, ms } from '#lib/format.js';

	/** Mean latency per bucket: a 2 px line over a soft area, with a crosshair and a tooltip. */
	let {
		buckets,
		start,
		step,
		height = 180
	}: { buckets: Bucket[]; start: string; step: number; height?: number } = $props();

	let width = $state(600);
	let hover = $state<number | null>(null);

	const pad = { top: 12, right: 12, bottom: 24, left: 48 };
	const t0 = $derived(Date.parse(start));
	const values = $derived(buckets.map((b) => b.latency_ms));
	const max = $derived.by(() => {
		const known = values.filter((v): v is number => v != null);
		if (!known.length) return 1;
		return niceMax(Math.max(...known) * 1.1);
	});
	const innerW = $derived(Math.max(10, width - pad.left - pad.right));
	const innerH = $derived(height - pad.top - pad.bottom);
	const x = (i: number) => pad.left + ((i + 0.5) / buckets.length) * innerW;
	const y = (v: number) => pad.top + innerH - (v / max) * innerH;

	function niceMax(v: number) {
		const p = 10 ** Math.floor(Math.log10(v));
		for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
		return 10 * p;
	}

	/** Runs of consecutive buckets with data, so gaps show as gaps. */
	const runs = $derived.by(() => {
		const out: { i: number; v: number }[][] = [];
		let run: { i: number; v: number }[] = [];
		values.forEach((v, i) => {
			if (v == null) {
				if (run.length) out.push(run);
				run = [];
			} else run.push({ i, v });
		});
		if (run.length) out.push(run);
		return out;
	});
	const line = (run: { i: number; v: number }[]) =>
		run.map((p, k) => `${k ? 'L' : 'M'}${x(p.i).toFixed(1)},${y(p.v).toFixed(1)}`).join('');
	const area = (run: { i: number; v: number }[]) =>
		`${line(run)}L${x(run[run.length - 1].i).toFixed(1)},${y(0)}L${x(run[0].i).toFixed(1)},${y(0)}Z`;

	const yTicks = $derived([0, max / 2, max]);
	const xTicks = $derived.by(() => {
		const total = buckets.length * step;
		const every = total > 3 * 86400 ? 86400 : total > 6 * 3600 ? 6 * 3600 : 3600;
		const ticks: { i: number; label: string }[] = [];
		const first = Math.ceil(t0 / 1000 / every) * every;
		for (let t = first; t < t0 / 1000 + total; t += every) {
			const i = (t - t0 / 1000) / step - 0.5;
			const d = new Date(t * 1000);
			ticks.push({
				i,
				label: every >= 86400 ? d.toLocaleDateString([], { weekday: 'short' }) : clock(d)
			});
		}
		return ticks;
	});

	function move(e: PointerEvent) {
		const rect = (e.currentTarget as SVGElement).getBoundingClientRect();
		const i = Math.floor(((e.clientX - rect.left - pad.left) / innerW) * buckets.length);
		hover = i >= 0 && i < buckets.length ? i : null;
	}

	const tip = $derived.by(() => {
		if (hover == null) return null;
		const v = values[hover];
		return {
			left: x(hover),
			top: v == null ? pad.top : y(v),
			value: v,
			range: `${clock(new Date(t0 + hover * step * 1000))} – ${clock(new Date(t0 + (hover + 1) * step * 1000))}`
		};
	});
</script>

<div class="relative" bind:clientWidth={width}>
	<svg
		{width}
		{height}
		class="block touch-none select-none"
		role="img"
		aria-label="Latency over time"
		onpointermove={move}
		onpointerleave={() => (hover = null)}
	>
		<defs>
			<linearGradient id="latency-fill" x1="0" y1="0" x2="0" y2="1">
				<stop offset="0" stop-color="var(--accent)" stop-opacity="0.22" />
				<stop offset="1" stop-color="var(--accent)" stop-opacity="0" />
			</linearGradient>
		</defs>
		{#each yTicks as t (t)}
			<line
				x1={pad.left}
				x2={width - pad.right}
				y1={y(t)}
				y2={y(t)}
				stroke={t === 0 ? 'var(--line-strong)' : 'var(--line)'}
			/>
			<text
				x={pad.left - 8}
				y={y(t)}
				dy="0.32em"
				text-anchor="end"
				class="tabular fill-faint text-[11px]"
			>
				{t === 0 ? '0' : ms(t)}
			</text>
		{/each}
		{#each xTicks as t (t.i)}
			<text
				x={x(t.i) + innerW / buckets.length / 2}
				y={height - 6}
				text-anchor="middle"
				class="tabular fill-faint text-[11px]"
			>
				{t.label}
			</text>
		{/each}
		{#each runs as run, k (k)}
			{#if run.length > 1}
				<path d={area(run)} fill="url(#latency-fill)" />
				<path
					d={line(run)}
					fill="none"
					stroke="var(--accent)"
					stroke-width="2"
					stroke-linejoin="round"
					stroke-linecap="round"
				/>
			{:else}
				<circle cx={x(run[0].i)} cy={y(run[0].v)} r="2.5" fill="var(--accent)" />
			{/if}
		{/each}
		{#if tip}
			<line
				x1={tip.left}
				x2={tip.left}
				y1={pad.top}
				y2={y(0)}
				stroke="var(--line-strong)"
				stroke-dasharray="3 3"
			/>
			{#if tip.value != null}
				<circle
					cx={tip.left}
					cy={tip.top}
					r="4.5"
					fill="var(--accent)"
					stroke="var(--surface)"
					stroke-width="2"
				/>
			{/if}
		{/if}
	</svg>
	{#if tip}
		<div
			class="pointer-events-none absolute z-20 -translate-x-1/2 -translate-y-full rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs whitespace-nowrap shadow-pop"
			style:left="clamp(4.5rem, {tip.left}px, calc(100% - 4.5rem))"
			style:top="{tip.top - 10}px"
		>
			<div class="tabular text-muted">{tip.range}</div>
			<div class="tabular font-medium">{tip.value == null ? 'No reply' : ms(tip.value)}</div>
		</div>
	{/if}
</div>
