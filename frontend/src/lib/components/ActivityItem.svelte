<script lang="ts">
	import type { Change, Device } from '#lib/api.js';
	import { dayClock } from '#lib/format.js';
	import ArrowUp from '@lucide/svelte/icons/arrow-up';
	import ArrowDown from '@lucide/svelte/icons/arrow-down';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';

	/** One line of activity: a check went up or down, or a service's software changed. With
	 * `link`, the device's name leads and the line opens the device. */
	let {
		change,
		device,
		link = false
	}: { change: Change; device?: Device; link?: boolean } = $props();

	const offline = $derived(device ? !device.always_on : false);
	const tone = $derived(
		change.kind === 'version'
			? { fill: 'var(--accent)', text: 'var(--accent)' }
			: change.up
				? { fill: 'var(--good)', text: 'var(--good-text)' }
				: offline
					? { fill: 'var(--offline)', text: 'var(--muted)' }
					: { fill: 'var(--critical)', text: 'var(--critical-text)' }
	);
</script>

<svelte:element
	this={link ? 'a' : 'div'}
	href={link ? `/devices/${change.device}` : undefined}
	class="flex gap-3 rounded-lg px-3 py-2.5 {link ? 'transition-colors hover:bg-surface-2' : ''}"
>
	<span
		class="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full"
		style:background="color-mix(in srgb, {tone.fill} 16%, transparent)"
		style:color={tone.text}
	>
		{#if change.kind === 'version'}
			<RefreshCw class="size-3" strokeWidth={2.5} />
		{:else if change.up}
			<ArrowUp class="size-3" strokeWidth={2.5} />
		{:else}
			<ArrowDown class="size-3" strokeWidth={2.5} />
		{/if}
	</span>
	<span class="min-w-0 flex-1 text-sm leading-snug">
		{#if link}<span class="font-medium">{device?.name ?? change.device}</span>{/if}
		<span class={link ? 'text-muted' : 'font-medium'}>{change.check}</span>
		{#if change.kind === 'version'}
			<span class="text-muted">updated</span>
			<span class="block text-xs text-muted">
				{change.from} <span class="text-faint">→</span> <span class="text-text">{change.to}</span>
			</span>
		{:else}
			<span class="text-muted">
				{change.up ? 'back up' : offline ? 'went offline' : 'went down'}
			</span>
			{#if !change.up && change.detail && !link}<span class="text-faint">
					· {change.detail}
				</span>{/if}
		{/if}
		<span class="tabular block text-xs text-faint">{dayClock(change.time)}</span>
	</span>
</svelte:element>
