<script lang="ts">
	import { api, type CollectSetup } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import SetupDialog from './SetupDialog.svelte';

	/** The collector for one host: what it is, whether it reads, and setting it up (a dialog:
	 * with your login, or the commands to run yourself). */
	let { device, canInstall }: { device: string; canInstall: boolean } = $props();

	let setup = $state<CollectSetup | null>(null);
	let error = $state<string | null>(null);
	let note = $state<string | null>(null);
	let busy = $state(false);
	let installing = $state(false);

	async function load() {
		error = null;
		try {
			setup = await api.collectSetup(device);
		} catch (e) {
			error = (e as Error).message;
		}
	}

	async function test() {
		busy = true;
		error = note = null;
		try {
			const r = await api.collectTest(device);
			const missing = Object.keys(r.errors);
			note = missing.length
				? `Answers; couldn't read: ${missing.join(', ')}`
				: 'Answers, and reads everything.';
		} catch (err) {
			error = (err as Error).message;
		} finally {
			busy = false;
		}
	}

	const button =
		'inline-flex h-8 items-center rounded-lg border border-line px-3 text-xs font-medium hover:border-line-strong disabled:opacity-50';
</script>

<details
	class="card mt-8 p-4"
	ontoggle={(e) => (e.currentTarget as HTMLDetailsElement).open && !setup && load()}
>
	<summary class="cursor-pointer text-sm font-semibold">Collector: how the host is doing</summary>
	{#if error}<p class="mt-2 text-xs" style:color="var(--critical-text)">{error}</p>{/if}
	{#if note}<p class="mt-2 text-xs" style:color="var(--good-text)">{note}</p>{/if}
	{#if !setup && !error}<p class="mt-2 text-xs text-muted">Loading…</p>{/if}
	{#if setup}
		<p class="mt-2 text-xs leading-relaxed text-muted">
			A user <b>{setup.user}</b> that can do one thing: run a read-only script as root, which
			reports disks and their space, RAID, disk health, temperatures, failed services, system
			updates and Docker containers (never their settings or passwords). Kastellan's collect key may
			run only that{setup.hub_ip ? `, and only from ${setup.hub_ip}` : ''}. Needs python3 on the
			host; disk health needs smartmontools.
		</p>
		<div class="mt-3 flex flex-wrap items-center gap-3 text-xs">
			{#if setup.state === 'reading'}<span style:color="var(--good-text)">
					Installed and reading.
				</span>
			{:else if setup.state === 'waiting'}<span class="text-muted">
					In config.yaml; waiting for its first report.
				</span>
			{:else if setup.state === 'failing'}<span style:color="var(--warning-text)">
					In config.yaml, but: {setup.error}
				</span>
			{:else}<span class="text-muted">Not set up.</span>{/if}
			<span class="ml-auto flex gap-2">
				{#if setup.configured}
					<button class={button} onclick={test} disabled={busy}>Try it now</button>
				{/if}
				<button class={button} onclick={() => (installing = true)}>
					{setup.configured ? 'Install again…' : 'Install…'}
				</button>
			</span>
		</div>
	{/if}
</details>

{#if installing && setup}
	<SetupDialog
		title="Install the collector"
		intro="Kastellan logs in once with your login, installs the collector as root, and adds collect: to this device in config.yaml."
		path="/devices/{encodeURIComponent(device)}/collect/install"
		script={setup.script}
		port={setup.port}
		canRun={canInstall}
		onclose={() => (installing = false)}
		ondone={async (o) => {
			if (o.ok) {
				await store.refresh();
				await load();
			}
		}}
	/>
{/if}
