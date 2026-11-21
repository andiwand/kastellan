<script lang="ts">
	import InternetSummary from '#lib/components/InternetSummary.svelte';
	import { store } from '#lib/state.svelte.js';
	import { deviceBuckets, needsAttention, statusOf, uptimeOf, type Status } from '#lib/status.js';
	import { ago, ms, percent, stateFor } from '#lib/format.js';
	import KindIcon from '#lib/components/KindIcon.svelte';
	import StatusBadge from '#lib/components/StatusBadge.svelte';
	import StatusDot from '#lib/components/StatusDot.svelte';
	import UptimeStrip from '#lib/components/UptimeStrip.svelte';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import ActivityEntryItem from '#lib/components/ActivityEntryItem.svelte';
	import { api } from '#lib/api.js';

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
	let ackError = $state<string | null>(null);
	// Known vulnerabilities that matter: exploited or critical, confirmed.
	const urgentVulns = $derived(
		devices.flatMap((d) =>
			(d.vulns?.items ?? []).flatMap((i) =>
				i.findings
					.filter((f) => f.confidence === 'exact' && (f.exploited || f.severity === 'critical'))
					.map((f) => ({ device: d, f, product: i.product }))
			)
		)
	);
	// Container updates over all Docker hosts, and what can't be checked.
	const updates = $derived(
		devices.flatMap((d) =>
			(d.containers ?? []).filter((c) => c.update && !c.unchecked).map((c) => ({ device: d, c }))
		)
	);
	const unchecked = $derived(
		devices.reduce(
			(n, d) =>
				n +
				(d.containers ?? []).filter((c) => c.unchecked && c.unchecked !== 'not checked yet').length,
			0
		)
	);

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
		{#if urgentVulns.length}
			<div class="relative mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
				<span class="font-medium" style:color="var(--critical-text)">
					{urgentVulns.length} serious {urgentVulns.length === 1
						? 'vulnerability'
						: 'vulnerabilities'}:
				</span>
				{#each urgentVulns.slice(0, 4) as { device, f, product } (`${device.id}/${f.id}/${product}`)}
					<a href="/devices/{device.id}" class="text-muted hover:text-text">
						{device.name}: {f.id}{f.exploited ? ' (exploited)' : ''}
					</a>
				{/each}
			</div>
		{/if}
		{#if updates.length || unchecked}
			<div class="relative mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
				{#if updates.length}
					<span class="font-medium text-accent">
						{updates.length}
						{updates.length === 1 ? 'container update' : 'container updates'}:
					</span>
					{#each updates.slice(0, 6) as { device, c } (`${device.id}/${c.key}`)}
						<a href="/devices/{device.id}" class="text-muted hover:text-text">
							{c.name} <span class="text-faint">→</span>
							{c.update_to ?? 'newer image'}
						</a>
					{/each}
				{/if}
				{#if unchecked}
					<span class="text-xs text-faint">
						{unchecked}
						{unchecked === 1 ? "container's" : "containers'"} updates can't be checked
					</span>
				{/if}
			</div>
		{/if}
	</section>

	{#if store.alarms?.open.length}
		<section class="mt-6">
			<div class="mb-3 flex items-baseline justify-between">
				<h2 class="text-sm font-semibold">Alarms</h2>
				<p class="text-xs text-muted">
					{store.alarms.channels
						? `sent to ${store.alarms.channels} ${store.alarms.channels === 1 ? 'channel' : 'channels'}`
						: 'no notify: channels in config.yaml'}
				</p>
			</div>
			{#if ackError}
				<p class="mb-2 text-xs" style:color="var(--critical-text)">{ackError}</p>
			{/if}
			<ul class="card divide-y divide-line">
				{#each store.alarms.open as alarm (alarm.key)}
					<li class="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-sm">
						<StatusDot status={alarm.severity === 'critical' ? 'down' : 'degraded'} size="size-2" />
						<a
							href="/devices/{alarm.device}"
							class="min-w-0 flex-1 truncate font-medium hover:underline"
						>
							{alarm.title}
						</a>
						{#if alarm.held}
							<span
								class="rounded-md bg-surface-2 px-1.5 py-0.5 text-xs text-muted"
								title="Opened during maintenance: sent only if still there when it ends"
							>
								maintenance · not sent
							</span>
						{/if}
						{#if alarm.acknowledged_by}
							<span class="text-xs text-muted">
								{alarm.acknowledged_by} is on it{alarm.acknowledged_note
									? `: ${alarm.acknowledged_note}`
									: ''}
							</span>
						{:else if store.me?.role === 'admin'}
							<button
								onclick={async () => {
									try {
										await api.acknowledge(alarm.key);
									} catch (e) {
										ackError = (e as Error).message;
									}
									store.refreshHistory();
								}}
								class="h-7 rounded-lg border border-line px-2.5 text-xs font-medium hover:border-line-strong"
							>
								Acknowledge
							</button>
						{/if}
						<!-- A fixed width, so what stands before it lines up from row to row. -->
						<span class="tabular ml-auto w-20 shrink-0 text-right text-xs text-faint">
							{ago(alarm.since ?? alarm.opened, store.now)}
						</span>
					</li>
				{/each}
			</ul>
		</section>
	{/if}

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
			<InternetSummary />
			<h2 class="mb-3 text-sm font-semibold">Last 24 hours</h2>
			<div class="card p-2">
				{#if store.activity.length === 0}
					<p class="px-3 py-6 text-center text-sm text-muted">
						No outages, no updates. A quiet day.
					</p>
				{:else}
					<ol class="max-h-[34rem] overflow-y-auto">
						{#each store.activity.slice(0, 40) as entry, i (i)}
							<li><ActivityEntryItem {entry} /></li>
						{/each}
					</ol>
				{/if}
			</div>
		</section>
	</div>
{/if}
