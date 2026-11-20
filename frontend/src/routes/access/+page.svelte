<script lang="ts">
	import { api, type HubKeys } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { ago } from '#lib/format.js';
	import Check from '@lucide/svelte/icons/check';
	import X from '@lucide/svelte/icons/x';
	import Copy from '@lucide/svelte/icons/copy';

	/** Kastellan's own SSH key: what it is, where it may log in, and rotating it. */
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
	const button =
		'inline-flex h-9 items-center gap-1.5 rounded-xl border border-line bg-surface px-3.5 text-sm font-medium shadow-card hover:border-line-strong disabled:opacity-50';
	const primary =
		'inline-flex h-9 items-center rounded-xl bg-accent px-3.5 text-sm font-medium text-accent-ink shadow-card hover:opacity-90 disabled:opacity-50';
</script>

<svelte:head><title>Access · Kastellan</title></svelte:head>

<h1 class="text-2xl font-semibold tracking-tight">Access</h1>
<p class="mt-1 max-w-2xl text-sm text-muted">
	Kastellan's own SSH key, which it logs in to devices with. Set it up per device on the device's
	page; which other keys may log in there is listed there too.
</p>

{#if error}<p class="mt-4 text-sm" style:color="var(--critical-text)" role="alert">{error}</p>{/if}

{#if keys}
	<section class="card mt-6 p-5">
		<div class="flex flex-wrap items-start justify-between gap-3">
			<div class="min-w-0">
				<h2 class="text-sm font-semibold">Kastellan's key</h2>
				<p class="mt-1 font-mono text-xs break-all text-muted">{keys.fingerprint}</p>
				<p class="mt-1 text-xs text-faint">Made {ago(keys.made, store.now)}</p>
			</div>
			<button
				class={button}
				onclick={async () => {
					await navigator.clipboard.writeText(keys!.public_key);
					copied = true;
					setTimeout(() => (copied = false), 1500);
				}}
			>
				<Copy class="size-4 text-muted" />
				{copied ? 'Copied' : 'Copy public key'}
			</button>
		</div>
	</section>

	<section class="card mt-4 p-5">
		<h2 class="text-sm font-semibold">Rotate the key</h2>
		<p class="mt-1 max-w-2xl text-sm text-muted">
			A new key replaces this one on every device with a login ({keys.devices.length}). First the
			new key is added and tried everywhere; only then does Kastellan switch to it and remove the
			old one. Nothing stops working on the way: until the end, both keys work.
		</p>

		{#if !rot}
			{#if keys.rotation?.stage === 'done'}
				<p class="mt-3 text-xs text-muted">
					Last rotated {ago(keys.rotation.finished ?? keys.rotation.started, store.now)}.
				</p>
			{/if}
			<button
				class="{primary} mt-4"
				disabled={!canChange || busy !== null || keys.devices.length === 0}
				title={canChange ? '' : 'turn on sign-in first'}
				onclick={() => act('start', api.rotateStart)}
			>
				{busy === 'start' ? 'Adding the new key…' : 'Start: add a new key everywhere'}
			</button>
		{:else}
			<ul class="mt-4 divide-y divide-line rounded-xl border border-line">
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
							/>{:else}<X class="mt-0.5 size-4 shrink-0" style="color: var(--critical-text)" />{/if}
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
			<div class="mt-4 flex flex-wrap items-center gap-2">
				{#if rot.stage === 'adding'}
					{#if missing.length}
						<button
							class={button}
							disabled={busy !== null}
							onclick={() => act('start', api.rotateStart)}
						>
							{busy === 'start' ? 'Trying…' : 'Try again'}
						</button>
					{/if}
					<button
						class={primary}
						disabled={busy !== null || (missing.length > 0 && !anyway)}
						onclick={() => act('finish', () => api.rotateFinish(anyway))}
					>
						{busy === 'finish' ? 'Switching…' : 'Switch, and remove the old key'}
					</button>
					<button
						class={button}
						disabled={busy !== null}
						onclick={() => act('cancel', api.rotateCancel)}
					>
						Cancel
					</button>
					{#if missing.length}
						<label class="flex items-center gap-2 text-xs text-muted">
							<input type="checkbox" bind:checked={anyway} />
							Switch anyway: Kastellan can't log in to {missing.map(name).join(', ')} then
						</label>
					{/if}
				{:else}
					<button
						class={primary}
						disabled={busy !== null}
						onclick={() => act('finish', () => api.rotateFinish())}
					>
						{busy === 'finish' ? 'Removing…' : 'Remove the old key where it is left'}
					</button>
				{/if}
			</div>
		{/if}
	</section>
{:else if store.me && store.me.role !== 'admin'}
	<p class="mt-6 text-sm text-muted">Only admins see Kastellan's keys.</p>
{:else if !error}
	<p class="mt-6 text-sm text-muted">Loading…</p>
{/if}
