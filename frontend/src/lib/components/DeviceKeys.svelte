<script lang="ts">
	import { api, type AuthorizedKey } from '#lib/api.js';
	import KeyRound from '@lucide/svelte/icons/key-round';
	import Trash from '@lucide/svelte/icons/trash-2';

	/** Which keys may log in on a device (its authorized_keys), and removing one. Kastellan's own
	 * keys are marked; they change through rotation on the Access page, not here. */
	let { device, canChange }: { device: string; canChange: boolean } = $props();

	let data = $state<{ everyone: boolean; login: string; keys: AuthorizedKey[] } | null>(null);
	let error = $state<string | null>(null);
	let busy = $state(false);
	let confirming = $state<string | null>(null); // `${user}|${file}|${fingerprint}`

	const id = (k: AuthorizedKey) => `${k.user}|${k.file}|${k.fingerprint}`;

	async function load() {
		error = null;
		try {
			data = await api.deviceKeys(device);
		} catch (e) {
			error = (e as Error).message;
		}
	}

	async function remove(k: AuthorizedKey) {
		busy = true;
		error = null;
		try {
			await api.removeKey(device, { user: k.user, file: k.file, fingerprint: k.fingerprint });
			confirming = null;
			await load();
		} catch (e) {
			error = (e as Error).message;
		} finally {
			busy = false;
		}
	}

	const users = $derived([...new Set((data?.keys ?? []).map((k) => k.user))]);
	const button =
		'inline-flex h-7 items-center gap-1 rounded-md border border-line px-2 text-xs font-medium hover:border-line-strong disabled:opacity-50';
</script>

<details
	class="card p-4"
	ontoggle={(e) => (e.currentTarget as HTMLDetailsElement).open && !data && load()}
>
	<summary class="cursor-pointer text-sm font-semibold">SSH keys on this device</summary>
	{#if error}<p class="mt-2 text-xs" style:color="var(--critical-text)">{error}</p>{/if}
	{#if !data && !error}<p class="mt-2 text-xs text-muted">Reading…</p>{/if}
	{#if data}
		<p class="mt-2 text-xs text-muted">
			{data.everyone
				? 'Every account’s authorized_keys.'
				: `Only ${data.login}’s own keys: Kastellan logs in as ${data.login}, which isn’t root and has no sudo.`}
		</p>
		{#if data.keys.length === 0}
			<p class="mt-3 text-sm text-muted">No keys.</p>
		{/if}
		{#each users as user (user)}
			<h3 class="mt-4 text-xs font-medium text-muted">{user}</h3>
			<ul class="mt-1 divide-y divide-line rounded-xl border border-line">
				{#each data.keys.filter((k) => k.user === user) as k, i (`${id(k)}|${i}`)}
					<li class="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
						<KeyRound class="size-4 shrink-0 text-muted" />
						<div class="min-w-0 flex-1">
							<p class="truncate text-sm">
								{k.comment || 'no comment'}
								{#if k.kastellan}
									<span
										class="ml-1 rounded-md bg-accent-soft px-1.5 py-0.5 text-[11px] font-medium text-accent"
									>
										{k.kastellan}
									</span>
								{/if}
							</p>
							<p class="truncate font-mono text-[11px] text-faint" title={k.file}>
								{k.type} · {k.fingerprint}{k.options ? ` · ${k.options}` : ''}
							</p>
						</div>
						{#if canChange && !k.kastellan}
							{#if confirming === id(k)}
								<span class="text-xs">Remove it? It can't log in then.</span>
								<button
									class="{button} border-transparent text-white"
									style="background: var(--critical-text)"
									disabled={busy}
									onclick={() => remove(k)}
								>
									Remove
								</button>
								<button class={button} onclick={() => (confirming = null)}>Keep</button>
							{:else}
								<button
									class="{button} text-muted"
									onclick={() => (confirming = id(k))}
									title="Remove this key from {k.file}"
								>
									<Trash class="size-3.5" /> Remove
								</button>
							{/if}
						{/if}
					</li>
				{/each}
			</ul>
		{/each}
		<div class="mt-3 flex justify-end">
			<button class={button} onclick={load} disabled={busy}>Read again</button>
		</div>
	{/if}
</details>
