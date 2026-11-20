<script lang="ts">
	import type { Snippet } from 'svelte';

	/** One form of the settings: its fields, and Save / Undo for just this part. */
	let {
		title,
		description,
		dirty,
		save,
		reset,
		canSave = true,
		children
	}: {
		title: string;
		description?: string;
		dirty: boolean;
		save: () => Promise<unknown>;
		reset: () => void;
		canSave?: boolean;
		children: Snippet;
	} = $props();

	let busy = $state(false);
	let error = $state<string | null>(null);
	let saved = $state(false);

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		busy = true;
		error = null;
		try {
			await save();
			saved = true;
			setTimeout(() => (saved = false), 2500);
		} catch (err) {
			error = (err as Error).message;
		} finally {
			busy = false;
		}
	}
</script>

<form class="card max-w-3xl p-5" onsubmit={submit}>
	<h2 class="text-sm font-semibold">{title}</h2>
	{#if description}<p class="mt-1 max-w-2xl text-xs leading-relaxed text-muted">
			{description}
		</p>{/if}
	<div class="mt-4 flex flex-col gap-4">
		{@render children()}
	</div>
	<div class="mt-5 flex flex-wrap items-center gap-3">
		<button class="btn btn-primary btn-sm" disabled={!dirty || busy || !canSave}>
			{busy ? 'Saving…' : 'Save'}
		</button>
		{#if dirty && !busy}
			<button type="button" class="btn btn-sm" onclick={() => ((error = null), reset())}>
				Undo
			</button>
		{/if}
		{#if error}<span class="text-xs" style:color="var(--critical-text)" role="alert">{error}</span>
		{:else if saved}<span class="text-xs" style:color="var(--good-text)" role="status">
				Saved, and in use.
			</span>{/if}
	</div>
</form>
