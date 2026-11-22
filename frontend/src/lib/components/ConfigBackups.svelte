<script lang="ts">
	import { more, type DeviceBackups } from '#lib/api.js';
	import BackupDiffDialog from './BackupDiffDialog.svelte';
	import { store } from '#lib/state.svelte.js';
	import { ago, bytes, dayClock } from '#lib/format.js';
	import Download from '@lucide/svelte/icons/download';
	import GitCompare from '@lucide/svelte/icons/git-compare';
	import Lock from '@lucide/svelte/icons/lock';

	/** A device's configuration backups (devbackups.py): each version once ("the same since"),
	 * what changed between two (secrets masked), and the download to restore it. Admins. */
	let { device }: { device: string } = $props();

	let data = $state<DeviceBackups | null>(null);
	let error = $state<string | null>(null);
	let busy = $state(false);
	let note = $state<string | null>(null);
	let diff = $state<{ older: string; newer: string } | null>(null);

	async function load() {
		try {
			data = await more.backupsOf(device);
			error = null;
		} catch (e) {
			error = (e as Error).message;
		}
	}
	$effect(() => {
		void device;
		load();
		const t = setInterval(load, 60_000);
		return () => clearInterval(t);
	});

	async function now() {
		busy = true;
		note =
			data?.kind === 'fritzbox-export'
				? 'If the FRITZ!Box asks for confirmation, press a button on it now.'
				: null;
		try {
			const res = await more.backupNow(device);
			data = res;
			note = res.new ? 'A new version.' : 'The same as the newest version: nothing new kept.';
		} catch (e) {
			note = (e as Error).message;
		} finally {
			busy = false;
		}
	}

	function compare(older: string, newer: string) {
		diff = { older, newer };
	}

	const signedIn = $derived(!!store.me?.user);
	const KIND: Record<string, string> = {
		'openwrt-sysupgrade':
			'OpenWrt backup (sysupgrade -b): restore it in LuCI, System → Backup / Flash',
		'fritzbox-export':
			'Extract the ZIP, then restore the .export in FRITZ!OS → System → Backup → Restore. Use the password in export-password.txt.'
	};
</script>

{#if data && (data.kind || data.versions.length)}
	<section class="card mt-8 p-5">
		<div class="flex flex-wrap items-baseline justify-between gap-2">
			<h2 class="text-sm font-semibold">Configuration backups</h2>
			<p class="text-xs text-muted">
				{#if data.status.error}
					<span style:color={data.status.denied ? undefined : 'var(--warning-text)'}>
						{data.status.error}
					</span>
				{:else if data.status.ok}
					taken {ago(data.status.ok, store.now)}, every {data.every_hours} h
				{:else}
					every {data.every_hours} h
				{/if}
				{#if data.kind && store.me?.role === 'admin'}
					·
					<button
						class="underline hover:text-text disabled:opacity-50"
						disabled={busy}
						onclick={now}
					>
						{busy ? 'backing up…' : 'back up now'}
					</button>
				{/if}
			</p>
		</div>
		{#if data.kind}<p class="mt-1 text-xs text-faint">{KIND[data.kind] ?? data.kind}</p>{/if}
		{#if note}<p class="mt-2 text-xs text-muted">{note}</p>{/if}
		{#if data.versions.length}
			<ol class="mt-3 divide-y divide-line text-sm">
				{#each data.versions as v, i (v.id)}
					<li class="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
						<span class="tabular">{dayClock(v.taken)}</span>
						<span class="text-xs text-muted">
							{v.files}
							{v.files === 1 ? 'file' : 'files'}, {bytes(v.size)}
							{#if v.checked !== v.taken}· the same until {ago(v.checked, store.now)}{/if}
							{#if v.by}· by {v.by}{/if}
						</span>
						<span class="ml-auto flex items-center gap-3 text-xs">
							{#if i < data.versions.length - 1 && signedIn}
								<button
									class="inline-flex items-center gap-1 text-muted hover:text-text"
									onclick={() => compare(data!.versions[i + 1].id, v.id)}
								>
									<GitCompare class="size-3.5" /> what changed
								</button>
							{/if}
							{#if signedIn}
								<a
									class="inline-flex items-center gap-1 text-muted hover:text-text"
									href={more.backupDownload(device, v.id)}
									download
									title="Everything, passwords and keys too"
								>
									<Download class="size-3.5" /> download
								</a>
							{/if}
						</span>
					</li>
				{/each}
			</ol>
			<p class="mt-2 flex items-center gap-1.5 text-xs text-faint">
				<Lock class="size-3" /> Kept encrypted; a version the same as the one before isn't kept twice.
			</p>
		{:else if !data.status.error}
			<p class="mt-3 text-sm text-muted">No version yet: the first is taken within minutes.</p>
		{/if}
		{#if error}<p class="mt-2 text-xs" style:color="var(--critical-text)">{error}</p>{/if}
	</section>
{/if}

{#if diff}
	{@const d = diff}
	<BackupDiffDialog
		load={() => more.backupDiff(device, d.older, d.newer)}
		onclose={() => (diff = null)}
	/>
{/if}
