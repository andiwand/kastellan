<script lang="ts">
	import KeyRound from '@lucide/svelte/icons/key-round';
	import { modal } from '#lib/modal.js';

	/** A device's web login from the People key ring, next to "Open web interface": the logins
	 * stored for this device; "Copy password" asks for your own password again (as revealing a
	 * secret on Keys does), puts it on the clipboard and never shows it; a new one can be stored
	 * here, for this device only. Through the hub's device pages, "Open signed in" has the hub sign
	 * in itself (LuCI, HTTP Basic): the password never reaches the browser. */
	let { device, proxied = false }: { device: string; proxied?: boolean } = $props();

	type Stored = { id: string; name: string; user: string };
	let open = $state(false);
	let logins = $state<Stored[]>([]);
	let ring = $state<{ vault: boolean; ring_open: boolean } | null>(null);
	let mine = $state('');
	let copying = $state<string | null>(null);
	let signing = $state<string | null>(null); // "open signed in" with this login
	let note = $state<{ ok: boolean; text: string } | null>(null);
	let adding = $state(false);
	let form = $state({ name: '', user: '', secret: '' });
	let busy = $state(false);
	function close() {
		open = adding = false;
		mine = '';
		copying = note = ring = null;
		signing = null;
		logins = [];
		form = { name: '', user: '', secret: '' };
	}

	const H = { 'X-Kastellan': '1', 'Content-Type': 'application/json' };
	async function json<T>(r: Response): Promise<T> {
		if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`);
		return r.json();
	}
	async function load() {
		try {
			const d = await json<{ vault: boolean; ring_open: boolean; stored: Stored[] }>(
				await fetch(`/api/devices/${encodeURIComponent(device)}/stored-logins`)
			);
			logins = d.stored;
			ring = d;
		} catch (e) {
			note = { ok: false, text: (e as Error).message };
		}
	}
	async function copy(id: string) {
		if (busy) return;
		busy = true;
		note = null;
		try {
			const { secret } = await json<{ secret: string }>(
				await fetch(`/api/vault/entries/${encodeURIComponent(id)}/reveal`, {
					method: 'POST',
					headers: H,
					body: JSON.stringify({ password: mine })
				})
			);
			await navigator.clipboard.writeText(secret);
			note = { ok: true, text: 'Copied. Paste it into the login of the device’s page.' };
			copying = null;
		} catch (e) {
			note = { ok: false, text: (e as Error).message };
		} finally {
			mine = '';
			busy = false;
		}
	}
	async function signIn(id: string) {
		if (busy) return;
		busy = true;
		const tab = window.open('about:blank', '_blank');
		if (tab) tab.opener = null;
		note = null;
		try {
			const { url } = await json<{ url: string }>(
				await fetch(`/api/devices/${encodeURIComponent(device)}/web/sign-in`, {
					method: 'POST',
					headers: H,
					body: JSON.stringify({ entry: id, password: mine })
				})
			);
			if (tab && !tab.closed) tab.location.href = url;
			else window.location.href = url;
			note = { ok: true, text: 'Opened, signed in. The password stayed on the hub.' };
			signing = null;
		} catch (e) {
			tab?.close();
			note = { ok: false, text: (e as Error).message };
		} finally {
			busy = false;
			mine = '';
		}
	}
	async function add(e: SubmitEvent) {
		e.preventDefault();
		if (busy) return;
		busy = true;
		note = null;
		try {
			await json(
				await fetch('/api/vault/entries', {
					method: 'POST',
					headers: H,
					body: JSON.stringify({
						ring: 'people',
						kind: 'password',
						name: form.name || `${device} web login`,
						user: form.user,
						secret: form.secret,
						devices: [device],
						password: mine
					})
				})
			);
			form = { name: '', user: '', secret: '' };
			adding = false;
			note = { ok: true, text: 'Stored in the People key ring, for this device only.' };
			await load();
		} catch (err) {
			note = { ok: false, text: (err as Error).message };
		} finally {
			mine = '';
			busy = false;
		}
	}
	const look =
		'inline-flex h-9 items-center gap-2 rounded-xl border border-line bg-surface px-3.5 text-sm font-medium shadow-card transition-colors hover:border-line-strong';
	const input = 'h-8 w-full rounded-lg border border-line bg-surface px-2.5 text-xs';
</script>

<div class="relative">
	<button
		class={look}
		onclick={() => ((open = true), load())}
		title="Its web login from the key ring"
	>
		<KeyRound class="size-4 text-muted" /> Login
	</button>
	{#if open}
		<dialog
			use:modal
			onclose={close}
			aria-label="Stored device logins"
			class="m-auto max-h-[calc(100dvh-2rem)] w-[min(24rem,calc(100vw-2rem))] overflow-y-auto rounded-xl border border-line bg-surface p-4 text-sm text-text shadow-pop backdrop:bg-black/40"
		>
			<div class="mb-3 flex items-center justify-between gap-3">
				<h2 class="font-semibold">Device logins</h2>
				<button class="text-xs underline" onclick={close}>Close</button>
			</div>
			{#if !ring}
				<p class="text-xs text-muted">Loading…</p>
			{:else if !ring.vault}
				<p class="text-xs text-muted">
					Logins are kept in the key rings: make them on the Keys page.
				</p>
			{:else if ring && !ring.ring_open}
				<p class="text-xs text-muted">The People key ring is locked: sign in again to open it.</p>
			{:else}
				{#each logins as l (l.id)}
					<div class="flex flex-wrap items-center justify-between gap-2 py-1">
						<span class="min-w-0 truncate">
							{l.name} <span class="text-xs text-muted">· {l.user}</span>
						</span>
						<button
							class="text-xs underline"
							disabled={busy}
							onclick={() => ((copying = l.id), (signing = null), (adding = false), (mine = ''))}
						>
							Copy password
						</button>
						{#if proxied}
							<button
								class="text-xs underline"
								disabled={busy}
								onclick={() => ((signing = l.id), (copying = null), (adding = false), (mine = ''))}
							>
								Open signed in
							</button>
						{/if}
					</div>
				{:else}
					<p class="text-xs text-muted">No login stored for this device.</p>
				{/each}
				{#if copying || signing}
					<form
						class="mt-2 flex gap-2"
						onsubmit={(e) => {
							e.preventDefault();
							if (signing) signIn(signing);
							else if (copying) copy(copying);
						}}
					>
						<input
							class={input}
							type="password"
							placeholder="Your password, again"
							bind:value={mine}
						/>
						<button class="btn btn-sm" disabled={busy || !mine}>{signing ? 'Open' : 'Copy'}</button>
					</form>
				{/if}
				{#if adding}
					<form class="mt-2 flex flex-col gap-1.5" onsubmit={add}>
						<input class={input} placeholder="Name (e.g. web admin)" bind:value={form.name} />
						<input class={input} placeholder="User" bind:value={form.user} autocomplete="off" />
						<input
							class={input}
							type="password"
							placeholder="Its password"
							bind:value={form.secret}
							autocomplete="new-password"
						/>
						<input
							class={input}
							type="password"
							placeholder="Your password, again"
							bind:value={mine}
						/>
						<button class="btn btn-sm self-start" disabled={busy || !form.secret || !mine}>
							Store it
						</button>
					</form>
				{:else}
					<button
						class="mt-2 text-xs text-muted underline"
						disabled={busy}
						onclick={() => ((adding = true), (copying = signing = null), (mine = ''))}
					>
						Store a web login…
					</button>
				{/if}
			{/if}
			{#if note}
				<p class="mt-2 text-xs" style:color={note.ok ? 'var(--good-text)' : 'var(--critical-text)'}>
					{note.text}
				</p>
			{/if}
		</dialog>
	{/if}
</div>
