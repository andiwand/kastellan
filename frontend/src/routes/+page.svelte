<script lang="ts">
	import InternetSummary from '#lib/components/InternetSummary.svelte';
	import { store } from '#lib/state.svelte.js';
	import { deviceBuckets, statusOf, uptimeOf, type Status } from '#lib/status.js';
	import { ago, ms, percent } from '#lib/format.js';
	import { attentionHeadline, count } from '#lib/glossary.js';
	import KindIcon from '#lib/components/KindIcon.svelte';
	import StatusBadge from '#lib/components/StatusBadge.svelte';
	import UptimeStrip from '#lib/components/UptimeStrip.svelte';
	import IssueRow from '#lib/components/ui/IssueRow.svelte';
	import Section from '#lib/components/ui/Section.svelte';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
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
	// One list, one count (attention.ts): the headline is the list's length.
	const attention = $derived(store.attention);
	const tone = $derived(attention.tone);
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
	// Every update waiting (the same list and count as the Updates page and its badge).
	const updates = $derived(attention.updates);
	const containerUpdates = $derived(updates.filter((u) => u.kind === 'container'));
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
					{#if attention.count}<TriangleAlert class="size-6" />{:else}<CircleCheck
							class="size-6"
						/>{/if}
				</div>
				<div>
					<h1 class="text-2xl font-semibold tracking-tight sm:text-[28px]">
						{attentionHeadline(attention.count)}
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

		{#if urgentVulns.length}
			<div class="relative mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
				<span class="font-medium">
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
					<a href="/updates" class="font-medium hover:underline">
						{count(updates.length, 'update')} waiting{containerUpdates.length ? ':' : ''}
					</a>
					{#each containerUpdates.slice(0, 6) as u (`${u.device.id}/${u.key}`)}
						<a href="/devices/{u.device.id}" class="text-muted hover:text-text">
							{u.name} <span class="text-faint">→</span>
							{u.to}
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

	{#if attention.count}
		<div class="mt-6">
			<Section
				title="Needs attention"
				count={attention.count}
				first
				meta={store.alarms?.channels
					? `alarms sent to ${count(store.alarms.channels, 'channel')}`
					: 'no notify: channels in config.yaml'}
			>
				{#if ackError}
					<p class="mb-2 text-xs" style:color="var(--critical-text)">{ackError}</p>
				{/if}
				<ul class="card divide-y divide-line">
					{#each attention.issues as issue (issue.key)}
						{@const alarm = issue.alarm}
						<IssueRow
							tone={issue.tone}
							title={issue.title}
							detail={issue.detail}
							meta={issue.since ? ago(issue.since, store.now) : undefined}
							href={issue.href}
							note={alarm?.acknowledged_by
								? `${alarm.acknowledged_by} is on it${alarm.acknowledged_note ? `: ${alarm.acknowledged_note}` : ''}`
								: alarm?.held
									? 'in maintenance · not sent'
									: undefined}
						>
							{#snippet action()}
								{#if alarm && !alarm.acknowledged_by && store.me?.role === 'admin'}
									<button
										onclick={async () => {
											try {
												await api.acknowledge(alarm.key);
											} catch (e) {
												ackError = (e as Error).message;
											}
											store.refreshHistory();
										}}
										class="btn btn-sm text-muted"
									>
										Acknowledge
									</button>
								{/if}
							{/snippet}
						</IssueRow>
					{/each}
				</ul>
			</Section>
		</div>
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
