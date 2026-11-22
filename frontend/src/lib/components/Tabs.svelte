<script lang="ts" generics="T extends string | number">
	import type { Component } from 'svelte';

	/** A row of choices, one of them current: tabs, filters (with how many each shows) and time
	 * ranges (`small`). `mono` choices name a file (config.yaml). */
	let {
		tabs,
		current,
		onselect,
		small = false,
		label,
		class: extra = ''
	}: {
		tabs: {
			id: T;
			label: string;
			mono?: boolean;
			count?: number;
			icon?: Component<{ class?: string; strokeWidth?: number }>;
		}[];
		current: T;
		onselect: (id: T) => void;
		small?: boolean;
		label?: string;
		class?: string;
	} = $props();
</script>

<div
	class="inline-flex max-w-full gap-1 overflow-x-auto rounded-xl border border-line bg-surface p-1 shadow-card {extra}"
	role="tablist"
	aria-label={label}
>
	{#each tabs as t (t.id)}
		<button
			role="tab"
			aria-selected={current === t.id}
			class="flex shrink-0 items-center gap-1.5 rounded-lg font-medium transition-colors {small
				? 'h-7 px-3 text-xs'
				: 'h-8 px-3.5 text-sm'} {current === t.id
				? 'bg-surface-3 text-text'
				: 'text-muted hover:text-text'} {t.mono ? 'font-mono text-[13px]' : ''}"
			onclick={() => onselect(t.id)}
		>
			{#if t.icon}<t.icon class="size-4" strokeWidth={1.9} />{/if}
			{t.label}
			{#if t.count !== undefined}<span class="tabular text-xs text-faint">{t.count}</span>{/if}
		</button>
	{/each}
</div>
