<script lang="ts">
	import { untrack } from 'svelte';
	import { api, type Vpn, type VpnSettings } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';

	/** One VPN's settings: a new one of `kind`, or `vpn` as it is. WireGuard: where wg-easy is and
	 * its login (the password kept as a secret, never in config.yaml); OpenVPN: the easy-rsa PKI's
	 * host, the server that gets the revocation list, and how Kastellan reaches them. Saved into
	 * config.yaml's `vpns:`. */
	let {
		kind,
		vpn = null,
		onsaved,
		oncancel
	}: {
		kind: Vpn['kind'];
		vpn?: Vpn | null;
		onsaved: (id: string) => void;
		oncancel: () => void;
	} = $props();

	const start = untrack(() => vpn?.settings);
	const isNew = untrack(() => vpn === null);
	let name = $state(untrack(() => vpn?.name ?? (kind === 'wireguard' ? 'WireGuard' : 'OpenVPN')));
	let id = $state(untrack(() => vpn?.id ?? ''));
	let idTouched = $state(false);
	let password = $state('');
	let wg = $state({
		wg_easy: start?.kind === 'wireguard' ? start.wg_easy : '',
		user: start?.kind === 'wireguard' ? start.user : 'admin'
	});
	let ovpn = $state({
		mode: start?.kind === 'openvpn' ? start.mode : ('restricted' as 'restricted' | 'direct'),
		pki: start?.kind === 'openvpn' ? start.pki : '',
		server: start?.kind === 'openvpn' ? (start.server ?? '') : '',
		days: start?.kind === 'openvpn' ? start.days : 365,
		instance: start?.kind === 'openvpn' ? String(start.instance ?? '') : ''
	});
	// config.yaml as the form opened: saving over a change made meanwhile is refused.
	let version = $state<string | null>(null);
	api.settings().then(
		(s) => (version = s.version),
		(e) => (error = (e as Error).message)
	);
	let error = $state<string | null>(null);
	let busy = $state(false);

	const slug = (s: string) =>
		s
			.toLowerCase()
			.normalize('NFKD')
			.replace(/\p{M}/gu, '')
			.replace(/[^a-z0-9]+/g, '-')
			.replace(/^-+|-+$/g, '')
			.slice(0, 40);
	$effect(() => {
		if (isNew && !idTouched) id = slug(name);
	});

	const sshDevices = $derived(
		(store.devices ?? []).filter((d) => d.ssh).sort((a, b) => a.name.localeCompare(b.name))
	);
	const passwordSet = $derived(!isNew && !!vpn?.password_set);
	const ready = $derived(
		/^[a-z0-9][a-z0-9_-]*$/.test(id) &&
			(kind === 'wireguard'
				? /^https?:\/\/\S+$/.test(wg.wg_easy) && (passwordSet || password !== '')
				: ovpn.pki !== '')
	);

	async function save(e: SubmitEvent) {
		e.preventDefault();
		busy = true;
		error = null;
		try {
			// Only this form's fields: the hub keeps the rest (paths, users, a client template) as the
			// file has it.
			const value: Partial<VpnSettings> =
				kind === 'wireguard'
					? { kind, name: name.trim(), wg_easy: wg.wg_easy, user: wg.user }
					: {
							kind,
							name: name.trim(),
							mode: ovpn.mode,
							pki: ovpn.pki,
							server: ovpn.server || null,
							days: ovpn.days,
							instance: ovpn.instance.trim() || null
						};
			if (version === null) throw new Error('config.yaml is still being read; try again');
			await api.putVpn(id, value, version, isNew);
			// The settings first: if they are refused, the working password isn't touched.
			if (kind === 'wireguard' && password) {
				const secret =
					start?.kind === 'wireguard' ? start.password_secret : isNew ? `wg-easy-${id}` : 'wg-easy';
				await api.putSecret(secret, password);
				password = '';
			}
			await store.refresh();
			onsaved(id);
		} catch (err) {
			error = (err as Error).message;
		} finally {
			busy = false;
		}
	}
</script>

<form onsubmit={save} class="flex flex-col gap-4">
	<div class="grid gap-3 sm:grid-cols-2">
		<label class="field">
			Name
			<input class="input" bind:value={name} maxlength="60" required />
		</label>
		{#if isNew}
			<label class="field">
				Id
				<input
					class="input font-mono"
					bind:value={id}
					oninput={() => (idTouched = true)}
					pattern="[a-z0-9][a-z0-9_\-]*"
					maxlength="40"
					required
				/>
				<span class="hint">Its name in config.yaml; it stays.</span>
			</label>
		{/if}
	</div>

	{#if kind === 'wireguard'}
		<div class="grid gap-3 sm:grid-cols-2">
			<label class="field sm:col-span-2">
				wg-easy's address
				<input
					class="input"
					type="url"
					placeholder="http://sv1:51821"
					bind:value={wg.wg_easy}
					required
				/>
			</label>
			<label class="field">
				User <input class="input" bind:value={wg.user} required />
			</label>
			<label class="field">
				Password
				<input
					class="input"
					type="password"
					autocomplete="off"
					placeholder={passwordSet ? 'set; empty keeps it' : 'needed'}
					bind:value={password}
				/>
			</label>
		</div>
	{:else}
		<div class="grid gap-3 sm:grid-cols-2">
			<label class="field">
				The PKI's host
				<select class="input" bind:value={ovpn.pki} required>
					<option value="" disabled>choose (devices with an SSH login)</option>
					{#each sshDevices as d (d.id)}<option value={d.id}>{d.name}</option>{/each}
				</select>
			</label>
			<label class="field">
				The OpenVPN server (gets the revocation list)
				<select class="input" bind:value={ovpn.server}>
					<option value="">none</option>
					{#each sshDevices as d (d.id)}<option value={d.id}>{d.name}</option>{/each}
				</select>
			</label>
			<label class="field">
				New certificates last (days)
				<input class="input" type="number" min="1" max="3650" bind:value={ovpn.days} />
			</label>
			<label class="field">
				Its server on that device
				<input
					class="input font-mono"
					bind:value={ovpn.instance}
					placeholder="only if it runs several"
					pattern="[\w.\-]{'{'}1,64{'}'}"
				/>
				<span class="hint">
					The uci section or the file in /etc/openvpn, for who is connected; needed when the server
					runs more than one OpenVPN.
				</span>
			</label>
		</div>
		<div class="flex flex-col gap-1.5 text-sm">
			<label class="choice">
				<input type="radio" bind:group={ovpn.mode} value="restricted" />
				<span>
					Restricted <span class="text-xs text-muted">
						— a key that may only run the PKI and CRL scripts (set up on the hosts)
					</span>
				</span>
			</label>
			<label class="choice">
				<input type="radio" bind:group={ovpn.mode} value="direct" />
				<span>
					Direct <span class="text-xs text-muted">
						— through the hosts' normal SSH logins, nothing installed
					</span>
				</span>
			</label>
		</div>
		{#if sshDevices.length === 0}
			<p class="hint" style:color="var(--warning-text)">
				No device has an SSH login yet: give the PKI's host one on its page first.
			</p>
		{/if}
	{/if}

	{#if error}
		<p class="text-sm" style:color="var(--critical-text)" role="alert">{error}</p>
	{/if}
	<div class="flex flex-wrap gap-2">
		<button class="btn btn-primary" disabled={busy || !ready || version === null}>
			{busy ? 'Saving…' : isNew ? 'Add this VPN' : 'Save'}
		</button>
		<button type="button" class="btn" onclick={oncancel}>Cancel</button>
	</div>
</form>
