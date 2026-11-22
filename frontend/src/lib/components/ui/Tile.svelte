<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { State } from '#lib/states.js';
	import StateBadge from './StateBadge.svelte';

	/** A number someone compares: its label, a state word, the number (with its unit), one
	 * muted line, and an optional micro-strip (an UptimeStrip, a sparkline) at the bottom. A link
	 * when `href`. Text that isn't a number belongs in KeyValues. Rows of 2-4:
	 * `<div class="grid grid-cols-2 gap-3 sm:grid-cols-4">`. */
	let {
		label,
		value,
		unit,
		state,
		sub,
		href,
		strip
	}: {
		label: string;
		value: string | number | null;
		unit?: string;
		state?: State;
		sub?: string;
		href?: string;
		strip?: Snippet;
	} = $props();
</script>

<svelte:element
	this={href ? 'a' : 'div'}
	{href}
	class="card flex min-w-0 flex-col p-4 {href ? 'transition-colors hover:border-line-strong' : ''}"
>
	<div class="flex items-center justify-between gap-2">
		<span class="truncate text-meta text-muted">{label}</span>
		{#if state}<StateBadge {state} quiet />{/if}
	</div>
	<p class="tabular mt-1.5 text-2xl font-semibold tracking-tight">
		{value ?? '–'}{#if unit && value != null}<span class="ml-1 text-sm font-normal text-faint">
				{unit}
			</span>{/if}
	</p>
	{#if sub}<p class="mt-0.5 truncate text-meta text-muted">{sub}</p>{/if}
	{#if strip}<div class="mt-3">{@render strip()}</div>{/if}
</svelte:element>
