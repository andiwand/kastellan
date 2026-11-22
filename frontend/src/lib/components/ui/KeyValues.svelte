<script lang="ts" module>
	import type { Snippet } from 'svelte';

	/** One fact: its label and value. A value of null or '' isn't known: it shows "Not read"
	 * (or `missing`), with a link to where it is set up (`setup`). `content` renders a value
	 * that is more than text (a badge, a link with an icon). */
	export interface KeyValue {
		label: string;
		value?: string | number | null;
		mono?: boolean; // an identifier: an address, a MAC, an image
		href?: string;
		hint?: string; // a muted second line under the value
		missing?: string;
		setup?: string; // where a missing value is set up
		content?: Snippet;
	}
</script>

<script lang="ts">
	import { WORD } from '#lib/glossary.js';

	/** Facts as label / value pairs, instead of a line of facts joined by "·". In one to three
	 * columns (one on phones); in a card unless `bare`. */
	let {
		items,
		columns = 2,
		bare = false
	}: { items: KeyValue[]; columns?: 1 | 2 | 3; bare?: boolean } = $props();
	const grid = $derived(
		columns === 3 ? 'sm:grid-cols-2 lg:grid-cols-3' : columns === 2 ? 'sm:grid-cols-2' : ''
	);
</script>

<!-- Each pair has a line above it; the first row's is cut off, so lines sit only between rows. -->
<div class="overflow-hidden {bare ? '' : 'card px-4'}">
	<dl class="-mt-px grid gap-x-8 {grid}">
		{#each items as item (item.label)}
			<div class="flex min-w-0 items-baseline justify-between gap-4 border-t border-line py-2.5">
				<dt class="shrink-0 text-cell text-muted">{item.label}</dt>
				<dd class="min-w-0 text-right text-cell">
					{#if item.content}
						{@render item.content()}
					{:else if item.value == null || item.value === ''}
						<span class="text-faint">{item.missing ?? WORD.notRead}</span>
						{#if item.setup}
							· <a class="text-accent hover:underline" href={item.setup}>{WORD.setUp}</a>
						{/if}
					{:else if item.href}
						<a class="break-words hover:underline {item.mono ? 'font-mono' : ''}" href={item.href}>
							{item.value}
						</a>
					{:else}
						<span class="tabular break-words {item.mono ? 'font-mono' : ''}">{item.value}</span>
					{/if}
					{#if item.hint}<span class="block text-meta text-faint">{item.hint}</span>{/if}
				</dd>
			</div>
		{/each}
	</dl>
</div>
