<script lang="ts">
	import SetupCard from './SetupCard.svelte';
	import { more } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';

	/** Kastellan's login on a FRITZ!Box: a FRITZ!Box user of its own (made in FRITZ!OS), its
	 * password kept in the key ring, and the box's certificate compared and confirmed here. */
	let { device, canSetUp }: { device: string; canSetUp: boolean } = $props();

	const dev = $derived(store.byId.get(device));
	let port = $state(49443);
	let user = $state('kastellan');
	let password = $state('');
	let fingerprint = $state<string | null>(null);
	let pinned = $state<string | null>(null);
	let confirmed = $state(false);
	let error = $state<string | null>(null);
	let done = $state<string | null>(null);
	let busy = $state(false);

	async function readCertificate() {
		error = null;
		confirmed = false;
		try {
			const c = await more.fritzCertificate(device, port);
			fingerprint = c.fingerprint;
			pinned = c.pinned;
		} catch (e) {
			error = (e as Error).message;
		}
	}

	async function save(e: SubmitEvent) {
		e.preventDefault();
		if (!fingerprint) return;
		busy = true;
		error = null;
		try {
			const r = await more.fritzSetup(device, { user, password, fingerprint, port });
			done = `Set up: ${r.model ?? 'FRITZ!Box'}, FRITZ!OS ${r.firmware ?? '?'}.`;
			password = '';
		} catch (err) {
			error = (err as Error).message;
		} finally {
			busy = false;
		}
	}

	async function remove() {
		error = null;
		try {
			await more.fritzRemove(device);
			done = 'Removed. Remove the user in FRITZ!OS too (System → FRITZ!Box Users).';
		} catch (e) {
			error = (e as Error).message;
		}
	}

	const pairs = (fp: string) => fp.match(/.{2}/g)?.join(':').toUpperCase() ?? fp;
	const input =
		'h-9 w-full rounded-lg border border-line bg-surface px-3 text-sm outline-none focus:border-accent';
</script>

<SetupCard
	title="FRITZ!Box login"
	about="Kastellan's own FRITZ!Box user: the internet line, Wi-Fi, who is connected, port forwards, the event log and settings backups."
	stage={dev?.fritzbox ? 'set' : 'available'}
	onopen={readCertificate}
>
	<ol class="mt-2 list-decimal space-y-2 pl-5 text-sm">
		<li>
			In FRITZ!OS, <b>Home Network → Network → Network Settings</b>: tick “Allow access for
			applications” (TR-064).
		</li>
		<li>
			<b>System → FRITZ!Box Users → Add User</b>: a user for Kastellan (e.g. <code>kastellan</code>)
			with a long password, the right <b>“FRITZ!Box Settings”</b> and nothing else (no access from the
			internet). TR-064 has no finer rights: with it Kastellan can read the settings and change Wi-Fi.
		</li>
		<li>
			Compare the certificate below with the one FRITZ!OS shows under <b>
				Internet → Permit Access → FRITZ!Box Services
			</b> (Certificate, SHA-256 fingerprint). Kastellan talks only to this certificate from then on.
		</li>
	</ol>

	<form class="mt-4 flex flex-col gap-3" onsubmit={save}>
		<label class="flex flex-col gap-1 text-xs font-medium text-muted">
			HTTPS port of TR-064
			<span class="flex gap-2">
				<input class="{input} w-32" type="number" min="1" max="65535" bind:value={port} />
				<button type="button" class="btn btn-sm self-center" onclick={readCertificate}>
					Read it again
				</button>
			</span>
		</label>
		{#if fingerprint}
			<div class="rounded-lg bg-surface-2 p-3 text-xs">
				<p class="font-medium">The certificate it shows now (SHA-256):</p>
				<p class="mt-1 font-mono break-all">{pairs(fingerprint)}</p>
				{#if pinned && pinned !== fingerprint}
					<p class="mt-1" style:color="var(--warning-text)">
						Another one than Kastellan confirmed before ({pairs(pinned).slice(0, 23)}…): the
						FRITZ!Box made a new one, or something is in between.
					</p>
				{/if}
				<label class="mt-2 flex items-center gap-2">
					<input type="checkbox" bind:checked={confirmed} /> It is the one FRITZ!OS shows.
				</label>
			</div>
		{/if}
		<label class="flex flex-col gap-1 text-xs font-medium text-muted">
			User
			<input class={input} bind:value={user} autocomplete="off" required />
		</label>
		<label class="flex flex-col gap-1 text-xs font-medium text-muted">
			Password (kept in Kastellan's key ring)
			<input
				class={input}
				type="password"
				bind:value={password}
				autocomplete="new-password"
				required
			/>
		</label>
		{#if error}<p class="text-xs" style:color="var(--critical-text)">{error}</p>{/if}
		{#if done}<p class="text-xs" style:color="var(--good-text)">{done}</p>{/if}
		<div class="flex flex-wrap gap-2">
			<button
				class="btn btn-primary btn-sm"
				disabled={!canSetUp || !confirmed || !password || !user || busy}
				title={canSetUp ? '' : 'Sign in first (sign-in must be on)'}
			>
				{busy ? 'Checking…' : dev?.fritzbox ? 'Save the new login' : 'Set it up'}
			</button>
			{#if dev?.fritzbox && canSetUp}
				<button type="button" class="btn btn-sm" onclick={remove}>Remove Kastellan's login</button>
			{/if}
		</div>
	</form>
</SetupCard>
