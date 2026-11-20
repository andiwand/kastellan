<script lang="ts">
	import { untrack } from 'svelte';
	import { streamSetup, type SetupOutcome } from '#lib/api.js';
	import X from '@lucide/svelte/icons/x';
	import Check from '@lucide/svelte/icons/check';
	import Copy from '@lucide/svelte/icons/copy';

	/** Setting something up on a device with your own login, step by step: the login and how to
	 * get root, then the output as it runs, then done (or why not). The password is used for this
	 * one connection and never stored. The same commands are there to copy, for doing it by hand
	 * (then Kastellan never sees a login). */
	let {
		title,
		intro,
		path,
		body = {},
		script,
		port,
		needsRoot = true,
		canRun = true,
		noRootFor = null,
		defaultLogin = '',
		onclose,
		ondone
	}: {
		title: string;
		intro: string;
		path: string; // the streaming endpoint
		body?: Record<string, unknown>; // what else the endpoint takes (abilities, user, ...)
		script: string;
		port: number;
		needsRoot?: boolean;
		canRun?: boolean; // false (sign-in off): only the commands to run by hand
		noRootFor?: string | null; // logged in as this user, root isn't needed
		defaultLogin?: string;
		onclose: () => void;
		ondone?: (outcome: SetupOutcome) => void | Promise<void>;
	} = $props();

	type Step = 'form' | 'running' | 'done' | 'failed';
	let step = $state<Step>('form');
	// What it starts with when it opens (a later change of the props doesn't reset the form).
	let login = $state(untrack(() => defaultLogin));
	let password = $state('');
	let how = $state<'sudo' | 'sudo-other' | 'su'>('sudo');
	let rootPassword = $state('');
	let log = $state<string[]>([]);
	let outcome = $state<SetupOutcome | null>(null);
	let error = $state<string | null>(null);
	let copied = $state(false);
	let manual = $state(untrack(() => !canRun));
	let dialog: HTMLDialogElement | undefined = $state();
	let logBox: HTMLPreElement | undefined = $state();

	$effect(() => {
		dialog?.showModal();
	});
	$effect(() => {
		const root = document.documentElement;
		const before = root.style.overflow;
		root.style.overflow = 'hidden';
		return () => {
			root.style.overflow = before;
		};
	});
	$effect(() => {
		void log.length;
		if (logBox) logBox.scrollTop = logBox.scrollHeight;
	});

	async function start(e: SubmitEvent) {
		e.preventDefault();
		step = 'running';
		log = [];
		error = null;
		const isRoot = login.trim() === 'root';
		const rootNeeded = needsRoot && login.trim() !== noRootFor;
		try {
			outcome = await streamSetup(
				path,
				{
					...body,
					login: login.trim(),
					password,
					port,
					elevate: !rootNeeded || isRoot ? 'none' : how === 'su' ? 'su' : 'sudo',
					root_password: how === 'sudo' || !rootNeeded ? null : rootPassword
				},
				(line) => (log = [...log, line])
			);
			step = outcome.ok ? 'done' : 'failed';
			if (!outcome.ok) error = outcome.error ?? 'it failed';
			try {
				await ondone?.(outcome);
			} catch {
				// refreshing the page's view afterwards; the setup itself is done
			}
		} catch (err) {
			error = (err as Error).message;
			step = 'failed';
		} finally {
			password = '';
			rootPassword = '';
		}
	}

	const input =
		'h-9 w-full rounded-lg border border-line bg-surface px-3 text-sm outline-none focus:border-accent';
	const label = 'flex flex-col gap-1 text-xs font-medium text-muted';
	const button =
		'inline-flex h-9 items-center justify-center gap-1.5 rounded-lg border border-line px-3 text-sm font-medium hover:border-line-strong disabled:opacity-50';
</script>

<dialog
	bind:this={dialog}
	{onclose}
	class="m-auto max-h-[calc(100dvh-2rem)] w-[min(40rem,calc(100vw-2rem))] rounded-2xl border border-line bg-surface p-0 text-text shadow-pop backdrop:bg-black/40"
	aria-labelledby="setup-title"
>
	<div class="flex max-h-[calc(100dvh-2rem)] flex-col">
		<div class="flex items-center justify-between border-b border-line px-5 py-4">
			<h2 id="setup-title" class="text-base font-semibold">{title}</h2>
			<button class="text-muted hover:text-text" onclick={() => dialog?.close()}>
				<X class="size-5" /><span class="sr-only">Close</span>
			</button>
		</div>

		<div class="flex flex-col gap-4 overflow-y-auto overscroll-contain px-5 py-4">
			{#if step === 'form'}
				<p class="text-sm leading-relaxed text-muted">{intro}</p>
				{#if !canRun}
					<p class="text-xs" style:color="var(--warning-text)">
						With sign-in off, Kastellan doesn't run setups itself: run the commands below as root on
						the device.
					</p>
				{/if}
				<form id="setup-form" hidden={!canRun} class="grid gap-3 sm:grid-cols-2" onsubmit={start}>
					<label class={label}>
						Your login on the device
						<input class={input} bind:value={login} autocomplete="off" required />
					</label>
					<label class={label}>
						Its password
						<input
							class={input}
							type="password"
							bind:value={password}
							autocomplete="off"
							required
						/>
					</label>
					{#if needsRoot && login.trim() !== 'root' && login.trim() !== noRootFor}
						<label class="{label} sm:col-span-2">
							Getting root
							<select class={input} bind:value={how}>
								<option value="sudo">sudo, with the same password</option>
								<option value="sudo-other">sudo, with another password</option>
								<option value="su">su, with root's password (no sudo for this login)</option>
							</select>
						</label>
						{#if how !== 'sudo'}
							<label class="{label} sm:col-span-2">
								{how === 'su' ? "Root's password" : 'The sudo password'}
								<input
									class={input}
									type="password"
									bind:value={rootPassword}
									autocomplete="off"
									required
								/>
							</label>
						{/if}
					{/if}
				</form>
				{#if canRun}
					<p class="text-xs text-faint">
						The passwords are used for this one connection and never stored or logged.
					</p>
				{/if}
				<details bind:open={manual} class="text-sm">
					<summary class="cursor-pointer text-xs text-muted">
						Or run it yourself (Kastellan never sees a login)
					</summary>
					<div class="mt-2 flex justify-end">
						<button
							class="inline-flex h-7 items-center gap-1 rounded-md border border-line px-2 text-xs hover:border-line-strong"
							onclick={async () => {
								await navigator.clipboard.writeText(script);
								copied = true;
								setTimeout(() => (copied = false), 1500);
							}}
						>
							<Copy class="size-3.5" />{copied ? 'Copied' : 'Copy'}
						</button>
					</div>
					<pre
						class="mt-1 max-h-60 overflow-auto rounded-lg bg-surface-2 p-3 font-mono text-[11px] leading-relaxed">{script}</pre>
				</details>
			{:else}
				<p class="flex items-center gap-2 text-sm">
					{#if step === 'running'}
						<span class="size-3 animate-spin rounded-full border-2 border-line border-t-accent"
						></span>
						Working on the device…
					{:else if step === 'done'}
						<Check class="size-4" style="color: var(--good-text)" />
						<span style:color="var(--good-text)">Done.</span>
					{:else}
						<span style:color="var(--critical-text)">It didn't work: {error}</span>
					{/if}
				</p>
				<pre
					bind:this={logBox}
					class="max-h-72 min-h-24 overflow-auto rounded-lg bg-surface-2 p-3 font-mono text-[11px] leading-relaxed">{log.join(
						'\n'
					) || '…'}</pre>
				{#if step === 'done' && outcome?.config}
					<p class="text-xs text-muted">Added to this device in config.yaml:</p>
					<pre class="rounded-lg bg-surface-2 p-2 font-mono text-[11px]">{outcome.config}</pre>
				{/if}
			{/if}
		</div>

		<div class="flex justify-end gap-2 border-t border-line px-5 py-3">
			{#if step === 'form'}
				<button class={button} onclick={() => dialog?.close()}>Cancel</button>
				{#if canRun}
					<button
						form="setup-form"
						class="{button} border-transparent bg-accent text-accent-ink hover:opacity-90"
					>
						Start
					</button>
				{/if}
			{:else if step === 'failed'}
				<button class={button} onclick={() => (step = 'form')}>Back</button>
				<button class={button} onclick={() => dialog?.close()}>Close</button>
			{:else}
				<button class={button} onclick={() => dialog?.close()}>Close</button>
			{/if}
		</div>
	</div>
</dialog>
