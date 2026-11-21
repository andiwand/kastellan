<script lang="ts">
	import CodeBlock from '#lib/components/CodeBlock.svelte';
	import { api, type Access } from '#lib/api.js';
	import Check from '@lucide/svelte/icons/check';
	import X from '@lucide/svelte/icons/x';
	import { store } from '#lib/state.svelte.js';
	import SetupDialog from './SetupDialog.svelte';

	/** SSH access for Kastellan to one device: what works, the commands, or set it up once with a
	 * login and password (used for that connection only). */
	let { device, canSetUp }: { device: string; canSetUp: boolean } = $props();

	let access = $state<Access | null>(null);
	let error = $state<string | null>(null);
	let way = $state<'existing' | 'dedicated'>('existing');
	let understood = $state(false);
	let setting = $state(false);
	let trusting = $state(false);
	let keyError = $state<string | null>(null);

	// The host key, right here: an unknown one trusted after comparing, a changed one forgotten
	// on purpose (never trusted in one click).
	async function keyAction(how: 'trust' | 'forget') {
		if (!access?.host_key.fingerprint) return;
		trusting = true;
		keyError = null;
		try {
			if (how === 'trust') await api.trustHost(device, access.host_key.fingerprint, access.port);
			else await api.forgetHost(device, access.port);
			await load();
		} catch (e) {
			keyError = (e as Error).message;
		} finally {
			trusting = false;
		}
	}

	async function load() {
		error = null;
		try {
			access = await api.access(device);
		} catch (e) {
			error = (e as Error).message;
		}
	}

	const button =
		'inline-flex h-8 items-center gap-1.5 rounded-lg border border-line px-2.5 text-xs font-medium hover:border-line-strong disabled:opacity-50';
</script>

<details
	class="card p-4"
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
						{c.name}{#if c.detail}<span class="text-muted">{' · '}{c.detail}</span>{/if}
					</span>
				</li>
			{/each}
		</ul>
		{#if access.host_key.state === 'unknown' || access.host_key.state === 'changed'}
			{@const changed = access.host_key.state === 'changed'}
			<div class="mt-3 rounded-lg border border-line p-3 text-xs leading-relaxed">
				<p style:color={changed ? 'var(--critical-text)' : undefined}>
					{changed
						? 'The host key changed since it was trusted: after a reinstall, or someone sits in between. Kastellan connects only once you know which.'
						: "Kastellan hasn't met this host yet. Compare its key with the host's own before trusting it:"}
				</p>
				<p class="mt-1 font-mono break-all">{access.host_key.fingerprint}</p>
				<p class="mt-1 break-all text-muted">
					On the host: <code class="font-mono">
						ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
					</code>; on OpenWrt:
					<code class="font-mono">dropbearkey -y -f /etc/dropbear/dropbear_ed25519_host_key</code>
				</p>
				<button
					class="btn btn-sm mt-2 {changed ? '' : 'btn-primary'}"
					disabled={trusting}
					onclick={() => keyAction(changed ? 'forget' : 'trust')}
				>
					{changed ? 'It was reinstalled: forget the old key' : 'Trust this key'}
				</button>
				{#if keyError}<p class="mt-1" style:color="var(--critical-text)">{keyError}</p>{/if}
			</div>
		{/if}

		<div
			class="mt-4 inline-flex max-w-full gap-1 rounded-xl border border-line bg-surface p-1 text-sm"
			role="tablist"
		>
			{#each [['existing', `One login: ${access.user}`], ['dedicated', 'A user of its own: kastellan']] as [id, label] (id)}
				<button
					role="tab"
					aria-selected={way === id}
					onclick={() => (way = id as typeof way)}
					class="h-8 rounded-lg px-3 text-xs font-medium whitespace-nowrap {way === id
						? 'bg-surface-3 text-text'
						: 'text-muted hover:text-text'}"
				>
					{label}
				</button>
			{/each}
		</div>
		<p class="mt-2 max-w-3xl text-xs leading-relaxed text-muted">
			{#if way === 'existing'}
				Kastellan uses your existing account <b>{access.user}</b> for everything. Run this as {access.user}.
			{:else}
				Kastellan gets an account of its own, with sudo without a password (so it can install what
				you ask it to, every time audited). Run this as root.
			{/if}
			{#if access.hub_ip}The key only works from the hub's address ({access.hub_ip}).{/if}
		</p>
		<CodeBlock text={access.scripts[way] ?? ''} class="mt-2" />

		{#if way === 'dedicated'}
			<label class="choice mt-3 text-xs">
				<input type="checkbox" bind:checked={understood} />
				<span>The user <b>kastellan</b> gets sudo without a password on this device.</span>
			</label>
		{/if}
		<div class="mt-3 flex justify-end">
			<button
				class={button}
				disabled={way === 'dedicated' && !understood}
				onclick={() => (setting = true)}
			>
				Set it up with your login
			</button>
		</div>
	{/if}
</details>

{#if setting && access}
	<SetupDialog
		title={way === 'dedicated'
			? 'A user of its own: kastellan'
			: `Kastellan's key for ${access.user}`}
		intro={way === 'dedicated'
			? 'Kastellan logs in once with your login, creates the user kastellan with sudo, adds its key, and adds ssh: to this device in config.yaml.'
			: `Kastellan logs in once as ${access.user}, adds its key to that account, and adds ssh: to this device in config.yaml.`}
		path="/devices/{encodeURIComponent(device)}/access/setup"
		body={{
			user: way === 'dedicated' ? 'kastellan' : access.user,
			create: way === 'dedicated',
			sudo: way === 'dedicated'
		}}
		script={access.scripts[way] ?? ''}
		port={access.port}
		needsRoot={true}
		noRootFor={way === 'existing' ? access.user : null}
		defaultLogin={way === 'existing' ? access.user : ''}
		canRun={canSetUp}
		onclose={() => (setting = false)}
		ondone={async (o) => {
			if (o.ok) {
				await store.refresh();
				await load();
			}
		}}
	/>
{/if}
