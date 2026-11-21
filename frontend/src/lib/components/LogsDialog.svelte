<script lang="ts">
	import { modal } from '#lib/modal.js';
	import { api } from '#lib/api.js';
	import { onMount, tick } from 'svelte';
	import X from '@lucide/svelte/icons/x';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';

	/** The last lines of a container's log or a service's journal, read through the host's actor
	 * (its container-logs / unit-logs abilities); newest at the bottom, read again on request. */
	let {
		device,
		what,
		onclose
	}: { device: string; what: { container: string } | { unit: string }; onclose: () => void } =
		$props();

	let dialog: HTMLDialogElement | undefined = $state();
	let box: HTMLPreElement | undefined = $state();
	let lines = $state<string[] | null>(null);
	let note = $state('');
	let error = $state<string | null>(null);
	let busy = $state(false);
	const name = $derived('unit' in what ? what.unit : what.container);

	async function read() {
		busy = true;
		error = null;
		try {
			const r = await api.logs(device, what);
			lines = r.lines;
			note =
				r.state != null && r.state !== ''
					? r.state
					: r.running === false
						? 'not running'
						: r.running
							? 'running'
							: '';
			await tick();
			if (box) box.scrollTop = box.scrollHeight;
		} catch (e) {
			const m = (e as Error).message;
			error = m.includes("isn't installed")
				? `${m}: set up the actor again and tick “${'unit' in what ? "Read services' logs" : "Read containers' logs"}”.`
				: m;
		} finally {
			busy = false;
		}
	}
	onMount(read);
</script>

<dialog
	use:modal
	bind:this={dialog}
	{onclose}
	class="m-auto max-h-[calc(100dvh-2rem)] w-[min(60rem,calc(100vw-2rem))] rounded-2xl border border-line bg-surface p-0 text-text shadow-pop backdrop:bg-black/40"
	aria-labelledby="logs-title"
>
	<div class="flex max-h-[calc(100dvh-2rem)] flex-col">
		<div class="flex items-center justify-between gap-3 border-b border-line px-5 py-4">
			<h2 id="logs-title" class="min-w-0 truncate text-base font-semibold">
				<span class="font-mono">{name}</span>
				{#if note}<span class="ml-1 text-xs font-normal text-muted">· {note}</span>{/if}
			</h2>
			<div class="flex shrink-0 items-center gap-2">
				<button class="btn btn-sm" onclick={read} disabled={busy}>
					<RefreshCw class="size-3.5 {busy ? 'animate-spin' : ''}" /> Read again
				</button>
				<button class="text-muted hover:text-text" onclick={() => dialog?.close()}>
					<X class="size-5" /><span class="sr-only">Close</span>
				</button>
			</div>
		</div>
		{#if error}
			<p class="px-5 py-4 text-sm" style:color="var(--critical-text)">{error}</p>
		{:else if lines === null}
			<p class="px-5 py-4 text-sm text-muted">Reading…</p>
		{:else if !lines.length}
			<p class="px-5 py-4 text-sm text-muted">Nothing logged.</p>
		{:else}
			<pre
				bind:this={box}
				class="min-h-0 flex-1 overflow-auto overscroll-contain bg-surface-2 px-5 py-3 font-mono text-[11px] leading-relaxed break-words whitespace-pre-wrap">{lines.join(
					'\n'
				)}</pre>
			<p class="border-t border-line px-5 py-2 text-xs text-faint">
				The last {lines.length} lines, newest at the bottom.
			</p>
		{/if}
	</div>
</dialog>
