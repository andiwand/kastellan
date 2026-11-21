<script lang="ts">
	import type { HostReport } from '#lib/api.js';
	import { ago, duration } from '#lib/format.js';
	import { store } from '#lib/state.svelte.js';
	import { runner, type Job } from '#lib/runner.svelte.js';
	import JobStatus from './JobStatus.svelte';
	import LogsDialog from './LogsDialog.svelte';
	import ReadAgain from './ReadAgain.svelte';

	/** How a host is doing, from its collector: system, disks and their space, RAID, disk
	 * health, temperatures, failed services, updates. Coloured only where something is wrong. */
	let {
		report,
		checked,
		error,
		forecast = {},
		device = '',
		canAct = false
	}: {
		report: HostReport;
		checked: string | null;
		error: string | null;
		forecast?: Record<string, number>; // days until full at the rate it fills
		device?: string;
		canAct?: boolean; // system updates and a reboot through the actor (the Updates page's queue)
	} = $props();

	const upgrade = $derived<Job>({ device, kind: 'packages' });
	const reboot = $derived<Job>({ device, kind: 'reboot' });
	const busy = (j: Job) => ['queued', 'running'].includes(runner.state(j)?.state ?? '');
	let asking = $state<'packages' | 'reboot' | null>(null);
	let logsOf = $state<string | null>(null); // the unit whose journal is open

	// The same limits the alarms use (config.yaml's alarms:).
	const WARN = $derived(store.alarms?.limits?.disk_warning ?? 85);
	const CRIT = $derived(store.alarms?.limits?.disk_critical ?? 95);
	const bytes = (n: number) =>
		n >= 1e12
			? `${(n / 1e12).toFixed(1)} TB`
			: n >= 1e9
				? `${(n / 1e9).toFixed(1)} GB`
				: `${(n / 1e6).toFixed(0)} MB`;
	const percent = (used: number, free: number) => (used + free ? (100 * used) / (used + free) : 0);
	const color = (p: number) =>
		p >= CRIT ? 'var(--critical)' : p >= WARN ? 'var(--warning)' : 'var(--accent)';

	const sys = $derived(report.system);
	const memUsed = $derived(sys ? sys.memory.total - sys.memory.available : 0);
	const disks = $derived(report.smart?.disks ?? []);
	const hot = $derived(
		[...(report.temperatures ?? [])].sort((a, b) => b.celsius - a.celsius).slice(0, 6)
	);
	const failed = $derived(report.services?.failed ?? []);
	const errors = $derived(Object.entries(report.errors ?? {}));
</script>

<section class="card mt-8 p-5">
	<div class="flex flex-wrap items-baseline justify-between gap-2">
		<h2 class="text-sm font-semibold">Host</h2>
		<p class="text-xs text-muted">
			{#if error}<span style:color="var(--warning-text)">collector: {error}</span> ·
			{/if}from the collector{checked ? `, ${ago(checked, store.now)}` : ''}
			{#if device}· <ReadAgain {device} />{/if}
		</p>
	</div>

	{#if sys}
		<dl class="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
			<div>
				<dt class="text-xs text-muted">Up</dt>
				<dd class="tabular">{duration(sys.uptime)}</dd>
			</div>
			<div>
				<dt class="text-xs text-muted">Load ({sys.cpus} cores)</dt>
				<dd class="tabular">{sys.load.map((l) => l.toFixed(2)).join(' · ')}</dd>
			</div>
			<div>
				<dt class="text-xs text-muted">Memory</dt>
				<dd class="tabular">{bytes(memUsed)} of {bytes(sys.memory.total)}</dd>
			</div>
			<div>
				<dt class="text-xs text-muted">Kernel</dt>
				<dd class="truncate font-mono text-xs" title={sys.kernel}>{sys.kernel}</dd>
			</div>
		</dl>
	{/if}

	{#if report.filesystems?.length}
		<h3 class="mt-5 text-xs font-medium text-muted">Space</h3>
		<ul class="mt-2 space-y-2">
			{#each report.filesystems as fs, i (i)}
				{@const p = percent(fs.used, fs.available)}
				<li class="grid grid-cols-[minmax(0,10rem)_1fr_auto] items-center gap-3 text-sm">
					<span class="truncate font-mono text-xs" title={fs.device}>{fs.mount}</span>
					<span class="h-2 overflow-hidden rounded-full bg-surface-3">
						<span class="block h-full rounded-full" style:width="{p}%" style:background={color(p)}
						></span>
					</span>
					<span class="tabular text-xs text-muted">
						{p.toFixed(0)}% · {bytes(fs.available)} free
						{#if forecast[fs.mount] != null}
							{@const days = forecast[fs.mount]}
							<span
								style:color={days < (store.alarms?.limits?.disk_days ?? 14)
									? 'var(--warning-text)'
									: ''}
								title="At the rate it filled over the last days"
							>
								· full {days < 1 ? 'within a day' : `in ~${Math.round(days)} d`}
							</span>
						{/if}
					</span>
				</li>
			{/each}
		</ul>
	{/if}

	<div class="mt-5 grid gap-5 sm:grid-cols-2">
		{#if report.raid?.length || disks.length}
			<div>
				<h3 class="text-xs font-medium text-muted">Disks</h3>
				<ul class="mt-2 space-y-1.5 text-sm">
					{#each report.raid ?? [] as md, i (i)}
						{@const bad = !md.active || md.status.includes('_') || md.failed.length > 0}
						<li class="flex flex-wrap items-baseline gap-x-2">
							<span class="font-mono text-xs">{md.name}</span>
							<span class="text-muted">{md.level} of {md.devices.join(', ')}</span>
							<span style:color={bad ? 'var(--critical-text)' : 'var(--good-text)'}>
								{bad ? `degraded [${md.status}]` : `healthy [${md.status}]`}
							</span>
							{#if md.progress}<span class="text-xs text-muted">
									{md.progress.what}
									{md.progress.percent}%
								</span>{/if}
						</li>
					{/each}
					{#each disks as d, i (i)}
						{@const wear =
							(d.reallocated ?? 0) +
							(d.pending ?? 0) +
							(d.uncorrectable ?? 0) +
							(d.nvme_media_errors ?? 0)}
						<li class="flex flex-wrap items-baseline gap-x-2">
							<span class="font-mono text-xs">{d.disk}</span>
							<span class="truncate text-muted">{d.model ?? ''}</span>
							{#if d.asleep}
								<span class="text-faint">asleep (not woken to read it)</span>
							{:else if d.error}
								<span class="text-faint">{d.error}</span>
							{:else if d.passed === false}
								<span style:color="var(--critical-text)">fails its health check</span>
							{:else if wear > 0}
								<span style:color="var(--warning-text)">wear: {wear} bad sectors/errors</span>
							{:else if d.passed}
								<span style:color="var(--good-text)">healthy</span>
							{:else}
								<span class="text-faint">health unknown</span>
							{/if}
							{#if d.temperature != null}<span class="tabular text-xs text-muted">
									{d.temperature} °C
								</span>{/if}
							{#if d.power_on_hours != null}<span class="tabular text-xs text-faint">
									{Math.round((d.power_on_hours / 24 / 365) * 10) / 10} years on
								</span>{/if}
						</li>
					{/each}
					{#if report.smart && !report.smart.available}
						<li class="text-xs text-faint">Disk health needs smartmontools on the host.</li>
					{/if}
				</ul>
			</div>
		{/if}

		{#if hot.length}
			<div>
				<h3 class="text-xs font-medium text-muted">Temperatures</h3>
				<ul class="mt-2 space-y-1 text-sm">
					{#each hot as t, i (i)}
						<li class="flex justify-between gap-3">
							<span class="truncate text-muted">{t.sensor}</span>
							<span
								class="tabular"
								style:color={t.limit != null && t.celsius >= t.limit
									? 'var(--warning-text)'
									: undefined}
							>
								{t.celsius.toFixed(0)} °C{t.limit != null ? ` / ${t.limit.toFixed(0)}` : ''}
							</span>
						</li>
					{/each}
				</ul>
			</div>
		{/if}
	</div>

	<div class="mt-5 flex flex-wrap gap-x-6 gap-y-2 text-sm">
		<p>
			<span class="text-xs text-muted">Services</span><br />
			{#if failed.length}
				<span style:color="var(--warning-text)">{failed.length} failed:</span>
				{#each failed as unit, i (unit)}
					{#if canAct}<button
							class="font-mono text-xs underline decoration-dotted hover:text-text"
							title="Its log"
							onclick={() => (logsOf = unit)}
						>
							{unit}
						</button>{:else}<span class="font-mono text-xs">{unit}</span>{/if}{i < failed.length - 1
						? ', '
						: ''}
				{/each}
			{:else}
				<span style:color="var(--good-text)">none failed</span>
			{/if}
		</p>
		{#if report.updates}
			<p>
				<span class="text-xs text-muted">Updates</span><br />
				{report.updates.available == null
					? 'unknown'
					: report.updates.available === 0
						? 'none pending'
						: `${report.updates.available} pending${report.updates.security ? `, ${report.updates.security} for security` : ''}`}
				{#if canAct && report.updates.available && asking !== 'packages'}
					<button
						class="btn btn-sm ml-1"
						disabled={busy(upgrade)}
						onclick={() => (asking = 'packages')}
					>
						Install
					</button>
				{/if}
				{#if report.updates.reboot}
					· <span style:color="var(--warning-text)">wants a reboot</span>
					{#if canAct && asking !== 'reboot'}
						<button
							class="btn btn-sm ml-1"
							disabled={busy(reboot)}
							onclick={() => (asking = 'reboot')}
						>
							Reboot
						</button>
					{/if}
				{/if}
				{#if asking}
					<span class="mt-1 flex flex-wrap items-center gap-2 text-xs">
						{asking === 'packages'
							? 'Install them now (apt; keeps config files, removes nothing)?'
							: 'Reboot it in a minute (cancel on the host: shutdown -c)?'}
						<button
							class="btn btn-sm btn-primary"
							onclick={() => {
								runner.add([asking === 'packages' ? upgrade : reboot]);
								asking = null;
							}}
						>
							{asking === 'packages' ? 'Install' : 'Reboot'}
						</button>
						<button class="btn btn-sm" onclick={() => (asking = null)}>Not now</button>
					</span>
				{/if}
				<JobStatus job={runner.state(upgrade)} />
				<JobStatus job={runner.state(reboot)} />
			</p>
		{/if}
	</div>

	{#if errors.length}
		<p class="mt-4 text-xs text-faint">
			Couldn't read: {errors.map(([k, v]) => `${k} (${v})`).join('; ')}
		</p>
	{/if}
</section>

{#if logsOf}
	<LogsDialog {device} what={{ unit: logsOf }} onclose={() => (logsOf = null)} />
{/if}
