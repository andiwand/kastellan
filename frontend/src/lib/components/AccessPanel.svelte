<script lang="ts">
	import { api, type HubKeys } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { ago } from '#lib/format.js';
	import Check from '@lucide/svelte/icons/check';
	import X from '@lucide/svelte/icons/x';
	import Copy from '@lucide/svelte/icons/copy';

	/** Kastellan's own SSH key (Settings, Access tab): what it is, and rotating it. Compact cards
	 * like the Keys page; the details of a rotation open while one is under way. */
	let keys = $state<HubKeys | null>(null);
	let error = $state<string | null>(null);
	let busy = $state<string | null>(null);
	let copied = $state(false);
	let anyway = $state(false);

	async function load() {
		error = null;
		try {
			keys = await api.hubKeys();
		} catch (e) {
			error = (e as Error).message;
		}
	}

	$effect(() => {
		if (store.me?.role === 'admin' && !keys) load();
	});

	async function act(what: string, run: () => Promise<unknown>) {
		busy = what;
		error = null;
		try {
			await run();
		} catch (e) {
			error = (e as Error).message;
		} finally {
			busy = null;
			await load();
		}
	}

	const rot = $derived(keys?.rotation && keys.rotation.stage !== 'done' ? keys.rotation : null);
	const name = (id: string) => store.byId.get(id)?.name ?? id;
	// As the hub decides it (a device whose login changed needs the key again).
	const missing = $derived(rot?.missing ?? []);
	const canChange = $derived(store.me?.role === 'admin' && !!store.me.user);
</script>

{#if error}<p class="mt-4 text-sm" style:color="var(--critical-text)" role="alert">{error}</p>{/if}

{#if keys}
	<div class="mt-6 space-y-3">
		<div class="card flex flex-wrap items-center justify-between gap-3 p-4">
			<div class="min-w-0">
				<h2 class="text-sm font-semibold">Kastellan's SSH key</h2>
				<p class="mt-0.5 truncate font-mono text-xs text-muted" title={keys.fingerprint}>
					{keys.fingerprint} · made {ago(keys.made, store.now)}
				</p>
			</div>
			<button
				class="btn btn-sm"
				onclick={async () => {
					await navigator.clipboard.writeText(keys!.public_key);
					copied = true;
					setTimeout(() => (copied = false), 1500);
				}}
			>
				<Copy class="size-3.5 text-muted" />
				{copied ? 'Copied' : 'Copy public key'}
			</button>
		</div>

		<details class="card p-4" open={!!rot}>
			<summary class="cursor-pointer text-sm font-semibold">
				Rotate it
				<span class="ml-1 text-xs font-normal text-muted">
					{rot
						? rot.stage === 'adding'
							? '· under way: new key being added'
							: '· under way: old key being removed'
						: keys.rotation?.stage === 'done'
							? `· last ${ago(keys.rotation.finished ?? keys.rotation.started, store.now)}`
							: `· on ${keys.devices.length} ${keys.devices.length === 1 ? 'device' : 'devices'}`}
				</span>
			</summary>
			<p class="mt-2 max-w-2xl text-xs leading-relaxed text-muted">
				A new key is added and tried on every device with a login first; only then does Kastellan
				switch and remove the old one. Both work until the end.
			</p>
			{#if !rot}
				<button
					class="btn btn-sm btn-primary mt-3"
					disabled={!canChange || busy !== null || keys.devices.length === 0}
					title={canChange ? '' : 'turn on sign-in first'}
					onclick={() => act('start', api.rotateStart)}
				>
					{busy === 'start' ? 'Adding the new key…' : 'Start: add a new key everywhere'}
				</button>
			{:else}
				<ul class="mt-3 divide-y divide-line rounded-xl border border-line">
					{#each keys.devices as id (id)}
						{@const step = rot.devices[id]}
						{@const ok =
							rot.stage === 'adding'
								? step?.added && step.current && !missing.includes(id)
								: step?.removed}
						<li class="flex items-start gap-2 px-3 py-2 text-sm">
							{#if ok}<Check
									class="mt-0.5 size-4 shrink-0"
									style="color: var(--good-text)"
								/>{:else}<X
									class="mt-0.5 size-4 shrink-0"
									style="color: var(--critical-text)"
								/>{/if}
							<span class="min-w-0">
								<a href="/devices/{id}" class="font-medium hover:underline">{name(id)}</a>
								<span class="text-muted">
									· {rot.stage === 'adding'
										? step?.added && !step.current
											? 'its login changed since: needs the new key again'
											: step?.added
												? 'has the new key, and it works'
												: (step?.error ?? 'not tried yet')
										: step?.removed
											? 'old key removed'
											: (step?.error ?? 'old key still there')}
								</span>
							</span>
						</li>
					{/each}
				</ul>
				<div class="mt-3 flex flex-wrap items-center gap-2">
					{#if rot.stage === 'adding'}
						{#if missing.length}
							<button
								class="btn btn-sm"
								disabled={busy !== null}
								onclick={() => act('start', api.rotateStart)}
							>
								{busy === 'start' ? 'Trying…' : 'Try again'}
							</button>
						{/if}
						<button
							class="btn btn-sm btn-primary"
							disabled={busy !== null || (missing.length > 0 && !anyway)}
							onclick={() => act('finish', () => api.rotateFinish(anyway))}
						>
							{busy === 'finish' ? 'Switching…' : 'Switch, and remove the old key'}
						</button>
						<button
							class="btn btn-sm"
							disabled={busy !== null}
							onclick={() => act('cancel', api.rotateCancel)}
						>
							Cancel
						</button>
						{#if missing.length}
							<label class="choice text-xs text-muted">
								<input type="checkbox" bind:checked={anyway} />
								Switch anyway: Kastellan can't log in to {missing.map(name).join(', ')} then
							</label>
						{/if}
					{:else}
						<button
							class="btn btn-sm btn-primary"
							disabled={busy !== null}
							onclick={() => act('finish', () => api.rotateFinish())}
						>
							{busy === 'finish' ? 'Removing…' : 'Remove the old key where it is left'}
						</button>
					{/if}
				</div>
			{/if}
		</details>
		<p class="text-xs text-faint">
			Set it up per device on the device's Setup tab; which other keys may log in is listed there
			too.
		</p>
	</div>
{:else if store.me && store.me.role !== 'admin'}
	<p class="mt-6 text-sm text-muted">Only admins see Kastellan's keys.</p>
{:else if !error}
	<p class="mt-6 text-sm text-muted">Loading…</p>
{/if}
