<script lang="ts">
	import type { BackupDiff } from '#lib/api.js';
	import { modal } from '#lib/modal.js';
	import X from '@lucide/svelte/icons/x';

	/** What changed between two backup versions (a device's or Kastellan's own): files that came
	 * and went, and the changed lines with secrets as fingerprints. */
	let { load, onclose }: { load: () => Promise<BackupDiff>; onclose: () => void } = $props();
	let view = $state<{ result: BackupDiff | null; error?: string }>({ result: null });
	let dialog: HTMLDialogElement | undefined = $state();
	$effect(() => {
		load().then(
			(result) => (view = { result }),
			(e) => (view = { result: null, error: (e as Error).message })
		);
	});
</script>

<dialog
	use:modal
	bind:this={dialog}
	{onclose}
	class="m-auto max-h-[calc(100dvh-2rem)] w-[min(56rem,calc(100vw-2rem))] rounded-2xl border border-line bg-surface p-0 text-text shadow-pop backdrop:bg-black/40"
>
	<div class="flex max-h-[calc(100dvh-2rem)] flex-col">
		<div class="flex items-center justify-between border-b border-line px-5 py-4">
			<h2 class="text-base font-semibold">What changed</h2>
			<button class="text-muted hover:text-text" onclick={() => dialog?.close()}>
				<X class="size-5" /><span class="sr-only">Close</span>
			</button>
		</div>
		<div class="overflow-y-auto overscroll-contain px-5 py-4 text-sm">
			{#if view.error}
				<p style:color="var(--critical-text)">{view.error}</p>
			{:else if !view.result}
				<p class="text-muted">Comparing…</p>
			{:else}
				{@const r = view.result}
				<p class="text-xs text-muted">
					Passwords and keys show as fingerprints: a change is visible, the value isn't.
				</p>
				{#if !r.added.length && !r.removed.length && !r.changed.length}
					<p class="mt-3">Nothing.</p>
				{/if}
				{#if r.added.length}
					<p class="mt-3 text-xs font-medium text-muted">New files</p>
					<ul class="font-mono text-xs">
						{#each r.added as p (p)}<li style:color="var(--good-text)">+ {p}</li>{/each}
					</ul>
				{/if}
				{#if r.removed.length}
					<p class="mt-3 text-xs font-medium text-muted">Gone</p>
					<ul class="font-mono text-xs">
						{#each r.removed as p (p)}<li style:color="var(--critical-text)">− {p}</li>{/each}
					</ul>
				{/if}
				{#each r.changed as c (c.path)}
					<p class="mt-4 font-mono text-xs font-semibold">{c.path}</p>
					{#if c.secret}
						<p class="text-xs text-muted">A secret as a whole (keys, passwords): it changed.</p>
					{:else if c.binary}
						<p class="text-xs text-muted">
							Not text: it changed ({c.size[0]} → {c.size[1]} bytes).
						</p>
					{:else if c.only_secrets}
						<p class="text-xs text-muted">Only masked values or metadata changed.</p>
					{:else}
						<pre
							class="mt-1 overflow-x-auto rounded-lg bg-surface-2 p-3 font-mono text-[11px] leading-relaxed">{#each c.diff ?? [] as line, i (i)}<span
									class="block"
									style:color={line.startsWith('+')
										? 'var(--good-text)'
										: line.startsWith('-')
											? 'var(--critical-text)'
											: line.startsWith('@@')
												? 'var(--faint)'
												: undefined}>{line}</span
								>{/each}</pre>
					{/if}
				{/each}
			{/if}
		</div>
	</div>
</dialog>
