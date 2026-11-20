<script lang="ts">
	import type { Container } from '#lib/api.js';
	import ArrowUpCircle from '@lucide/svelte/icons/circle-arrow-up';
	import CircleHelp from '@lucide/svelte/icons/circle-help';
	import Check from '@lucide/svelte/icons/check';

	/** A host's containers: what runs, and whether a newer image exists (or why that's unknown). */
	let { containers }: { containers: Container[] } = $props();

	const order = (c: Container) => (c.update ? 0 : c.unchecked ? 1 : 2);
	const sorted = $derived(
		[...containers].sort((a, b) => order(a) - order(b) || a.name.localeCompare(b.name))
	);
</script>

<div class="card overflow-hidden">
	<div
		class="hidden grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_6rem_minmax(0,1.2fr)] gap-4 border-b border-line px-5 py-2.5 text-xs font-medium text-muted md:grid"
	>
		<span>Container</span><span>Image</span><span>State</span><span>Update</span>
	</div>
	<ul class="divide-y divide-line">
		{#each sorted as c (c.name)}
			<li
				class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 px-5 py-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_6rem_minmax(0,1.2fr)]"
			>
				<p class="truncate text-sm font-medium">{c.name}</p>
				<p
					class="col-span-2 row-start-2 truncate font-mono text-xs text-muted md:col-span-1 md:row-start-auto"
					title="{c.image}:{c.tag} ({c.image_id})"
				>
					{c.image}<span class="text-faint">:</span><span class="text-text">{c.tag}</span>
				</p>
				<span
					class="hidden text-xs md:block"
					style:color={c.state === 'running' ? 'var(--good-text)' : 'var(--muted)'}
				>
					{c.state}
				</span>
				<span class="flex items-center gap-1.5 text-xs">
					{#if c.update}
						<ArrowUpCircle class="size-3.5 shrink-0 text-accent" />
						<span class="font-medium text-accent">{c.update_to ?? 'newer image'}</span>
					{:else if c.unchecked}
						<CircleHelp class="size-3.5 shrink-0 text-faint" />
						<span class="truncate text-muted" title={c.unchecked}>can't check: {c.unchecked}</span>
					{:else}
						<Check class="size-3.5 shrink-0" style="color: var(--good-text)" />
						<span class="text-muted">up to date</span>
					{/if}
				</span>
			</li>
		{/each}
	</ul>
</div>
