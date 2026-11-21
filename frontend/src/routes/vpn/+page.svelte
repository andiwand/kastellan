<script lang="ts">
	import FritzWireguard from '#lib/components/FritzWireguard.svelte';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import Tabs from '#lib/components/Tabs.svelte';
	import { api, type OvpnClient, type Vpn, type VpnServer, type WgClient } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { ago } from '#lib/format.js';
	import Download from '@lucide/svelte/icons/download';
	import QrCode from '@lucide/svelte/icons/qr-code';
	import Plus from '@lucide/svelte/icons/plus';
	import VpnSetup from '#lib/components/VpnSetup.svelte';
	import VpnForm from '#lib/components/VpnForm.svelte';
	import VpnKey from '#lib/components/VpnKey.svelte';
	import VpnServers from '#lib/components/VpnServers.svelte';

	/** The VPNs whose clients Kastellan manages (any number, of either kind): a card each, with
	 * its clients, giving and taking access, its settings and its setup; and adding another. */
	let vpns = $state<Vpn[] | null>(null);
	let servers = $state<VpnServer[] | null>(null);
	let error = $state<string | null>(null);
	let busy = $state(false);
	let adding = $state<Vpn['kind'] | 'choose' | null>(null);
	let editing = $state<string | null>(null); // a VPN id whose settings are open
	let qr = $state<string | null>(null); // "<vpn>/<client id>" whose QR code is shown
	let confirm = $state<string | null>(null); // what awaits a second click
	let newName = $state<Record<string, string>>({});
	let newDays = $state<Record<string, number | null>>({});

	const admin = $derived(store.me?.role === 'admin' && (store.me.user || !store.me.signin));
	// Clients: who has access, and since when (for everyone); Setup: the VPNs themselves, their
	// hosts and Kastellan's VPN key (admins).
	const canSetUp = $derived(store.me?.role === 'admin');
	const tab = $derived(
		canSetUp && page.url.searchParams.get('tab') === 'setup' ? 'setup' : 'clients'
	);
	function show(t: 'clients' | 'setup') {
		goto(t === 'setup' ? '?tab=setup' : '?', { replaceState: true });
	}
	const canManage = $derived(store.me?.role === 'admin' && !!store.me.user);
	const canEdit = $derived(canManage && store.editBlocked === null);
	const names = $derived(Object.fromEntries((vpns ?? []).map((v) => [v.id, v.name])));
	const restricted = $derived(
		(vpns ?? []).some((v) => v.kind === 'openvpn' && v.mode === 'restricted')
	);

	const KINDS = {
		wireguard: {
			name: 'WireGuard',
			via: 'wg-easy',
			about: 'Kastellan adds and removes clients in wg-easy, through its login.'
		},
		openvpn: {
			name: 'OpenVPN',
			via: 'easy-rsa',
			about:
				'Kastellan issues and revokes client certificates in an easy-rsa PKI on one of your hosts, and brings the revocation list to the server.'
		}
	} as const;

	async function load() {
		try {
			const d = await api.vpn();
			vpns = d.vpns;
			servers = d.servers ?? [];
			error = null;
		} catch (e) {
			error = (e as Error).message;
		}
	}
	async function act(fn: () => Promise<unknown>) {
		busy = true;
		error = null;
		try {
			await fn();
			confirm = null;
			await load();
		} catch (e) {
			error = (e as Error).message;
		} finally {
			busy = false;
		}
	}
	onMount(load);

	// config.yaml as the removal was asked for: a change made meanwhile refuses it.
	let removeVersion = $state<string | null>(null);
	async function askRemove(v: Vpn) {
		confirm = `vpn:${v.id}`;
		removeVersion = null;
		try {
			removeVersion = (await api.settings()).version;
		} catch (e) {
			error = (e as Error).message;
		}
	}
	async function remove(v: Vpn) {
		if (removeVersion === null) return;
		const version = removeVersion;
		await act(() => api.deleteVpn(v.id, version));
	}

	function bytes(n: number | null) {
		if (n == null) return '–';
		const units = ['B', 'kB', 'MB', 'GB', 'TB'];
		let i = 0;
		while (n >= 1000 && i < units.length - 1) {
			n /= 1000;
			i++;
		}
		return `${n < 10 && i ? n.toFixed(1) : Math.round(n)} ${units[i]}`;
	}
	const wgClients = (v: Vpn) => v.clients as WgClient[];
	const ovpnClients = (v: Vpn) => v.clients as OvpnClient[];
	const input =
		'h-9 rounded-lg border border-line bg-surface px-3 text-sm outline-none focus:border-accent';
	const button =
		'inline-flex h-8 items-center gap-1.5 rounded-lg border border-line px-2.5 text-xs font-medium hover:border-line-strong disabled:opacity-50';
</script>

<svelte:head><title>VPN · Kastellan</title></svelte:head>

<div class="flex flex-wrap items-baseline justify-between gap-3">
	<h1 class="text-2xl font-semibold tracking-tight">VPN</h1>
	{#if tab === 'setup' && canEdit && vpns?.length && adding === null}
		<button class="btn btn-sm" onclick={() => (adding = 'choose')}>
			<Plus class="size-3.5" /> Add a VPN
		</button>
	{/if}
</div>
<p class="mt-1 text-sm text-muted">
	Who can reach the network from outside, and how. Kastellan manages each VPN's clients; it doesn't
	run the VPN itself.
</p>

{#if servers}<VpnServers {servers} {names} />{/if}

{#each (store.devices ?? []).filter((d) => d.fritz?.wireguard) as d (d.id)}
	<section class="card mt-5 p-4">
		<h2 class="text-sm font-semibold">
			WireGuard on <a class="hover:underline" href="/devices/{d.id}">{d.name}</a>
		</h2>
		<div class="mt-1"><FritzWireguard wg={d.fritz!.wireguard!} /></div>
		<p class="mt-2 text-xs text-faint">
			Read from the FRITZ!Box; connections are added in FRITZ!OS (Internet → Permit Access → VPN
			(WireGuard)).
		</p>
	</section>
{/each}

{#if canSetUp}
	<div class="mt-5">
		<Tabs
			tabs={[
				{ id: 'clients', label: 'Clients' },
				{ id: 'setup', label: 'Setup' }
			]}
			current={tab}
			onselect={show}
		/>
	</div>
{/if}

{#if error}
	<p
		class="mt-4 rounded-lg border border-line bg-surface px-4 py-3 text-sm"
		style:color="var(--critical-text)"
	>
		{error}
	</p>
{/if}

{#snippet kindChoice()}
	<div class="grid gap-3 sm:grid-cols-2">
		{#each Object.entries(KINDS) as [kind, k] (kind)}
			<button
				class="card group flex flex-col items-start gap-1 p-4 text-left transition-all hover:-translate-y-px hover:border-line-strong hover:shadow-pop"
				onclick={() => (adding = kind as Vpn['kind'])}
			>
				<span class="text-sm font-semibold">
					{k.name} <span class="font-normal text-faint">· {k.via}</span>
				</span>
				<span class="text-xs leading-relaxed text-muted">{k.about}</span>
				<span
					class="mt-2 inline-flex items-center gap-1 text-xs font-medium text-accent group-hover:underline"
				>
					Set up {k.name}
					<ChevronRight class="size-3.5" />
				</span>
			</button>
		{/each}
	</div>
{/snippet}

{#if vpns && vpns.length === 0 && adding === null}
	<div class="mt-6">
		{#if canEdit && tab === 'setup'}
			<p class="mb-3 text-sm text-muted">No VPN yet. Which kind is yours?</p>
			{@render kindChoice()}
		{:else if canEdit}
			<p class="text-sm text-muted">
				No VPN yet. <button class="underline" onclick={() => show('setup')}>Set one up</button>.
			</p>
		{:else}
			<p class="text-sm text-muted">No VPN set up.</p>
		{/if}
	</div>
{/if}

{#if tab !== 'setup'}
	<!-- adding a VPN is Setup's -->
{:else if adding === 'choose'}
	<section class="mt-6">
		<div class="mb-3 flex items-baseline justify-between gap-2">
			<h2 class="text-sm font-semibold">Add a VPN: which kind?</h2>
			<button class="btn btn-sm" onclick={() => (adding = null)}>Cancel</button>
		</div>
		{@render kindChoice()}
	</section>
{:else if adding}
	<section class="card mt-6 p-5">
		<h2 class="mb-4 text-sm font-semibold">
			Add a VPN: {KINDS[adding].name}
			<span class="font-normal text-faint">· {KINDS[adding].via}</span>
		</h2>
		<VpnForm
			kind={adding}
			onsaved={async () => {
				adding = null;
				await load();
			}}
			oncancel={() => (adding = null)}
		/>
	</section>
{/if}

{#each vpns ?? [] as v (v.id)}
	<section class="card mt-6 scroll-mt-4 p-5" id="vpn-{v.id}">
		<div class="flex flex-wrap items-baseline justify-between gap-2">
			<h2 class="text-base font-semibold">
				{v.name}
				<span class="text-sm font-normal text-faint">
					· {KINDS[v.kind].name} through {KINDS[v.kind].via}
				</span>
			</h2>
			{#if tab === 'setup' && canEdit && editing !== v.id}
				<div class="flex flex-wrap gap-1.5">
					<button class="btn btn-sm" onclick={() => (editing = v.id)}>Settings</button>
					{#if confirm === `vpn:${v.id}`}
						<button
							class="btn btn-sm"
							style:color="var(--critical-text)"
							disabled={busy || removeVersion === null}
							onclick={() => remove(v)}
						>
							Remove {v.name}?
						</button>
						<button class="btn btn-sm" onclick={() => (confirm = null)}>Keep</button>
						{#if v.kind === 'openvpn' && v.settings?.kind === 'openvpn' && v.settings.mode === 'restricted'}
							<span class="basis-full text-xs text-muted">
								The VPN and its clients stay. Kastellan's VPN key stays bound to its scripts on the
								PKI host and the server, and is no longer rotated there: take its line out of the
								restricted logins' authorized_keys there if it shouldn't stay.
							</span>
						{/if}
					{:else}
						<button
							class="btn btn-sm text-muted"
							title="Kastellan stops managing its clients; the VPN and its clients stay as they are"
							onclick={() => askRemove(v)}
						>
							Remove
						</button>
					{/if}
				</div>
			{/if}
		</div>
		{#if v.error}<p class="mt-2 text-xs" style:color="var(--warning-text)">{v.error}</p>{/if}

		{#if tab === 'setup' && editing === v.id}
			<div class="mt-4 rounded-xl border border-line p-4">
				<VpnForm
					kind={v.kind}
					vpn={v}
					onsaved={async () => {
						editing = null;
						await load();
					}}
					oncancel={() => (editing = null)}
				/>
			</div>
		{/if}

		{#if tab === 'setup'}
			{#if v.kind === 'openvpn' && canManage}
				<!-- Keyed: changed hosts or mode give fresh commands (not the ones read before). -->
				{#key JSON.stringify(v.settings ?? null)}
					<VpnSetup vid={v.id} canInstall={canManage} />
				{/key}
			{:else if editing !== v.id}
				<p class="mt-2 text-xs text-muted">
					{v.kind === 'wireguard'
						? 'Kastellan reaches wg-easy with the address and password in Settings; nothing to set up on a host.'
						: 'Sign in to set up its hosts.'}
				</p>
			{/if}
		{:else if v.kind === 'wireguard'}
			{#if v.insecure}
				<p class="mt-2 text-xs" style:color="var(--warning-text)">
					wg-easy is reached over plain HTTP on another machine, so its admin password crosses the
					network with every request. Let Kastellan reach it on localhost or a shared Docker
					network, and don't publish its web port.
				</p>
			{/if}
			<ul class="mt-4 divide-y divide-line rounded-xl border border-line">
				{#each wgClients(v) as c (c.id)}
					<li class="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 text-sm">
						<span
							class="size-2 rounded-full"
							style:background={c.enabled ? 'var(--good)' : 'var(--offline)'}
						></span>
						<div class="min-w-0 flex-1">
							<p class="truncate font-medium">
								{c.name}{#if !c.enabled}<span class="text-xs font-normal text-muted">
										· off
									</span>{/if}
							</p>
							<p class="tabular text-xs text-muted">
								{c.address ?? ''}
								{#if c.last_handshake && store.now - Date.parse(c.last_handshake) < 180_000}
									· <span style:color="var(--good-text)">connected</span>{:else if c.last_handshake}
									· last seen {ago(c.last_handshake, store.now)}{:else}
									· never connected{/if}
								{#if c.rx != null}
									· ↓ {bytes(c.rx)} ↑ {bytes(c.tx)}{/if}
								{#if c.expires}
									· until {c.expires.slice(0, 10)}{/if}
							</p>
						</div>
						{#if canManage}
							<div class="flex flex-wrap gap-1.5">
								<a class={button} href="/api/vpns/{v.id}/wireguard/{c.id}/config" download>
									<Download class="size-3.5" /> Config
								</a>
								<button
									class={button}
									onclick={() => (qr = qr === `${v.id}/${c.id}` ? null : `${v.id}/${c.id}`)}
								>
									<QrCode class="size-3.5" /> QR
								</button>
								<button
									class={button}
									disabled={busy}
									onclick={() => act(() => api.wgEnable(v.id, c.id, !c.enabled))}
								>
									{c.enabled ? 'Turn off' : 'Turn on'}
								</button>
								{#if confirm === `wg:${v.id}/${c.id}`}
									<button
										class={button}
										style:color="var(--critical-text)"
										disabled={busy}
										onclick={() => act(() => api.wgDelete(v.id, c.id))}
									>
										Delete {c.name}?
									</button>
									<button class={button} onclick={() => (confirm = null)}>Keep</button>
								{:else}
									<button class={button} onclick={() => (confirm = `wg:${v.id}/${c.id}`)}>
										Delete
									</button>
								{/if}
							</div>
						{/if}
						{#if qr === `${v.id}/${c.id}`}
							<div class="w-full">
								<img
									src="/api/vpns/{v.id}/wireguard/{c.id}/qr"
									alt="QR code for {c.name}"
									class="size-56 rounded-lg bg-white p-2"
								/>
								<p class="mt-1 text-xs text-muted">
									Scan with the WireGuard app. Anyone who sees it can use this access.
								</p>
							</div>
						{/if}
					</li>
				{:else}
					<li class="px-4 py-6 text-center text-sm text-muted">No clients yet.</li>
				{/each}
			</ul>
		{:else}
			{#if v.crl_to_server}
				<div class="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
					{#if v.crl?.error}
						<span style:color="var(--critical-text)">
							The revocation list didn't reach the server: {v.crl.error}. Revoked clients may still
							connect.
						</span>
					{:else if v.crl?.pushed}
						<span>
							Revocation list on the server since {ago(v.crl.pushed, store.now)}{v.crl.next_update
								? `, valid until ${v.crl.next_update.slice(0, 10)}`
								: ''}; renewed daily.
						</span>
					{:else}
						<span>The revocation list is renewed and sent to the server daily.</span>
					{/if}
					{#if canManage}
						<button class={button} disabled={busy} onclick={() => act(() => api.ovpnPushCrl(v.id))}>
							Send it now
						</button>
					{/if}
				</div>
			{:else if canManage}
				<p class="mt-2 text-xs" style:color="var(--warning-text)">
					No OpenVPN server chosen (its Settings): a revoked client keeps working until the
					revocation list reaches the server.
				</p>
			{/if}
			{#if canManage && v.seen?.how === 'not-allowed'}
				<p class="mt-2 text-xs text-muted">
					Who is connected isn't known: Kastellan's login on
					<a class="underline" href="/devices/{v.seen.device}">the OpenVPN server</a> may not read it.
					Set that login up again there with “See who is connected to its OpenVPN server” ticked.
				</p>
			{:else if canManage && v.seen?.how === 'no-openwrt' && v.crl_to_server}
				<p class="mt-2 text-xs text-muted">
					Who is connected is read from an OpenVPN server on OpenWrt, through Kastellan's login
					there; this server has none.
				</p>
			{:else if canManage && v.seen?.error}
				<p class="mt-2 text-xs" style:color="var(--warning-text)">
					Who is connected can't be read right now: {v.seen.error}
				</p>
			{/if}
			<ul class="mt-4 divide-y divide-line rounded-xl border border-line">
				{#each ovpnClients(v) as c (c.name + c.serial)}
					<li
						class="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 text-sm"
						class:opacity-60={c.status !== 'valid'}
					>
						<span
							class="size-2 rounded-full"
							style:background={c.status === 'valid' ? 'var(--good)' : 'var(--offline)'}
						></span>
						<div class="min-w-0 flex-1">
							<p class="truncate font-medium">{c.name}</p>
							<p class="tabular text-xs text-muted">
								{c.status}{c.expires ? ` · until ${c.expires}` : ''}
								{#if c.connected}
									· <span style:color="var(--good-text)">connected</span>
									{c.connected.address ? `from ${c.connected.address}` : ''}
									{c.connected.since
										? `since ${ago(c.connected.since, store.now).replace(' ago', '')}`
										: ''}
									{#if c.connected.rx != null}
										· ↓ {bytes(c.connected.rx)} ↑ {bytes(c.connected.tx)}{/if}
								{:else if c.last_seen}
									· last seen {ago(c.last_seen, store.now)}
								{:else if v.seen?.how === 'openwrt' && v.seen.checked && !v.seen.error}
									· not seen yet
								{/if}
							</p>
						</div>
						{#if canManage && c.status === 'valid'}
							<div class="flex flex-wrap gap-1.5">
								<a
									class={button}
									href="/api/vpns/{v.id}/openvpn/{encodeURIComponent(c.name)}/config"
									download
								>
									<Download class="size-3.5" /> .ovpn
								</a>
								{#if confirm === `ovpn:${v.id}/${c.name}`}
									<button
										class={button}
										style:color="var(--critical-text)"
										disabled={busy}
										onclick={() =>
											act(async () => {
												const r = await api.ovpnRevoke(v.id, c.name);
												if (!r.crl_pushed)
													throw new Error(
														`${c.name} is revoked, but the server didn't get the new list yet (${r.error ?? 'unknown'}). Send it again.`
													);
											})}
									>
										Revoke {c.name}?
									</button>
									<button class={button} onclick={() => (confirm = null)}>Keep</button>
								{:else}
									<button class={button} onclick={() => (confirm = `ovpn:${v.id}/${c.name}`)}>
										Revoke
									</button>
								{/if}
							</div>
						{/if}
					</li>
				{:else}
					<li class="px-4 py-6 text-center text-sm text-muted">No clients yet.</li>
				{/each}
			</ul>
		{/if}

		{#if tab === 'clients' && canManage}
			<form
				class="mt-3 flex flex-wrap items-center gap-2"
				onsubmit={(e) => {
					e.preventDefault();
					const name = (newName[v.id] ?? '').trim();
					const days = newDays[v.id] ?? null;
					act(async () => {
						if (v.kind === 'wireguard') await api.wgCreate(v.id, name, days);
						else await api.ovpnIssue(v.id, name, days);
						newName[v.id] = '';
						newDays[v.id] = null;
					});
				}}
			>
				<input
					class={input}
					bind:value={newName[v.id]}
					placeholder={v.kind === 'wireguard'
						? 'New client, e.g. andreas-phone'
						: 'New client, e.g. andreas-laptop'}
					required
					maxlength="64"
					pattern={v.kind === 'openvpn' ? '[a-z0-9][a-z0-9_.\\-]{0,63}' : undefined}
				/>
				<input
					class="{input} w-32"
					type="number"
					min="1"
					max="3650"
					bind:value={newDays[v.id]}
					placeholder={v.kind === 'wireguard' ? 'Days (all)' : 'Days'}
				/>
				<button class={button} disabled={busy || !(newName[v.id] ?? '').trim()}>
					<Plus class="size-3.5" />
					{v.kind === 'wireguard' ? 'Add' : 'Issue'}
				</button>
			</form>
		{/if}
	</section>
{/each}

{#if tab === 'setup' && canManage}
	<div class="mt-6"><VpnKey canChange={canManage} {names} {restricted} /></div>
{/if}

{#if admin && !canManage}
	<p class="mt-6 text-xs text-muted">Giving and taking VPN access needs sign-in to be on.</p>
{/if}
