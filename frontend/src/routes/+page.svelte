<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { store } from '#lib/state.svelte.js';
	import { api, more, type Backup, type DeviceBackupSummary } from '#lib/api.js';
	import type { InternetView, SignInsSummary } from '#lib/api.js';
	import { statusOf, type Status } from '#lib/status.js';
	import { ago } from '#lib/format.js';
	import { attentionHeadline, count } from '#lib/glossary.js';
	import { areasOf, fleetCounts, internetStrip, type AreaId, type Read } from '#lib/overview.js';
	import DeviceList from '#lib/components/DeviceList.svelte';
	import ActivityEntryItem from '#lib/components/ActivityEntryItem.svelte';
	import UptimeStrip from '#lib/components/UptimeStrip.svelte';
	import IssueRow from '#lib/components/ui/IssueRow.svelte';
	import Section from '#lib/components/ui/Section.svelte';
	import StateBadge from '#lib/components/ui/StateBadge.svelte';
	import StateDot from '#lib/components/ui/StateDot.svelte';
	import EmptyState from '#lib/components/ui/EmptyState.svelte';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import Globe from '@lucide/svelte/icons/globe';
	import Wifi from '@lucide/svelte/icons/wifi';
	import HardDrive from '@lucide/svelte/icons/hard-drive';
	import DatabaseBackup from '@lucide/svelte/icons/database-backup';
	import LogIn from '@lucide/svelte/icons/log-in';
	import Download from '@lucide/svelte/icons/download';

	const devices = $derived(store.devices ?? []);
	// One list, one count (attention.ts): the headline is the list's length.
	const attention = $derived(store.attention);
	const admin = $derived(store.me?.role === 'admin');
	const fleet = $derived(fleetCounts(devices));

	// What the areas need beyond the store, read now and then.
	let internet = $state<Read<InternetView>>(null);
	let backups = $state<Read<Backup[]>>(null);
	let configBackups = $state<Read<DeviceBackupSummary[]>>(null);
	let signins = $state<Read<SignInsSummary>>(null);

	async function read<T>(load: () => Promise<T>, keep: (v: Read<T>) => void, had: Read<T>) {
		try {
			keep(await load());
		} catch {
			if (had === null) keep('error'); // a later failure keeps what was read
		}
	}
	function readAreas(slow = false) {
		if (!admin) {
			read(api.backups, (v) => (backups = v), backups);
			return;
		}
		read(
			() => more.internet(24),
			(v) => (internet = v),
			internet
		);
		read(api.backups, (v) => (backups = v), backups);
		read(more.deviceBackups, (v) => (configBackups = v), configBackups);
		if (slow || signins === null)
			read(
				() => more.signinsSummary(24),
				(v) => (signins = v),
				signins
			);
	}
	onMount(() => {
		let n = 0;
		// Sign-ins read the logs: every fifth time (5 min), the rest every minute.
		const t = setInterval(() => readAreas(++n % 5 === 0), 60_000);
		return () => clearInterval(t);
	});
	// Again once it is known who is looking (admins see more).
	const who = $derived(store.me ? (store.me.role ?? 'nobody') : null);
	$effect(() => {
		if (who) untrack(() => readAreas(true));
	});

	const areas = $derived(
		areasOf({ attention, devices, admin, internet, backups, configBackups, signins })
	);
	const strip = $derived(internet && internet !== 'error' ? internetStrip(internet) : null);
	const icons: Record<AreaId, typeof Globe> = {
		internet: Globe,
		wifi: Wifi,
		storage: HardDrive,
		backups: DatabaseBackup,
		signins: LogIn,
		updates: Download
	};

	// Devices: the ones with problems (the Devices badge's), then the rest on request.
	let showAll = $state(false);
	const rank: Record<Status, number> = { down: 0, degraded: 1, waiting: 2, up: 3, offline: 4 };
	const byState = (list: typeof devices) =>
		[...list].sort((a, b) => rank[statusOf(a)] - rank[statusOf(b)] || a.name.localeCompare(b.name));
	const problems = $derived(byState(attention.problemDevices));
	const rest = $derived(byState(devices.filter((d) => !attention.problemDevices.includes(d))));
	const deviceSections = $derived([
		{
			site: '',
			name: '',
			rows: [...problems, ...(showAll ? rest : [])].map((device) => ({ device, depth: 0 }))
		}
	]);

	let ackError = $state<string | null>(null);
	async function acknowledge(key: string) {
		try {
			await api.acknowledge(key);
			ackError = null;
		} catch (e) {
			ackError = (e as Error).message;
		}
		store.refreshHistory();
	}
</script>

{#snippet fine()}
	<button
		onclick={() => (showAll = !showAll)}
		aria-expanded={showAll}
		class="flex w-full items-center gap-2 px-5 py-3 text-left text-body text-muted transition-colors hover:bg-surface-2/70 hover:text-text"
	>
		<ChevronDown class="size-4 shrink-0 transition-transform {showAll ? 'rotate-180' : ''}" />
		{#if showAll}
			Show only the ones with problems
		{:else}
			{problems.length
				? `${rest.length} more, all fine`
				: `${count(rest.length, 'device')}, all fine`}
		{/if}
	</button>
{/snippet}

<svelte:head><title>Overview · Kastellan</title></svelte:head>

{#if store.devices === null}
	<div class="space-y-4">
		<div class="h-14 w-80 max-w-full animate-pulse rounded-xl bg-surface-2"></div>
		<div class="h-48 animate-pulse rounded-2xl bg-surface-2"></div>
		<div class="h-64 animate-pulse rounded-2xl bg-surface-2"></div>
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
	<!-- The verdict: is everything all right? -->
	<header class="mb-6">
		<h1 class="flex items-center gap-3 text-title">
			<StateDot
				tone={attention.tone}
				label={attention.count ? 'Needs attention' : 'All good'}
				size="size-2.5"
			/>
			{attentionHeadline(attention.count)}
		</h1>
		<p class="tabular mt-1 text-body text-muted">
			{fleet.online} of {count(fleet.devices, 'device')} online{#if fleet.services}
				· {fleet.servicesUp} of {count(fleet.services, 'service')} up{/if}
			· {count(attention.updates.length, 'update')} waiting
			<span class="text-faint">
				· updated {ago(store.updated ? new Date(store.updated).toISOString() : null, store.now)}
			</span>
		</p>
	</header>

	<div class="grid gap-x-8 xl:grid-cols-[minmax(0,1fr)_20rem]">
		<div class="min-w-0">
			{#if attention.count}
				<Section
					title="Needs attention"
					count={attention.count}
					first
					meta={store.alarms?.channels
						? `alarms sent to ${count(store.alarms.channels, 'channel')}`
						: 'no notify: channels in config.yaml'}
				>
					{#if ackError}
						<p class="mb-2 text-meta" style:color="var(--critical-text)">{ackError}</p>
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
									{#if alarm && !alarm.acknowledged_by}
										<button
											onclick={() => acknowledge(alarm.key)}
											disabled={!admin}
											title={admin ? 'Say you are on it: no more reminders' : 'Admins only'}
											class="btn btn-sm text-muted disabled:opacity-50"
										>
											Acknowledge
										</button>
									{:else if issue.area === 'updates' && !alarm}
										<a href="/updates" class="btn btn-sm text-muted">Updates</a>
									{/if}
								{/snippet}
							</IssueRow>
						{/each}
					</ul>
				</Section>
			{/if}

			<Section title="Areas" first={!attention.count}>
				<ul class="card divide-y divide-line">
					{#each areas as area (area.id)}
						{@const Icon = icons[area.id]}
						<li>
							<a
								href={area.href}
								class="grid grid-cols-[1.25rem_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1.5 px-4 py-3 transition-colors hover:bg-surface-2/70 sm:grid-cols-[1.25rem_7rem_8.5rem_minmax(0,1fr)_auto]"
							>
								<Icon class="size-4 text-muted" />
								<span class="text-body font-medium">{area.label}</span>
								<span class="justify-self-end sm:justify-self-start">
									<StateBadge state={area.state} quiet />
								</span>
								<span
									class="col-span-2 col-start-2 flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5 sm:col-span-1 sm:col-start-auto"
								>
									{#if area.value != null}<span class="tabular text-body font-semibold">
											{area.value}
										</span>{/if}
									{#if area.sub}<span class="text-meta text-muted">{area.sub}</span>{/if}
									{#if area.sentence}<span class="basis-full text-meta text-text">
											{area.sentence}
										</span>{/if}
									{#if area.id === 'internet' && strip}
										<span class="mt-1 block basis-full sm:max-w-80">
											<UptimeStrip
												buckets={strip.buckets}
												start={strip.start}
												step={strip.step}
												height="h-3"
												label="Internet"
											/>
										</span>
									{/if}
								</span>
								<ChevronRight
									class="hidden size-4 text-faint sm:col-start-5 sm:row-start-1 sm:block"
								/>
							</a>
						</li>
					{/each}
				</ul>
			</Section>

			<Section
				title="Devices"
				href="/devices"
				count={problems.length || null}
				meta={problems.length ? 'with problems' : undefined}
			>
				<DeviceList
					sections={deviceSections}
					flat
					compact
					footer={rest.length ? fine : undefined}
				/>
			</Section>
		</div>

		<aside class="mt-section min-w-0 xl:mt-0">
			<div class="xl:sticky xl:top-6">
				<Section title="Last 24 hours" first>
					{#if store.activity.length === 0}
						<EmptyState text="No outages, no updates. A quiet day." />
					{:else}
						<div class="card p-1.5">
							<ol class="max-h-[40rem] overflow-y-auto">
								{#each store.activity.slice(0, 40) as entry, i (i)}
									<li><ActivityEntryItem {entry} /></li>
								{/each}
							</ol>
						</div>
					{/if}
				</Section>
			</div>
		</aside>
	</div>
{/if}
