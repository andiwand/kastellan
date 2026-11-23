<script lang="ts">
	import type { WifiRadio, WifiScanView } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { bandLabel } from '#lib/format.js';
	import { channelUse, covers } from '#lib/wifi.js';

	/** One band's channels: how busy each was where our radios listened (the newest scans; the
	 * highest share any radio measured), our radios' own channels outlined, and under the axis a
	 * line per radio over the channels it covers. Hover or tap a channel for who measured what
	 * and which neighbours were heard there. `device` keeps one device's radios (its page). */
	let {
		band,
		scans,
		radios,
		device
	}: {
		band: string;
		scans: Record<string, WifiScanView>;
		radios: WifiRadio[];
		device?: string;
	} = $props();

	const name = (id: string) => store.byId.get(id)?.name ?? id;
	const use = $derived(channelUse(band, scans, name));
	const index = $derived(new Map(use.map((u, i) => [u.channel, i])));
	const mine = $derived(
		radios
			.filter((r) => r.band === band && r.channel && (!device || r.device === device))
			.map((r) => {
				const cover = covers(r.channel!, r.width, band).filter((c) => index.has(c));
				const at = cover.map((c) => index.get(c)!);
				return {
					key: `${r.device}/${r.radio}`,
					name: name(r.device),
					channel: r.channel!,
					from: Math.min(...at),
					to: Math.max(...at)
				};
			})
			.filter((r) => Number.isFinite(r.from))
	);
	const own = $derived(new Set(mine.map((r) => r.channel)));
	let hover = $state<number | null>(null);
	const shown = $derived(use.find((u) => u.channel === hover) ?? null);
	const shownAt = $derived(hover == null ? -1 : (index.get(hover) ?? -1));
	const BUSY = 60; // from here a bar turns amber
	// How much a neighbour network gets in the way, from its signal where our radio heard it.
	const strength = (dbm: number) => (dbm >= -65 ? 'strong' : dbm >= -80 ? 'moderate' : 'faint');
	const cols = $derived(`repeat(${use.length}, minmax(0, 1fr))`);
	const dense = $derived(use.length > 16);
	// Leave space between axis labels; every channel remains available by hovering or tapping.
	function labelClass(i: number, channel: number): string {
		const besideOwn = own.has(use[i - 1]?.channel) || own.has(use[i + 1]?.channel);
		return [
			own.has(channel) ? 'font-semibold text-text' : '',
			dense && !own.has(channel) && (besideOwn || i % 2) ? 'sm:invisible' : '',
			i % (dense ? 4 : 2) ? 'max-sm:invisible' : ''
		].join(' ');
	}
</script>

<div class="card p-4">
	<p class="text-section">{bandLabel(band)}</p>
	<div class="mt-3 flex gap-2">
		<div
			class="flex w-24 shrink-0 flex-col justify-between pb-5 text-right text-meta text-faint sm:w-32"
		>
			<span>100 %</span><span>50 %</span><span>0</span>
		</div>
		<div class="min-w-0 flex-1">
			<!-- the bars -->
			<div
				class="relative grid h-28 items-end gap-0.5 border-b border-line"
				style:grid-template-columns={cols}
				role="group"
				aria-label="How busy each {bandLabel(band)} channel was"
				onmouseleave={() => (hover = null)}
			>
				<span
					class="pointer-events-none absolute inset-x-0 top-0 border-t border-dashed border-line"
				></span>
				<span
					class="pointer-events-none absolute inset-x-0 top-1/2 border-t border-dashed border-line"
				></span>
				{#each use as u (u.channel)}
					<button
						type="button"
						class="relative flex h-full items-end justify-center rounded-t-[4px] outline-offset-1 {own.has(
							u.channel
						)
							? 'outline-2 outline-accent'
							: ''} {hover === u.channel ? 'bg-surface-2' : ''}"
						style:outline-style={own.has(u.channel) ? 'solid' : undefined}
						aria-label="Channel {u.channel}: {u.busy != null ? `${u.busy} % busy` : 'not measured'}"
						onmouseenter={() => (hover = u.channel)}
						onfocus={() => (hover = u.channel)}
						onclick={() => (hover = u.channel)}
					>
						{#if u.busy != null}
							<span
								class="block w-full rounded-t-[4px]"
								style:height="{Math.max(2, Math.min(100, u.busy))}%"
								style:background={u.busy >= BUSY ? 'var(--warning)' : 'var(--faint)'}
							></span>
						{/if}
					</button>
				{/each}
			</div>
			<!-- the axis: channel numbers -->
			<div
				class="mt-1 grid gap-0.5 text-center text-[10px] text-faint"
				style:grid-template-columns={cols}
			>
				{#each use as u, i (u.channel)}
					<span class="tabular {labelClass(i, u.channel)}">
						{u.channel}
					</span>
				{/each}
			</div>
		</div>
	</div>
	<!-- our radios over the channels they cover -->
	{#if mine.length}
		<div class="mt-2 space-y-1">
			{#each mine as r (r.key)}
				<div class="flex items-center gap-2">
					<span class="w-24 shrink-0 truncate text-right text-meta text-muted sm:w-32">
						{r.name}
					</span>
					<div class="grid min-w-0 flex-1 gap-0.5" style:grid-template-columns={cols}>
						<span
							class="h-2 rounded-full border border-accent bg-accent-soft"
							style:grid-column="{r.from + 1} / {r.to + 2}"
							title="{r.name}: channel {r.channel}"
						></span>
					</div>
				</div>
			{/each}
		</div>
	{/if}
	<!-- what the marks mean -->
	<ul class="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-meta text-muted">
		<li class="flex items-center gap-1.5">
			<span class="size-2.5 rounded-[2px]" style:background="var(--faint)"></span>
			How busy the air was (your own traffic too)
		</li>
		<li class="flex items-center gap-1.5">
			<span class="size-2.5 rounded-[2px]" style:background="var(--warning)"></span>
			Busy {BUSY} % or more
		</li>
		{#if mine.length}
			<li class="flex items-center gap-1.5">
				<span class="size-2.5 rounded-[2px] border-2 border-accent"></span>
				Your access points’ channels; the lines show how far each spreads
			</li>
		{/if}
	</ul>
	<!-- the channel pointed at -->
	<div class="mt-2 min-h-[3lh] text-meta text-muted" aria-live="polite">
		{#if shown}
			{@const here = mine.filter((r) => r.from <= shownAt && shownAt <= r.to)}
			<p class="font-medium text-text">Channel {shown.channel}</p>
			<dl class="mt-0.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
				<dt>Busy</dt>
				<dd>
					{#if shown.busy != null}
						<span class="tabular">{shown.busy} %</span> of the time{#if shown.by.length > 1}, the
							most any of your radios measured ({[...shown.by]
								.sort((a, b) => b.busy - a.busy)
								.map((b) => `${b.name} ${b.busy} %`)
								.join(', ')}){:else}, measured by {shown.by[0].name}{/if}
					{:else}
						Not measured: none of your radios listened here
					{/if}
				</dd>
				<dt>Your access points</dt>
				<dd>
					{#if here.length}
						{here
							.map((r) =>
								r.channel === shown.channel ? r.name : `${r.name} (on ${r.channel}, reaches here)`
							)
							.join(', ')}
					{:else}
						None on or near it
					{/if}
				</dd>
				<dt>Other networks</dt>
				<dd>
					{#if shown.heard.length}
						{@const heard = [...shown.heard].sort(
							(a, b) => (b.signal ?? -200) - (a.signal ?? -200)
						)}
						{heard
							.slice(0, 5)
							.map(
								(h) =>
									`${h.ssid || 'hidden network'}${h.signal != null ? ` (${strength(h.signal)}, ${h.signal} dBm)` : ''}`
							)
							.join(', ')}{heard.length > 5 ? ` and ${heard.length - 5} more` : ''}
					{:else}
						None heard
					{/if}
				</dd>
			</dl>
		{:else}
			<p>Point at or tap a channel to see who uses it.</p>
		{/if}
	</div>
</div>
