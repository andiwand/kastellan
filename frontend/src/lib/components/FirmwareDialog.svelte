<script lang="ts">
	import { streamSetup, type RouterReport, type SetupOutcome } from '#lib/api.js';
	import { untrack } from 'svelte';
	import X from '@lucide/svelte/icons/x';
	import Check from '@lucide/svelte/icons/check';

	/** Updating an OpenWrt device's firmware, one step at a time: OpenWrt's image builder makes
	 * an image of the next release for this model, with its packages; Kastellan checks it, the
	 * device tests it, and installs it keeping its settings. Then it restarts (a few minutes). */
	let {
		device,
		advice,
		model,
		onclose
	}: {
		device: string;
		advice: NonNullable<RouterReport['firmware']>;
		model: string | null;
		onclose: () => void;
	} = $props();

	type Step = 'ask' | 'running' | 'done' | 'failed';
	let step = $state<Step>('ask');
	let understood = $state(false);
	let log = $state<string[]>([]);
	let error = $state<string | null>(null);
	let dialog: HTMLDialogElement | undefined = $state();

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

	// Fixed when the dialog opens: the report changes while it runs.
	const target = untrack(() => advice.next_step ?? '');
	const series = untrack(() => advice.series);
	const current = untrack(() => advice.current);
	const crossing = !target.startsWith(series + '.');

	async function start() {
		step = 'running';
		log = [];
		try {
			const outcome: SetupOutcome = await streamSetup(
				`/devices/${encodeURIComponent(device)}/openwrt/firmware`,
				{ version: target },
				(line) => (log = [...log, line])
			);
			step = outcome.ok ? 'done' : 'failed';
			error = outcome.ok ? null : (outcome.error ?? 'it failed');
		} catch (e) {
			error = (e as Error).message;
			step = 'failed';
		}
	}

	const button =
		'inline-flex h-9 items-center justify-center rounded-lg border border-line px-3 text-sm font-medium hover:border-line-strong disabled:opacity-50';
</script>

<dialog
	bind:this={dialog}
	{onclose}
	class="m-auto max-h-[calc(100dvh-2rem)] w-[min(40rem,calc(100vw-2rem))] rounded-2xl border border-line bg-surface p-0 text-text shadow-pop backdrop:bg-black/40"
	aria-labelledby="fw-title"
>
	<div class="flex max-h-[calc(100dvh-2rem)] flex-col">
		<div class="flex items-center justify-between border-b border-line px-5 py-4">
			<h2 id="fw-title" class="text-base font-semibold">
				OpenWrt {current} → {target}
			</h2>
			<button class="text-muted hover:text-text" onclick={() => dialog?.close()}>
				<X class="size-5" /><span class="sr-only">Close</span>
			</button>
		</div>
		<div class="flex flex-col gap-3 overflow-y-auto overscroll-contain px-5 py-4 text-sm">
			{#if step === 'ask'}
				<p class="leading-relaxed text-muted">
					OpenWrt's image builder makes an image of {target} for this {model ?? 'device'}, with the
					packages it has now. Kastellan checks the image, the device tests it, then installs it,
					keeping its settings, and restarts: it is away for a few minutes, and so is everything
					that goes through it.
				</p>
				{#if crossing}
					<p style:color="var(--warning-text)">
						This moves to the next release series ({target.split('.').slice(0, 2).join('.')}):
						OpenWrt keeps the settings across one series, but some packages change names or go. Do
						one device first, and see that it comes back right.
					</p>
				{/if}
				<label class="flex items-start gap-2">
					<input type="checkbox" class="mt-[3px] size-4 shrink-0" bind:checked={understood} />
					<span>I can get to the device if it doesn't come back (cable, or its reset button).</span>
				</label>
			{:else}
				<p class="flex items-center gap-2">
					{#if step === 'running'}
						<span class="size-3 animate-spin rounded-full border-2 border-line border-t-accent"
						></span>
						Working…
						<span class="text-xs text-faint">
							(closing this doesn't stop it; the audit log has the outcome)
						</span>
					{:else if step === 'done'}
						<Check class="size-4" style="color: var(--good-text)" />
						<span style:color="var(--good-text)">Done.</span>
					{:else}
						<span style:color="var(--critical-text)">It didn't work: {error}</span>
					{/if}
				</p>
				<pre
					class="max-h-72 min-h-24 overflow-auto rounded-lg bg-surface-2 p-3 font-mono text-[11px] leading-relaxed">{log.join(
						'\n'
					) || '…'}</pre>
			{/if}
		</div>
		<div class="flex justify-end gap-2 border-t border-line px-5 py-3">
			{#if step === 'ask'}
				<button class={button} onclick={() => dialog?.close()}>Cancel</button>
				<button
					class="{button} border-transparent bg-accent text-accent-ink hover:opacity-90"
					disabled={!understood || !target}
					onclick={start}
				>
					Update to {target}
				</button>
			{:else}
				<button class={button} onclick={() => dialog?.close()}>Close</button>
			{/if}
		</div>
	</div>
</dialog>
