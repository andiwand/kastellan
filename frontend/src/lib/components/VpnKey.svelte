<script lang="ts">
	import { api, type VpnKey } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { ago } from '#lib/format.js';
	import Check from '@lucide/svelte/icons/check';
	import X from '@lucide/svelte/icons/x';

	/** Rotating Kastellan's VPN key (restricted mode): the scripts on the PKI host and the
	 * OpenVPN server take the new key from the old one, with exactly its rights; then only the new
	 * key stays (whatever else was bound to the scripts goes too). Both work until the end; a host
	 * that can't be reached holds the end (and the old key) until it works or is forgotten. */
	let {
		canChange,
		names = {}, // VPN id -> name
		restricted = true // an OpenVPN in restricted mode uses the key; else only a rotation left over shows
	}: { canChange: boolean; names?: Record<string, string>; restricted?: boolean } = $props();

	let key = $state<VpnKey | null>(null);
	let error = $state<string | null>(null);
	let busy = $state<string | null>(null);
	let anyway = $state(false);

	$effect(() => {
		if (!restricted && !key) load();
	});

	async function load() {
		try {
			key = await api.vpnKey();
			error = null;
		} catch (e) {
			error = (e as Error).message;
		}
	}

	async function act(step: 'start' | 'finish' | 'cancel' | `forget:${string}`) {
		busy = step;
		error = null;
		try {
			key = step.startsWith('forget:')
				? await api.vpnKeyForget(step.slice(7))
				: await api.vpnKeyRotate(step as 'start' | 'finish' | 'cancel', anyway);
		} catch (e) {
			error = (e as Error).message;
			await load();
		} finally {
			busy = null;
		}
	}

	const rot = $derived(key?.rotation && key.rotation.stage !== 'done' ? key.rotation : null);
	// Given up on at the end: the old key may still be trusted there.
	const forgotten = $derived(
		key?.rotation?.stage === 'done'
			? Object.entries(key.rotation.devices).filter(([, s]) => s.forgotten)
			: []
	);
	const roles: Record<string, string> = { pki: 'PKI host', server: 'OpenVPN server' };
	// A host of the rotation: "<vpn>:<role>" (one key for every OpenVPN in restricted mode).
	const hostName = (host: string): string => {
		const [vid, role] = host.includes(':') ? host.split(':') : ['', host];
		return `${vid ? `${names[vid] ?? vid}: ` : ''}${roles[role] ?? role}`;
	};
	const missing = $derived(
		rot ? Object.entries(rot.devices).filter(([, s]) => !s.added) : ([] as [string, unknown][])
	);
	const button =
		'inline-flex h-8 items-center gap-1.5 rounded-lg border border-line px-2.5 text-xs font-medium hover:border-line-strong disabled:opacity-50';
</script>

{#if restricted || rot}<details
		class="card mt-3 p-4"
		ontoggle={(e) => (e.currentTarget as HTMLDetailsElement).open && !key && load()}
	>
		<summary class="cursor-pointer text-sm font-medium">Kastellan's VPN key</summary>
		<p class="mt-2 max-w-3xl text-xs leading-relaxed text-muted">
			The key that may run the two scripts on the hosts. Rotating it: the scripts add the new key
			with exactly the old one's rights, Kastellan tries it, switches, and then only the new key
			stays bound to the scripts (a key someone added with a leaked one goes too); the old key must
			stop working.
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
								{hostName(role)}
								<span class="text-muted">
									· {rot.stage === 'adding'
										? step.added
											? 'has the new key, and it works'
											: (step.error ?? 'not tried yet')
										: step.removed
											? `only the new key left${step.note ? ` (${step.note})` : ''}`
											: (step.error ?? 'the old key is still trusted here')}
								</span>
								{#if rot.stage === 'removing' && !step.removed && canChange}
									<button
										class="ml-1 underline decoration-dotted hover:text-text"
										disabled={busy !== null}
										title="Finish without this host: the old key may stay trusted there until you remove it by hand"
										onclick={() => act(`forget:${role}`)}
									>
										forget this host
									</button>
								{/if}
							</span>
						</li>
					{/each}
				</ul>
			{:else if key.rotation?.stage === 'done'}
				<p class="mt-2 text-xs text-muted">
					Last rotated {ago(key.rotation.finished ?? key.rotation.started, store.now)}.
				</p>
				{#each forgotten as [role, step] (role)}
					<p class="mt-1 text-xs" style:color="var(--warning-text)">
						{hostName(role)} was left out: the old key may still be trusted on {step.target}; remove
						its line there by hand.
					</p>
				{/each}
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
								Switch anyway: {missing.map(([r]) => hostName(r)).join(', ')} keeps trusting the old key
								until it answers again (then it gets the new one), or you forget it
							</label>
						{/if}
					{:else}
						<button class={button} disabled={busy !== null} onclick={() => act('finish')}>
							{busy === 'finish' ? 'Removing…' : 'Try the hosts left again'}
						</button>
					{/if}
				</div>
			{/if}
		{:else if !error}
			<p class="mt-2 text-xs text-muted">Loading…</p>
		{/if}
	</details>{/if}
