<script lang="ts">
	import { api, type VpnKey } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { ago } from '#lib/format.js';
	import Check from '@lucide/svelte/icons/check';
	import X from '@lucide/svelte/icons/x';

	/** Rotating Kastellan's VPN key (restricted mode): the scripts on the PKI host and the
	 * OpenVPN server take the new key from the old one, with exactly its rights; then the old key
	 * goes. Both work until the end. */
	let { canChange }: { canChange: boolean } = $props();

	let key = $state<VpnKey | null>(null);
	let error = $state<string | null>(null);
	let busy = $state<string | null>(null);
	let anyway = $state(false);

	async function load() {
		try {
			key = await api.vpnKey();
			error = null;
		} catch (e) {
			error = (e as Error).message;
		}
	}

	async function act(step: 'start' | 'finish' | 'cancel') {
		busy = step;
		error = null;
		try {
			key = await api.vpnKeyRotate(step, anyway);
		} catch (e) {
			error = (e as Error).message;
			await load();
		} finally {
			busy = null;
		}
	}

	const rot = $derived(key?.rotation && key.rotation.stage !== 'done' ? key.rotation : null);
	const roles: Record<string, string> = { pki: 'PKI host', server: 'OpenVPN server' };
	const missing = $derived(
		rot ? Object.entries(rot.devices).filter(([, s]) => !s.added) : ([] as [string, unknown][])
	);
	const button =
		'inline-flex h-8 items-center gap-1.5 rounded-lg border border-line px-2.5 text-xs font-medium hover:border-line-strong disabled:opacity-50';
</script>

<details
	class="card mt-3 p-4"
	ontoggle={(e) => (e.currentTarget as HTMLDetailsElement).open && !key && load()}
>
	<summary class="cursor-pointer text-sm font-medium">Kastellan's VPN key</summary>
	<p class="mt-2 max-w-3xl text-xs leading-relaxed text-muted">
		The key that may run the two scripts on the hosts. Rotating it: the scripts add the new key with
		exactly the old one's rights, Kastellan tries it, switches, and then the old key is removed and
		must stop working.
	</p>
	{#if error}<p class="mt-2 text-xs" style:color="var(--critical-text)">{error}</p>{/if}
	{#if key}
		<p class="mt-2 font-mono text-[11px] break-all text-muted">{key.fingerprint}</p>
		{#if rot}
			<ul class="mt-3 space-y-1">
				{#each Object.entries(rot.devices) as [role, step] (role)}
					{@const ok = rot.stage === 'adding' ? step.added : step.removed}
					<li class="flex items-start gap-2 text-xs">
						{#if ok}<Check
								class="mt-px size-3.5 shrink-0"
								style="color: var(--good-text)"
							/>{:else}<X
								class="mt-px size-3.5 shrink-0"
								style="color: var(--critical-text)"
							/>{/if}
						<span>
							{roles[role] ?? role}
							<span class="text-muted">
								· {rot.stage === 'adding'
									? step.added
										? 'has the new key, and it works'
										: (step.error ?? 'not tried yet')
									: step.removed
										? 'old key removed'
										: (step.error ?? 'old key still there')}
							</span>
						</span>
					</li>
				{/each}
			</ul>
		{:else if key.rotation?.stage === 'done'}
			<p class="mt-2 text-xs text-muted">
				Last rotated {ago(key.rotation.finished ?? key.rotation.started, store.now)}.
			</p>
		{/if}
		{#if canChange}
			<div class="mt-3 flex flex-wrap items-center gap-1.5">
				{#if !rot}
					<button class={button} disabled={busy !== null} onclick={() => act('start')}>
						{busy === 'start' ? 'Adding the new key…' : 'Rotate the key'}
					</button>
				{:else if rot.stage === 'adding'}
					{#if missing.length}
						<button class={button} disabled={busy !== null} onclick={() => act('start')}>
							Try again
						</button>
					{/if}
					<button
						class={button}
						disabled={busy !== null || (missing.length > 0 && !anyway)}
						onclick={() => act('finish')}
					>
						{busy === 'finish' ? 'Switching…' : 'Switch, and remove the old key'}
					</button>
					<button class={button} disabled={busy !== null} onclick={() => act('cancel')}>
						Cancel
					</button>
					{#if missing.length}
						<label class="choice text-xs text-muted">
							<input type="checkbox" bind:checked={anyway} />
							Switch anyway: Kastellan can't reach {missing.map(([r]) => roles[r] ?? r).join(', ')} with
							the VPN key then
						</label>
					{/if}
				{:else}
					<button class={button} disabled={busy !== null} onclick={() => act('finish')}>
						{busy === 'finish' ? 'Removing…' : 'Remove the old key where it is left'}
					</button>
				{/if}
			</div>
		{/if}
	{:else if !error}
		<p class="mt-2 text-xs text-muted">Loading…</p>
	{/if}
</details>
