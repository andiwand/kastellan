<script lang="ts">
	import { onMount } from 'svelte';
	import { api, type DataStore, type HubResources } from '#lib/api.js';
	import { bytes } from '#lib/format.js';

	/** What Kastellan costs the machine: its own CPU and memory next to the machine's, and its
	 * data folder store by store, with where each levels off and when the disk would be full. */
	let r = $state<HubResources | null>(null);
	let error = $state<string | null>(null);

	async function load() {
		try {
			r = await api.hubResources();
			error = null;
		} catch (e) {
			error = (e as Error).message;
		}
	}
	onMount(() => {
		load();
		const t = setInterval(load, 30_000);
		return () => clearInterval(t);
	});

	const pct = (part: number | null | undefined, whole: number | null | undefined) =>
		part != null && whole ? Math.min(100, (100 * part) / whole) : 0;
	const days = (d: number) =>
		d < 1
			? 'within a day'
			: d < 60
				? `in ~${Math.round(d)} days`
				: d < 3650
					? `in ~${Math.round(d / 30)} months`
					: 'in more than ten years';
	function levels(s: DataStore): string {
		if (s.steady == null) return s.per_day ? 'keeps growing' : '–';
		if (s.days_to_steady === 0) return `${bytes(s.steady)}, reached`;
		return `~${bytes(s.steady)}${s.days_to_steady != null ? ` ${days(s.days_to_steady)}` : ''}`;
	}
	const machineUsed = $derived(
		r?.machine.memory_total != null && r.machine.memory_available != null
			? r.machine.memory_total - r.machine.memory_available
			: null
	);
	const diskUsed = $derived(
		r?.disk.total != null && r.disk.free != null ? r.disk.total - r.disk.free : null
	);
</script>

{#if error}
	<p class="mt-6 text-sm" style:color="var(--critical-text)">{error}</p>
{/if}
{#if r}
	{@const f = r.forecast}
	<p class="mt-6 max-w-3xl text-sm">
		Kastellan's data takes <b>{bytes(f.used)}</b>{#if f.per_day > 0}, growing by
			{bytes(f.per_day)} a day{/if}.
		{#if f.days_to_steady}It levels off at about <b>{bytes(f.steady)}</b>
			{days(f.days_to_steady)}, once old data goes as fast as new comes.{:else if f.steady}It has
			levelled off at about {bytes(f.steady)}.{/if}
		{#if f.growing_per_day > 0}
			<span class="text-muted">
				Only the audit and alarm logs keep growing ({bytes(f.growing_per_day)} a day){#if f.days_until_full != null}:
					at that rate the disk is full {days(f.days_until_full)}{/if}.
			</span>
		{/if}
	</p>

	<div class="mt-4 grid gap-3 md:grid-cols-3">
		<div class="card p-4">
			<p class="text-xs text-muted">Kastellan</p>
			<p class="tabular mt-1 text-xl font-semibold">
				{r.hub.cpu != null ? `${r.hub.cpu.toFixed(1)} %` : '–'}
				<span class="text-xs font-normal text-muted">of a core</span>
			</p>
			<p class="tabular mt-1 text-xs text-muted">
				{bytes(r.hub.memory)} memory{r.hub.memory_is_peak ? ' (its peak)' : ''}
			</p>
		</div>
		<div class="card p-4">
			<p class="text-xs text-muted">This machine</p>
			<p class="tabular mt-1 text-xl font-semibold">
				{r.machine.cpu != null ? `${r.machine.cpu.toFixed(0)} %` : '–'}
				<span class="text-xs font-normal text-muted">
					CPU{r.machine.cores ? ` of ${r.machine.cores} cores` : ''}
				</span>
			</p>
			{#if machineUsed != null}
				<span class="mt-2 block h-1 overflow-hidden rounded-full bg-[var(--empty)]">
					<span
						class="block h-full rounded-full bg-accent"
						style:width="{pct(machineUsed, r.machine.memory_total)}%"
					></span>
				</span>
				<p class="tabular mt-1 text-xs text-muted">
					{bytes(machineUsed)} of {bytes(r.machine.memory_total)} memory in use
				</p>
			{/if}
		</div>
		<div class="card p-4" title={r.disk.path}>
			<p class="text-xs text-muted">Its disk</p>
			<p class="tabular mt-1 text-xl font-semibold">
				{bytes(r.disk.free)} <span class="text-xs font-normal text-muted">free</span>
			</p>
			{#if diskUsed != null}
				<!-- Everything on the disk, and Kastellan's part of it in the accent colour. -->
				<span class="mt-2 block h-1 overflow-hidden rounded-full bg-[var(--empty)]">
					<span
						class="relative block h-full bg-line-strong"
						style:width="{pct(diskUsed, r.disk.total)}%"
					>
						<span class="absolute inset-y-0 left-0 bg-accent" style:width="{pct(f.used, diskUsed)}%"
						></span>
					</span>
				</span>
				<p class="tabular mt-1 text-xs text-muted">
					{bytes(f.used)} Kastellan · {bytes(diskUsed)} used of {bytes(r.disk.total)}
				</p>
			{/if}
		</div>
	</div>

	<div class="card mt-3 overflow-x-auto">
		<table class="w-full min-w-[40rem] text-sm">
			<thead>
				<tr class="border-b border-line text-left text-xs text-faint">
					<th class="px-4 py-2 font-medium">Data</th>
					<th class="px-2 py-2 text-right font-medium">Now</th>
					<th class="px-2 py-2 text-right font-medium">A day</th>
					<th class="px-2 py-2 font-medium">Kept</th>
					<th class="px-4 py-2 font-medium">Levels off at</th>
				</tr>
			</thead>
			<tbody>
				{#each r.stores as s (s.name)}
					<tr class="border-b border-line last:border-0">
						<td class="px-4 py-2">
							{s.name}
							{#if s.path}<span class="ml-1 font-mono text-xs text-faint">{s.path}</span>{/if}
						</td>
						<td class="tabular px-2 py-2 text-right">{bytes(s.used)}</td>
						<td class="tabular px-2 py-2 text-right text-muted">
							{s.per_day != null ? bytes(s.per_day) : '–'}
						</td>
						<td class="px-2 py-2 text-muted">{s.kept}</td>
						<td class="tabular px-4 py-2 text-muted">{levels(s)}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
	<p class="mt-2 text-xs text-faint">
		A day: what each part filled per day so far. Parts kept for a time level off at that rate times
		how long they keep; backups at the number of versions kept (an upper bound: unchanged files are
		stored once). Retention is set under Settings.
	</p>
{:else if !error}
	<p class="mt-6 text-sm text-muted">Loading…</p>
{/if}
