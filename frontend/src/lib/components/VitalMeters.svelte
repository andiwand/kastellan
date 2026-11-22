<script lang="ts">
	import type { VitalField, Vitals } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import {
		detailOf,
		diskLimits,
		formatVital,
		isStale,
		LABEL,
		limitsOf,
		toneColor,
		toneOf,
		toneText
	} from '#lib/vitals.js';

	/** A device's CPU, memory and storage as three slim bars (the devices list): the value, and
	 * colour only when it is high. A CPU known only by its load shows the load. */
	let { vitals }: { vitals: Vitals | null | undefined } = $props();

	const disk = $derived(diskLimits(store.alarms));
	const SHORT: Record<string, string> = { cpu: 'CPU', memory: 'RAM', storage: 'Disk' };
	const stale = $derived(vitals ? isStale(vitals, store.now) : false);
	const rows = $derived(
		vitals
			? (['cpu', 'memory', 'storage'] as VitalField[])
					.map((f) => ({ f, v: vitals[f] as number | null }))
					.filter(({ f, v }) => v != null || (f === 'cpu' && vitals.load != null))
			: []
	);
</script>

{#if vitals && rows.length}
	<div class="grid w-full gap-1" class:opacity-60={stale} aria-label="Vitals">
		{#if stale}<span class="text-[10px] text-muted">Old reading</span>{/if}
		{#each rows as { f, v } (f)}
			{@const tone = stale ? 'normal' : toneOf(v, limitsOf(f, disk))}
			<div
				class="grid grid-cols-[1.9rem_minmax(0,1fr)_2.2rem] items-center gap-1.5 text-[10.5px] leading-none"
				title="{LABEL[f]}: {v == null ? '' : formatVital(f, v)}{detailOf(f, vitals)
					? ` (${detailOf(f, vitals)})`
					: ''}"
			>
				<span class="text-faint">{SHORT[f]}</span>
				{#if v == null}
					<span class="tabular truncate text-faint">load {vitals.load?.toFixed(2)}</span>
					<span></span>
				{:else}
					<span class="h-1 overflow-hidden rounded-full bg-[var(--empty)]">
						<span
							class="block h-full rounded-full"
							style:width="{Math.max(2, Math.min(100, v))}%"
							style:background={toneColor[tone]}
						></span>
					</span>
					<span class="tabular text-right" style:color={toneText[tone]}>
						{v.toFixed(0)}%
					</span>
				{/if}
			</div>
		{/each}
	</div>
{/if}
