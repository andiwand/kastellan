<script lang="ts">
	import { api, type Access } from '#lib/api.js';
	import Check from '@lucide/svelte/icons/check';
	import X from '@lucide/svelte/icons/x';
	import Copy from '@lucide/svelte/icons/copy';

	/** SSH access for Kastellan to one device: what works, the commands, or set it up once with a
	 * login and password (used for that connection only). */
	let { device, canSetUp }: { device: string; canSetUp: boolean } = $props();

	let access = $state<Access | null>(null);
	let error = $state<string | null>(null);
	let way = $state<'existing' | 'dedicated'>('existing');
	let copied = $state(false);
	let login = $state('');
	let password = $state('');
	let busy = $state(false);
	let result = $state<{ output: string; config: string } | null>(null);

	async function load() {
		error = null;
		try {
			access = await api.access(device);
		} catch (e) {
			error = (e as Error).message;
		}
	}

	async function copy(text: string) {
		await navigator.clipboard.writeText(text);
		copied = true;
		setTimeout(() => (copied = false), 1500);
	}

	async function setUp(e: SubmitEvent) {
		e.preventDefault();
		if (!access) return;
		busy = true;
		error = null;
		const dedicated = way === 'dedicated';
		try {
			result = await api.setUpAccess(device, {
				login: login.trim(),
				password,
				user: dedicated ? 'kastellan' : access.user,
				create: dedicated,
				sudo: dedicated,
				port: access.port
			});
			await load();
		} catch (err) {
			error = (err as Error).message;
		} finally {
			password = '';
			busy = false;
		}
	}

	const input =
		'h-9 rounded-lg border border-line bg-surface px-3 text-sm outline-none focus:border-accent';
	const button =
		'inline-flex h-8 items-center gap-1.5 rounded-lg border border-line px-2.5 text-xs font-medium hover:border-line-strong disabled:opacity-50';
</script>

<details
	class="card mt-8 p-4"
	ontoggle={(e) => (e.currentTarget as HTMLDetailsElement).open && !access && load()}
>
	<summary class="cursor-pointer text-sm font-semibold">SSH access for Kastellan</summary>
	{#if error}<p class="mt-2 text-xs" style:color="var(--critical-text)">{error}</p>{/if}
	{#if !access && !error}<p class="mt-2 text-xs text-muted">Checking…</p>{/if}
	{#if access}
		<ul class="mt-3 space-y-1">
			{#each access.checks as c (c.name)}
				<li class="flex items-start gap-2 text-xs">
					{#if c.ok}<Check
							class="mt-px size-3.5 shrink-0"
							style="color: var(--good-text)"
						/>{:else}<X class="mt-px size-3.5 shrink-0" style="color: var(--critical-text)" />{/if}
					<span>
						{c.name}{#if c.detail}<span class="text-muted"> · {c.detail}</span>{/if}
					</span>
				</li>
			{/each}
		</ul>

		<div
			class="mt-4 flex gap-1 rounded-xl border border-line bg-surface p-1 text-sm"
			role="tablist"
		>
			{#each [['existing', `One login: ${access.user}`], ['dedicated', 'A user of its own: kastellan']] as [id, label] (id)}
				<button
					role="tab"
					aria-selected={way === id}
					onclick={() => (way = id as typeof way)}
					class="h-8 flex-1 rounded-lg px-3 text-xs font-medium {way === id
						? 'bg-surface-3 text-text'
						: 'text-muted hover:text-text'}"
				>
					{label}
				</button>
			{/each}
		</div>
		<p class="mt-2 text-xs leading-relaxed text-muted">
			{#if way === 'existing'}
				Kastellan uses your existing account <b>{access.user}</b> for everything. Run this as {access.user}.
			{:else}
				Kastellan gets an account of its own, with sudo without a password (so it can install what
				you ask it to, every time audited). Run this as root.
			{/if}
			{#if access.hub_ip}The key only works from the hub's address ({access.hub_ip}).{/if}
		</p>
		<div class="mt-2 flex justify-end">
			<button class={button} onclick={() => copy(access!.scripts[way])}>
				<Copy class="size-3.5" />
				{copied ? 'Copied' : 'Copy'}
			</button>
		</div>
		<pre
			class="mt-1 max-h-72 overflow-auto rounded-lg bg-surface-2 p-3 font-mono text-[11px] leading-relaxed">{access
				.scripts[way]}</pre>

		{#if canSetUp}
			<form class="mt-4 border-t border-line pt-4" onsubmit={setUp}>
				<p class="text-sm font-medium">Or let Kastellan do it</p>
				<p class="mt-1 text-xs text-muted">
					With a login that exists on the device{way === 'dedicated'
						? ' and may use sudo (or root)'
						: ''}. Its password is used for this one connection and never stored.
				</p>
				<div class="mt-3 flex flex-wrap gap-2">
					<input
						class={input}
						bind:value={login}
						placeholder="Login, e.g. {way === 'existing' ? access.user : 'root'}"
						autocomplete="off"
						required
					/>
					<input
						class={input}
						type="password"
						bind:value={password}
						placeholder="Its password"
						autocomplete="off"
						required
					/>
					<button class={button} disabled={busy}>{busy ? 'Setting up…' : 'Set it up'}</button>
				</div>
			</form>
		{/if}
		{#if result}
			<div class="mt-3 text-xs">
				<p style:color="var(--good-text)">{result.output.split('\n').at(-1)}</p>
				<p class="mt-2 text-muted">Add this to the device in config.yaml:</p>
				<pre class="mt-1 rounded-lg bg-surface-2 p-2 font-mono text-[11px]">{result.config}</pre>
			</div>
		{/if}
	{/if}
</details>
