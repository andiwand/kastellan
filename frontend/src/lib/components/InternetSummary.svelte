<script lang="ts">
	import { onMount } from 'svelte';
	import { more, type InternetView } from '#lib/api.js';
	import { bitrate } from '#lib/format.js';
	import Globe from '@lucide/svelte/icons/globe';

	/** The internet connection in a few words, for the overview (the Internet page has it all). */
	let view = $state<InternetView | null>(null);

	async function load() {
		try {
			view = await more.internet(24);
		} catch {
			/* the card keeps what it had */
		}
	}
	onMount(() => {
		load();
		const t = setInterval(load, 30_000);
		return () => clearInterval(t);
	});

	const STATE: Record<string, [string, string]> = {
		up: ['var(--good-text)', 'Online'],
		degraded: ['var(--warning-text)', 'Slow'],
		down: ['var(--critical-text)', 'Down'],
		unknown: ['var(--muted)', 'Unknown']
	};
	const today = $derived(view?.days.at(-1));
</script>

{#if view?.enabled && view.now}
	{@const s = view.now}
	<a
		href="/internet"
		class="card mb-6 block p-4 transition-all hover:-translate-y-px hover:border-line-strong hover:shadow-pop"
	>
		<div class="flex items-center gap-2 text-sm font-semibold">
			<Globe class="size-4 text-muted" /> Internet
			<span class="ml-auto text-xs font-medium" style:color={STATE[s.state][0]}>
				{STATE[s.state][1]}
			</span>
		</div>
		{#if s.state === 'down' && s.why}<p class="mt-1 text-xs" style:color="var(--critical-text)">
				{s.why}
			</p>{/if}
		<p class="tabular mt-2 text-xs text-muted">
			{s.ms != null ? `${s.ms} ms` : '–'} · loss {s.loss != null
				? `${Math.round(s.loss * 100)} %`
				: '–'}
			{#if s.down != null}· ↓ {bitrate(s.down)} ↑ {bitrate(s.up)}{/if}
		</p>
		{#if today?.availability != null}
			<p class="tabular mt-1 text-xs text-faint">
				today {(today.availability * 100).toFixed(2)} % online{view.outages.length
					? ` · last outage ${new Date(view.outages[0].start * 1000).toLocaleDateString()}`
					: ''}
			</p>
		{/if}
	</a>
{/if}
