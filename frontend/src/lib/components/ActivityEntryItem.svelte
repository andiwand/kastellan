<script lang="ts">
	import type { ActivityEntry } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { clock, dayClock, duration, versionSubject } from '#lib/format.js';
	import ArrowDown from '@lucide/svelte/icons/arrow-down';
	import Activity from '@lucide/svelte/icons/activity';
	import Unplug from '@lucide/svelte/icons/unplug';
	import Power from '@lucide/svelte/icons/power';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';

	/** One line of the day: an outage and how long, short drops counted, many devices at once,
	 * when a device that may be off was on, or a software update. */
	let { entry }: { entry: ActivityEntry } = $props();

	const name = (id: string) => store.byId.get(id)?.name ?? id;
	const seconds = (a: string, b: string | null) =>
		((b ? Date.parse(b) : store.now) - Date.parse(a)) / 1000;
	const span = (a: string | null, b: string | null) =>
		a && b
			? `${dayClock(a)}–${clock(b)}`
			: a
				? `since ${dayClock(a)}`
				: b
					? `until ${dayClock(b)}`
					: '';

	const tone = $derived(
		entry.kind === 'version'
			? 'var(--accent)'
			: entry.kind === 'online' || entry.kind === 'blips'
				? 'var(--offline)'
				: entry.kind === 'together'
					? 'var(--warning)'
					: entry.end
						? 'var(--warning)'
						: 'var(--critical)'
	);
	const link = $derived('device' in entry ? `/devices/${entry.device}` : null);
</script>

<svelte:element
	this={link ? 'a' : 'div'}
	href={link ?? undefined}
	class="flex gap-3 rounded-lg px-3 py-2.5 {link ? 'transition-colors hover:bg-surface-2' : ''}"
>
	<span
		class="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full"
		style:background="color-mix(in srgb, {tone} 16%, transparent)"
		style:color={tone === 'var(--offline)' ? 'var(--muted)' : tone}
	>
		{#if entry.kind === 'version'}<RefreshCw class="size-3" strokeWidth={2.5} />
		{:else if entry.kind === 'online'}<Power class="size-3" strokeWidth={2.5} />
		{:else if entry.kind === 'blips'}<Activity class="size-3" strokeWidth={2.5} />
		{:else if entry.kind === 'together'}<Unplug class="size-3" strokeWidth={2.5} />
		{:else}<ArrowDown class="size-3" strokeWidth={2.5} />{/if}
	</span>
	<span class="min-w-0 flex-1 text-sm leading-snug">
		{#if entry.kind === 'outage'}
			<span class="font-medium">{name(entry.device)}</span>
			{#if entry.check !== 'ping'}<span class="text-muted">{entry.check}</span>{/if}
			<span class="text-muted">
				{entry.end ? `was down for ${duration(seconds(entry.start, entry.end))}` : 'is down'}
			</span>
			{#if entry.detail}<span class="text-faint"> · {entry.detail}</span>{/if}
			<span class="tabular block text-xs text-faint">{span(entry.start, entry.end)}</span>
		{:else if entry.kind === 'blips'}
			<span class="font-medium">{name(entry.device)}</span>
			{#if entry.check !== 'ping'}<span class="text-muted">{entry.check}</span>{/if}
			<span class="text-muted">
				{entry.count === 1 ? 'one short drop' : `${entry.count} short drops`}
			</span>
			<span class="tabular block text-xs text-faint">last {dayClock(entry.last)}</span>
		{:else if entry.kind === 'together'}
			<span class="font-medium">{entry.count} devices</span>
			<span class="text-muted">
				unreachable at once{entry.end ? ` for ${duration(seconds(entry.start, entry.end))}` : ''}
			</span>
			<span class="block text-xs text-muted" title={entry.devices.map(name).join(', ')}>
				Likely the hub or the network in front of it, not each device ·
				<span class="tabular text-faint">{span(entry.start, entry.end)}</span>
			</span>
		{:else if entry.kind === 'online'}
			<span class="font-medium">{name(entry.device)}</span>
			<span class="text-muted">{entry.end ? 'was online' : 'is online'}</span>
			<span class="tabular block text-xs text-faint">{span(entry.start, entry.end)}</span>
		{:else}
			<span class="font-medium">{name(entry.device)}</span>
			<span class="text-muted">{versionSubject(entry.check)} updated</span>
			<span class="block text-xs text-muted">
				{entry.from} <span class="text-faint">→</span> <span class="text-text">{entry.to}</span>
			</span>
			<span class="tabular block text-xs text-faint">{dayClock(entry.time)}</span>
		{/if}
	</span>
</svelte:element>
