<script lang="ts">
	import type { Snippet } from 'svelte';

	/** The top of every page: the title, at most one line saying what the page is for, the
	 * page's actions on the right (a primary button, Follow, Sign in to manage), and below it one
	 * row of controls (search, filters, range, view switch) or the page's tabs. */
	let {
		title,
		summary,
		actions,
		controls,
		tabs
	}: {
		title: string;
		summary?: string;
		actions?: Snippet;
		controls?: Snippet;
		tabs?: Snippet;
	} = $props();
</script>

<header class="mb-6">
	<div class="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
		<div class="min-w-0">
			<h1 class="text-title">{title}</h1>
			{#if summary}<p class="mt-1 max-w-2xl text-body text-muted">{summary}</p>{/if}
		</div>
		{#if actions}<div class="flex flex-wrap items-center gap-2">{@render actions()}</div>{/if}
	</div>
	{#if tabs}<div class="mt-4">{@render tabs()}</div>{/if}
	{#if controls}<div class="mt-4 flex flex-wrap items-center gap-3">{@render controls()}</div>{/if}
</header>
