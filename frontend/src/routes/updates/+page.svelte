<script lang="ts">
	import { more } from '#lib/api.js';
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
	import { store } from '#lib/state.svelte.js';
	import { containerAsk, scriptName, tagOf, updatesOf, type Update } from '#lib/updates.js';
	import KindIcon from '#lib/components/KindIcon.svelte';
	import FirmwareDialog from '#lib/components/FirmwareDialog.svelte';
	import Check from '@lucide/svelte/icons/check';
	import JobStatus from '#lib/components/JobStatus.svelte';
	import RenewScripts from '#lib/components/RenewScripts.svelte';
	import { runner, type Job } from '#lib/runner.svelte.js';

	/** Every update waiting on the network, in one list: what, where, from what to what, and the
	 * action, where Kastellan may take it (the device's actor or OpenWrt login allows it). */
	const updates = $derived(updatesOf(store.devices ?? []));
	const canAct = $derived(store.me?.role === 'admin' && !!store.me.user);

	let confirm = $state<string | null>(null); // one update, asked once before it runs
	let confirmAll = $state(false);
	let selected = $state<string[]>([]);
	let renewing = $state<{ device: string; names: string[] } | null>(null);
	let firmware = $state<Extract<Update, { kind: 'firmware' }> | null>(null);
	let dismissError = $state<string | null>(null);
	let dismissing = $state<string | null>(null);

	const keyOf = (u: Update) =>
		`${u.device.id}:${u.kind}:${u.kind === 'container' ? u.key : u.kind === 'abilities' ? u.login : ''}`;
	async function dismiss(u: Update, login: 'actor' | 'openwrt') {
		dismissing = keyOf(u);
		dismissError = null;
		try {
			await more.notNeeded(u.device.id, login);
			await store.refresh();
		} catch (e) {
			dismissError = (e as Error).message;
		} finally {
			dismissing = null;
		}
	}

	/** What the actor does for it, if it can (the same jobs as a device's container table). */
	function jobOf(u: Update): Job | null {
		if (!canAct || !possible(u)) return null;
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

	const LABEL: Record<Update['kind'], string> = {
		container: 'Update',
		packages: 'Install',
		reboot: 'Reboot',
		firmware: 'Update firmware',
		scripts: '',
		abilities: ''
	};
	// Whether Kastellan may do it on that device at all (the host decides in the end).
	const possible = (u: Update) => (u.kind === 'firmware' ? !!u.device.openwrt : !!u.device.act);
	type ContainerUpdate = Extract<Update, { kind: 'container' }>;
	const askOf = (u: ContainerUpdate, to: string) =>
		containerAsk({
			name: u.name,
			from: u.from,
			to,
			compose: u.compose
		});
	/** The newest within its major version, when `to` is a new major one: its own job. */
	function startWithin(u: ContainerUpdate) {
		runner.add([{ device: u.device.id, kind: 'container', name: u.name, tag: u.within! }]);
		confirm = null;
	}
	let skipError = $state<string | null>(null);
	async function notNeeded(u: ContainerUpdate) {
		dismissing = keyOf(u);
		skipError = null;
		try {
			await more.updateNotNeeded(u.device.id, u.key);
			await store.refresh();
		} catch (e) {
			skipError = (e as Error).message;
		} finally {
			dismissing = null;
		}
	}
	const admin = $derived(store.me?.role === 'admin');
	const counts = $derived({
		devices: new Set(updates.map((u) => u.device.id)).size,
		security: updates.filter((u) => u.kind === 'packages' && u.security).length
	});
</script>

{#if dismissError || skipError}<p class="mt-3 text-sm" style:color="var(--critical-text)">
		{dismissError ?? skipError}
	</p>{/if}

<svelte:head><title>Updates · Kastellan</title></svelte:head>

<h1 class="text-2xl font-semibold tracking-tight">Updates</h1>
<p class="mt-1 max-w-2xl text-sm text-muted">
	Available updates and new device abilities. Actions use each device's configured permissions.
</p>

{#if store.health?.newer}
	{@const n = store.health.newer}
	<div class="card mt-6 flex flex-wrap items-center gap-x-3 gap-y-1 p-4 text-sm">
		<span class="font-medium">Kastellan {n.version} is out</span>
		<span class="text-muted">this hub runs {store.health.version}</span>
		<a class="text-accent hover:underline" href={n.url} target="_blank" rel="noopener noreferrer">
			What changed
		</a>
		<span class="w-full text-xs text-muted">
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
	<div class="card mt-6 p-4 text-sm" role="status">
		<div class="flex flex-wrap items-center justify-between gap-2">
			<p>
				<b>{finished} of {p.total}</b> done{#if p.failed}, <span style:color="var(--critical-text)">
						{p.failed} failed
					</span>{/if}{#if p.waiting}, {p.waiting} waiting{/if}{#if !runner.working}. Finished.{/if}
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
			<p class="mt-2 text-xs">
				<span class="font-medium">{job.label}</span>
			</p>
			<JobStatus {job} />
		{/each}
	</div>
{/if}

{#if store.devices === null}
	<div class="card mt-6 h-48 animate-pulse"></div>
{:else if updates.length === 0}
	<div class="card mt-6 flex items-center gap-3 px-5 py-8 text-sm text-muted">
		<Check class="size-5" style="color: var(--good-text)" />
		Nothing waiting, as far as Kastellan can see. (It sees containers and packages where a collector is
		set up, and OpenWrt devices through their login.)
	</div>
{:else}
	<p class="mt-6 text-sm">
		<b>{updates.length}</b>
		{updates.length === 1 ? 'update' : 'updates'} on {counts.devices}
		{counts.devices === 1 ? 'device' : 'devices'}{#if counts.security}, <span
				style:color="var(--warning-text)"
			>
				security updates on {counts.security}
				{counts.security === 1 ? 'host' : 'hosts'}
			</span>{/if}.
	</p>
	{#if runnable.length}
		<div class="mt-3 flex flex-wrap items-center gap-3 text-sm">
			<label class="choice text-xs">
				<input
					type="checkbox"
					checked={chosen.length > 0 && chosen.length === runnable.filter((u) => !busyOf(u)).length}
					onchange={(e) =>
						(selected = e.currentTarget.checked
							? runnable.filter((u) => !busyOf(u)).map(keyOf)
							: [])}
				/>
				<span>All Kastellan can do here</span>
			</label>
			{#if chosen.length}
				<button class="btn btn-sm btn-primary" onclick={() => (confirmAll = true)}>
					Update selected ({chosen.length})
				</button>
			{:else}
				<span class="text-xs text-muted">or tick some to run them one after another</span>
			{/if}
		</div>
		{#if confirmAll && chosen.length}
			<div class="card mt-3 p-4 text-sm">
				<p>
					These {chosen.length} go one after another on each device (a reboot last), devices side by side:
				</p>
				<ul class="mt-2 list-inside list-disc text-xs text-muted">
					{#each chosen as u (keyOf(u))}
						<li>
							{u.device.name}: {u.kind === 'container'
								? askOf(u, u.to)
								: u.kind === 'packages'
									? `${u.count} system packages`
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
	{/if}
	<ul class="card mt-3 divide-y divide-line">
		{#each updates as u (keyOf(u))}
			{@const key = keyOf(u)}
			<li class="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
				{#if runnable.length}
					<!-- Only what can go in a batch gets a box; the rest has its own button. -->
					{#if jobOf(u)}
						<input
							type="checkbox"
							class="shrink-0"
							aria-label="Choose this update"
							value={key}
							bind:group={selected}
							disabled={busyOf(u)}
						/>
					{:else}
						<span class="inline-block size-[13px] shrink-0" aria-hidden="true"></span>
					{/if}
				{/if}
				<a href="/devices/{u.device.id}" class="flex w-48 min-w-0 items-center gap-2.5 text-sm">
					<KindIcon kind={u.device.kind} class="size-4 shrink-0 text-muted" />
					<span class="truncate font-medium hover:underline">{u.device.name}</span>
				</a>
				<div class="min-w-0 basis-full text-sm break-words sm:flex-1 sm:basis-0">
					{#if u.kind === 'container'}
						Container <span class="font-mono text-xs">{u.name}</span>
						<span class="text-muted">
							<span class="font-mono text-xs">{u.from}</span> →
							<span class="font-mono text-xs">{u.to}</span>
						</span>
						{#if u.within}<span
								class="text-xs text-muted"
								title="{u.to} is a new major version; {u.within} is the newest of the one it runs"
							>
								· or <span class="font-mono">{u.within}</span> within its major version
							</span>{/if}
					{:else if u.kind === 'packages'}
						{u.count} system {u.count === 1 ? 'package' : 'packages'}
						{#if u.security}<span class="text-xs" style:color="var(--warning-text)">
								· {u.security} security
							</span>{/if}
					{:else if u.kind === 'reboot'}
						Wants a reboot
						{#if u.why.length}<span class="text-xs text-muted">
								· for {u.why.slice(0, 3).join(', ')}{u.why.length > 3 ? ', …' : ''}
							</span>{/if}
					{:else if u.kind === 'firmware'}
						OpenWrt <span class="text-muted">{u.from} → {u.to}</span>
						{#if !u.supported}<span class="text-xs" style:color="var(--warning-text)">
								· gets no more security fixes
							</span>{/if}
					{:else if u.kind === 'scripts'}
						Kastellan's {u.names.map(scriptName).join(' and ')} there
						<span class="text-xs text-muted">· older than this Kastellan</span>
						{#if u.offered?.length}<span class="text-xs text-muted">
								· new to tick: {u.offered.map((n) => ABILITY[n] ?? n).join(', ')}
							</span>{/if}
					{:else if u.kind === 'abilities'}
						New for its {u.login === 'actor' ? 'actor' : 'OpenWrt login'}:
						{u.names.map((n) => ABILITY[n] ?? n).join(', ')}
						<span class="text-xs text-muted">· not ticked there yet</span>
					{/if}
					{#if jobOf(u)}<JobStatus job={runner.state(jobOf(u)!)} />{/if}
				</div>
				{#if u.kind === 'container' && possible(u) && canAct && (confirm === key || confirm === `${key}:within`)}
					{@const to = confirm === key ? u.to : u.within!}
					<div class="flex basis-full flex-wrap items-center gap-2 text-xs">
						<span class="min-w-0 flex-1">{askOf(u, to)}</span>
						<button
							class="btn btn-sm btn-primary"
							onclick={() => (confirm === key ? start([u]) : startWithin(u))}
						>
							{tagOf(to) ? `Update to ${to}` : 'Update'}
						</button>
						<button class="btn btn-sm" onclick={() => (confirm = null)}>Not now</button>
					</div>
				{:else if u.kind === 'container' && possible(u) && canAct}
					<div class="flex flex-wrap items-center gap-2">
						<button class="btn btn-sm" disabled={busyOf(u)} onclick={() => (confirm = key)}>
							{tagOf(u.to) ? `Update to ${u.to}` : 'Update'}
						</button>
						{#if tagOf(u.within)}
							<button
								class="btn btn-sm"
								disabled={busyOf(u)}
								title="The newest within the major version it runs"
								onclick={() => (confirm = `${key}:within`)}
							>
								to {u.within}
							</button>
						{/if}
						<button
							class="btn btn-sm"
							title="Updates stops showing this one; a newer version shows again"
							disabled={dismissing !== null}
							onclick={() => notNeeded(u)}
						>
							Not needed
						</button>
					</div>
				{:else if LABEL[u.kind]}
					<div class="flex items-center gap-2">
						{#if u.kind === 'container' && admin}
							<button
								class="btn btn-sm"
								title="Updates stops showing this one; a newer version shows again"
								disabled={dismissing !== null}
								onclick={() => notNeeded(u)}
							>
								Not needed
							</button>
						{/if}
						{#if !possible(u)}
							<a class="text-xs text-muted hover:text-text" href="/devices/{u.device.id}">
								{u.kind === 'firmware' ? 'needs its OpenWrt login' : 'needs the actor there'}
							</a>
						{:else if !canAct}
							<span class="text-xs text-muted">{admin ? 'needs sign-in' : 'admins only'}</span>
						{:else if u.kind === 'firmware'}
							<button class="btn btn-sm" onclick={() => (firmware = u)}>{LABEL[u.kind]}</button>
						{:else if confirm === key}
							<span class="text-xs">{u.kind === 'reboot' ? 'Reboot it now?' : 'Go ahead?'}</span>
							<button class="btn btn-sm btn-primary" onclick={() => start([u])}>
								{LABEL[u.kind]}
							</button>
							<button class="btn btn-sm" onclick={() => (confirm = null)}>Not now</button>
						{:else}
							<button class="btn btn-sm" disabled={busyOf(u)} onclick={() => (confirm = key)}>
								{LABEL[u.kind]}
							</button>
						{/if}
					</div>
				{:else if u.kind === 'abilities'}
					{#if admin}
						<div class="flex items-center gap-2">
							<a class="btn btn-sm" href="/devices/{u.device.id}?tab=setup">Set up</a>
							<button
								class="btn btn-sm"
								title="Updates stops offering these for this device"
								disabled={dismissing !== null}
								onclick={() => dismiss(u, u.login)}
							>
								Not needed
							</button>
						</div>
					{/if}
				{:else if u.kind === 'scripts'}
					{#if admin}
						<div class="flex flex-wrap items-center gap-2">
							{#if u.names.some((n) => n.startsWith('vpn-'))}
								<a class="btn btn-sm" href="/vpn?tab=setup">VPN setup</a>
							{/if}
							{#if u.names.includes('openwrt')}
								<a class="btn btn-sm" href="/devices/{u.device.id}?tab=setup">OpenWrt setup</a>
								{#if u.offered?.length}
									<button
										class="btn btn-sm"
										title="Stop offering these new abilities"
										disabled={dismissing !== null}
										onclick={() => dismiss(u, 'openwrt')}
									>
										Not needed
									</button>
								{/if}
							{/if}
							{#if u.names.some((n) => n === 'collector' || n === 'actor')}
								<button
									class="btn btn-sm"
									title="Renewing the scripts takes a login on the host"
									onclick={() => (renewing = { device: u.device.id, names: u.names })}
								>
									Renew host scripts
								</button>
							{/if}
						</div>
					{:else}
						<span class="text-xs text-muted">admins only</span>
					{/if}
				{/if}
			</li>
		{/each}
	</ul>
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
