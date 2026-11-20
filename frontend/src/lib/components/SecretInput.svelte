<script lang="ts">
	import { KEEP } from '#lib/api.js';

	/** A secret the page never gets: empty keeps the stored one (KEEP), typing replaces it,
	 * "remove" removes it. `stored`: there is one stored for this very thing. */
	let {
		value = $bindable(),
		stored,
		placeholder = '',
		optional = true
	}: {
		value: string | null | undefined;
		stored: boolean;
		placeholder?: string;
		optional?: boolean;
	} = $props();

	let removed = $state(false);
	let typed = $state(value && value !== KEEP ? value : '');

	// Whenever what is stored changes (another channel kind, another address), start over.
	$effect(() => {
		if (!stored) removed = false;
		value = typed ? typed : stored && !removed ? KEEP : null;
	});
</script>

<span class="flex items-center gap-2">
	<input
		class="input"
		type="password"
		autocomplete="off"
		placeholder={stored && !removed ? 'set; empty keeps it' : removed ? 'removed' : placeholder}
		bind:value={typed}
	/>
	{#if stored && optional && !typed}
		<button
			type="button"
			class="text-xs whitespace-nowrap text-muted underline hover:text-text"
			onclick={() => (removed = !removed)}
		>
			{removed ? 'keep it' : 'remove'}
		</button>
	{/if}
</span>
