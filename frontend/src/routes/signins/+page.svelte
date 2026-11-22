<script lang="ts">
	import { onMount } from 'svelte';
	import { more, type SignIn, type SignInSource, type SignIns } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { dayClock } from '#lib/format.js';
	import { count } from '#lib/glossary.js';
	import { signinFlag, signinState, type State } from '#lib/states.js';
	import Tabs from '#lib/components/Tabs.svelte';
	import PageHeader from '#lib/components/ui/PageHeader.svelte';
	import Section from '#lib/components/ui/Section.svelte';
	import Table, { type Column } from '#lib/components/ui/Table.svelte';
	import StateBadge from '#lib/components/ui/StateBadge.svelte';
	import Tile from '#lib/components/ui/Tile.svelte';
	import RangePicker from '#lib/components/ui/RangePicker.svelte';
	import EmptyState from '#lib/components/ui/EmptyState.svelte';
	import X from '@lucide/svelte/icons/x';

	/** Who signs in where, and who tries: SSH, VPN, the routers' web interfaces and the hub,
	 * from the central logs and the audit log (signins.py). Per address with flags, and the
	 * events themselves. A VPN client's address suggests the path, not who it was. */
	const SERVICE: Record<string, string> = {
		ssh: 'SSH',
		vpn: 'VPN',
		router: 'Router login',
		hub: 'Kastellan'
	};
	const OUTCOMES = [
		{ id: 'all', label: 'All' },
		{ id: 'ok', label: 'Signed in' },
		{ id: 'failed', label: 'Failed and broken off' }
	] as const;

	let hours = $state(24);
	let data = $state<SignIns | null>(null);
	let error = $state<string | null>(null);
	let outcome = $state<'all' | 'ok' | 'failed'>('all');
	let outsideOnly = $state(false);
	let showOwn = $state(false);
	let address = $state<string | null>(null);
	let busy = $state(false);

	async function load() {
		busy = true;
		try {
			data = await more.signins(hours);
			error = null;
		} catch (e) {
			error = (e as Error).message;
		} finally {
			busy = false;
		}
	}
	onMount(load);

	const name = (id: string) =>
		id === 'kastellan' ? 'Kastellan' : (store.devices?.find((d) => d.id === id)?.name ?? id);
	const events = $derived(
		(data?.events ?? [])
			.filter(
				(e: SignIn) =>
					(showOwn || !e.own) &&
					(!outsideOnly || e.outside) &&
					(outcome === 'all' || (outcome === 'ok' ? e.outcome === 'ok' : e.outcome !== 'ok')) &&
					(!address || e.address === address)
			)
			.map((e, i) => ({ ...e, i }))
	);
	const sources = $derived(
		(data?.sources ?? []).filter(
			(s) => (!outsideOnly || s.outside) && (showOwn || !data?.own.includes(s.address))
		)
	);
	const flagged = $derived(sources.filter((s) => s.flags.length));

	type Event = SignIn & { i: number };
	const sourceColumns: Column<SignInSource>[] = [
		{ key: 'from', label: 'From', cell: sourceAddress },
		{ key: 'why', label: 'Why', wrap: true, cell: sourceFlags },
		{
			key: 'where',
			label: 'Where',
			priority: 2,
			grow: true,
			value: (s) =>
				`${s.services.map((x) => SERVICE[x] ?? x).join(', ')} on ${s.devices.map(name).join(', ')}`
		},
		{
			key: 'as',
			label: 'As',
			priority: 3,
			value: (s) => s.users.slice(0, 5).join(', ') + (s.users.length > 5 ? ', …' : '')
		},
		{ key: 'ok', label: 'Signed in', numeric: true, priority: 3, value: (s) => s.ok },
		{ key: 'failed', label: 'Failed', numeric: true, value: (s) => s.failed },
		{
			key: 'attempts',
			label: 'Broken off',
			numeric: true,
			priority: 3,
			title: 'Connections that ended before a login',
			value: (s) => s.attempts
		},
		{ key: 'last', label: 'Last', numeric: true, priority: 2, value: (s) => dayClock(s.last) }
	];
	const eventColumns: Column<Event>[] = [
		{ key: 'when', label: 'When', numeric: true, align: 'left', value: (e) => dayClock(e.ts) },
		{ key: 'outcome', label: 'Outcome', cell: eventOutcome },
		{
			key: 'where',
			label: 'Where',
			priority: 2,
			value: (e) => `${SERVICE[e.service] ?? e.service} · ${name(e.device)}`
		},
		{ key: 'account', label: 'Account', priority: 3, value: (e) => e.user },
		{ key: 'from', label: 'From', priority: 2, cell: eventFrom }
	];
</script>

{#snippet addressButton(at: string, outside: boolean)}
	<button class="font-mono hover:underline" onclick={() => (address = at)}>{at}</button>
	{#if outside}<span class="ml-1 rounded bg-surface-2 px-1 py-px text-meta text-muted">
			outside
		</span>{/if}
{/snippet}
{#snippet sourceAddress(s: SignInSource)}
	{@render addressButton(s.address, s.outside)}
{/snippet}
{#snippet sourceFlags(s: SignInSource)}
	<span class="flex flex-wrap gap-1">
		{#each s.flags as f (f)}<StateBadge state={signinFlag[f]} />{/each}
	</span>
{/snippet}
{#snippet eventOutcome(e: Event)}
	<StateBadge state={signinState[e.outcome]} quiet />
	{#if !e.verified}<span
			class="ml-1 text-meta text-faint"
			title="From syslog without TLS: it can't raise a successful-sign-in alert"
		>
			unverified
		</span>{/if}
{/snippet}
{#snippet eventFrom(e: Event)}
	{#if e.address}{@render addressButton(e.address, e.outside)}{/if}
	{#if e.via}<span class="text-faint"> via VPN {e.via}</span>{/if}
	{#if e.line}
		<a
			class="ml-1 text-faint hover:underline"
			href="/logs?device={encodeURIComponent(e.device)}&q={encodeURIComponent(
				e.address ?? e.user ?? ''
			)}"
		>
			log
		</a>
	{/if}
{/snippet}

<svelte:head><title>Sign-ins · Kastellan</title></svelte:head>

<PageHeader
	title="Sign-ins"
	summary="SSH, VPN, router and hub logins from the logs. A VPN address suggests a path, not who it was."
>
	{#snippet actions()}
		<RangePicker
			value={hours}
			onchange={(h) => {
				hours = h;
				load();
			}}
		/>
	{/snippet}
</PageHeader>

{#if error}
	<EmptyState kind="error" text={error} onretry={load} />
{/if}

{#if data}
	<div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
		{#each Object.entries(SERVICE) as [id, label] (id)}
			{@const c = data.summary.by_service[id] ?? { ok: 0, failed: 0, attempt: 0 }}
			{@const state: State | undefined = c.failed
				? { word: count(c.failed, 'failure'), tone: 'warning' }
				: c.ok
					? { word: 'No failures', tone: 'good' }
					: undefined}
			<Tile
				{label}
				value={c.ok}
				unit="signed in"
				{state}
				sub={c.attempt ? `${c.attempt} broken off before a login` : undefined}
			/>
		{/each}
	</div>
	<p class="mt-2 text-meta text-muted">
		{count(data.summary.outside_addresses, 'outside address', 'outside addresses')} tried;
		{count(data.summary.outside_ok, 'success', 'successes')} from outside. Syslog without TLS is unverified
		and can't raise a successful-sign-in alert.
	</p>

	{#if flagged.length}
		<Section title="Worth a look" count={flagged.length}>
			<Table
				columns={sourceColumns}
				rows={flagged.slice(0, 30)}
				key={(s) => s.address}
				label="Addresses worth a look"
			/>
		</Section>
	{/if}

	<Section title="Events" count={events.length}>
		{#snippet actions()}
			<Tabs
				small
				label="Outcome"
				tabs={[...OUTCOMES]}
				current={outcome}
				onselect={(o) => (outcome = o)}
			/>
			<label class="flex items-center gap-1.5 text-meta text-text">
				<input type="checkbox" bind:checked={outsideOnly} /> From outside only
			</label>
			<label
				class="flex items-center gap-1.5 text-meta text-text"
				title="Kastellan's own logins and checks (from {data?.own.join(', ') || 'the hub'})"
			>
				<input type="checkbox" bind:checked={showOwn} /> Kastellan's own
			</label>
			{#if address}
				<button
					class="inline-flex h-7 items-center gap-1 rounded-lg border border-line px-2 font-mono text-meta text-text hover:border-line-strong"
					onclick={() => (address = null)}
					title="Show every address"
				>
					{address}
					<X class="size-3.5" />
				</button>
			{/if}
		{/snippet}
		<Table
			columns={eventColumns}
			rows={events.slice(0, 500)}
			key={(e) => String(e.i)}
			label="Sign-in events"
			empty="Nothing {busy
				? 'yet'
				: 'in this time'}. Sign-ins show when the devices' logs reach Kastellan (Logs → sources)."
		/>
		{#if events.length > 500}
			<p class="mt-2 text-meta text-faint">The newest 500 of {events.length}.</p>
		{/if}
	</Section>
{/if}
