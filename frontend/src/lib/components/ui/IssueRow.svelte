<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { Tone } from '#lib/states.js';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import StateDot from './StateDot.svelte';

	/** One problem or finding as a sentence row (not a table: a sentence doesn't split into
	 * columns): its tone's dot, the sentence (it wraps, never cut), a muted line with the detail
	 * and when, the action that fixes it on the right, and a link to where it is. Put rows in a
	 * `<ul class="card divide-y divide-line">`. `note`: a muted tag after the sentence
	 * ("Ada is on it", "in maintenance"). */
	let {
		tone,
		title,
		detail,
		meta,
		href,
		note,
		action
	}: {
		tone: Tone;
		title: string;
		detail?: string;
		meta?: string;
		href?: string;
		note?: string;
		action?: Snippet;
	} = $props();
	const word = $derived(tone === 'critical' ? 'Critical' : tone === 'warning' ? 'Warning' : 'Note');
</script>

<li class="flex items-start gap-3 px-4 py-3">
	<span class="mt-1.5"><StateDot {tone} label={word} /></span>
	<div class="min-w-0 flex-1">
		<p class="text-body font-medium">
			{#if href}<a {href} class="hover:underline">{title}</a>{:else}{title}{/if}
			{#if note}<span
					class="ml-1 rounded-md bg-surface-2 px-1.5 py-0.5 align-[1px] text-meta font-normal text-muted"
				>
					{note}
				</span>{/if}
		</p>
		{#if detail || meta}
			<p class="mt-0.5 text-meta text-muted">
				{detail}{#if detail && meta}<span class="text-faint"> · </span>{/if}{#if meta}<span
						class="tabular text-faint"
					>
						{meta}
					</span>{/if}
			</p>
		{/if}
	</div>
	{#if action}<div class="flex shrink-0 items-center gap-2">{@render action()}</div>{/if}
	{#if href}
		<a {href} class="-mr-1 grid size-7 shrink-0 place-items-center text-faint hover:text-text">
			<ChevronRight class="size-4" />
			<span class="sr-only">Open</span>
		</a>
	{/if}
</li>
