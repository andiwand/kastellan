<script lang="ts">
	import CodeBlock from '#lib/components/CodeBlock.svelte';
	import { api, type Setup } from '#lib/api.js';
	import Check from '@lucide/svelte/icons/check';
	import X from '@lucide/svelte/icons/x';

	/** Setting up OpenVPN on the hosts: what works now, the commands, and installing them. */
	let { vid, canInstall }: { vid: string; canInstall: boolean } = $props();

	let setup = $state<Setup | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let shown = $state<Record<string, boolean>>({});
	let confirm = $state<string | null>(null);
	let output = $state<Record<string, string>>({});

	async function check() {
		loading = true;
		error = null;
		try {
			setup = await api.ovpnSetup(vid);
		} catch (e) {
			error = (e as Error).message;
		} finally {
			loading = false;
		}
	}

	async function install(role: string) {
		confirm = null;
		loading = true;
		try {
			output[role] = (await api.ovpnInstall(vid, role)).output;
			setup = await api.ovpnSetup(vid);
		} catch (e) {
			output[role] = `Failed: ${(e as Error).message}`;
		} finally {
			loading = false;
		}
	}

	const roleName = { pki: 'PKI host (easy-rsa)', server: 'OpenVPN server' };
	const button =
		'inline-flex h-8 items-center gap-1.5 rounded-lg border border-line px-2.5 text-xs font-medium hover:border-line-strong disabled:opacity-50';
</script>

<details
	class="mt-3 rounded-xl border border-line p-4"
	ontoggle={(e) => (e.currentTarget as HTMLDetailsElement).open && !setup && check()}
>
	<summary class="cursor-pointer text-sm font-medium">Setup on the hosts</summary>
	<p class="mt-2 max-w-3xl text-xs leading-relaxed text-muted">
		{#if setup?.mode === 'direct'}
			Mode <b>direct</b>: nothing is installed; Kastellan sends its scripts over each host's normal
			login. Simpler, but that login can do anything. Switch to <code>mode: restricted</code> and install
			the scripts to limit Kastellan to these few actions.
		{:else}
			Mode <b>restricted</b>: on each host, Kastellan's VPN key may run one small script and nothing
			else. Install it with the commands below (as root), or let Kastellan do it over the host's
			normal login.
		{/if}
	</p>
	{#if error}<p class="mt-2 text-xs" style:color="var(--critical-text)">{error}</p>{/if}
	{#if loading && !setup}<p class="mt-3 text-xs text-muted">Checking the hosts…</p>{/if}

	{#each setup?.hosts ?? [] as host (host.role)}
		<div class="mt-4 rounded-lg border border-line p-3">
			<p class="text-sm font-medium">
				{roleName[host.role]}: {host.name}
				<span class="font-mono text-xs text-muted">{host.host}</span>
			</p>
			<ul class="mt-2 space-y-1">
				{#each host.checks as c (c.name)}
					<li class="flex items-start gap-2 text-xs">
						{#if c.ok}<Check
								class="mt-px size-3.5 shrink-0"
								style="color: var(--good-text)"
							/>{:else}<X
								class="mt-px size-3.5 shrink-0"
								style="color: var(--critical-text)"
							/>{/if}
						<span>
							{c.name}{#if c.detail}<span class="text-muted"> · {c.detail}</span>{/if}
						</span>
					</li>
				{/each}
			</ul>
			{#if setup?.mode === 'restricted'}
				<div class="mt-3 flex flex-wrap gap-1.5">
					<button class={button} onclick={() => (shown[host.role] = !shown[host.role])}>
						{shown[host.role] ? 'Hide' : 'Show'} the commands
					</button>
					{#if canInstall && host.can_install}
						{#if confirm === host.role}
							<button
								class={button}
								style:color="var(--critical-text)"
								disabled={loading}
								onclick={() => install(host.role)}
							>
								Run them as root on {host.host}?
							</button>
							<button class={button} onclick={() => (confirm = null)}>No</button>
						{:else}
							<button class={button} disabled={loading} onclick={() => (confirm = host.role)}>
								Install for me
							</button>
						{/if}
					{/if}
				</div>
				{#if shown[host.role]}
					<div class="mt-2">
						<p class="text-xs text-muted">Run as root on {host.host}:</p>
						<CodeBlock text={host.script} maxHeight="max-h-80" class="mt-1" />
					</div>
				{/if}
				{#if output[host.role]}
					<pre
						class="mt-2 max-h-40 overflow-auto rounded-lg bg-surface-2 p-2 font-mono text-[11px]">{output[
							host.role
						]}</pre>
				{/if}
			{/if}
		</div>
	{/each}
	{#if setup}
		<button class="{button} mt-3" disabled={loading} onclick={check}>Check again</button>
	{/if}
</details>
