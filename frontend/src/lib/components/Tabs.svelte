<script lang="ts" generics="T extends string">
	/** A row of tabs; `mono` tabs name a file (config.yaml). */
	let {
		tabs,
		current,
		onselect
	}: {
		tabs: { id: T; label: string; mono?: boolean }[];
		current: T;
		onselect: (id: T) => void;
	} = $props();
</script>

<div
	class="inline-flex max-w-full gap-1 overflow-x-auto rounded-xl border border-line bg-surface p-1 shadow-card"
	role="tablist"
>
	{#each tabs as t (t.id)}
		<button
			role="tab"
			aria-selected={current === t.id}
			class="h-8 shrink-0 rounded-lg px-3.5 text-sm font-medium {current === t.id
				? 'bg-surface-3 text-text'
				: 'text-muted hover:text-text'} {t.mono ? 'font-mono text-[13px]' : ''}"
			onclick={() => onselect(t.id)}
		>
			{t.label}
		</button>
	{/each}
</div>
