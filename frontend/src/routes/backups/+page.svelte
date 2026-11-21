<script lang="ts">
	import { onMount } from 'svelte';
	import { api, more, type Backup, type DeviceBackupSummary } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { ago, dayClock } from '#lib/format.js';
	import KindIcon from '#lib/components/KindIcon.svelte';

	/** Every backup in one place: the jobs that report in (backups.py) and the devices'
	 * configurations Kastellan keeps itself (devbackups.py). */
	let jobs = $state<Backup[] | null>(null);
	let configs = $state<DeviceBackupSummary[] | null>(null);
	let error = $state<string | null>(null);
	let busy = $state<string | null>(null);
	let notes = $state<Record<string, string>>({});

	async function load() {
		try {
			[jobs, configs] = await Promise.all([api.backups(), more.deviceBackups()]);
			error = null;
		} catch (e) {
			error = (e as Error).message;
		}
	}
	onMount(() => {
		load();
		const t = setInterval(load, 60_000);
		return () => clearInterval(t);
	});

	async function now(id: string) {
		busy = id;
		try {
			const res = await more.backupNow(id);
			notes[id] = res.new ? 'new version kept' : 'unchanged';
			await load();
		} catch (e) {
			notes[id] = (e as Error).message;
		} finally {
			busy = null;
		}
	}

	const name = (id: string) => store.byId.get(id)?.name ?? id;
	const kind = (id: string) => store.byId.get(id)?.kind ?? 'other';
	const TONE: Record<Backup['state'], [string, string]> = {
		ok: ['var(--good-text)', 'ran'],
		failed: ['var(--critical-text)', 'failed'],
		late: ['var(--warning-text)', 'late'],
		waiting: ['var(--muted)', 'waiting for its first report']
	};
	const KIND: Record<string, string> = {
		'openwrt-sysupgrade': 'OpenWrt',
		'fritzbox-export': 'FRITZ!Box export'
	};
</script>

<svelte:head><title>Backups · Kastellan</title></svelte:head>

<h1 class="text-2xl font-semibold tracking-tight">Backups</h1>
<p class="mt-1 max-w-2xl text-sm text-muted">
	Your backup jobs that report in, and the devices' own configurations, which Kastellan keeps
	itself: each version once, encrypted, with what changed from one to the next.
</p>
{#if error}<p class="mt-4 text-sm" style:color="var(--critical-text)">{error}</p>{/if}

<h2 class="mt-8 mb-3 text-sm font-semibold">Device configurations</h2>
{#if configs === null}
	<div class="card h-32 animate-pulse"></div>
{:else if !configs.length}
	<div class="card px-5 py-6 text-sm text-muted">
		None yet. Kastellan backs up OpenWrt devices whose login may (tick “Download its configuration
		backup” in the device's Setup) and FRITZ!Boxes with Kastellan's login.
	</div>
{:else}
	<div class="card divide-y divide-line">
		{#each configs as c (c.device)}
			<div class="flex flex-wrap items-center gap-x-4 gap-y-1 px-5 py-3 text-sm">
				<a
					href="/devices/{c.device}"
					class="flex min-w-48 items-center gap-2 font-medium hover:underline"
				>
					<KindIcon kind={kind(c.device)} class="size-4 text-muted" />
					{name(c.device)}
				</a>
				<span class="text-xs text-muted">
					{c.kind ? (KIND[c.kind] ?? c.kind) : 'no login any more'}
				</span>
				<span class="text-xs">
					{c.versions}
					{c.versions === 1 ? 'version' : 'versions'}{#if c.latest}, newest {dayClock(
							c.latest.taken
						)}{#if c.latest.checked !== c.latest.taken}
							· the same until {ago(c.latest.checked, store.now)}{/if}{/if}
				</span>
				<span class="ml-auto flex items-center gap-3 text-xs">
					{#if c.status.error}
						<span style:color={c.status.denied ? 'var(--muted)' : 'var(--warning-text)'}>
							{c.status.error}
						</span>
					{:else if notes[c.device]}
						<span class="text-muted">{notes[c.device]}</span>
					{/if}
					{#if c.kind}
						<button class="btn btn-sm" disabled={busy !== null} onclick={() => now(c.device)}>
							{busy === c.device ? 'Backing up…' : 'Back up now'}
						</button>
					{/if}
					<a class="btn btn-sm" href="/devices/{c.device}#config-backups">Versions</a>
				</span>
			</div>
		{/each}
	</div>
{/if}

<h2 class="mt-8 mb-3 text-sm font-semibold">Backup jobs</h2>
{#if jobs === null}
	<div class="card h-24 animate-pulse"></div>
{:else if !jobs.length}
	<div class="card px-5 py-6 text-sm text-muted">
		None. A backup job (restic, borg, a script) can tell Kastellan when it ran: add it under
		Settings → Backups, then make its token on the device's page.
	</div>
{:else}
	<div class="card divide-y divide-line">
		{#each jobs as b (b.id)}
			<div class="flex flex-wrap items-center gap-x-4 gap-y-1 px-5 py-3 text-sm">
				<span class="min-w-48 font-medium">{b.name}</span>
				<a href="/devices/{b.device}" class="text-xs text-muted hover:underline">
					{name(b.device)}
				</a>
				<span class="text-xs" style:color={TONE[b.state][0]}>
					{TONE[b.state][1] +
						(b.state === 'ok' && b.ok
							? ` ${ago(b.ok, store.now)}`
							: b.state === 'failed' && b.message
								? `: ${b.message}`
								: '')}
				</span>
				<span class="ml-auto text-xs text-faint">expected every {b.within_hours} h</span>
			</div>
		{/each}
	</div>
{/if}
