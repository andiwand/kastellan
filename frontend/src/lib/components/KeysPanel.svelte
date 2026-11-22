<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import {
		api,
		more,
		type Ring,
		type RingChoice,
		type UnlockItem,
		type Vault,
		type VaultEntry,
		type VaultForm,
		type VaultLevel
	} from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { ago } from '#lib/format.js';
	import Lock from '@lucide/svelte/icons/lock';
	import LockOpen from '@lucide/svelte/icons/lock-open';
	import Plus from '@lucide/svelte/icons/plus';
	import PageHeader from '#lib/components/ui/PageHeader.svelte';
	import EmptyState from '#lib/components/ui/EmptyState.svelte';
	import { WORD } from '#lib/glossary.js';

	/** Key rings (vault.py): Kastellan's secrets and people's logins, encrypted, each ring opened
	 * its own way (an admin signing in, a key file, an environment variable, a key in
	 * config.yaml) and closed again as its policy says. Nothing in them is shown unless asked
	 * for, with the password typed again. */
	let vault = $state<Vault | null>(null);
	// Secrets config.yaml names that aren't set (a notification token, a device login).
	let missing = $state<{ name: string; used_by: string[] }[]>([]);
	onMount(async () => {
		try {
			missing = (await more.secrets()).filter((x) => x.missing && x.used_by.length);
		} catch {
			// shown nowhere: the rest of the page says what is wrong
		}
	});
	let error = $state<string | null>(null);
	let notes = $state<string[]>([]);
	let busy = $state(false);

	let password = $state(''); // typed again for each change; never kept
	let recovery = $state<Record<string, string> | null>(null); // shown once, until saved
	let saved = $state(false);
	let unlocking = $state<{ ring: string; recovery: boolean } | null>(null);
	let recoveryInput = $state('');
	let adding = $state(false);
	let editing = $state<string | null>(null);
	let form = $state<VaultForm>(blank());
	let revealing = $state<string | null>(null);
	let shown = $state<Record<string, string>>({});
	let deleting = $state<string | null>(null);
	let newRing = $state<{ id: string; name: string } | null>(null);
	let choosing = $state(false); // the level and ways form
	let level = $state<VaultLevel>('balanced');
	let choices = $state<Record<string, RingForm>>({});

	/** One ring's choices as the form edits them. */
	interface RingForm {
		own: boolean; // its own ways (else: the level's)
		password: boolean;
		config: boolean;
		keyfile: string;
		makeKeyfile: boolean;
		env: string;
		stays: string; // '' : the level's
		background: boolean | null;
	}

	function blank(ring = 'people'): VaultForm {
		return { ring, kind: 'password', name: '', user: '', notes: '', secret: '', devices: [] };
	}
	let devicesBefore = $state<string[]>([]); // a login's devices as it was opened
	const devicesChanged = $derived(
		[...(form.devices ?? [])].sort().join() !== [...devicesBefore].sort().join()
	);

	const admin = $derived(store.me?.role === 'admin');
	const canChange = $derived(admin && !!store.me?.user && !store.health?.demo);
	const anyOpen = $derived(!!vault?.rings.some((r) => r.open));

	async function load() {
		error = null;
		try {
			vault = await api.vault();
		} catch (e) {
			error = (e as Error).message;
		}
		forgetShown();
	}
	// A shown secret goes again: when its ring is no longer open (locked, closed by its policy),
	// and a minute after it was shown.
	function forgetShown() {
		const open = new Set((vault?.entries ?? []).filter((e) => isOpen(e.ring)).map((e) => e.id));
		for (const id of Object.keys(shown)) if (!open.has(id)) delete shown[id];
	}
	const isOpen = (ring: string) => !!vault?.rings.find((r) => r.id === ring)?.open;
	$effect(() => {
		void store.health?.vault; // the hub says a ring closed: look again
		if (admin) untrack(load);
	});

	async function run(what: () => Promise<unknown>) {
		busy = true;
		error = null;
		try {
			await what();
			await store.refresh();
		} catch (e) {
			error = (e as Error).message;
		} finally {
			busy = false;
			password = '';
			await load();
		}
	}
	const submit = (what: () => Promise<unknown>) => (e: SubmitEvent) => {
		e.preventDefault();
		return run(what);
	};

	const create = submit(async () => {
		const made = await api.vaultCreate(password);
		recovery = made.recovery;
		notes = made.notes;
		saved = false;
	});
	const unlock = submit(async () => {
		if (!unlocking) return;
		await api.vaultUnlock(
			unlocking.recovery ? { recovery: recoveryInput, ring: unlocking.ring } : { password }
		);
		recoveryInput = '';
		unlocking = null;
	});
	const addRing = submit(async () => {
		if (!newRing) return;
		const key = (await api.vaultAddRing(newRing.id, newRing.name, password)).recovery;
		recovery = { [newRing.id]: key };
		saved = false;
		newRing = null;
	});
	const save = submit(async () => {
		const body = {
			...form,
			secret: form.secret || null,
			devices: devicesChanged ? form.devices : undefined,
			password: devicesChanged ? password : undefined
		};
		if (editing) await api.vaultChange(editing, body);
		else await api.vaultAdd(body);
		adding = false;
		editing = null;
		form = blank();
	});
	const reveal = (id: string) =>
		submit(async () => {
			shown[id] = (await api.vaultReveal(id, password)).secret;
			revealing = null;
			setTimeout(() => delete shown[id], 60_000);
		});
	const newRecovery = (ring: string) =>
		submit(async () => {
			recovery = { [ring]: (await api.vaultRecovery(ring, password)).recovery };
			saved = false;
		});

	function edit(entry: VaultEntry) {
		adding = false;
		editing = entry.id;
		const { ring, kind, name, user, notes, devices } = entry;
		form = { ring, kind, name, user, notes, secret: '', devices: [...devices] };
		devicesBefore = [...devices];
	}

	// --- The level and each ring's ways ---------------------------------------------------------

	function formOf(r: Ring): RingForm {
		const way = (w: string) => r.ways.find((x) => x.way === w);
		return {
			own: false,
			password: !!way('password'),
			config: !!way('config'),
			keyfile: way('keyfile')?.source ?? '',
			makeKeyfile: false,
			env: way('env')?.source ?? '',
			stays: '',
			background: null
		};
	}
	function openChoices() {
		if (!vault) return;
		level = vault.level;
		choices = Object.fromEntries(vault.rings.map((r) => [r.id, formOf(r)]));
		choosing = true;
	}
	function choiceOf(f: RingForm): RingChoice {
		const unlock: UnlockItem[] = [];
		if (f.password) unlock.push('password');
		if (f.config) unlock.push('config');
		if (f.keyfile.trim()) unlock.push({ keyfile: f.keyfile.trim() });
		if (f.env.trim()) unlock.push({ env: f.env.trim() });
		return {
			unlock: f.own ? unlock : null,
			stays_unlocked: f.stays || null,
			background: f.background,
			make_keyfile: f.own && f.makeKeyfile
		};
	}
	const saveChoices = submit(async () => {
		const rings = Object.fromEntries(Object.entries(choices).map(([id, f]) => [id, choiceOf(f)]));
		notes = (await api.vaultSettings(password, level, rings)).notes;
		choosing = false;
	});

	const LEVELS: { id: VaultLevel; title: string; text: string }[] = [
		{
			id: 'convenient',
			title: 'Convenient',
			text: "Kastellan's ring opens by itself at start, with a key kept in config.yaml: everything works again after a power cut. Whoever has a copy of the config folder can open it. People's logins open when an admin signs in."
		},
		{
			id: 'balanced',
			title: 'Balanced',
			text: "Nothing opens by itself. Once an admin signs in, Kastellan's ring stays open until a restart; people's logins close again when the last admin signs out."
		},
		{
			id: 'strict',
			title: 'Strict',
			text: "As balanced, and people's logins close again after 15 minutes without use."
		}
	];

	function stays(s: string): string {
		if (s === 'signed_in') return 'open while an admin is signed in';
		if (s.startsWith('idle:')) return `closes after ${s.slice(5)} minutes without use`;
		return 'open until Lock or a restart';
	}
	function way(w: Ring['ways'][number]): string {
		if (w.way === 'password')
			return w.users.length ? `an admin's password (${w.users.join(', ')})` : "an admin's password";
		if (w.way === 'keyfile') return `the key file ${w.source}`;
		if (w.way === 'env') return `$${w.source}`;
		return 'the key in config.yaml';
	}
	const ringName = (id: string) => vault?.rings.find((r) => r.id === id)?.name ?? id;
</script>

<svelte:head><title>Keys · Kastellan</title></svelte:head>

<PageHeader
	title="Keys"
	summary="Key rings: passwords, tokens and keys, encrypted. Each ring opens its own way, as secure as you choose."
>
	{#snippet actions()}
		{#if anyOpen && canChange}
			<button class="btn btn-sm" disabled={busy} onclick={() => run(() => api.vaultLock())}>
				<Lock class="size-3.5" /> Lock all
			</button>
		{/if}
	{/snippet}
</PageHeader>

{#if !admin}
	<EmptyState kind="unavailable" text={WORD.adminsOnly} />
{:else if error && !vault}
	<!-- Without sign-in (or in demo mode) there are no key rings: a reason, not an error. -->
	<EmptyState
		kind={store.me?.user ? 'error' : 'unavailable'}
		text={store.me?.user ? error : `Key rings need sign-in: ${error}.`}
		onretry={store.me?.user ? load : undefined}
	/>
{:else if vault}
	{#if error}<p class="mt-4 text-sm" style:color="var(--critical-text)" role="alert">
			{error}
		</p>{/if}
	{#each notes as note (note)}<p class="mt-2 text-sm" style:color="var(--warning-text)">
			{note}
		</p>{/each}

	{#if recovery}
		<section class="card mt-6 max-w-2xl p-5">
			<h2 class="text-sm font-semibold">
				{Object.keys(recovery).length > 1 ? 'The recovery keys' : 'The recovery key'}
			</h2>
			<p class="mt-1 text-sm text-muted">
				Each opens its ring when no admin password does (all forgotten, or changed while it was
				locked). Write them down or keep them in your own password manager, away from this machine.
				They are shown only now.
			</p>
			{#each Object.entries(recovery) as [ring, key] (ring)}
				<p class="mt-3 text-xs font-medium text-muted">{ringName(ring)}</p>
				<p class="mt-1 rounded-lg bg-surface-2 p-3 font-mono text-sm break-all select-all">{key}</p>
			{/each}
			<label class="choice mt-3 text-sm">
				<input type="checkbox" bind:checked={saved} />
				<span>I have saved {Object.keys(recovery).length > 1 ? 'them' : 'it'}</span>
			</label>
			<button class="btn btn-primary mt-3" disabled={!saved} onclick={() => (recovery = null)}>
				Done
			</button>
		</section>
	{:else if vault.state === 'damaged'}
		<section class="card mt-6 max-w-2xl p-5">
			<h2 class="text-sm font-semibold" style:color="var(--critical-text)">
				The key rings can't be read
			</h2>
			<p class="mt-1 text-sm text-muted">
				{vault.damaged}. Nothing in them can be used until the file is right again: put back
				<code class="font-mono">vault.json</code> from a backup of the data folder. Sign-in and the checks
				go on meanwhile.
			</p>
		</section>
	{:else if vault.state === 'absent'}
		<section class="card mt-6 max-w-2xl p-5">
			<h2 class="text-sm font-semibold">No key rings yet</h2>
			<p class="mt-1 text-sm text-muted">
				Encrypt Kastellan's secrets and your logins, opened with your password.
			</p>
			<details class="mt-2 text-xs text-muted">
				<summary class="cursor-pointer">How it works</summary>
				<p class="mt-1 max-w-2xl leading-relaxed">
					Two rings: Kastellan's (what it uses by itself) and people's logins (never used by
					Kastellan by itself). Kastellan's takes in what is kept as plain files so far{vault.files
						.length
						? ` (${vault.files.join(', ')})`
						: ''}, its SSH keys that can change things and push's key; then those files are removed.
					Each admin opens them with their own password; you get a recovery key for each, once. How
					they open after a restart is yours to choose afterwards.
				</p>
			</details>
			{#if canChange}
				<form class="mt-4 flex flex-wrap items-end gap-2" onsubmit={create}>
					<label class="field w-64">
						Your password, again
						<input
							class="input"
							type="password"
							bind:value={password}
							autocomplete="current-password"
							required
						/>
					</label>
					<button class="btn btn-primary" disabled={busy || !password}>Make the key rings</button>
				</form>
			{:else}
				<p class="mt-3 text-sm text-muted">
					{store.health?.demo ? 'Not in demo mode.' : 'Needs sign-in on (a user in config.yaml).'}
				</p>
			{/if}
		</section>
	{:else}
		<!-- The rings -->
		<div class="mt-6 grid items-start gap-4 lg:grid-cols-2">
			{#each vault.rings as r (r.id)}
				<section class="card p-4">
					<div class="flex flex-wrap items-center gap-2">
						{#if r.open}
							<LockOpen class="size-4" style="color: var(--good-text)" />
						{:else}
							<Lock class="size-4" style="color: var(--warning-text)" />
						{/if}
						<h2 class="text-sm font-semibold">{r.name}</h2>
						<span class="text-xs" style:color={r.open ? 'var(--good-text)' : 'var(--warning-text)'}>
							{r.open ? 'open' : 'locked'}
						</span>
						<span class="ml-auto text-xs text-faint">
							{r.entries}
							{r.entries === 1 ? 'entry' : 'entries'}
						</span>
					</div>
					<ul class="mt-2 space-y-0.5 text-xs text-muted">
						<li>
							Opens with {r.ways.map(way).join(', or ') || 'only its recovery key'}.
						</li>
						{#each r.ways.filter((w) => !w.ready && w.way !== 'password') as w (w.way + w.source)}
							<li style:color="var(--warning-text)">
								Not set up yet: {way(w)} (it is, once the ring is open and the settings saved).
							</li>
						{/each}
						{#if r.ways.some((w) => w.way === 'config')}
							<li style:color="var(--warning-text)">
								Whoever has a copy of config.yaml can open this ring.
							</li>
						{/if}
						<li>{stays(r.stays_unlocked)}.</li>
						<li>
							{r.background
								? 'Kastellan uses its entries by itself.'
								: 'Kastellan never uses its entries by itself: only what an admin starts.'}
						</li>
						{#if r.problem}<li style:color="var(--warning-text)">
								Didn't open by itself: {r.problem}
							</li>{/if}
					</ul>
					{#if canChange}
						{#if unlocking?.ring === r.id}
							<form class="mt-3 flex flex-wrap items-end gap-2" onsubmit={unlock}>
								{#if unlocking.recovery}
									<label class="field w-72">
										Its recovery key
										<input
											class="input font-mono"
											bind:value={recoveryInput}
											autocomplete="off"
											required
										/>
									</label>
								{:else}
									<label class="field w-56">
										Your password
										<input
											class="input"
											type="password"
											bind:value={password}
											autocomplete="current-password"
											required
										/>
									</label>
								{/if}
								<button class="btn btn-sm btn-primary" disabled={busy}>Unlock</button>
								<button
									type="button"
									class="btn btn-sm"
									onclick={() => unlocking && (unlocking.recovery = !unlocking.recovery)}
								>
									{unlocking.recovery ? 'Use my password' : 'Use its recovery key'}
								</button>
								<button type="button" class="btn btn-sm" onclick={() => (unlocking = null)}>
									Cancel
								</button>
							</form>
						{:else}
							<p class="mt-3 flex flex-wrap gap-2">
								{#if r.open}
									<button
										class="btn btn-sm"
										disabled={busy}
										onclick={() => run(() => api.vaultLock(r.id))}
									>
										<Lock class="size-3.5" /> Lock
									</button>
								{:else}
									<button
										class="btn btn-sm"
										onclick={() => ((unlocking = { ring: r.id, recovery: false }), (password = ''))}
									>
										<LockOpen class="size-3.5" /> Unlock
									</button>
								{/if}
								{#if !r.builtin && r.entries === 0}
									<button
										class="btn btn-sm"
										disabled={busy}
										onclick={() => run(() => api.vaultRemoveRing(r.id))}
									>
										Remove
									</button>
								{/if}
							</p>
						{/if}
					{/if}
				</section>
			{/each}
		</div>

		{#if canChange}
			<div class="mt-4 flex flex-wrap gap-2">
				{#if !choosing}
					<button class="btn" onclick={openChoices}>How secure: level and ways to open</button>
				{/if}
				{#if !newRing}
					<button class="btn" onclick={() => (newRing = { id: '', name: '' })}>
						<Plus class="size-4" /> Add a ring
					</button>
				{/if}
			</div>

			{#if newRing}
				<form class="card mt-3 grid max-w-2xl gap-3 p-4 sm:grid-cols-3" onsubmit={addRing}>
					<label class="field">
						Id
						<input
							class="input font-mono"
							bind:value={newRing.id}
							pattern="[a-z0-9][a-z0-9_\-]*"
							required
						/>
					</label>
					<label class="field">
						Name
						<input class="input" bind:value={newRing.name} placeholder="Hetzner logins" />
					</label>
					<label class="field">
						Your password, again
						<input class="input" type="password" bind:value={password} required />
					</label>
					<p class="text-xs text-muted sm:col-span-3">
						Opened by admins' passwords, like people's logins; its own recovery key is shown once.
					</p>
					<div class="flex gap-2 sm:col-span-3">
						<button class="btn btn-primary" disabled={busy}>Add the ring</button>
						<button type="button" class="btn" onclick={() => (newRing = null)}>Cancel</button>
					</div>
				</form>
			{/if}

			{#if choosing}
				<form class="card mt-3 max-w-3xl p-4" onsubmit={saveChoices}>
					<fieldset>
						<legend class="text-sm font-semibold">Level</legend>
						<div class="mt-2 grid gap-2 sm:grid-cols-3">
							{#each LEVELS as l (l.id)}
								<label
									class="choice rounded-lg border p-3 text-sm {level === l.id
										? 'border-accent'
										: 'border-line'}"
								>
									<input type="radio" bind:group={level} value={l.id} />
									<span>
										<b>{l.title}</b>
										<span class="block text-xs text-muted">{l.text}</span>
									</span>
								</label>
							{/each}
						</div>
					</fieldset>
					{#each vault.rings as r (r.id)}
						{@const f = choices[r.id]}
						{#if f}
							<fieldset class="mt-5 border-t border-line pt-4">
								<legend class="text-sm font-semibold">{r.name}</legend>
								<label class="choice mt-2 text-sm">
									<input type="checkbox" bind:checked={f.own} />
									<span>Its own ways to open (else: as the level says)</span>
								</label>
								{#if f.own}
									<div class="mt-2 grid gap-2 pl-6 text-sm sm:grid-cols-2">
										<label class="choice">
											<input type="checkbox" bind:checked={f.password} /> An admin's password
										</label>
										<label class="choice">
											<input type="checkbox" bind:checked={f.config} /> A key in config.yaml
										</label>
										<label class="field">
											Key file (an absolute path)
											<input
												class="input font-mono"
												bind:value={f.keyfile}
												placeholder="/run/secrets/kastellan"
											/>
										</label>
										<label class="field">
											Environment variable
											<input
												class="input font-mono"
												bind:value={f.env}
												placeholder="KASTELLAN_RING_KEY"
											/>
										</label>
										{#if f.keyfile.trim()}
											<label class="choice sm:col-span-2">
												<input type="checkbox" bind:checked={f.makeKeyfile} />
												<span>Write a new key to that file (only if there is none)</span>
											</label>
										{/if}
										{#if f.config}
											<p class="text-xs sm:col-span-2" style:color="var(--warning-text)">
												The ring's key goes into config.yaml: whoever has a copy of the config
												folder (a backup, a synced folder) can open this ring.
											</p>
										{/if}
										{#if r.id === 'people' && (f.config || f.keyfile.trim() || f.env.trim())}
											<p class="text-xs sm:col-span-2" style:color="var(--warning-text)">
												People's logins would then be open from the start, readable by whoever has
												that key, with nobody signed in. Kastellan still never uses them by itself.
											</p>
										{/if}
									</div>
								{/if}
								<div class="mt-3 flex flex-wrap gap-4 text-sm">
									<label class="field w-64">
										Stays open
										<select class="input" bind:value={f.stays}>
											<option value="">as the level says</option>
											<option value="lock">until Lock or a restart</option>
											<option value="signed_in">while an admin is signed in</option>
											<option value="idle:15">15 minutes without use</option>
											<option value="idle:60">an hour without use</option>
										</select>
									</label>
									{#if r.id !== 'people'}
										<label class="field w-64">
											Kastellan uses it by itself
											<select
												class="input"
												value={f.background === null ? '' : String(f.background)}
												onchange={(e) => {
													const v = e.currentTarget.value;
													f.background = v === '' ? null : v === 'true';
												}}
											>
												<option value="">as the level says</option>
												<option value="true">yes</option>
												<option value="false">no, only what an admin starts</option>
											</select>
										</label>
									{/if}
								</div>
							</fieldset>
						{/if}
					{/each}
					<div class="mt-5 flex flex-wrap items-end gap-2 border-t border-line pt-4">
						<label class="field w-64">
							Your password, again
							<input class="input" type="password" bind:value={password} required />
						</label>
						<button class="btn btn-primary" disabled={busy}>Save</button>
						<button type="button" class="btn" onclick={() => (choosing = false)}>Cancel</button>
					</div>
					<p class="mt-2 text-xs text-muted">
						Saved in config.yaml (`vault:`); a ring that is locked gets its new ways once it is
						open.
					</p>
				</form>
			{/if}
		{/if}

		{#if missing.length}
			<section class="card mt-6 p-4 text-sm">
				<h2 class="text-sm font-semibold" style:color="var(--warning-text)">Named but not set</h2>
				<ul class="mt-1 text-xs">
					{#each missing as m (m.name)}
						<li>
							<span class="font-mono">{m.name}</span>
							<span class="text-muted">· {m.used_by.join(', ')}</span>
						</li>
					{/each}
				</ul>
				<p class="mt-1 text-xs text-muted">
					config.yaml names these secrets; add each to Kastellan's ring with that id.
				</p>
			</section>
		{/if}

		<!-- The entries, by ring -->
		{#each vault.rings as r (r.id)}
			{@const entries = vault.entries.filter((e) => e.ring === r.id)}
			<h2 class="mt-8 text-sm font-semibold">{r.name}</h2>
			<ul class="card mt-3 divide-y divide-line">
				{#each entries as e (e.id)}
					<li class="px-4 py-3 text-sm">
						<div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
							<span class="font-medium">{e.name || e.id}</span>
							<span class="font-mono text-xs text-faint">{e.id}</span>
							<span class="text-xs text-muted">{e.kind}{e.user ? ` · ${e.user}` : ''}</span>
							<span class="ml-auto text-xs text-faint">
								changed {ago(new Date(e.changed * 1000).toISOString(), store.now)}
							</span>
						</div>
						{#if e.used_by.length}
							<p class="mt-1 text-xs text-muted">Used by {e.used_by.join(', ')}</p>
						{/if}
						{#if e.notes}<p class="mt-1 text-xs whitespace-pre-wrap text-muted">{e.notes}</p>{/if}
						{#if e.devices?.length}
							<p class="mt-1 text-xs text-muted">
								For {e.devices.map((d) => store.byId.get(d)?.name ?? d).join(', ')}
							</p>
						{/if}
						{#if e.public}
							<p class="mt-1 truncate font-mono text-[11px] text-faint" title={e.public}>
								{e.public}
							</p>
						{/if}
						{#if shown[e.id] !== undefined}
							<p class="mt-2 flex items-center gap-2">
								<span class="rounded bg-surface-2 px-2 py-1 font-mono text-xs break-all select-all">
									{shown[e.id]}
								</span>
								<button
									class="text-xs text-muted hover:text-text"
									onclick={() => delete shown[e.id]}
								>
									Hide
								</button>
							</p>
						{/if}
						{#if canChange && !e.own && r.open}
							{#if revealing === e.id}
								<form class="mt-2 flex flex-wrap items-end gap-2" onsubmit={reveal(e.id)}>
									<label class="field w-56">
										Your password, to show it
										<input class="input" type="password" bind:value={password} required />
									</label>
									<button class="btn btn-sm" disabled={busy}>Show</button>
									<button type="button" class="btn btn-sm" onclick={() => (revealing = null)}>
										Cancel
									</button>
								</form>
							{:else if deleting === e.id}
								<p class="mt-2 flex flex-wrap items-center gap-2 text-xs">
									Remove it from the key ring? It can't be undone.
									<button
										class="btn btn-sm"
										style="color: var(--critical-text)"
										disabled={busy}
										onclick={() =>
											run(async () => ((deleting = null), await api.vaultDelete(e.id)))}
									>
										Remove
									</button>
									<button class="btn btn-sm" onclick={() => (deleting = null)}>Keep</button>
								</p>
							{:else}
								<p class="mt-2 flex flex-wrap gap-2">
									{#if e.kind !== 'ssh-key' && shown[e.id] === undefined}
										<button
											class="btn btn-sm"
											onclick={() => ((revealing = e.id), (password = ''))}
										>
											Show
										</button>
									{/if}
									<button class="btn btn-sm" onclick={() => edit(e)}>Edit</button>
									<button
										class="btn btn-sm"
										disabled={e.used_by.length > 0}
										title={e.used_by.length ? 'In use' : ''}
										onclick={() => (deleting = e.id)}
									>
										Remove
									</button>
								</p>
							{/if}
						{/if}
						{#if editing === e.id}
							{@render entryForm()}
						{/if}
					</li>
				{:else}
					<li class="px-4 py-3 text-sm text-muted">Nothing here yet.</li>
				{/each}
			</ul>
			{#if canChange && r.open}
				<details class="mt-2 max-w-2xl text-xs text-muted">
					<summary class="cursor-pointer">Its recovery key</summary>
					<form class="mt-2 flex flex-wrap items-end gap-2" onsubmit={newRecovery(r.id)}>
						<label class="field w-56">
							Your password, again
							<input class="input" type="password" bind:value={password} required />
						</label>
						<button class="btn btn-sm" disabled={busy}>Make a new one</button>
						<span>(the old one stops working)</span>
					</form>
				</details>
			{/if}
		{/each}

		{#if canChange && anyOpen}
			<div class="mt-6">
				{#if !adding}
					<button
						class="btn"
						onclick={() => (
							(adding = true),
							(editing = null),
							(form = blank()),
							(devicesBefore = [])
						)}
					>
						<Plus class="size-4" /> Add a login
					</button>
				{:else}
					<div class="card mt-3 max-w-2xl p-4">{@render entryForm()}</div>
				{/if}
			</div>
		{/if}
	{/if}
{/if}

{#snippet entryForm()}
	<form class="mt-3 grid gap-3 sm:grid-cols-2" onsubmit={save}>
		<label class="field">
			Name
			<input class="input" bind:value={form.name} placeholder="NAS root" required />
		</label>
		<label class="field">
			Login name
			<input class="input" bind:value={form.user} placeholder="root" autocomplete="off" />
		</label>
		<label class="field">
			Kind
			<select class="input" bind:value={form.kind}>
				<option value="password">password</option>
				<option value="token">token</option>
				<option value="other">other</option>
			</select>
		</label>
		<label class="field">
			Ring
			<select class="input" bind:value={form.ring}>
				{#each vault?.rings.filter((r) => r.open) ?? [] as r (r.id)}
					<option value={r.id}>{r.name}</option>
				{/each}
			</select>
		</label>
		<label class="field sm:col-span-2">
			{editing ? 'New secret (empty: keep it)' : 'Secret'}
			<input
				class="input font-mono"
				type="password"
				bind:value={form.secret}
				autocomplete="new-password"
				required={!editing}
			/>
		</label>
		<label class="field sm:col-span-2">
			Notes
			<textarea class="input h-16 py-2" bind:value={form.notes}></textarea>
		</label>
		{#if form.ring === 'people' && form.kind === 'password'}
			<fieldset class="field sm:col-span-2">
				<legend class="mb-1">A terminal may use it on</legend>
				<span class="text-xs font-normal text-muted">
					Only these devices are sent this login: a stored password never goes to another host.
				</span>
				<div class="mt-1 flex flex-wrap gap-x-4 gap-y-1">
					{#each store.devices ?? [] as d (d.id)}
						<label class="choice text-xs font-normal">
							<input type="checkbox" value={d.id} bind:group={form.devices} />
							<span>{d.name}</span>
						</label>
					{/each}
				</div>
			</fieldset>
			{#if devicesChanged}
				<label class="field sm:col-span-2">
					Your password, again (it decides where a password may go)
					<input
						class="input"
						type="password"
						bind:value={password}
						autocomplete="current-password"
						required
					/>
				</label>
			{/if}
		{/if}
		<div class="flex gap-2 sm:col-span-2">
			<button class="btn btn-primary" disabled={busy}>{editing ? 'Save' : 'Add'}</button>
			<button
				type="button"
				class="btn"
				onclick={() => ((adding = false), (editing = null), (form = blank()), (devicesBefore = []))}
			>
				Cancel
			</button>
		</div>
	</form>
{/snippet}
