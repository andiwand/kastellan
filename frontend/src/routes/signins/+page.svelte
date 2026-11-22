<script lang="ts">
	import { onMount } from 'svelte';
	import { more, type SignIn, type SignIns } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';

	/** Who signs in where, and who tries: SSH, VPN, the routers' web interfaces and the hub,
	 * from the central logs and the audit log (signins.py). Per address with flags, and the
	 * events themselves. A VPN client's address suggests the path, not who it was. */
	const RANGES = [
		{ id: 1, label: '1 h' },
		{ id: 24, label: '24 h' },
		{ id: 168, label: '7 d' },
		{ id: 720, label: '30 d' }
	];
	const SERVICE: Record<string, string> = {
		ssh: 'SSH',
		vpn: 'VPN',
		router: 'Router login',
		hub: 'Kastellan'
	};
	const FLAG: Record<string, { text: string; color: string; title: string }> = {
		'new-outside': {
			text: 'new from outside',
			color: 'var(--critical-text)',
			title: 'A success from an outside address that never succeeded before this week'
		},
		'outside-ok': {
			text: 'signed in from outside',
			color: 'var(--warning-text)',
			title: 'A success from a public address'
		},
		repeated: {
			text: 'many failures',
			color: 'var(--warning-text)',
			title: 'Ten or more failed or broken-off tries: a password guesser or a scanner'
		}
	};

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

	const name = (id: string) => store.devices?.find((d) => d.id === id)?.name ?? id;
	const when = (ts: number) =>
		new Date(ts * 1000).toLocaleString(undefined, {
			month: 'short',
			day: 'numeric',
			hour: '2-digit',
			minute: '2-digit'
		});
	const events = $derived(
		(data?.events ?? []).filter(
			(e: SignIn) =>
				(showOwn || !e.own) &&
				(!outsideOnly || e.outside) &&
				(outcome === 'all' || (outcome === 'ok' ? e.outcome === 'ok' : e.outcome !== 'ok')) &&
				(!address || e.address === address)
		)
	);
	const sources = $derived(
		(data?.sources ?? []).filter(
			(s) => (!outsideOnly || s.outside) && (showOwn || !data?.own.includes(s.address))
		)
	);
	const flagged = $derived(sources.filter((s) => s.flags.length));
	const color = (o: string) =>
		o === 'ok' ? 'var(--good-text)' : o === 'failed' ? 'var(--critical-text)' : 'var(--muted)';
	const chip =
		'rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors hover:border-line-strong';
</script>

<svelte:head><title>Sign-ins · Kastellan</title></svelte:head>

<div class="flex flex-wrap items-baseline justify-between gap-3">
	<h1 class="text-2xl font-semibold tracking-tight">Sign-ins</h1>
	<div class="flex flex-wrap gap-1.5">
		{#each RANGES as r (r.id)}
			<button
				class="{chip} {hours === r.id ? 'border-accent text-accent' : 'border-line'}"
				onclick={() => ((hours = r.id), load())}
			>
				{r.label}
			</button>
		{/each}
	</div>
</div>
<p class="mt-1 max-w-3xl text-sm text-muted">
	SSH, VPN, router and hub logins from the logs. VPN addresses suggest a path, not an identity.
	Unverified syslog entries cannot trigger successful-sign-in alerts.
</p>

{#if error}
	<p class="mt-4 text-sm" style:color="var(--critical-text)">{error}</p>
{/if}

{#if data}
	<div class="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
		{#each Object.entries(SERVICE) as [id, label] (id)}
			{@const c = data.summary.by_service[id] ?? { ok: 0, failed: 0, attempt: 0 }}
			<div class="card p-3">
				<div class="text-xs text-muted">{label}</div>
				<div class="tabular mt-1 text-lg font-semibold">
					<span style:color="var(--good-text)">{c.ok}</span>
					<span class="text-sm font-normal text-faint">ok</span>
					<span class="ml-1" style:color={c.failed ? 'var(--critical-text)' : undefined}>
						{c.failed}
					</span>
					<span class="text-sm font-normal text-faint">failed</span>
				</div>
				{#if c.attempt}
					<div class="text-xs text-faint">{c.attempt} broken off before a login</div>
				{/if}
			</div>
		{/each}
	</div>
	<p class="mt-2 text-xs text-muted">
		{data.summary.outside_addresses} outside
		{data.summary.outside_addresses === 1 ? 'address' : 'addresses'} tried;
		{data.summary.outside_ok}
		{data.summary.outside_ok === 1 ? 'success' : 'successes'} from outside.
	</p>

	{#if flagged.length}
		<section class="card mt-5 overflow-hidden">
			<h2 class="px-4 pt-4 text-sm font-semibold">Worth a look</h2>
			<div class="mt-2 overflow-x-auto">
				<table class="w-full min-w-[52rem] text-sm">
					<thead>
						<tr class="border-b border-line text-left text-xs text-faint">
							<th class="px-4 py-1.5 font-medium">From</th>
							<th class="px-2 py-1.5 font-medium">Why</th>
							<th class="px-2 py-1.5 font-medium">Where</th>
							<th class="px-2 py-1.5 font-medium">As</th>
							<th class="px-2 py-1.5 text-right font-medium">Ok</th>
							<th class="px-2 py-1.5 text-right font-medium">Failed</th>
							<th
								class="px-2 py-1.5 text-right font-medium"
								title="Connections that ended before a login"
							>
								Broken off
							</th>
							<th class="px-4 py-1.5 text-right font-medium">Last</th>
						</tr>
					</thead>
					<tbody>
						{#each flagged.slice(0, 30) as s (s.address)}
							<tr class="border-b border-line align-baseline last:border-0">
								<td class="px-4 py-1.5 whitespace-nowrap">
									<button
										class="font-mono text-xs hover:underline"
										onclick={() => (address = s.address)}
									>
										{s.address}
									</button>
								</td>
								<td class="px-2 py-1.5 text-xs whitespace-nowrap">
									{#each s.flags as f, i (f)}
										<span class="font-medium" style:color={FLAG[f].color} title={FLAG[f].title}>
											{FLAG[f].text}
										</span>{i < s.flags.length - 1 ? ', ' : ''}
									{/each}
								</td>
								<td class="px-2 py-1.5 text-xs text-muted">
									{s.services.map((x) => SERVICE[x] ?? x).join(', ')} on {s.devices
										.map(name)
										.join(', ')}
								</td>
								<td
									class="max-w-[16rem] truncate px-2 py-1.5 text-xs text-muted"
									title={s.users.join(', ')}
								>
									{s.users.slice(0, 5).join(', ')}{s.users.length > 5 ? ', …' : ''}
								</td>
								<td
									class="tabular px-2 py-1.5 text-right text-xs"
									style:color={s.ok ? 'var(--text)' : 'var(--faint)'}
								>
									{s.ok}
								</td>
								<td
									class="tabular px-2 py-1.5 text-right text-xs"
									style:color={s.failed ? 'var(--text)' : 'var(--faint)'}
								>
									{s.failed}
								</td>
								<td
									class="tabular px-2 py-1.5 text-right text-xs"
									style:color={s.attempts ? 'var(--text)' : 'var(--faint)'}
								>
									{s.attempts}
								</td>
								<td class="tabular px-4 py-1.5 text-right text-xs whitespace-nowrap text-muted">
									{when(s.last)}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</section>
	{/if}

	<section class="card mt-5 overflow-hidden">
		<div class="flex flex-wrap items-center gap-2 px-4 pt-4">
			<h2 class="mr-auto text-sm font-semibold">Events</h2>
			{#each [['all', 'All'], ['ok', 'Successes'], ['failed', 'Failures and tries']] as [id, label] (id)}
				<button
					class="{chip} {outcome === id ? 'border-accent text-accent' : 'border-line'}"
					onclick={() => (outcome = id as typeof outcome)}
				>
					{label}
				</button>
			{/each}
			<label class="flex items-center gap-1.5 text-xs">
				<input type="checkbox" bind:checked={outsideOnly} /> From outside only
			</label>
			<label
				class="flex items-center gap-1.5 text-xs"
				title="Kastellan's own logins and checks (from {data.own.join(', ') || 'the hub'})"
			>
				<input type="checkbox" bind:checked={showOwn} /> Kastellan's own
			</label>
			{#if address}
				<button class="{chip} border-accent text-accent" onclick={() => (address = null)}>
					{address} ✕
				</button>
			{/if}
		</div>
		{#if !events.length}
			<p class="px-4 py-4 text-sm text-muted">
				Nothing {busy ? 'yet' : 'in this time'}. Sign-ins show when the devices' logs reach
				Kastellan (Logs → sources).
			</p>
		{:else}
			<div class="mt-2 overflow-x-auto">
				<table class="w-full min-w-[44rem] text-sm">
					<thead>
						<tr class="text-left text-xs text-faint">
							<th class="px-4 py-1.5 font-medium">When</th>
							<th class="px-2 py-1.5 font-medium">Outcome</th>
							<th class="px-2 py-1.5 font-medium">Where</th>
							<th class="px-2 py-1.5 font-medium">Account</th>
							<th class="px-4 py-1.5 font-medium">From</th>
						</tr>
					</thead>
					<tbody>
						{#each events.slice(0, 500) as e, i (i)}
							<tr class="border-t border-line">
								<td class="tabular px-4 py-1.5 text-xs whitespace-nowrap text-muted">
									{when(e.ts)}
								</td>
								<td class="px-2 py-1.5 text-xs font-medium" style:color={color(e.outcome)}>
									{e.outcome === 'attempt' ? 'broken off' : e.outcome}
									{#if !e.verified}<span class="text-faint"> · unverified</span>{/if}
								</td>
								<td class="px-2 py-1.5 text-xs">
									{SERVICE[e.service] ?? e.service}
									<span class="text-faint">
										· {e.device === 'kastellan' ? 'Kastellan' : name(e.device)}
									</span>
								</td>
								<td class="px-2 py-1.5 text-xs">{e.user ?? '–'}</td>
								<td class="px-4 py-1.5 text-xs">
									{#if e.address}
										<button
											class="font-mono hover:underline"
											onclick={() => (address = e.address)}
											style:color={e.outside ? 'var(--warning-text)' : undefined}
										>
											{e.address}
										</button>
									{/if}
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
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			{#if events.length > 500}
				<p class="px-4 py-2 text-xs text-faint">The newest 500 of {events.length}.</p>
			{/if}
		{/if}
	</section>
{/if}
