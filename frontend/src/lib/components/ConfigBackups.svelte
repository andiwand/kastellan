<script lang="ts">
	import { more, type BackupDiff, type DeviceBackups } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { ago, dayClock } from '#lib/format.js';
	import { modal } from '#lib/modal.js';
	import X from '@lucide/svelte/icons/x';
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
	let diff = $state<{
		older: string;
		newer: string;
		result: BackupDiff | null;
		error?: string;
	} | null>(null);
	let dialog: HTMLDialogElement | undefined = $state();

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
		note = null;
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

	async function compare(older: string, newer: string) {
		const current = {
			older,
			newer,
			result: null as BackupDiff | null,
			error: undefined as string | undefined
		};
		diff = current;
		try {
			const result = await more.backupDiff(device, older, newer);
			if (diff?.older === older && diff?.newer === newer) diff.result = result;
		} catch (e) {
			if (diff?.older === older && diff?.newer === newer) diff.error = (e as Error).message;
		}
	}

	const size = (n: number) =>
		n < 1024
			? `${n} B`
			: n < 1048576
				? `${(n / 1024).toFixed(0)} KB`
				: `${(n / 1048576).toFixed(1)} MB`;
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
							{v.files === 1 ? 'file' : 'files'}, {size(v.size)}
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
	<dialog
		use:modal
		bind:this={dialog}
		onclose={() => (diff = null)}
		class="m-auto max-h-[calc(100dvh-2rem)] w-[min(56rem,calc(100vw-2rem))] rounded-2xl border border-line bg-surface p-0 text-text shadow-pop backdrop:bg-black/40"
	>
		<div class="flex max-h-[calc(100dvh-2rem)] flex-col">
			<div class="flex items-center justify-between border-b border-line px-5 py-4">
				<h2 class="text-base font-semibold">What changed</h2>
				<button class="text-muted hover:text-text" onclick={() => dialog?.close()}>
					<X class="size-5" /><span class="sr-only">Close</span>
				</button>
			</div>
			<div class="overflow-y-auto overscroll-contain px-5 py-4 text-sm">
				{#if diff.error}
					<p style:color="var(--critical-text)">{diff.error}</p>
				{:else if !diff.result}
					<p class="text-muted">Comparing…</p>
				{:else}
					{@const r = diff.result}
					<p class="text-xs text-muted">
						Passwords and keys show as fingerprints: a change is visible, the value isn't.
					</p>
					{#if !r.added.length && !r.removed.length && !r.changed.length}
						<p class="mt-3">Nothing.</p>
					{/if}
					{#if r.added.length}
						<p class="mt-3 text-xs font-medium text-muted">New files</p>
						<ul class="font-mono text-xs">
							{#each r.added as p (p)}<li style:color="var(--good-text)">+ {p}</li>{/each}
						</ul>
					{/if}
					{#if r.removed.length}
						<p class="mt-3 text-xs font-medium text-muted">Gone</p>
						<ul class="font-mono text-xs">
							{#each r.removed as p (p)}<li style:color="var(--critical-text)">− {p}</li>{/each}
						</ul>
					{/if}
					{#each r.changed as c (c.path)}
						<p class="mt-4 font-mono text-xs font-semibold">{c.path}</p>
						{#if c.secret}
							<p class="text-xs text-muted">A secret as a whole (keys, passwords): it changed.</p>
						{:else if c.binary}
							<p class="text-xs text-muted">
								Not text: it changed ({c.size[0]} → {c.size[1]} bytes).
							</p>
						{:else if c.only_secrets}
							<p class="text-xs text-muted">Only masked values or metadata changed.</p>
						{:else}
							<pre
								class="mt-1 overflow-x-auto rounded-lg bg-surface-2 p-3 font-mono text-[11px] leading-relaxed">{#each c.diff ?? [] as line, i (i)}<span
										class="block"
										style:color={line.startsWith('+')
											? 'var(--good-text)'
											: line.startsWith('-')
												? 'var(--critical-text)'
												: line.startsWith('@@')
													? 'var(--faint)'
													: undefined}>{line}</span
									>{/each}</pre>
						{/if}
					{/each}
				{/if}
			</div>
		</div>
	</dialog>
{/if}
