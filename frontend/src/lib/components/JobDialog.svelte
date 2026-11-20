<script lang="ts">
	import type { Snippet } from 'svelte';
	import { streamSetup } from '#lib/api.js';
	import X from '@lucide/svelte/icons/x';
	import Check from '@lucide/svelte/icons/check';

	/** A change that runs on the hub, step by step: a form (the children), then its output live.
	 * It runs to its end even if this closes (the audit log has the outcome). */
	let {
		title,
		path,
		body,
		action,
		children,
		onclose,
		ondone
	}: {
		title: string;
		path: string;
		body: () => object | null; // null: the form isn't complete
		action: string;
		children: Snippet;
		onclose: () => void;
		ondone?: () => void;
	} = $props();

	let step = $state<'ask' | 'running' | 'done' | 'failed'>('ask');
	let log = $state<string[]>([]);
	let error = $state<string | null>(null);
	let dialog: HTMLDialogElement | undefined = $state();
	const ready = $derived(body() !== null);

	$effect(() => {
		dialog?.showModal();
	});
	$effect(() => {
		const root = document.documentElement;
		const before = root.style.overflow;
		root.style.overflow = 'hidden';
		return () => {
			root.style.overflow = before;
		};
	});

	async function start(e: SubmitEvent) {
		e.preventDefault();
		const payload = body();
		if (!payload) return;
		step = 'running';
		try {
			const outcome = await streamSetup(path, payload, (line) => (log = [...log, line]));
			step = outcome.ok ? 'done' : 'failed';
			error = outcome.ok ? null : (outcome.error ?? 'it failed');
		} catch (err) {
			error = (err as Error).message;
			step = 'failed';
		}
		ondone?.();
	}

	const button =
		'inline-flex h-9 items-center justify-center rounded-lg border border-line px-3 text-sm font-medium hover:border-line-strong disabled:opacity-50';
</script>

<dialog
	bind:this={dialog}
	{onclose}
	class="m-auto max-h-[calc(100dvh-2rem)] w-[min(40rem,calc(100vw-2rem))] rounded-2xl border border-line bg-surface p-0 text-text shadow-pop backdrop:bg-black/40"
	aria-labelledby="job-title"
>
	<form class="flex max-h-[calc(100dvh-2rem)] flex-col" onsubmit={start}>
		<div class="flex items-center justify-between border-b border-line px-5 py-4">
			<h2 id="job-title" class="text-base font-semibold">{title}</h2>
			<button type="button" class="text-muted hover:text-text" onclick={() => dialog?.close()}>
				<X class="size-5" /><span class="sr-only">Close</span>
			</button>
		</div>
		<div class="flex flex-col gap-3 overflow-y-auto overscroll-contain px-5 py-4 text-sm">
			{#if step === 'ask'}
				{@render children()}
			{:else}
				<p class="flex items-center gap-2">
					{#if step === 'running'}
						<span class="size-3 animate-spin rounded-full border-2 border-line border-t-accent"
						></span>
						Working, one device after the other…
					{:else if step === 'done'}
						<Check class="size-4" style="color: var(--good-text)" />
						<span style:color="var(--good-text)">Done.</span>
					{:else}
						<span style:color="var(--critical-text)">{error}</span>
					{/if}
				</p>
				<pre
					class="max-h-72 min-h-16 overflow-auto rounded-lg bg-surface-2 p-3 font-mono text-[11px] leading-relaxed">{log.join(
						'\n'
					) || '…'}</pre>
			{/if}
		</div>
		<div class="flex justify-end gap-2 border-t border-line px-5 py-3">
			{#if step === 'ask'}
				<button type="button" class={button} onclick={() => dialog?.close()}>Cancel</button>
				<button
					class="{button} border-transparent bg-accent text-accent-ink hover:opacity-90"
					disabled={!ready}
				>
					{action}
				</button>
			{:else}
				<button type="button" class={button} onclick={() => dialog?.close()}>Close</button>
			{/if}
		</div>
	</form>
</dialog>
