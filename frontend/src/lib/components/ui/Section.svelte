<script lang="ts">
	import type { Snippet } from 'svelte';

	/** A part of a page: its heading outside the card, with a muted count, a meta note (source,
	 * freshness) and actions on the right; the content below. `id` is the anchor deep links land
	 * on (attention.ts SECTION); `href` makes the title a link (a host's section links to its
	 * device). The first section of a page passes `first` (no gap above). */
	let {
		title,
		href,
		count,
		meta,
		id,
		first = false,
		actions,
		children
	}: {
		title: string;
		href?: string;
		count?: number | null;
		meta?: string | Snippet;
		id?: string;
		first?: boolean;
		actions?: Snippet;
		children: Snippet;
	} = $props();
</script>

<section {id} class="scroll-mt-6 {first ? '' : 'mt-section'}">
	<div class="mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
		<h2 class="text-section">
			{#if href}<a {href} class="hover:underline">
					{title}
				</a>{:else}{title}{/if}{#if count != null}<span
					class="tabular ml-1.5 font-normal text-faint"
				>
					{count}
				</span>{/if}
		</h2>
		{#if meta || actions}
			<div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-meta text-muted">
				{#if typeof meta === 'string'}<span>{meta}</span>{:else if meta}{@render meta()}{/if}
				{#if actions}{@render actions()}{/if}
			</div>
		{/if}
	</div>
	{@render children()}
</section>
