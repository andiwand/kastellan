<script lang="ts">
	import { runner, type Job } from '#lib/runner.svelte.js';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';

	/** Reboot a Linux host through its actor (in a minute: `shutdown -r +1`, so it can still be
	 * cancelled there), from the top of its page; the same queue as the Updates page. */
	let { device, wanted = false }: { device: string; wanted?: boolean } = $props();
	let asking = $state(false);
	const job = $derived<Job>({ device, kind: 'reboot' });
	const st = $derived(runner.state(job));
</script>

{#if asking}
	<span class="btn-header hover:border-line">
		Reboot in a minute?
		<button
			class="underline"
			style:color="var(--critical-text)"
			onclick={() => ((asking = false), runner.add([job]))}
		>
			Yes
		</button>
		<button class="underline" onclick={() => (asking = false)}>No</button>
	</span>
{:else}
	<button
		class="btn-header"
		disabled={st?.state === 'queued' || st?.state === 'running'}
		title={st?.state === 'failed'
			? st?.text
			: wanted
				? 'It wants a reboot (updates)'
				: 'Reboot it in a minute (cancel on the host: shutdown -c)'}
		onclick={() => (asking = true)}
	>
		<RotateCcw class="size-4 text-muted" />
		{#if st?.state === 'queued' || st?.state === 'running'}Rebooting…{:else if st?.state === 'done'}<span
				style:color="var(--good-text)"
			>
				Reboot asked
			</span>{:else if st?.state === 'failed'}<span style:color="var(--critical-text)">
				Reboot failed
			</span>{:else}Reboot{/if}
		{#if wanted && !st}<span class="size-2 rounded-full" style:background="var(--warning)"
			></span>{/if}
	</button>
{/if}
