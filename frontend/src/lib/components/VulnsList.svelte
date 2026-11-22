<script lang="ts">
	import type { Vulns } from '#lib/api.js';
	import { ago } from '#lib/format.js';
	import { store } from '#lib/state.svelte.js';
	import { count } from '#lib/glossary.js';
	import { severityState, vulnRows, type VulnRow } from '#lib/vulns.js';
	import Table, { type Column } from './ui/Table.svelte';
	import StateBadge from './ui/StateBadge.svelte';
	import StateDot from './ui/StateDot.svelte';
	import ExternalLink from '@lucide/svelte/icons/external-link';

	/** Known vulnerabilities in what a device runs, one table grouped by product (most findings
	 * first), within each the exploited and most serious first; honest about doubt (a fix the
	 * distribution may have made, an unknown patch level) and about what couldn't be looked up.
	 * Phones keep the CVE and its severity. */
	let { vulns, exposed }: { vulns: Vulns; exposed: boolean } = $props();

	let all = $state(false);
	const list = $derived(vulnRows(vulns, all));
	const columns: Column<VulnRow>[] = [
		{ key: 'id', label: 'Vulnerability', cell: idCell },
		{ key: 'severity', label: 'Severity', cell: severityCell, width: '7rem' },
		{
			key: 'score',
			label: 'Score',
			numeric: true,
			priority: 3,
			title: 'CVSS',
			value: (f) => (f.score != null ? f.score.toFixed(1) : null)
		},
		{ key: 'note', label: 'Note', priority: 2, cell: noteCell },
		{ key: 'summary', label: 'What it is', priority: 3, grow: true, value: (f) => f.summary }
	];
</script>

{#snippet idCell(f: VulnRow)}
	<a
		href={f.url}
		target="_blank"
		rel="noopener noreferrer"
		class="inline-flex items-center gap-1 font-mono hover:underline"
		title={f.summary}
	>
		{f.id}<ExternalLink class="size-3 text-faint" />
	</a>
{/snippet}

{#snippet severityCell(f: VulnRow)}
	<StateBadge state={severityState(f)} quiet />
{/snippet}

{#snippet noteCell(f: VulnRow)}
	<span class="inline-flex flex-wrap items-center gap-x-2 text-meta text-muted">
		{#if f.exploited}<span
				class="font-medium text-text"
				title="In CISA's catalogue of vulnerabilities exploited in the wild"
			>
				exploited
			</span>{/if}
		{#if f.confidence === 'patch'}
			<span
				title="NVD distinguishes patch levels that this banner omits. Check the full version on the device."
			>
				patch level unknown
			</span>
		{:else if f.confidence !== 'exact'}
			<span
				title="The distribution may have fixed this without changing the upstream version; check its security notice"
			>
				maybe fixed by your distribution
			</span>
		{/if}
		{#if f.fixed_in}<span>fixed in {f.fixed_in}</span>{/if}
	</span>
{/snippet}

<div class="mb-3 flex flex-wrap items-baseline justify-between gap-2">
	<p class="flex items-center gap-2 text-body font-medium">
		{#if list.total}
			<StateDot tone="warning" label="Known vulnerabilities" />
			{count(list.total, 'known vulnerability', 'known vulnerabilities')}
		{:else if vulns.items.length}
			<StateDot tone="good" label="None known" />
			None known for {vulns.items.map((i) => i.product).join(', ')}
		{:else}
			<StateDot tone="neutral" label="Not looked up" />
			Nothing here could be looked up
		{/if}
		{#if exposed}<span class="rounded-md bg-surface-2 px-1.5 py-0.5 text-meta font-normal">
				internet-facing
			</span>{/if}
	</p>
	<p class="text-meta text-muted">NVD, OSV, CISA KEV · {ago(vulns.checked, store.now)}</p>
</div>

{#if list.total}
	<Table
		{columns}
		rows={list.rows}
		key={(f) => f.key}
		group={(f) => `${f.product} · ${f.where}`}
		label="Known vulnerabilities"
	/>
	{#if list.hidden}
		<button
			class="mt-2 text-meta font-medium text-accent hover:underline"
			onclick={() => (all = true)}
		>
			Show all {list.total}
		</button>
	{/if}
{/if}

<div class="mt-3 space-y-1.5 text-meta text-muted">
	{#if vulns.unknown.length}
		<p class="flex items-baseline gap-1.5">
			<StateDot tone="warning" label="Not looked up" size="size-1.5" />
			<span>
				Couldn't be looked up just now: {vulns.unknown.map((u) => u.product).join(', ')}
				{#if Object.keys(vulns.errors).length}({Object.values(vulns.errors).join('; ')}){/if}.
				That's not the same as safe.
			</span>
		</p>
	{/if}
	{#if vulns.kev}
		<p class="flex items-baseline gap-1.5">
			<StateDot tone="warning" label="Out of date" size="size-1.5" />
			<span>{vulns.kev}: the "exploited" marks may be out of date.</span>
		</p>
	{/if}
	{#if vulns.not_looked_up?.length}
		<p>Not looked up (a moving tag, no exact version): {vulns.not_looked_up.join(', ')}.</p>
	{/if}
	<p class="text-faint">
		Only what Kastellan can identify is looked up: services that name their version, firmware,
		images with an exact tag. NVD lags behind with recent CVEs.
	</p>
</div>
