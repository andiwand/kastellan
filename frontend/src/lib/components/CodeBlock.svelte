<script lang="ts">
	import Copy from '@lucide/svelte/icons/copy';
	import Check from '@lucide/svelte/icons/check';

	/** Text to copy (commands, a key), with its copy button inside the box, top right. */
	let {
		text,
		maxHeight = 'max-h-72',
		wrap = false,
		class: extra = ''
	}: { text: string; maxHeight?: string; wrap?: boolean; class?: string } = $props();

	let copied = $state(false);

	async function copy() {
		await navigator.clipboard.writeText(text);
		copied = true;
		setTimeout(() => (copied = false), 1500);
	}
</script>

<div class="group relative {extra}">
	<pre
		class="{maxHeight} overflow-auto rounded-lg bg-surface-2 p-3 pr-20 font-mono text-[11px] leading-relaxed {wrap
			? 'break-all whitespace-pre-wrap'
			: ''}">{text}</pre>
	<button
		type="button"
		class="absolute top-2 right-2 inline-flex h-7 items-center gap-1 rounded-md border border-line bg-surface px-2 text-xs text-muted shadow-card hover:border-line-strong hover:text-text"
		onclick={copy}
	>
		{#if copied}<Check class="size-3.5" style="color: var(--good-text)" />Copied{:else}<Copy
				class="size-3.5"
			/>Copy{/if}
	</button>
</div>
