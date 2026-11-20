<script lang="ts">
	import { onMount } from 'svelte';
	import { api, type Vpn } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { ago } from '#lib/format.js';
	import Download from '@lucide/svelte/icons/download';
	import QrCode from '@lucide/svelte/icons/qr-code';
	import Plus from '@lucide/svelte/icons/plus';
	import X from '@lucide/svelte/icons/x';

	let vpn = $state<Vpn | null>(null);
	let error = $state<string | null>(null);
	let busy = $state(false);
	let qr = $state<string | null>(null); // a WireGuard client id whose QR code is shown
	let confirm = $state<string | null>(null); // "wg:<id>" or "ovpn:<name>" awaiting a second click
	let wgName = $state('');
	let wgDays = $state<number | null>(null);
	let ovpnName = $state('');
	let ovpnDays = $state<number | null>(null);

	const admin = $derived(store.me?.role === 'admin' && (store.me.user || !store.me.signin));
	const canManage = $derived(store.me?.role === 'admin' && !!store.me.user);

	async function load() {
		try {
			vpn = await api.vpn();
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
	const input =
		'h-9 rounded-lg border border-line bg-surface px-3 text-sm outline-none focus:border-accent';
	const button =
		'inline-flex h-8 items-center gap-1.5 rounded-lg border border-line px-2.5 text-xs font-medium hover:border-line-strong disabled:opacity-50';
</script>

<svelte:head><title>VPN · Kastellan</title></svelte:head>

<h1 class="text-2xl font-semibold tracking-tight">VPN</h1>
<p class="mt-1 text-sm text-muted">Who can reach the home network from outside, and how.</p>

{#if error}
	<p
		class="mt-4 rounded-lg border border-line bg-surface px-4 py-3 text-sm"
		style:color="var(--critical-text)"
	>
		{error}
	</p>
{/if}

{#if vpn && !vpn.wireguard && !vpn.openvpn}
	<div class="card mt-6 px-6 py-8 text-sm leading-relaxed text-muted">
		<p class="font-medium text-text">No VPN set up in config.yaml.</p>
		<pre class="mt-3 rounded-lg bg-surface-2 p-3 font-mono text-[13px] text-text">vpn:
  wireguard:
    wg_easy: http://sv1:51821   # kastellan secret set wg-easy
  openvpn:
    pki: sv1          # a device with ssh:, running kastellan-pki
    server: openwrt   # a device with ssh:, running kastellan-crl</pre>
	</div>
{/if}

{#if vpn?.wireguard}
	<section class="mt-8">
		<div class="mb-3 flex flex-wrap items-baseline justify-between gap-2">
			<h2 class="text-sm font-semibold">
				WireGuard <span class="font-normal text-faint">· wg-easy</span>
			</h2>
			{#if vpn.wireguard.error}<p class="text-xs" style:color="var(--warning-text)">
					{vpn.wireguard.error}
				</p>{/if}
		</div>
		<ul class="card divide-y divide-line">
			{#each vpn.wireguard.clients as c (c.id)}
				<li class="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 text-sm">
					<span
						class="size-2 rounded-full"
						style:background={c.enabled ? 'var(--good)' : 'var(--offline)'}
					></span>
					<div class="min-w-0 flex-1">
						<p class="truncate font-medium">{c.name}</p>
						<p class="tabular text-xs text-muted">
							{c.address ?? ''}
							{#if c.last_handshake}
								· seen {ago(c.last_handshake, store.now)}{:else}
								· never connected{/if}
							{#if c.rx != null}
								· ↓ {bytes(c.rx)} ↑ {bytes(c.tx)}{/if}
							{#if c.expires}
								· until {c.expires.slice(0, 10)}{/if}
						</p>
					</div>
					{#if canManage}
						<div class="flex flex-wrap gap-1.5">
							<a class={button} href="/api/vpn/wireguard/{c.id}/config" download>
								<Download class="size-3.5" /> Config
							</a>
							<button class={button} onclick={() => (qr = qr === c.id ? null : c.id)}>
								<QrCode class="size-3.5" /> QR
							</button>
							<button
								class={button}
								disabled={busy}
								onclick={() => act(() => api.wgEnable(c.id, !c.enabled))}
							>
								{c.enabled ? 'Turn off' : 'Turn on'}
							</button>
							{#if confirm === `wg:${c.id}`}
								<button
									class={button}
									style:color="var(--critical-text)"
									disabled={busy}
									onclick={() => act(() => api.wgDelete(c.id))}
								>
									Delete {c.name}?
								</button>
								<button class={button} onclick={() => (confirm = null)}>Keep</button>
							{:else}
								<button class={button} onclick={() => (confirm = `wg:${c.id}`)}>Delete</button>
							{/if}
						</div>
					{/if}
					{#if qr === c.id}
						<div class="w-full">
							<img
								src="/api/vpn/wireguard/{c.id}/qr"
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
				<li class="px-4 py-6 text-center text-sm text-muted">No clients.</li>
			{/each}
		</ul>
		{#if canManage}
			<form
				class="mt-3 flex flex-wrap items-center gap-2"
				onsubmit={(e) => {
					e.preventDefault();
					act(async () => {
						await api.wgCreate(wgName.trim(), wgDays);
						wgName = '';
						wgDays = null;
					});
				}}
			>
				<input
					class={input}
					bind:value={wgName}
					placeholder="New client, e.g. andreas-phone"
					required
					maxlength="64"
				/>
				<input
					class="{input} w-32"
					type="number"
					min="1"
					max="3650"
					bind:value={wgDays}
					placeholder="Days (all)"
				/>
				<button class={button} disabled={busy || !wgName.trim()}>
					<Plus class="size-3.5" /> Add
				</button>
			</form>
		{/if}
	</section>
{/if}

{#if vpn?.openvpn}
	<section class="mt-8">
		<div class="mb-3 flex flex-wrap items-baseline justify-between gap-2">
			<h2 class="text-sm font-semibold">
				OpenVPN <span class="font-normal text-faint">· easy-rsa</span>
			</h2>
			{#if vpn.openvpn.error}<p class="text-xs" style:color="var(--warning-text)">
					{vpn.openvpn.error}
				</p>{/if}
		</div>
		<ul class="card divide-y divide-line">
			{#each vpn.openvpn.clients as c (c.name + c.serial)}
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
						<p class="text-xs text-muted">{c.status}{c.expires ? ` · until ${c.expires}` : ''}</p>
					</div>
					{#if canManage && c.status === 'valid'}
						<div class="flex flex-wrap gap-1.5">
							<a
								class={button}
								href="/api/vpn/openvpn/{encodeURIComponent(c.name)}/config"
								download
							>
								<Download class="size-3.5" /> .ovpn
							</a>
							{#if confirm === `ovpn:${c.name}`}
								<button
									class={button}
									style:color="var(--critical-text)"
									disabled={busy}
									onclick={() => act(() => api.ovpnRevoke(c.name))}
								>
									Revoke {c.name}?
								</button>
								<button class={button} onclick={() => (confirm = null)}>
									<X class="size-3.5" />
								</button>
							{:else}
								<button class={button} onclick={() => (confirm = `ovpn:${c.name}`)}>Revoke</button>
							{/if}
						</div>
					{/if}
				</li>
			{:else}
				<li class="px-4 py-6 text-center text-sm text-muted">No clients.</li>
			{/each}
		</ul>
		{#if canManage}
			<form
				class="mt-3 flex flex-wrap items-center gap-2"
				onsubmit={(e) => {
					e.preventDefault();
					act(async () => {
						await api.ovpnIssue(ovpnName.trim(), ovpnDays);
						ovpnName = '';
						ovpnDays = null;
					});
				}}
			>
				<input
					class={input}
					bind:value={ovpnName}
					placeholder="New client, e.g. andreas-laptop"
					required
					pattern="[a-z0-9][a-z0-9_.\-]{'{'}0,63{'}'}"
				/>
				<input
					class="{input} w-32"
					type="number"
					min="1"
					max="3650"
					bind:value={ovpnDays}
					placeholder="Days"
				/>
				<button class={button} disabled={busy || !ovpnName.trim()}>
					<Plus class="size-3.5" /> Issue
				</button>
			</form>
			{#if !vpn.openvpn.crl_to_server}
				<p class="mt-2 text-xs" style:color="var(--warning-text)">
					No vpn.openvpn.server set: a revoked client keeps working until the CRL reaches the
					server.
				</p>
			{/if}
		{/if}
	</section>
{/if}

{#if admin && !canManage}
	<p class="mt-6 text-xs text-muted">Giving and taking VPN access needs sign-in to be on.</p>
{/if}
