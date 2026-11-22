<script lang="ts">
	import { more } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import {
		byUrgency,
		containerAsk,
		scriptName,
		setupFor,
		skipOf,
		tagOf,
		updateStateOf,
		urgencyOf,
		URGENCY,
		type Update
	} from '#lib/updates.js';
	import { count, WORD } from '#lib/glossary.js';
	import { runner, type Job } from '#lib/runner.svelte.js';
	import KindIcon from '#lib/components/KindIcon.svelte';
	import FirmwareDialog from '#lib/components/FirmwareDialog.svelte';
	import JobStatus from '#lib/components/JobStatus.svelte';
	import RenewScripts from '#lib/components/RenewScripts.svelte';
	import PageHeader from '#lib/components/ui/PageHeader.svelte';
	import Table, { type Column } from '#lib/components/ui/Table.svelte';
	import StateBadge from '#lib/components/ui/StateBadge.svelte';
	import StateDot from '#lib/components/ui/StateDot.svelte';
	import EmptyState from '#lib/components/ui/EmptyState.svelte';
	import LogIn from '@lucide/svelte/icons/log-in';

	/** Every update waiting on the network, in one list by how soon it matters (security fixes
	 * and firmware out of support first, then reboots, then the rest, then Kastellan's own
	 * helpers): what, where, from what to what, and the action on its row where Kastellan may
	 * take it (the device's actor or OpenWrt login allows it), or what to set up first. */
	const ABILITY: Record<string, string> = {
		'rollback-container': 'going back to a container’s previous image',
		'container-logs': 'container logs',
		'unit-logs': 'service logs',
		journal: 'the host’s journal for Logs',
		'docker-logs': 'every container’s log for Logs',
		logs: 'its system log for Logs',
		backup: 'configuration backups',
		scan: 'Wi-Fi scans',
		bandwidth: 'bandwidth per device',
		roaming: 'Wi-Fi roaming'
	};
	const ability = (n: string) => ABILITY[n] ?? n;

	const updates = $derived(byUrgency(store.attention.updates)); // the same list the badge counts
	const admin = $derived(store.me?.role === 'admin');
	const canAct = $derived(admin && !!store.me?.user);
	// Signed out (sign-in off, so no one is signed in): one "Sign in to manage" in the header;
	// rows that are problems keep their action, greyed, with the reason.
	const signedOut = $derived(!!store.me && !store.me.user);
	const why = $derived(
		signedOut ? 'Actions need sign-in: add a user under Settings' : WORD.adminsOnly
	);

	let confirm = $state<string | null>(null); // one update, asked once before it runs
	let confirmAll = $state(false);
	let selected = $state<string[]>([]);
	let renewing = $state<{ device: string; names: string[] } | null>(null);
	let firmware = $state<Extract<Update, { kind: 'firmware' }> | null>(null);
	let dismissing = $state<string | null>(null); // the update being skipped, until the hub has it
	let dismissError = $state<string | null>(null);

	const keyOf = (u: Update) =>
		`${u.device.id}:${u.kind}:${u.kind === 'container' ? u.key : u.kind === 'abilities' ? u.login : ''}`;
	/** Skip: Updates stops showing it (the not-needed routes): a container's update or a
	 * device's (until a newer one), new abilities (with the OpenWrt helpers' row: those offered). */
	async function skip(u: Update) {
		dismissing = keyOf(u);
		dismissError = null;
		try {
			if (u.kind === 'container') await more.updateNotNeeded(u.device.id, u.key);
			else if (u.kind === 'abilities') await more.notNeeded(u.device.id, u.login);
			else if (u.kind === 'scripts') await more.notNeeded(u.device.id, 'openwrt');
			else await more.deviceUpdateNotNeeded(u.device.id, u.kind);
			await store.refresh();
		} catch (e) {
			dismissError = (e as Error).message;
		} finally {
			dismissing = null;
		}
	}

	/** What the actor does for it, if it can (the same jobs as a device's container table). */
	function jobOf(u: Update): Job | null {
		if (!canAct || setupFor(u)) return null;
		if (u.kind === 'container')
			return {
				device: u.device.id,
				kind: 'container',
				name: u.name,
				...(tagOf(u.to) ? { tag: tagOf(u.to) } : {})
			};
		if (u.kind === 'packages' || u.kind === 'reboot') return { device: u.device.id, kind: u.kind };
		return null;
	}
	const runnable = $derived(updates.filter((u) => jobOf(u)));
	const chosen = $derived(runnable.filter((u) => selected.includes(keyOf(u))));
	const busyOf = (u: Update) => {
		const j = jobOf(u);
		const s = j ? runner.state(j)?.state : undefined;
		return s === 'queued' || s === 'running';
	};

	function start(list: Update[]) {
		runner.add(list.map(jobOf).filter((j): j is Job => j !== null));
		selected = selected.filter((k) => !list.some((u) => keyOf(u) === k));
		confirm = null;
		confirmAll = false;
	}

	/** The action that does it. */
	const LABEL: Partial<Record<Update['kind'], string>> = {
		container: 'Update',
		packages: 'Install',
		reboot: 'Reboot',
		firmware: 'Update firmware'
	};
	type ContainerUpdate = Extract<Update, { kind: 'container' }>;
	const askOf = (u: ContainerUpdate, to: string) =>
		containerAsk({ name: u.name, from: u.from, to, compose: u.compose });
	/** The newest within its major version, when `to` is a new major one: its own job. */
	function startWithin(u: ContainerUpdate) {
		runner.add([{ device: u.device.id, kind: 'container', name: u.name, tag: u.within! }]);
		confirm = null;
	}
	const devices = $derived(new Set(updates.map((u) => u.device.id)).size);
	const urgent = $derived(updates.filter((u) => urgencyOf(u) === 'urgent').length);
	const summary = $derived(
		updates.length
			? `${count(updates.length, 'update')} on ${count(devices, 'device')}${urgent ? `, ${urgent} of them for security or out of support` : ''}. Actions use each device's own permissions.`
			: 'Updates waiting on your devices, and what Kastellan can do about them.'
	);

	const columns = $derived<Column<Update>[]>([
		...(canAct && runnable.length
			? [{ key: 'pick', label: '', width: '2.5rem', cell: pick } satisfies Column<Update>]
			: []),
		{ key: 'device', label: 'Device', priority: 2, width: '12rem', cell: deviceCell },
		{ key: 'what', label: 'Update', wrap: true, cell: what },
		{ key: 'state', label: WORD.state, priority: 2, width: '9.5rem', cell: state },
		{ key: 'action', label: '', align: 'right', cell: action }
	]);
</script>

{#snippet pick(u: Update)}
	{#if jobOf(u)}
		<input
			type="checkbox"
			aria-label="Choose this update"
			value={keyOf(u)}
			bind:group={selected}
			disabled={busyOf(u)}
		/>
	{/if}
{/snippet}

{#snippet deviceCell(u: Update)}
	<a href="/devices/{u.device.id}" class="flex min-w-0 items-center gap-2.5">
		<KindIcon kind={u.device.kind} class="size-4 shrink-0 text-muted" />
		<span class="truncate font-medium hover:underline">{u.device.name}</span>
	</a>
{/snippet}

{#snippet state(u: Update)}
	<StateBadge state={updateStateOf(u)} quiet />
{/snippet}

{#snippet what(u: Update)}
	{@const key = keyOf(u)}
	<div class="min-w-0 break-words">
		<!-- On a phone the Device column is dropped: the name leads the line. -->
		<a href="/devices/{u.device.id}" class="font-medium hover:underline sm:hidden">
			{u.device.name}
		</a>
		<span class="sm:hidden">
			<StateDot tone={updateStateOf(u).tone} label={updateStateOf(u).word} size="size-1.5" />
		</span>
		<span class="block sm:inline">
			{#if u.kind === 'container'}
				Container <span class="font-mono">{u.name}</span>
				<span class="text-muted">
					<span class="font-mono">{u.from}</span> → <span class="font-mono">{u.to}</span>
				</span>
				{#if u.within}<span
						class="text-muted"
						title="{u.to} is a new major version; {u.within} is the newest of the one it runs"
					>
						· or <span class="font-mono">{u.within}</span> within its major version
					</span>{/if}
			{:else if u.kind === 'packages'}
				{count(u.count, 'system package')}{#if u.security}<span class="text-muted">
						· {u.security} for security
					</span>{/if}
			{:else if u.kind === 'reboot'}
				Installed updates take effect after a reboot
				{#if u.why.length}<span class="text-muted">
						· for {u.why.slice(0, 3).join(', ')}{u.why.length > 3 ? ', …' : ''}
					</span>{/if}
			{:else if u.kind === 'firmware'}
				OpenWrt <span class="text-muted">{u.from} → {u.to}</span>
				{#if !u.supported}<span class="text-muted">
						· {u.from} gets no more security fixes
					</span>{/if}
			{:else if u.kind === 'scripts'}
				Kastellan's {u.names.map(scriptName).join(' and ')}
				<span class="text-muted">· older than this Kastellan</span>
				{#if u.offered?.length}<span class="text-muted">
						· new to add: {u.offered.map(ability).join(', ')}
					</span>{/if}
			{:else if u.kind === 'abilities'}
				New for its {u.login === 'actor' ? 'actor' : 'OpenWrt login'}:
				{u.names.map(ability).join(', ')}
			{/if}
		</span>
		{#if jobOf(u)}<JobStatus job={runner.state(jobOf(u)!)} />{/if}
		{#if u.kind === 'container' && (confirm === key || confirm === `${key}:within`)}
			<p class="mt-1.5 text-meta">{askOf(u, confirm === key ? u.to : u.within!)}</p>
		{:else if confirm === key}
			<p class="mt-1.5 text-meta">
				{u.kind === 'reboot' ? `Reboot ${u.device.name} now?` : 'Go ahead?'}
			</p>
		{/if}
	</div>
{/snippet}

{#snippet skipButton(u: Update)}
	{@const s = skipOf(u)}
	<button
		class="h-8 px-1 text-meta font-medium whitespace-nowrap text-muted hover:text-text disabled:opacity-50"
		title={s.title}
		disabled={dismissing !== null || busyOf(u)}
		onclick={() => skip(u)}
	>
		{s.label}
	</button>
{/snippet}

{#snippet action(u: Update)}
	{@const key = keyOf(u)}
	{@const setup = setupFor(u)}
	<div class="flex flex-wrap items-center justify-end gap-x-2 gap-y-1">
		{#if u.kind === 'scripts' || u.kind === 'abilities'}
			<!-- Kastellan's own helpers: set up again or add abilities (admins). -->
			{#if admin}
				{#if u.kind === 'abilities'}
					<a class="btn btn-sm" href="/devices/{u.device.id}?tab=setup">Add abilities</a>
				{:else}
					{#if u.names.some((n) => n.startsWith('vpn-'))}
						<a class="btn btn-sm" href="/vpn?tab=setup">VPN setup</a>
					{/if}
					{#if u.names.includes('openwrt')}
						<button
							class="btn btn-sm"
							title="Set up Kastellan's login there again with this Kastellan's helpers, keeping what it may do now (root's password, once)"
							onclick={() => (renewing = { device: u.device.id, names: ['openwrt'] })}
						>
							Renew
						</button>
						{#if u.offered?.length}
							<a
								class="btn btn-sm"
								href="/devices/{u.device.id}?tab=setup"
								title="Tick the new abilities there"
							>
								Add abilities
							</a>
						{/if}
					{/if}
					{#if u.names.some((n) => n === 'collector' || n === 'actor')}
						<button
							class="btn btn-sm"
							title="Renewing the scripts takes a login on the host"
							onclick={() => (renewing = { device: u.device.id, names: u.names })}
						>
							Renew
						</button>
					{/if}
				{/if}
				{#if u.kind === 'abilities' || u.offered?.length}{@render skipButton(u)}{/if}
			{/if}
		{:else if setup}
			<!-- Not possible from here yet: the next step, not a blocker. -->
			{#if admin}
				<a class="btn btn-sm" href={setup.href} title={setup.title}>{setup.label}</a>
				{@render skipButton(u)}
			{/if}
		{:else if !canAct}
			{#if urgencyOf(u) === 'urgent'}
				<!-- A problem row keeps its fix in sight, greyed, with the reason. -->
				<button class="btn btn-sm" disabled title={why}>{LABEL[u.kind]}</button>
			{/if}
		{:else if confirm === key || confirm === `${key}:within`}
			{@const to = u.kind === 'container' && confirm !== key ? u.within! : null}
			<button
				class="btn btn-sm btn-primary"
				onclick={() => (to && u.kind === 'container' ? startWithin(u) : start([u]))}
			>
				{u.kind === 'container'
					? tagOf(to ?? u.to)
						? `Update to ${to ?? u.to}`
						: 'Update'
					: LABEL[u.kind]}
			</button>
			<button class="btn btn-sm" onclick={() => (confirm = null)}>Not now</button>
		{:else if u.kind === 'firmware'}
			<button class="btn btn-sm" onclick={() => (firmware = u)}>{LABEL[u.kind]}</button>
			{@render skipButton(u)}
		{:else}
			<button class="btn btn-sm" disabled={busyOf(u)} onclick={() => (confirm = key)}>
				{u.kind === 'container' && tagOf(u.to) ? `Update to ${u.to}` : LABEL[u.kind]}
			</button>
			{#if u.kind === 'container' && tagOf(u.within)}
				<button
					class="btn btn-sm"
					disabled={busyOf(u)}
					title="The newest within the major version it runs"
					onclick={() => (confirm = `${key}:within`)}
				>
					to {u.within}
				</button>
			{/if}
			{@render skipButton(u)}
		{/if}
	</div>
{/snippet}

{#snippet selection()}
	<label class="choice items-center text-meta text-muted">
		<input
			type="checkbox"
			checked={chosen.length > 0 && chosen.length === runnable.filter((u) => !busyOf(u)).length}
			onchange={(e) =>
				(selected = e.currentTarget.checked ? runnable.filter((u) => !busyOf(u)).map(keyOf) : [])}
		/>
		<span>All Kastellan can do here</span>
	</label>
	{#if chosen.length}
		<button class="btn btn-sm btn-primary" onclick={() => (confirmAll = true)}>
			Update selected ({chosen.length})
		</button>
	{/if}
{/snippet}

<svelte:head><title>Updates · Kastellan</title></svelte:head>

{#snippet signIn()}
	<a class="btn btn-sm" href="/settings" title={why}>
		<LogIn class="size-3.5" />
		{WORD.signInToManage}
	</a>
{/snippet}

<PageHeader
	title="Updates"
	{summary}
	actions={signedOut && updates.length ? signIn : undefined}
	controls={runnable.length ? selection : undefined}
/>

{#if store.health?.newer}
	{@const n = store.health.newer}
	<div class="card mb-6 flex flex-wrap items-center gap-x-3 gap-y-1 p-4 text-body">
		<StateDot tone="accent" label="New" />
		<span class="font-medium">Kastellan {n.version} is out</span>
		<span class="text-muted">this hub runs {store.health.version}</span>
		<a class="text-accent hover:underline" href={n.url} target="_blank" rel="noopener noreferrer">
			What changed
		</a>
		<span class="w-full text-meta text-muted">
			Set the image tag in compose.yaml to {n.version}, then run:
			<code class="font-mono">
				docker compose pull kastellan && docker compose up -d kastellan
			</code>
		</span>
	</div>
{/if}

{#if runner.progress.total}
	{@const p = runner.progress}
	{@const finished = p.done + p.failed}
	<div class="card mb-6 p-4 text-body" role="status">
		<div class="flex flex-wrap items-center justify-between gap-2">
			<p>
				<b>{finished} of {p.total}</b> done{#if p.failed}, {p.failed} failed{/if}{#if p.waiting}, {p.waiting}
					waiting{/if}{#if !runner.working}. Finished.{/if}
			</p>
			{#if !runner.working}
				<button class="btn btn-sm" onclick={() => runner.clear()}>Clear</button>
			{/if}
		</div>
		<div class="mt-2 flex h-2 overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
			<div style:width="{(p.done / p.total) * 100}%" style:background="var(--good)"></div>
			<div style:width="{(p.failed / p.total) * 100}%" style:background="var(--critical)"></div>
			<div
				class="animate-pulse"
				style:width="{(p.running.length / p.total) * 100}%"
				style:background="var(--accent)"
			></div>
		</div>
		{#each p.running as job (job.label)}
			<p class="mt-2 text-meta font-medium">{job.label}</p>
			<JobStatus {job} />
		{/each}
	</div>
{/if}

{#if store.devices === null}
	<div class="card h-48 animate-pulse"></div>
{:else if updates.length === 0}
	<EmptyState
		text="Nothing waiting, as far as Kastellan can see. It sees containers and packages where a collector is set up, and OpenWrt devices through their login."
	/>
{:else}
	{#if confirmAll && chosen.length}
		<div class="card mb-3 p-4 text-body">
			<p>
				These {chosen.length} go one after another on each device (a reboot last), devices side by side:
			</p>
			<ul class="mt-2 list-inside list-disc text-meta text-muted">
				{#each chosen as u (keyOf(u))}
					<li>
						{u.device.name}: {u.kind === 'container'
							? askOf(u, u.to)
							: u.kind === 'packages'
								? count(u.count, 'system package')
								: 'reboot'}
					</li>
				{/each}
			</ul>
			<div class="mt-3 flex gap-2">
				<button class="btn btn-sm btn-primary" onclick={() => start(chosen)}>
					Update {chosen.length}
				</button>
				<button class="btn btn-sm" onclick={() => (confirmAll = false)}>Not now</button>
			</div>
		</div>
	{/if}
	<Table
		{columns}
		rows={updates}
		key={keyOf}
		group={(u) => URGENCY[urgencyOf(u)]}
		label="Updates waiting"
	/>
	{#if dismissError}<p class="mt-3 text-body" style:color="var(--critical-text)" role="alert">
			{dismissError}
		</p>{/if}
{/if}

{#if renewing}
	<RenewScripts device={renewing.device} names={renewing.names} onclose={() => (renewing = null)} />
{/if}

{#if firmware && firmware.device.router?.firmware}
	<FirmwareDialog
		device={firmware.device.id}
		advice={firmware.device.router.firmware}
		model={firmware.device.router.board.model}
		onclose={() => (firmware = null)}
	/>
{/if}
