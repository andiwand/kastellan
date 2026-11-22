<script lang="ts">
	import type { Snippet } from 'svelte';

	/** The top of every page: the title, at most one line saying what the page is for, the
	 * page's actions on the right (a primary button, Follow, Sign in to manage), and below it one
	 * row of controls (search, filters, range, view switch) or the page's tabs. `badges` sit
	 * next to the title (a device's State, its maintenance). */
	let {
		title,
		badges,
		summary,
		actions,
		controls,
		tabs
	}: {
		title: string;
		badges?: Snippet;
		summary?: string;
		actions?: Snippet;
		controls?: Snippet;
		tabs?: Snippet;
	} = $props();
</script>

<header class="mb-6">
	<div class="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
		<div class="min-w-0">
			{#if badges}<div class="flex flex-wrap items-center gap-x-3 gap-y-1.5">
					<h1 class="text-title">{title}</h1>
					{@render badges()}
				</div>{:else}<h1 class="text-title">{title}</h1>{/if}
			{#if summary}<p class="mt-1 max-w-2xl text-body text-muted">{summary}</p>{/if}
		</div>
		<!-- On a phone the actions get their own row under the title, so neither is squeezed. -->
		{#if actions}<div class="flex flex-wrap items-center gap-2 max-sm:w-full">
				{@render actions()}
			</div>{/if}
	</div>
	{#if tabs}<div class="mt-4">{@render tabs()}</div>{/if}
	{#if controls}<div class="mt-4 flex flex-wrap items-center gap-3">{@render controls()}</div>{/if}
</header>
