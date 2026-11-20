<script lang="ts">
	import { store } from '#lib/state.svelte.js';
	import { deviceBuckets, needsAttention, statusOf, uptimeOf, type Status } from '#lib/status.js';
	import { ago, dayClock, ms, percent, stateFor } from '#lib/format.js';
	import KindIcon from '#lib/components/KindIcon.svelte';
	import StatusBadge from '#lib/components/StatusBadge.svelte';
	import StatusDot from '#lib/components/StatusDot.svelte';
	import UptimeStrip from '#lib/components/UptimeStrip.svelte';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import ArrowUp from '@lucide/svelte/icons/arrow-up';
	import ArrowDown from '@lucide/svelte/icons/arrow-down';

	const devices = $derived(store.devices ?? []);
	const counts = $derived(
		devices.reduce((c, d) => ((c[statusOf(d)] += 1), c), {
			up: 0,
			degraded: 0,
			down: 0,
			offline: 0,
			waiting: 0
		} as Record<Status, number>)
	);
	const problems = $derived(
		devices
			.filter(needsAttention)
			.flatMap((d) => d.checks.filter((c) => c.up === false).map((c) => ({ device: d, check: c })))
			.sort((a, b) => Date.parse(b.check.since ?? '') - Date.parse(a.check.since ?? ''))
	);
	const attention = $derived(devices.filter(needsAttention).length);
	// Amber while things only limp along, red once something that should be on is down.
	const tone = $derived(counts.down ? 'critical' : counts.degraded ? 'warning' : 'good');
	const checkCount = $derived(devices.reduce((n, d) => n + d.checks.length, 0));

	const alwaysOn = $derived(devices.filter((d) => d.always_on));
	const fleetUptime = $derived.by(() => {
		if (!store.series) return null;
		const values = alwaysOn
			.map((d) => uptimeOf(deviceBuckets(store.series!.devices[d.id])))
			.filter((v): v is number => v != null);
		return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
	});
	const medianLatency = $derived.by(() => {
		const values = devices
			.map((d) => d.checks.find((c) => c.type === 'ping' && c.up)?.latency_ms)
			.filter((v): v is number => v != null)
			.sort((a, b) => a - b);
		return values.length ? values[Math.floor(values.length / 2)] : null;
	});

	const sorted = $derived(
		[...devices].sort((a, b) => {
			const rank: Record<Status, number> = { down: 0, degraded: 1, waiting: 2, up: 3, offline: 4 };
			return rank[statusOf(a)] - rank[statusOf(b)] || a.name.localeCompare(b.name);
		})
	);
</script>

<svelte:head><title>Overview · Kastellan</title></svelte:head>

{#if store.devices === null}
	<div class="space-y-4">
		<div class="h-36 animate-pulse rounded-2xl bg-surface-2"></div>
		<div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
			{#each [0, 1, 2] as i (i)}<div
					class="h-32 animate-pulse rounded-2xl bg-surface-2"
				></div>{/each}
		</div>
	</div>
{:else if devices.length === 0}
	<div class="card mx-auto max-w-lg px-8 py-12 text-center">
		<h1 class="text-xl font-semibold tracking-tight">Nothing to watch yet</h1>
		<p class="mt-2 text-sm leading-relaxed text-muted">
			Add your devices to <code class="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[13px]">
				config.yaml
			</code>. Kastellan notices the change by itself and starts checking them within seconds.
		</p>
	</div>
{:else}
	<!-- The headline: is everything all right? -->
	<section class="card relative overflow-hidden p-6 sm:p-8">
		<div
			class="pointer-events-none absolute -top-24 -right-24 size-72 rounded-full opacity-[0.12] blur-3xl"
			style:background="var(--{tone})"
		></div>
		<div class="relative flex flex-col gap-6 lg:flex-row lg:items-center lg:justify-between">
			<div class="flex items-start gap-4">
				<div
					class="grid size-12 shrink-0 place-items-center rounded-2xl"
					style:background="color-mix(in srgb, var(--{tone}) 13%, transparent)"
					style:color="var(--{tone}-text)"
				>
					{#if attention}<TriangleAlert class="size-6" />{:else}<CircleCheck class="size-6" />{/if}
				</div>
				<div>
					<h1 class="text-2xl font-semibold tracking-tight sm:text-[28px]">
						{#if attention}
							{attention} {attention === 1 ? 'device needs' : 'devices need'} attention
						{:else}
							Everything is running
						{/if}
					</h1>
					<p class="mt-1.5 text-sm text-muted">
						{devices.length} devices · {checkCount} checks · updated {ago(
							store.updated ? new Date(store.updated).toISOString() : null,
							store.now
						)}
					</p>
				</div>
			</div>
			<dl class="grid grid-cols-3 gap-6 sm:gap-10">
				<div>
					<dt class="text-xs text-muted">Online</dt>
					<dd class="tabular mt-1 text-2xl font-semibold tracking-tight">
						{counts.up + counts.degraded}<span class="text-base font-normal text-faint">
							/{devices.length}
						</span>
					</dd>
				</div>
				<div>
					<dt class="text-xs text-muted">Uptime 24 h</dt>
					<dd class="tabular mt-1 text-2xl font-semibold tracking-tight">
						{percent(fleetUptime)}
					</dd>
				</div>
				<div>
					<dt class="text-xs text-muted">Median ping</dt>
					<dd class="tabular mt-1 text-2xl font-semibold tracking-tight">{ms(medianLatency)}</dd>
				</div>
			</dl>
		</div>

		{#if problems.length}
			<ul class="relative mt-6 divide-y divide-line rounded-xl border border-line bg-surface-2/60">
				{#each problems as { device, check } (`${device.id}/${check.name}`)}
					<li>
						<a
							href="/devices/{device.id}"
							class="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-surface-2"
						>
							<StatusDot status={statusOf(device)} size="size-2" />
							<span class="min-w-0 flex-1 truncate text-sm">
								<span class="font-medium">{device.name}</span>
								<span class="text-muted">· {check.name}</span>
								<span class="text-faint"> — {check.detail}</span>
							</span>
							<span class="tabular shrink-0 text-xs text-muted">
								{check.since ? `for ${stateFor(check, store.now)}` : ''}
							</span>
							<ChevronRight class="size-4 shrink-0 text-faint" />
						</a>
					</li>
				{/each}
			</ul>
		{/if}
	</section>

	<div class="mt-8 grid gap-8 xl:grid-cols-[1fr_20rem]">
		<!-- Devices -->
		<section>
			<div class="mb-3 flex items-baseline justify-between">
				<h2 class="text-sm font-semibold">Devices</h2>
				<a href="/devices" class="text-xs font-medium text-accent hover:underline">All devices</a>
			</div>
			<div class="grid gap-3 sm:grid-cols-2">
				{#each sorted as device (device.id)}
					{@const status = statusOf(device)}
					{@const buckets = deviceBuckets(store.series?.devices[device.id])}
					{@const ping = device.checks.find((c) => c.up && c.latency_ms != null)}
					<a
						href="/devices/{device.id}"
						class="card group block p-4 transition-all hover:-translate-y-px hover:border-line-strong hover:shadow-pop"
					>
						<div class="flex items-start gap-3">
							<div
								class="grid size-9 shrink-0 place-items-center rounded-xl bg-surface-2 text-muted transition-colors group-hover:text-text"
							>
								<KindIcon kind={device.kind} />
							</div>
							<div class="min-w-0 flex-1">
								<p class="truncate text-sm font-medium">{device.name}</p>
								<p class="truncate font-mono text-xs text-faint">{device.host}</p>
							</div>
							<StatusBadge {status} />
						</div>
						<div class="mt-4">
							{#if store.series && buckets.length}
								<UptimeStrip
									{buckets}
									start={store.series.start}
									step={store.series.step_seconds}
									alwaysOn={device.always_on}
									height="h-5"
								/>
							{:else}
								<div class="h-5 rounded bg-surface-2"></div>
							{/if}
						</div>
						<div class="tabular mt-2.5 flex justify-between text-[11px] text-faint">
							<span>
								{device.always_on
									? `24 h · ${percent(uptimeOf(buckets))}`
									: `on ${Math.round((uptimeOf(buckets) ?? 0) * 100)} % of the day`}
							</span>
							<span>
								{ping ? ms(ping.latency_ms) : status === 'offline' ? 'asleep or off' : ''}
							</span>
						</div>
					</a>
				{/each}
			</div>
		</section>

		<!-- Activity -->
		<section>
			<h2 class="mb-3 text-sm font-semibold">Last 24 hours</h2>
			<div class="card p-2">
				{#if store.changes.length === 0}
					<p class="px-3 py-6 text-center text-sm text-muted">No ups and downs. A quiet day.</p>
				{:else}
					<ol class="max-h-[34rem] overflow-y-auto">
						{#each store.changes.slice(0, 40) as change (`${change.time}/${change.device}/${change.check}`)}
							{@const device = store.byId.get(change.device)}
							<li>
								<a
									href="/devices/{change.device}"
									class="flex gap-3 rounded-lg px-3 py-2.5 transition-colors hover:bg-surface-2"
								>
									<span
										class="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full"
										style:background="color-mix(in srgb, {change.up
											? 'var(--good)'
											: device && !device.always_on
												? 'var(--offline)'
												: 'var(--critical)'} 16%, transparent)"
										style:color={change.up
											? 'var(--good-text)'
											: device && !device.always_on
												? 'var(--muted)'
												: 'var(--critical-text)'}
									>
										{#if change.up}<ArrowUp class="size-3" strokeWidth={2.5} />{:else}<ArrowDown
												class="size-3"
												strokeWidth={2.5}
											/>{/if}
									</span>
									<span class="min-w-0 flex-1 text-sm leading-snug">
										<span class="font-medium">{device?.name ?? change.device}</span>
										<span class="text-muted">
											{change.check}
											{change.up
												? 'back up'
												: device && !device.always_on
													? 'went offline'
													: 'went down'}
										</span>
										<span class="tabular block text-xs text-faint">{dayClock(change.time)}</span>
									</span>
								</a>
							</li>
						{/each}
					</ol>
				{/if}
			</div>
		</section>
	</div>
{/if}
