<script lang="ts">
	import type { Bucket } from '#lib/api.js';
	import { bucketStatus, statusColor, statusLabel } from '#lib/status.js';
	import { clock, ms, percent } from '#lib/format.js';
	import { mergeBuckets } from '#lib/status.js';

	let {
		buckets: raw,
		start,
		step: rawStep,
		alwaysOn = true,
		height = 'h-6',
		label = 'Uptime'
	}: {
		buckets: Bucket[];
		start: string;
		step: number;
		alwaysOn?: boolean;
		height?: string;
		label?: string;
	} = $props();

	// Each bar needs a few pixels; when the strip is narrow, neighbouring buckets merge so it
	// never overflows its column.
	let width = $state(0);
	const group = $derived(width ? Math.max(1, Math.ceil(raw.length / Math.floor(width / 5))) : 1);
	const buckets = $derived(mergeBuckets(raw, group));
	const step = $derived(rawStep * group);

	let hover = $state<number | null>(null);
	const t0 = $derived(Date.parse(start));
	const tip = $derived.by(() => {
		if (hover == null) return null;
		const b = buckets[hover];
		const from = new Date(t0 + hover * step * 1000);
		const to = new Date(t0 + (hover + 1) * step * 1000);
		const s = bucketStatus(b.uptime, alwaysOn);
		return {
			x: (hover + 0.5) / buckets.length,
			range: `${clock(from)} – ${clock(to)}`,
			text:
				s === 'empty'
					? 'No data'
					: `${s === 'up' ? statusLabel.up : s === 'offline' ? statusLabel.offline : `${percent(b.uptime)} up`}${b.latency_ms != null ? ` · ${ms(b.latency_ms)}` : ''}`
		};
	});
</script>

<div
	class="relative"
	bind:clientWidth={width}
	role="img"
	aria-label="{label}: one bar per {Math.round(step / 60)} minutes"
	onpointermove={(e) => {
		const rect = e.currentTarget.getBoundingClientRect();
		const i = Math.floor(((e.clientX - rect.left) / rect.width) * buckets.length);
		hover = i >= 0 && i < buckets.length ? i : null;
	}}
	onpointerleave={() => (hover = null)}
>
	<div class="flex gap-[2px] {height}">
		{#each buckets as b, i (i)}
			{@const s = bucketStatus(b.uptime, alwaysOn)}
			<div
				class="flex-1 rounded-[2px] transition-opacity"
				class:opacity-60={hover != null && hover !== i}
				style:background={s === 'empty'
					? 'var(--empty)'
					: s === 'up'
						? 'var(--good-soft)'
						: statusColor[s].fill}
			></div>
		{/each}
	</div>
	{#if tip}
		<div
			class="pointer-events-none absolute bottom-full z-20 mb-2 -translate-x-1/2 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs whitespace-nowrap shadow-pop"
			style:left="clamp(4rem, {tip.x * 100}%, calc(100% - 4rem))"
		>
			<div class="tabular text-muted">{tip.range}</div>
			<div class="tabular font-medium">{tip.text}</div>
		</div>
	{/if}
</div>
