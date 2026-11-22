<script lang="ts">
	import { more, type ChannelPlan, type WifiOverview } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { bandLabel, dayClock } from '#lib/format.js';
	import Table, { type Column } from './ui/Table.svelte';
	import EmptyState from './ui/EmptyState.svelte';
	import JobDialog from './JobDialog.svelte';
	import StateDot from './ui/StateDot.svelte';

	/** The channel plan (wifi.plan): asked for, never applied by itself. Each radio now and
	 * suggested, with why; the admin ticks which to move and applies them one access point at a
	 * time (OpenWrt with its rollback; a FRITZ!Box has none), then a scan follows. `suggest()`
	 * can be called from outside (the Findings' "Suggest channels"). */
	let {
		plans = [],
		onapplied
	}: {
		plans?: WifiOverview['plans'];
		onapplied?: () => void;
	} = $props();

	type Row = ChannelPlan['radios'][number];
	let plan = $state<ChannelPlan | null>(null);
	let error = $state<string | null>(null);
	let busy = $state(false);
	let dfs = $state(false);
	let chosen = $state<Set<string>>(new Set());
	let applying = $state(false);

	const name = (id: string) => store.byId.get(id)?.name ?? id;
	const keyOf = (r: Row) => `${r.device}/${r.radio}`;
	const canChange = $derived(store.editBlocked === null);

	export async function suggest() {
		error = null;
		busy = true;
		try {
			plan = await more.wifiPlan(dfs);
			chosen = new Set(plan.radios.filter((r) => r.change).map(keyOf));
		} catch (e) {
			error = (e as Error).message;
		} finally {
			busy = false;
		}
	}
	function toggle(r: Row, on: boolean) {
		const next = new Set(chosen);
		if (on) next.add(keyOf(r));
		else next.delete(keyOf(r));
		chosen = next;
	}
	const width = (r: Row) => (r.htmode ? Number(r.htmode.replace(/^[A-Z]+/, '')) : r.width);

	const columns: Column<Row>[] = [
		{ key: 'pick', label: '', width: '2.5rem', cell: pick },
		{ key: 'ap', label: 'Access point', grow: true, cell: apCell },
		{ key: 'now', label: 'Now', priority: 2, width: '6rem', cell: nowCell },
		{ key: 'then', label: 'Suggested', width: '7rem', cell: thenCell },
		{ key: 'why', label: 'Why', priority: 3, grow: true, cell: whyCell }
	];
</script>

{#snippet pick(r: Row)}
	<input
		type="checkbox"
		aria-label="Move {name(r.device)} {bandLabel(r.band)}"
		disabled={!r.change || !canChange}
		checked={chosen.has(keyOf(r))}
		onchange={(e) => toggle(r, (e.currentTarget as HTMLInputElement).checked)}
	/>
{/snippet}
{#snippet apCell(r: Row)}
	<span class="block font-medium max-sm:whitespace-normal">{name(r.device)}</span>
	<span class="block text-meta text-muted">{bandLabel(r.band)}</span>
{/snippet}
{#snippet nowCell(r: Row)}
	<span class="tabular">{r.current ?? 'auto'}</span>
{/snippet}
{#snippet thenCell(r: Row)}
	{#if r.change}
		<span class="tabular text-muted sm:hidden">{r.current ?? 'auto'} → </span>
		<span class="tabular font-medium">{r.channel}</span>
		{#if r.htmode}<span class="block text-meta text-muted sm:inline"> · {width(r)} MHz</span>{/if}
	{:else}<span class="text-muted">
			<span class="sm:hidden">{r.current ?? 'auto'} · </span>stays
		</span>{/if}
{/snippet}
{#snippet whyCell(r: Row)}
	<span class="block text-meta whitespace-normal text-muted">{r.reason}</span>
{/snippet}

<div class="flex flex-wrap items-center gap-3">
	<button class="btn btn-sm" disabled={busy} onclick={suggest}>
		{plan ? 'Suggest again' : 'Suggest channels'}
	</button>
	<label class="flex items-center gap-1.5 text-meta text-muted">
		<input type="checkbox" bind:checked={dfs} onchange={() => plan && suggest()} />
		Radar channels (DFS)
	</label>
</div>
{#if error}<div class="mt-3"><EmptyState kind="error" text={error} onretry={suggest} /></div>{/if}
{#if plan}
	<div class="mt-3">
		{#if !plan.scanned}
			<p class="mb-2 flex items-center gap-2 text-meta text-muted">
				<StateDot tone="warning" label="Note" /> No recent scan: radios whose allowed channels aren’t
				known keep theirs.
			</p>
		{/if}
		<Table {columns} rows={plan.radios} key={keyOf} label="Channel plan" />
		<p class="mt-2 text-meta text-muted">
			Disturbance (lower is better): now <span class="tabular">{plan.before}</span>, with the plan
			<span class="tabular">{plan.after}</span>.
		</p>
		{#each plan.hints as h (h)}
			<p class="mt-1 flex items-center gap-2 text-meta text-muted">
				<StateDot tone="warning" label="Note" />{h}
			</p>
		{/each}
		{#if chosen.size}
			<button
				class="btn btn-primary btn-sm mt-3"
				disabled={!canChange}
				title={canChange ? undefined : `Can't change here: ${store.editBlocked}`}
				onclick={() => (applying = true)}
			>
				Apply {chosen.size}
				{chosen.size === 1 ? 'change' : 'changes'}
			</button>
		{/if}
	</div>
{/if}
{#if plans.length}
	<p class="mt-4 text-meta font-medium text-muted">Applied before</p>
	<ul class="mt-1 space-y-0.5 text-meta text-muted">
		{#each [...plans].reverse() as p (p.t)}
			<li>
				<span class="tabular">{dayClock(new Date(p.t * 1000))}</span> by {p.by}: {Object.entries(
					p.outcome
				)
					.map(([k, v]) => `${name(k.split('/')[0])} ${v}`)
					.join(', ')}{#if p.before != null}<span class="tabular">
						· disturbance {p.before} → {p.after} (expected)
					</span>{/if}
			</li>
		{/each}
	</ul>
{/if}

{#if applying && plan}
	{@const p = plan}
	<JobDialog
		title="Apply the channel plan"
		path="/wifi/plan/apply"
		action="Apply"
		body={() => ({
			radios: p.radios
				.filter((r) => chosen.has(keyOf(r)))
				.map((r) => ({
					device: r.device,
					radio: r.radio,
					channel: String(r.channel),
					...(r.htmode ? { htmode: r.htmode } : {})
				})),
			before: p.before,
			after: p.after
		})}
		onclose={() => (applying = false)}
		ondone={onapplied}
	>
		<p>
			{chosen.size}
			{chosen.size === 1 ? 'radio moves' : 'radios move'}, one access point at a time. Clients
			briefly reconnect. OpenWrt rolls back a radio that doesn’t come back; a FRITZ!Box doesn’t.
		</p>
	</JobDialog>
{/if}
