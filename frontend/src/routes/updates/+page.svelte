<script lang="ts">
	import { api } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { updatesOf, type Update } from '#lib/updates.js';
	import KindIcon from '#lib/components/KindIcon.svelte';
	import FirmwareDialog from '#lib/components/FirmwareDialog.svelte';
	import Check from '@lucide/svelte/icons/check';

	/** Every update waiting on the network, in one list: what, where, from what to what, and the
	 * action, where Kastellan may take it (the device's actor or OpenWrt login allows it). */
	const updates = $derived(updatesOf(store.devices ?? []));
	const canAct = $derived(store.me?.role === 'admin' && !!store.me.user);

	let busy = $state<string | null>(null);
	let notes = $state<Record<string, { ok: boolean; text: string }>>({});
	let confirm = $state<string | null>(null);
	let firmware = $state<Extract<Update, { kind: 'firmware' }> | null>(null);

	const keyOf = (u: Update) => `${u.device.id}:${u.kind}:${u.kind === 'container' ? u.name : ''}`;

	async function run(u: Update, fn: () => Promise<string>) {
		const key = keyOf(u);
		busy = key;
		confirm = null;
		try {
			notes[key] = { ok: true, text: await fn() };
			await store.refresh();
		} catch (e) {
			notes[key] = { ok: false, text: (e as Error).message };
		} finally {
			busy = null;
		}
	}

	function act(u: Update) {
		if (u.kind === 'container')
			return run(u, async () => {
				const r = await api.act(u.device.id, 'update-container', u.name);
				return r.updated
					? `Updated (${r.from} → ${r.to}).`
					: `Nothing to do: ${r.why ?? 'newest'}.`;
			});
		if (u.kind === 'packages')
			return run(u, async () => {
				const r = await api.act(u.device.id, 'upgrade-packages');
				const n = r.upgraded ?? 0;
				return `${n} ${n === 1 ? 'package' : 'packages'} updated${r.reboot ? '; it wants a reboot now' : ''}.`;
			});
		if (u.kind === 'reboot')
			return run(u, async () => {
				await api.act(u.device.id, 'reboot');
				return 'Rebooting in a minute.';
			});
		if (u.kind === 'firmware') firmware = u;
	}

	const LABEL: Record<Update['kind'], string> = {
		container: 'Update',
		packages: 'Install',
		reboot: 'Reboot',
		firmware: 'Update firmware',
		'router-packages': '',
		scripts: ''
	};
	// Whether Kastellan may do it on that device at all (the host decides in the end).
	const possible = (u: Update) =>
		u.kind === 'firmware'
			? !!u.device.openwrt
			: u.kind === 'router-packages'
				? false
				: !!u.device.act;
	// The actor pulls the container's own tag again: a newer build of it, not a new tag.
	const newTag = (u: Update) => u.kind === 'container' && u.to !== 'newer build';
	const admin = $derived(store.me?.role === 'admin');
	const counts = $derived({
		devices: new Set(updates.map((u) => u.device.id)).size,
		security: updates.filter((u) => u.kind === 'packages' && u.security).length
	});
</script>

<svelte:head><title>Updates · Kastellan</title></svelte:head>

<h1 class="text-2xl font-semibold tracking-tight">Updates</h1>
<p class="mt-1 max-w-2xl text-sm text-muted">
	What is waiting to be updated on the network: containers, system packages, reboots after updates,
	router firmware. Kastellan installs only where a device allows it, and nothing by itself (except a
	fix for a known vulnerability on an internet-facing device, where that's switched on).
</p>

{#if store.devices === null}
	<div class="card mt-6 h-48 animate-pulse"></div>
{:else if updates.length === 0}
	<div class="card mt-6 flex items-center gap-3 px-5 py-8 text-sm text-muted">
		<Check class="size-5" style="color: var(--good-text)" />
		Nothing waiting, as far as Kastellan can see. (It sees containers and packages where a collector is
		set up, and OpenWrt devices through their login.)
	</div>
{:else}
	<p class="mt-6 text-sm">
		<b>{updates.length}</b>
		{updates.length === 1 ? 'update' : 'updates'} on {counts.devices}
		{counts.devices === 1 ? 'device' : 'devices'}{#if counts.security}, <span
				style:color="var(--warning-text)"
			>
				security updates on {counts.security}
				{counts.security === 1 ? 'host' : 'hosts'}
			</span>{/if}.
	</p>
	<ul class="card mt-3 divide-y divide-line">
		{#each updates as u (keyOf(u))}
			{@const key = keyOf(u)}
			<li class="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
				<a href="/devices/{u.device.id}" class="flex w-48 min-w-0 items-center gap-2.5 text-sm">
					<KindIcon kind={u.device.kind} class="size-4 shrink-0 text-muted" />
					<span class="truncate font-medium hover:underline">{u.device.name}</span>
				</a>
				<div class="min-w-0 flex-1 text-sm">
					{#if u.kind === 'container'}
						Container <span class="font-mono text-xs">{u.name}</span>
						<span class="text-muted">
							<span class="font-mono text-xs">{u.from}</span> →
							<span class="font-mono text-xs">{u.to}</span>
						</span>
					{:else if u.kind === 'packages'}
						{u.count} system {u.count === 1 ? 'package' : 'packages'}
						{#if u.security}<span class="text-xs" style:color="var(--warning-text)">
								· {u.security} security
							</span>{/if}
					{:else if u.kind === 'reboot'}
						Wants a reboot
						{#if u.why.length}<span class="text-xs text-muted">
								· for {u.why.slice(0, 3).join(', ')}{u.why.length > 3 ? ', …' : ''}
							</span>{/if}
					{:else if u.kind === 'firmware'}
						OpenWrt <span class="text-muted">{u.from} → {u.to}</span>
						{#if !u.supported}<span class="text-xs" style:color="var(--warning-text)">
								· gets no more security fixes
							</span>{/if}
					{:else if u.kind === 'scripts'}
						Kastellan's {u.names.join(' and ')} there
						<span class="text-xs text-muted">· older than this Kastellan</span>
					{:else}
						{u.names.length} OpenWrt {u.names.length === 1 ? 'package' : 'packages'}
						<span class="text-xs text-muted">· come with the next firmware</span>
					{/if}
					{#if notes[key]}
						<span
							class="block text-xs"
							style:color={notes[key].ok ? 'var(--good-text)' : 'var(--critical-text)'}
						>
							{notes[key].text}
						</span>
					{/if}
				</div>
				{#if LABEL[u.kind]}
					<div class="flex items-center gap-2">
						{#if newTag(u)}
							<span
								class="text-xs text-muted"
								title="Kastellan pulls the tag the container runs; a new tag is chosen where it is set up (compose, run command)"
							>
								change the tag on the host
							</span>
						{:else if !possible(u)}
							<a class="text-xs text-muted hover:text-text" href="/devices/{u.device.id}">
								{u.kind === 'firmware' ? 'needs its OpenWrt login' : 'needs the actor there'}
							</a>
						{:else if !canAct}
							<span class="text-xs text-muted">{admin ? 'needs sign-in' : 'admins only'}</span>
						{:else if confirm === key}
							<span class="text-xs">{u.kind === 'reboot' ? 'Reboot it now?' : 'Go ahead?'}</span>
							<button
								class="btn btn-sm btn-primary"
								disabled={busy !== null}
								onclick={() => act(u)}
							>
								{LABEL[u.kind]}
							</button>
							<button class="btn btn-sm" onclick={() => (confirm = null)}>Not now</button>
						{:else}
							<button
								class="btn btn-sm"
								disabled={busy !== null}
								onclick={() => (u.kind === 'firmware' ? act(u) : (confirm = key))}
							>
								{busy === key ? 'Working…' : LABEL[u.kind]}
							</button>
						{/if}
					</div>
				{:else if u.kind === 'scripts'}
					<a
						class="text-xs text-muted hover:text-text"
						href="/devices/{u.device.id}#setup"
						title="Kastellan's keys may only run these scripts, not replace them: renewing takes a login on the host"
					>
						set up again there
					</a>
				{/if}
			</li>
		{/each}
	</ul>
{/if}

{#if firmware && firmware.device.router?.firmware}
	<FirmwareDialog
		device={firmware.device.id}
		advice={firmware.device.router.firmware}
		model={firmware.device.router.board.model}
		onclose={() => (firmware = null)}
	/>
{/if}
