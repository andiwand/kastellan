<script lang="ts">
	import type { HostReport } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import DiskActivity from './DiskActivity.svelte';
	import { bytes } from '#lib/format.js';
	import { diskLimits, toneColor, toneOf } from '#lib/vitals.js';

	/** A Linux host's storage and hardware, from its collector: each filesystem's space (and when
	 * it is full at the rate it fills), RAID arrays and disk health (SMART), its temperatures.
	 * The fullest filesystem is also among the vitals. */
	let {
		device,
		report,
		forecast = {}
	}: { device: string; report: HostReport; forecast?: Record<string, number> } = $props();

	// The same limits the alarms use (config.yaml's alarms:).
	const disk = $derived(diskLimits(store.alarms));
	const percent = (used: number, free: number) => (used + free ? (100 * used) / (used + free) : 0);
	const disks = $derived(report.smart?.disks ?? []);
	const hot = $derived(
		[...(report.temperatures ?? [])].sort((a, b) => b.celsius - a.celsius).slice(0, 6)
	);
</script>

{#if report.filesystems?.length || report.raid?.length || disks.length || hot.length || report.activity?.length}
	<section class="mt-8">
		<h2 class="text-sm font-semibold">Storage and hardware</h2>
		{#if report.filesystems?.length}
			<div class="card mt-3 p-4">
				<h3 class="text-xs font-medium text-muted">Space</h3>
				<ul class="mt-2 space-y-2">
					{#each report.filesystems as fs, i (i)}
						{@const p = percent(fs.used, fs.available)}
						<li
							class="grid grid-cols-[minmax(0,1fr)_2.5rem_minmax(0,1.8fr)] items-center gap-3 text-sm sm:grid-cols-[minmax(0,10rem)_1fr_auto]"
						>
							<span class="truncate font-mono text-xs" title={fs.device}>{fs.mount}</span>
							<span class="h-1 overflow-hidden rounded-full bg-[var(--empty)]">
								<span
									class="block h-full rounded-full"
									style:width="{p}%"
									style:background={toneColor[toneOf(p, disk)]}
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
			</div>
		{/if}

		<div class="mt-3 grid gap-3 empty:hidden sm:grid-cols-2">
			{#if report.raid?.length || disks.length}
				<div class="card p-4">
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
				<div class="card p-4">
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
		<DiskActivity {device} />
	</section>
{/if}
