<script lang="ts">
	import { modal } from '#lib/modal.js';
	import { date } from '#lib/format.js';
	import { api } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { pushState, thisEndpoint, turnOff, turnOn, type PushState } from '#lib/push.js';
	import X from '@lucide/svelte/icons/x';

	/** Alarms as notifications on this phone or computer (web push), and the person's other
	 * browsers that have them on. */
	let { onclose }: { onclose: () => void } = $props();

	let dialog: HTMLDialogElement | undefined = $state();
	let mode = $state<PushState | null>(null);
	let info = $state<Awaited<ReturnType<typeof api.push>> | null>(null);
	let here = $state<string | null>(null);
	let busy = $state(false);
	let error = $state<string | null>(null);
	let note = $state<string | null>(null);

	async function load() {
		try {
			[mode, info, here] = await Promise.all([pushState(), api.push(), thisEndpoint()]);
		} catch (e) {
			error = (e as Error).message;
		}
	}
	load();

	async function run(fn: () => Promise<unknown>, done?: string) {
		busy = true;
		error = note = null;
		try {
			await fn();
			note = done ?? null;
			await load();
		} catch (e) {
			error = (e as Error).message;
		} finally {
			busy = false;
		}
	}

	const remove = (endpoint: string) =>
		run(() => (endpoint === here ? turnOff() : api.pushUnsubscribe(endpoint)));
</script>

<dialog
	use:modal
	bind:this={dialog}
	{onclose}
	class="m-auto w-[min(30rem,calc(100vw-2rem))] rounded-2xl border border-line bg-surface p-0 text-text shadow-pop backdrop:bg-black/40"
	aria-labelledby="push-title"
>
	<div class="flex items-center justify-between border-b border-line px-5 py-4">
		<h2 id="push-title" class="text-base font-semibold">Notifications on this device</h2>
		<button class="text-muted hover:text-text" onclick={() => dialog?.close()}>
			<X class="size-5" /><span class="sr-only">Close</span>
		</button>
	</div>
	<div class="flex flex-col gap-3 px-5 py-4 text-sm">
		{#if mode === null && !error}
			<p class="text-muted">Loading…</p>
		{:else if mode === 'insecure'}
			<p class="text-muted">
				Browsers allow notifications only on pages served over HTTPS. Reach Kastellan through an
				HTTPS reverse proxy (see the README), then turn them on here.
			</p>
		{:else if mode === 'unsupported'}
			<p class="text-muted">
				This browser can't receive them here. On an iPhone or iPad, add Kastellan to the home screen
				(Share, Add to Home Screen) and open it from there.
			</p>
		{:else if mode === 'denied'}
			<p class="text-muted">
				Notifications are blocked for this page: allow them in the browser's site settings, then
				open this again.
			</p>
		{:else if mode}
			<p class="text-muted">
				{mode === 'on'
					? 'On: alarms come here, also while Kastellan is closed.'
					: 'Alarms as notifications here, also while Kastellan is closed.'}
			</p>
			<div class="flex flex-wrap gap-2">
				{#if mode === 'on'}
					<button class="btn" disabled={busy} onclick={() => run(turnOff)}>Turn off here</button>
					<button
						class="btn"
						disabled={busy}
						onclick={() => run(api.pushTest, 'Sent: it should show in a moment.')}
					>
						Send a test
					</button>
				{:else}
					<button
						class="btn btn-primary"
						disabled={busy || !info}
						onclick={() => run(() => turnOn(info!.public_key))}
					>
						Turn on here
					</button>
				{/if}
			</div>
		{/if}
		{#if info && !info.channel}
			<p class="text-xs" style:color="var(--warning-text)">
				No push channel reaches you yet: {store.me?.role === 'admin' ? 'add' : 'an admin adds'} one in
				Config, Notifications (kind Push, user {store.me?.user}).
			</p>
		{/if}
		{#if error}<p class="text-xs" style:color="var(--critical-text)">{error}</p>{/if}
		{#if note}<p class="text-xs" style:color="var(--good-text)">{note}</p>{/if}
		{#if info?.subscriptions.length}
			<div>
				<h3 class="text-xs font-medium text-muted">Your browsers with notifications</h3>
				<ul class="mt-1.5 divide-y divide-line rounded-xl border border-line">
					{#each info.subscriptions as s (s.endpoint)}
						<li class="flex items-center gap-2 px-3 py-2 text-xs">
							<span class="flex-1 truncate">
								{s.label || 'A browser'}{#if s.endpoint === here}<span class="text-faint">
										· this one
									</span>{/if}
							</span>
							<span class="text-faint">{date(s.created)}</span>
							<button class="btn btn-sm" disabled={busy} onclick={() => remove(s.endpoint)}>
								Remove
							</button>
						</li>
					{/each}
				</ul>
			</div>
		{/if}
	</div>
</dialog>
