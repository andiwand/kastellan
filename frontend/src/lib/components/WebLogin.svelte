<script lang="ts">
	import KeyRound from '@lucide/svelte/icons/key-round';

	/** A device's web login from the People key ring, next to "Open web interface": the logins
	 * stored for this device; "Copy password" asks for your own password again (as revealing a
	 * secret on Keys does), puts it on the clipboard and never shows it; a new one can be stored
	 * here, for this device only. Filling it into the device's page comes later (plan). */
	let { device }: { device: string } = $props();

	type Stored = { id: string; name: string; user: string };
	let open = $state(false);
	let logins = $state<Stored[]>([]);
	let ring = $state<{ vault: boolean; ring_open: boolean } | null>(null);
	let mine = $state('');
	let copying = $state<string | null>(null);
	let note = $state<{ ok: boolean; text: string } | null>(null);
	let adding = $state(false);
	let form = $state({ name: '', user: '', secret: '' });

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
		}
	}
	async function add(e: SubmitEvent) {
		e.preventDefault();
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
		}
	}
	const look =
		'inline-flex h-9 items-center gap-2 rounded-xl border border-line bg-surface px-3.5 text-sm font-medium shadow-card transition-colors hover:border-line-strong';
	const input = 'h-8 w-full rounded-lg border border-line bg-surface px-2.5 text-xs';
</script>

<div class="relative">
	<button
		class={look}
		onclick={() => ((open = !open), open && load())}
		title="Its web login from the key ring"
	>
		<KeyRound class="size-4 text-muted" /> Login
	</button>
	{#if open}
		<div
			class="absolute right-0 z-20 mt-2 w-80 rounded-xl border border-line bg-surface p-3 text-sm shadow-pop"
		>
			{#if ring && !ring.vault}
				<p class="text-xs text-muted">
					Logins are kept in the key rings: make them on the Keys page.
				</p>
			{:else if ring && !ring.ring_open}
				<p class="text-xs text-muted">The People key ring is locked: sign in again to open it.</p>
			{:else}
				{#each logins as l (l.id)}
					<div class="flex items-center justify-between gap-2 py-1">
						<span class="min-w-0 truncate">
							{l.name} <span class="text-xs text-muted">· {l.user}</span>
						</span>
						<button class="text-xs underline" onclick={() => ((copying = l.id), (adding = false))}>
							Copy password
						</button>
					</div>
				{:else}
					<p class="text-xs text-muted">No login stored for this device.</p>
				{/each}
				{#if copying}
					<form class="mt-2 flex gap-2" onsubmit={(e) => (e.preventDefault(), copy(copying!))}>
						<input
							class={input}
							type="password"
							placeholder="Your password, again"
							bind:value={mine}
						/>
						<button class="btn btn-sm" disabled={!mine}>Copy</button>
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
						<button class="btn btn-sm self-start" disabled={!form.secret || !mine}>Store it</button>
					</form>
				{:else}
					<button
						class="mt-2 text-xs text-muted underline"
						onclick={() => ((adding = true), (copying = null))}
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
		</div>
	{/if}
</div>
