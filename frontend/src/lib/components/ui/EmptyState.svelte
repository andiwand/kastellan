<script lang="ts">
	import type { Snippet } from 'svelte';
	import Lock from '@lucide/svelte/icons/lock';
	import Inbox from '@lucide/svelte/icons/inbox';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';

	/** What stands where content would be. `empty`: nothing yet, with how it comes (one muted
	 * line). `unavailable`: it can't be shown here (sign-in off, demo mode, admins only), with
	 * the reason and where that is fixed; never red. `error`: reading it failed (the one red
	 * case), with a retry. A section that would only ever be empty is better hidden. */
	let {
		kind = 'empty',
		text,
		action,
		href,
		onretry,
		bare = false,
		children
	}: {
		kind?: 'empty' | 'unavailable' | 'error';
		text?: string;
		action?: string; // the link's words, with href
		href?: string;
		onretry?: () => void;
		bare?: boolean; // without the card
		children?: Snippet;
	} = $props();
	const Icon = $derived(kind === 'unavailable' ? Lock : kind === 'error' ? TriangleAlert : Inbox);
</script>

<div
	class="flex items-start gap-3 text-body {bare ? 'py-2' : 'card px-5 py-5'} {kind === 'error'
		? ''
		: 'text-muted'}"
	style:color={kind === 'error' ? 'var(--critical-text)' : undefined}
	role={kind === 'error' ? 'alert' : undefined}
>
	<Icon class="mt-0.5 size-4 shrink-0 {kind === 'error' ? '' : 'text-faint'}" />
	<div class="min-w-0">
		{#if text}<p>{text}</p>{/if}
		{#if children}{@render children()}{/if}
		{#if (action && href) || onretry}
			<p class="mt-1">
				{#if action && href}<a class="font-medium text-accent hover:underline" {href}>
						{action}
					</a>{/if}
				{#if onretry}<button class="font-medium text-accent hover:underline" onclick={onretry}>
						Try again
					</button>{/if}
			</p>
		{/if}
	</div>
</div>
