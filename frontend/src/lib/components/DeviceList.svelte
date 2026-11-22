<script lang="ts" module>
	import type { Device } from '#lib/api.js';

	/** A part of the list: a site's devices (no name: one site, no heading row). */
	export interface DeviceSection {
		site: string;
		name: string;
		rows: { device: Device; depth: number }[];
	}
</script>

<script lang="ts">
	import type { Snippet } from 'svelte';
	import { store } from '#lib/state.svelte.js';
	import { checkStatusOf, deviceBuckets, statusColor, statusOf, uptimeOf } from '#lib/status.js';
	import { ms, percent } from '#lib/format.js';
	import KindIcon from './KindIcon.svelte';
	import StatusBadge from './StatusBadge.svelte';
	import UptimeStrip from './UptimeStrip.svelte';
	import VitalMeters from './VitalMeters.svelte';

	/** The device list (the Devices page's, and the overview's compact one): name and address,
	 * state, checks, the last 24 hours, vitals, ping. `compact` leaves out checks and vitals (the
	 * overview); `flat` drops the tree's indent (a filtered list); `footer` is a last row (the
	 * overview's "N more, all fine"). */
	let {
		sections,
		flat = false,
		compact = false,
		empty,
		footer
	}: {
		sections: DeviceSection[];
		flat?: boolean;
		compact?: boolean;
		empty?: string;
		footer?: Snippet;
	} = $props();

	const rows = $derived(sections.reduce((n, s) => n + s.rows.length, 0));
	// The list's columns, the same in its header and every row (each row is its own grid).
	const cols = $derived(
		compact
			? 'lg:grid-cols-[minmax(0,1.6fr)_7rem_minmax(0,1.9fr)_4.5rem]'
			: 'lg:grid-cols-[minmax(0,1.6fr)_7rem_minmax(0,1fr)_minmax(0,1.3fr)_8.5rem_4.5rem]'
	);
</script>

<div class="card overflow-hidden">
	{#if rows || empty}
		<div
			class="hidden {cols} gap-4 border-b border-line px-5 py-2.5 text-xs font-medium text-muted lg:grid"
		>
			<span>Device</span><span>State</span>
			{#if !compact}<span>Checks</span>{/if}
			<span>Last 24 hours</span>
			{#if !compact}<span>Vitals</span>{/if}
			<span class="text-right">Ping</span>
		</div>
	{/if}
	{#if !rows && empty}
		<p class="px-5 py-10 text-center text-sm text-muted">{empty}</p>
	{/if}
	<ul class="divide-y divide-line">
		{#each sections as section (section.site)}
			{#if section.name}
				<li
					class="bg-surface-2/60 px-5 py-1.5 text-xs font-semibold tracking-wide text-muted uppercase"
				>
					{section.name}
				</li>
			{/if}
			{#each section.rows as { device, depth } (device.id)}
				{@const status = statusOf(device)}
				{@const buckets = deviceBuckets(store.series?.devices[device.id])}
				{@const ping = device.checks.find((c) => c.type === 'ping')}
				<li>
					<a
						href="/devices/{device.id}"
						class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-3 px-5 transition-colors hover:bg-surface-2/70 {compact
							? 'py-2.5'
							: 'py-3.5'} {cols}"
					>
						<div
							class="flex min-w-0 items-center gap-3"
							style:padding-left="{flat ? 0 : depth * 1.25}rem"
						>
							{#if depth && !flat}<span class="-ml-3 h-px w-2 shrink-0 bg-line-strong"></span>{/if}
							<div
								class="grid size-8 shrink-0 place-items-center rounded-lg bg-surface-2 text-muted"
							>
								<KindIcon kind={device.kind} />
							</div>
							<div class="min-w-0">
								<p class="truncate text-sm font-medium">{device.name}</p>
								<p class="truncate font-mono text-xs text-faint">{device.host}</p>
							</div>
						</div>
						<div><StatusBadge {status} /></div>
						{#if !compact}
							<div class="col-span-2 flex flex-wrap gap-1 lg:col-span-1">
								{#each device.checks as check (check.name)}
									<span
										class="inline-flex items-center gap-1 rounded-md border border-line px-1.5 py-0.5 text-[11px] text-muted"
										title={check.detail || ''}
									>
										<span
											class="size-1.5 rounded-full"
											style:background={statusColor[checkStatusOf(device, check)].fill}
										></span>
										{check.name}
									</span>
								{/each}
							</div>
						{/if}
						<div class="col-span-2 flex items-center gap-3 lg:col-span-1">
							<div class="flex-1">
								{#if store.series && buckets.length}
									<UptimeStrip
										{buckets}
										start={store.series.start}
										step={store.series.step_seconds}
										alwaysOn={device.always_on}
										height="h-4"
									/>
								{/if}
							</div>
							<span class="tabular w-14 shrink-0 text-right text-xs text-muted">
								{percent(uptimeOf(buckets))}
							</span>
						</div>
						{#if !compact}
							<div class="{device.vitals ? '' : 'hidden lg:block'} col-span-2 lg:col-span-1">
								<VitalMeters vitals={device.vitals} />
							</div>
						{/if}
						<span class="tabular hidden text-right text-sm lg:block">
							{ping?.up ? ms(ping.latency_ms) : '–'}
						</span>
					</a>
				</li>
			{/each}
		{/each}
		{#if footer}<li>{@render footer()}</li>{/if}
	</ul>
</div>
