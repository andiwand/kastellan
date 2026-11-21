<script lang="ts">
	import { onMount } from 'svelte';
	import { store } from '#lib/state.svelte.js';
	import { ago, dayClock } from '#lib/format.js';
	import BackupDiffDialog from './BackupDiffDialog.svelte';
	import CodeBlock from './CodeBlock.svelte';
	import Download from '@lucide/svelte/icons/download';
	import GitCompare from '@lucide/svelte/icons/git-compare';
	import ShieldCheck from '@lucide/svelte/icons/shield-check';

	/** Kastellan's own backup (hubbackup.py): the passphrase (made once, shown once), the versions,
	 * the copies away from the hub, and checks that the passphrase and recovery keys still work. */
	type View = {
		ready: boolean;
		legacy: boolean;
		keyring: boolean; // opens with the key rings (an admin's password, a recovery key)
		vault: boolean; // the hub has key rings
		made: string | null;
		status: {
			ok?: string;
			local_ok?: string;
			error?: string | null;
			running?: boolean;
			targets?: Record<string, { ok?: string; error?: string; written?: number; path?: string }>;
		};
		versions: {
			id: string;
			taken: string;
			checked: string;
			files: number;
			size: number;
			by: string | null;
		}[];
		every_hours: number;
		keep: number;
		targets: { path: string; device: string | null }[];
		checks: Record<string, string>;
		rings: string[];
	};
	let view = $state<View | null>(null);
	let error = $state<string | null>(null);
	let phrase = $state<string | null>(null);
	let busy = $state(false);
	let note = $state<string | null>(null);
	let diff = $state<{ older: string; newer: string } | null>(null);
	let checkPhrase = $state('');
	let ring = $state('kastellan');
	let recovery = $state('');
	let replacing = $state(false);
	let checkNote = $state<{ ok: boolean; text: string } | null>(null);

	async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
		const r = await fetch(`/api/hub-backup${path}`, {
			method,
			headers:
				method === 'GET'
					? { 'X-Kastellan-Auto': '1' }
					: { 'X-Kastellan': '1', 'Content-Type': 'application/json' },
			body: body === undefined ? undefined : JSON.stringify(body)
		});
		if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`);
		return r.json();
	}
	async function load() {
		try {
			view = await call<View>('GET', '');
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

	async function makePhrase(replace = false, mode: 'keyring' | 'passphrase' = 'passphrase') {
		busy = true;
		error = null;
		try {
			phrase = (await call<{ passphrase: string | null }>('POST', '/passphrase', { replace, mode }))
				.passphrase;
			await load();
		} catch (e) {
			error = (e as Error).message;
		} finally {
			busy = false;
		}
	}
	async function now() {
		busy = true;
		note = null;
		try {
			const s = await call<{ error?: string | null; new?: boolean }>('POST', '');
			note = s.error ?? (s.new ? 'Backup saved.' : 'No changes; destinations checked.');
			await load();
		} catch (e) {
			note = (e as Error).message;
		} finally {
			busy = false;
		}
	}
	async function check(body: object) {
		checkNote = null;
		try {
			const r = await call<{ right: boolean }>('POST', '/check', body);
			checkNote = r.right
				? { ok: true, text: 'Recovery key verified.' }
				: { ok: false, text: "That isn't it." };
			checkPhrase = recovery = '';
			await load();
		} catch (e) {
			checkNote = { ok: false, text: (e as Error).message };
		}
	}

	const size = (n: number) =>
		n < 1048576 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1048576).toFixed(1)} MB`;
	const signedIn = $derived(!!store.me?.user && !store.health?.demo);
	const old = (iso: string | undefined) => !iso || Date.now() - Date.parse(iso) > 180 * 86400_000;
</script>

<section class="card p-5">
	<div class="flex flex-wrap items-baseline justify-between gap-2">
		<h2 class="text-sm font-semibold">Kastellan itself</h2>
		{#if view?.ready && signedIn}
			<button
				class="btn btn-sm"
				disabled={busy || view.status.running || view.legacy}
				onclick={now}
			>
				{busy ? 'Backing up…' : 'Back up now'}
			</button>
		{/if}
	</div>
	<p class="mt-1 max-w-3xl text-xs text-muted">
		Encrypted backups of configuration, keys and state. Keep the recovery passphrase separately.
	</p>
	{#if error}<p class="mt-2 text-sm" style:color="var(--critical-text)">{error}</p>{/if}

	{#if phrase}
		<div class="mt-4 rounded-lg border border-line bg-surface-2 p-4">
			<p class="text-sm font-medium">The passphrase: write it down now, away from the hub.</p>
			<p class="mt-2 font-mono text-base tracking-wide break-all select-all">{phrase}</p>
			<p class="mt-2 text-xs text-muted">
				Shown once. Restoring on a new hub requires it; this hub keeps a derived key for scheduled
				backups.
			</p>
			<button class="btn btn-sm mt-3" onclick={() => (phrase = null)}>I wrote it down</button>
		</div>
	{/if}

	{#if view && !view.ready}
		{#if view.vault}
			<p class="mt-4 text-sm">
				No backup yet. It can open with the key rings: an admin's password (as it was at that
				backup) or the Kastellan ring's recovery key, nothing else to keep. Or with a passphrase of
				its own, shown once.
			</p>
		{:else}
			<p class="mt-4 text-sm">
				No backup yet: it needs its passphrase first. (With key rings, made on the Keys page, it
				opens with an admin's password or a recovery key instead.)
			</p>
		{/if}
		{#if signedIn}
			<div class="mt-2 flex flex-wrap gap-2">
				{#if view.vault}
					<button
						class="btn btn-primary btn-sm"
						disabled={busy}
						onclick={() => makePhrase(false, 'keyring')}
					>
						Use the key rings
					</button>
					<button class="btn btn-sm" disabled={busy} onclick={() => makePhrase()}>
						A passphrase instead
					</button>
				{:else}
					<button class="btn btn-primary btn-sm" disabled={busy} onclick={() => makePhrase()}>
						Make the passphrase
					</button>
				{/if}
			</div>
		{:else}<p class="mt-1 text-xs text-muted">Needs sign-in to be on.</p>{/if}
	{:else if view}
		{#if view.legacy}
			<p class="mt-3 text-sm" style:color="var(--warning-text)">
				This backup uses the old folder format. Download a .kbak to keep it, then make a new
				passphrase.
			</p>
		{/if}
		<p class="mt-4 text-sm">
			{#if view.status.error}
				<span style:color="var(--warning-text)">{view.status.error}</span>
			{:else if view.status.local_ok || view.status.ok}
				Backed up {ago(view.status.local_ok ?? view.status.ok!, store.now)}
			{:else}
				Not backed up yet (within minutes)
			{/if}
			<span class="text-xs text-muted">
				· every {view.every_hours} h, {view.keep} versions kept
			</span>
		</p>
		{#if note}<p class="mt-1 text-xs text-muted">{note}</p>{/if}

		<h3 class="mt-4 text-xs font-medium text-muted">Copies</h3>
		{#if view.targets.length}
			<ul class="mt-1 text-sm">
				{#each view.targets as t (`${t.device}:${t.path}`)}
					{@const name = t.device ? `${t.device}:${t.path}` : t.path}
					{@const st = view.status.targets?.[name]}
					<li>
						<span class="font-mono text-xs break-all">
							{t.device ? `${t.device}:` : ''}{st?.path ?? t.path}
						</span>
						{#if st?.error}<span class="text-xs" style:color="var(--warning-text)">
								· {st.error}
							</span>{:else if st?.ok}<span class="text-xs text-muted">
								· copied {ago(st.ok, store.now)}
							</span>{/if}
					</li>
				{/each}
			</ul>
		{:else}
			<p class="mt-1 text-sm" style:color="var(--warning-text)">
				No external copy. Add a mounted folder or an SFTP destination in Settings → Kastellan's own
				backup.
			</p>
		{/if}

		{#if view.versions.length}
			<h3 class="mt-4 text-xs font-medium text-muted">Versions</h3>
			<ol class="mt-1 divide-y divide-line text-sm">
				{#each view.versions.slice(0, 8) as v, i (v.id)}
					<li class="flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5">
						<span class="tabular">{dayClock(v.taken)}</span>
						<span class="text-xs text-muted">
							{v.files} files, {size(v.size)}{v.checked !== v.taken
								? ` · the same until ${ago(v.checked, store.now)}`
								: ''}
						</span>
						{#if signedIn}
							<span class="ml-auto flex gap-3 text-xs">
								{#if i < view.versions.length - 1}
									<button
										class="inline-flex items-center gap-1 text-muted hover:text-text"
										onclick={() => (diff = { older: view!.versions[i + 1].id, newer: v.id })}
									>
										<GitCompare class="size-3.5" /> what changed
									</button>
								{/if}
								<a
									class="inline-flex items-center gap-1 text-muted hover:text-text"
									href="/api/hub-backup/{v.id}/download"
									download
								>
									<Download class="size-3.5" /> download
								</a>
							</span>
						{/if}
					</li>
				{/each}
			</ol>
		{/if}

		<h3 class="mt-5 flex items-center gap-1.5 text-xs font-medium text-muted">
			<ShieldCheck class="size-3.5" /> Can you still open it?
		</h3>
		<ul class="mt-1 text-xs">
			{#if view.keyring}
				<li>
					It opens with an admin's password as it was at that backup, or the Kastellan ring's
					recovery key:
				</li>
			{:else}
				<li style:color={old(view.checks.passphrase) ? 'var(--warning-text)' : undefined}>
					The backup's passphrase: {view.checks.passphrase
						? `checked ${ago(view.checks.passphrase, store.now)}`
						: 'never checked'}
				</li>
			{/if}
			{#each view.rings as r (r)}
				<li style:color={old(view.checks[`recovery:${r}`]) ? 'var(--warning-text)' : undefined}>
					The {r} key ring's recovery key: {view.checks[`recovery:${r}`]
						? `checked ${ago(view.checks[`recovery:${r}`], store.now)}`
						: 'never checked'}
				</li>
			{/each}
		</ul>
		{#if signedIn}
			{#if !view.keyring}
				<div class="mt-2 flex flex-wrap items-end gap-2">
					<input
						class="h-8 w-full min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 font-mono text-xs sm:w-auto sm:min-w-64"
						type="password"
						placeholder="The passphrase"
						bind:value={checkPhrase}
					/>
					<button
						class="btn btn-sm"
						disabled={!checkPhrase}
						onclick={() => check({ passphrase: checkPhrase })}
					>
						Check
					</button>
				</div>
			{/if}
			{#if view.rings.length}
				<div class="mt-2 flex flex-wrap items-end gap-2">
					<select
						class="h-8 rounded-lg border border-line bg-surface px-2 text-xs"
						bind:value={ring}
					>
						{#each view.rings as r (r)}<option value={r}>{r}</option>{/each}
					</select>
					<input
						class="h-8 w-full min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 font-mono text-xs sm:w-auto sm:min-w-64"
						type="password"
						placeholder="Its recovery key"
						bind:value={recovery}
					/>
					<button class="btn btn-sm" disabled={!recovery} onclick={() => check({ ring, recovery })}>
						Check
					</button>
				</div>
			{/if}
			{#if checkNote}
				<p
					class="mt-1 text-xs"
					style:color={checkNote.ok ? 'var(--good-text)' : 'var(--critical-text)'}
				>
					{checkNote.text}
				</p>
			{/if}
		{/if}

		<details class="mt-5 text-sm">
			<summary class="cursor-pointer text-xs font-medium text-muted">How to restore</summary>
			<p class="mt-2 text-xs text-muted">
				Use the same Kastellan version and empty data and config folders. Mount a copied kastellan-…
				folder or a .kbak file, then run with the hub stopped:
			</p>
			<CodeBlock
				text={'docker compose stop kastellan\ndocker compose run --rm --no-deps -v /path/to/the/copy:/restore:ro kastellan kastellan restore /restore\n# asks for the passphrase (key-ring backups: an admin login or a recovery key); --list shows the versions, --version <id> takes an older one\ndocker compose up -d'}
			/>
			<p class="mt-2 text-xs text-muted">
				Restore external unlock keys separately. Sign in with the saved account, or unlock the key
				rings with their recovery keys.
			</p>
		</details>
		{#if signedIn}
			{#if replacing}
				<p class="mt-4 text-xs">
					Start a new backup? Old local and destination copies stay under their old passphrase;
					remove them manually when no longer needed.
					<button
						class="ml-1 underline"
						disabled={busy}
						style:color="var(--critical-text)"
						onclick={() => ((replacing = false), makePhrase(true))}
					>
						Make a new one
					</button>
					<button class="ml-1 underline" onclick={() => (replacing = false)}>No</button>
				</p>
			{:else}
				<button
					class="mt-4 text-xs text-muted underline hover:text-text"
					onclick={() => (replacing = true)}
				>
					A new passphrase…
				</button>
			{/if}
		{/if}
	{/if}
</section>

{#if diff}
	{@const d = diff}
	<BackupDiffDialog
		load={async () => {
			const r = await fetch(`/api/hub-backup/diff?older=${d.older}&newer=${d.newer}`);
			if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`);
			return r.json();
		}}
		onclose={() => (diff = null)}
	/>
{/if}
