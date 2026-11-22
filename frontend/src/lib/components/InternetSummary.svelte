<script lang="ts">
	import StateBadge from './ui/StateBadge.svelte';
	import { internetState } from '#lib/states.js';
	import { onMount } from 'svelte';
	import { more, type InternetView } from '#lib/api.js';
	import { bitrate, date } from '#lib/format.js';
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

	const lineState = (s: string) =>
		internetState[s as keyof typeof internetState] ?? internetState.unknown;
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
			<span class="ml-auto"><StateBadge state={lineState(s.state)} quiet /></span>
		</div>
		{#if s.state === 'down' && s.why}<p class="mt-1 text-xs">
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
					? ` · last outage ${date(view.outages[0].start)}`
					: ''}
			</p>
		{/if}
	</a>
{/if}
