<script lang="ts">
	import type { JobState } from '#lib/runner.svelte.js';
	import { store } from '#lib/state.svelte.js';

	/** Where a queued update or restart stands (runner.svelte.ts), the same everywhere: a line,
	 * and on request its log (each step the host reported, with times) and the answer. */
	let { job, dismiss }: { job: JobState | undefined; dismiss?: () => void } = $props();
	let open = $state(false);

	const seconds = (ms: number) =>
		ms < 60_000
			? `${Math.round(ms / 1000)} s`
			: `${Math.floor(ms / 60_000)} min ${Math.round((ms % 60_000) / 1000)} s`;
	const clock = (at: number) =>
		new Date(at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
	const last = $derived(job?.log.at(-1)?.text);
</script>

{#if job}
	<span class="block text-xs">
		{#if job.state === 'queued'}
			<span class="text-muted">Waiting its turn on this host.</span>
		{:else if job.state === 'running'}
			<span class="inline-flex items-center gap-1.5 text-muted">
				<span class="size-3 animate-spin rounded-full border-2 border-line border-t-accent"></span>
				{last && last !== 'started' ? last : 'Working on it'}
				· {seconds(store.now - (job.started ?? store.now))}
			</span>
		{:else}
			<span style:color={job.state === 'done' ? 'var(--good-text)' : 'var(--critical-text)'}>
				{job.state === 'failed' ? 'Failed: ' : ''}{job.text}
			</span>
			{#if job.started && job.ended}<span class="text-faint">
					· took {seconds(job.ended - job.started)}
				</span>{/if}
		{/if}
		<button class="ml-1 text-faint underline hover:text-text" onclick={() => (open = !open)}>
			{open ? 'Hide details' : 'Details'}
		</button>
		{#if dismiss && (job.state === 'done' || job.state === 'failed')}
			<button class="ml-1 text-faint underline hover:text-text" onclick={dismiss}>Dismiss</button>
		{/if}
	</span>
	{#if open}
		<div class="mt-1 rounded-lg bg-surface-2 p-2 font-mono text-[11px] leading-relaxed">
			<ol>
				{#each job.log as line, i (i)}
					<li class="flex gap-2">
						<span class="tabular shrink-0 text-faint">{clock(line.at)}</span>
						<span class="min-w-0 break-words">{line.text}</span>
					</li>
				{/each}
			</ol>
			{#if job.result}
				{@const r = job.result}
				<p class="mt-1 border-t border-line pt-1 text-muted">
					{#if r.image}image {r.image}{/if}
					{#if r.from && r.to}· {r.from} → {r.to}{/if}
					{#if r.packages?.length}packages: {r.packages.join(', ')}{/if}
					{#if r.seconds != null}· {r.seconds} s on the host{/if}
				</p>
			{/if}
		</div>
	{/if}
{/if}
