<script lang="ts">
	import { api, type GroupSurvey } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import JobDialog from '#lib/components/JobDialog.svelte';

	/** Groups of OpenWrt devices (`groups:` in config.yaml): their Wi-Fi networks lined up by
	 * name, the mesh, root's password; each changed on every member at once. The devices are
	 * the truth: nothing here is stored, it is read from them. */
	let groups = $state<{ id: string; name: string; devices: string[] }[] | null>(null);
	let surveys = $state<Record<string, GroupSurvey | { error: string }>>({});
	let error = $state<string | null>(null);

	type Open =
		| { kind: 'network'; group: string; ssid: string }
		| { kind: 'mesh'; group: string; survey: GroupSurvey; existing: boolean }
		| { kind: 'root'; group: string };
	let open = $state<Open | null>(null);

	// The forms' fields (one dialog at a time).
	let newSsid = $state('');
	let key = $state('');
	let key2 = $state('');
	let encryption = $state('');
	let meshId = $state('');
	let radios = $state<Record<string, string>>({});
	let channel = $state('');

	function close() {
		// Typed passwords don't stay around after the dialog.
		key = key2 = '';
		open = null;
	}

	async function load() {
		error = null;
		try {
			groups = await api.groups();
			await Promise.all(groups.map((g) => survey(g.id)));
		} catch (e) {
			error = (e as Error).message;
		}
	}

	async function survey(id: string) {
		try {
			surveys[id] = await api.group(id);
		} catch (e) {
			surveys[id] = { error: (e as Error).message };
		}
	}

	$effect(() => {
		if (store.me?.role === 'admin' && groups === null) load();
	});

	function show(o: Open) {
		newSsid = key = key2 = encryption = '';
		meshId = o.kind === 'mesh' && o.survey.mesh.length === 1 ? o.survey.mesh[0].mesh_id : '';
		radios = {};
		channel = '';
		open = o;
	}

	const name = (id: string) => store.byId.get(id)?.name ?? id;
	// Whether a network is open now (no password): a password alone would do nothing.
	function openNow(group: string, ssid: string): boolean {
		const s = surveys[group];
		if (!s || 'error' in s) return false;
		const net = s.networks.find((n) => n.ssid === ssid);
		return !!net?.on.some((e) => ['none', 'owe', null].includes(e.encryption));
	}
	const canChange = $derived(store.me?.role === 'admin' && !!store.me.user);
	const keyOk = (k: string) => k.length >= 8 && k.length <= 63;
	const input =
		'h-9 w-full rounded-lg border border-line bg-surface px-3 text-sm outline-none focus:border-accent';
	const label = 'flex flex-col gap-1 text-xs font-medium text-muted';
	const small =
		'inline-flex h-8 items-center rounded-lg border border-line px-3 text-xs font-medium hover:border-line-strong disabled:opacity-50';
	const ENCRYPTIONS = [
		['sae', 'WPA3'],
		['sae-mixed', 'WPA2/WPA3'],
		['psk2', 'WPA2'],
		['psk-mixed', 'WPA/WPA2'],
		['owe', 'Open, encrypted (OWE)'],
		['none', 'Open']
	];
	const ENCRYPTION_NAME = Object.fromEntries(ENCRYPTIONS);
</script>

<svelte:head><title>Groups · Kastellan</title></svelte:head>

<h1 class="text-2xl font-semibold tracking-tight">Groups</h1>
<p class="mt-1 max-w-2xl text-sm text-muted">
	OpenWrt devices whose Wi-Fi, mesh and root password are kept the same. Read from the devices
	themselves; a change goes to every member with its rollback (the mesh to all of them at once, so
	none is cut off). Passwords are only compared, never shown.
</p>

{#if error}<p class="mt-4 text-sm" style:color="var(--critical-text)">{error}</p>{/if}
{#if groups && !groups.length}
	<div class="card mt-6 p-5 text-sm">
		<p>No groups yet. Add one to config.yaml (devices with Kastellan's OpenWrt login):</p>
		<pre class="mt-3 rounded-lg bg-surface-2 p-3 font-mono text-xs">groups:
  aps:
    name: Access points
    devices: [wifi1, wifi2, wifi3]</pre>
	</div>
{/if}

{#each groups ?? [] as g (g.id)}
	{@const s = surveys[g.id]}
	<section class="card mt-6 p-5">
		<div class="flex flex-wrap items-baseline justify-between gap-2">
			<h2 class="text-base font-semibold">{g.name}</h2>
			<p class="text-xs text-muted">
				{#each g.devices as id, i (id)}{i ? ', ' : ''}<a
						class="hover:underline"
						href="/devices/{id}"
					>
						{name(id)}
					</a>{/each}
				· <button class="underline" onclick={() => survey(g.id)}>read again</button>
			</p>
		</div>

		{#if !s}
			<p class="mt-3 text-sm text-muted">Reading the devices…</p>
		{:else if 'error' in s}
			<p class="mt-3 text-sm" style:color="var(--critical-text)">{s.error}</p>
		{:else}
			{#each Object.entries(s.errors) as [id, why] (id)}
				<p class="mt-2 text-xs" style:color="var(--warning-text)">
					{name(id)}: {why}{why.includes('not allowed') ? ' (tick Wi-Fi in its OpenWrt setup)' : ''}
				</p>
			{/each}

			<h3 class="mt-5 text-xs font-medium text-muted">Wi-Fi networks</h3>
			<ul class="mt-2 divide-y divide-line">
				{#each s.networks as net (net.ssid)}
					<li class="flex flex-wrap items-center gap-x-4 gap-y-1 py-2 text-sm">
						<span class="font-medium">{net.ssid}</span>
						<span class="text-xs text-muted">
							{net.devices.map(name).join(', ')}
							· {[...new Set(net.on.map((e) => e.band).filter(Boolean))].join(' + ')}
							· {[
								...new Set(net.on.map((e) => ENCRYPTION_NAME[e.encryption ?? ''] ?? e.encryption))
							].join(' / ')}
						</span>
						<span class="text-xs">
							{#if !net.same_key}<span style:color="var(--warning-text)">passwords differ</span>
							{:else}<span style:color="var(--good-text)">same password</span>{/if}
							{#if !net.same_encryption}<span style:color="var(--warning-text)">
									· encryption differs
								</span>{/if}
							{#if net.missing.length}<span class="text-muted">
									· not on {net.missing.map(name).join(', ')}
								</span>{/if}
						</span>
						{#if canChange}
							<button
								class="{small} ml-auto"
								onclick={() => show({ kind: 'network', group: g.id, ssid: net.ssid })}
							>
								Change on all
							</button>
						{/if}
					</li>
				{:else}
					<li class="py-2 text-sm text-muted">None read.</li>
				{/each}
			</ul>

			<h3 class="mt-5 text-xs font-medium text-muted">Mesh</h3>
			<ul class="mt-2 divide-y divide-line">
				{#each s.mesh as m (m.mesh_id)}
					<li class="flex flex-wrap items-center gap-x-4 gap-y-1 py-2 text-sm">
						<span class="font-medium">{m.mesh_id || '(no id)'}</span>
						<span class="text-xs text-muted">{m.devices.map(name).join(', ')}</span>
						<span class="text-xs">
							{#if !m.same_key}<span style:color="var(--warning-text)">passwords differ</span>
							{:else}<span style:color="var(--good-text)">same password</span>{/if}
							{#if m.missing.length}<span class="text-muted">
									· not on {m.missing.map(name).join(', ')}
								</span>{/if}
						</span>
						{#if canChange}
							<button
								class="{small} ml-auto"
								onclick={() => show({ kind: 'mesh', group: g.id, survey: s, existing: true })}
							>
								Set on all
							</button>
						{/if}
					</li>
				{:else}
					<li class="flex items-center py-2 text-sm text-muted">
						No mesh.
						{#if canChange}
							<button
								class="{small} ml-auto"
								onclick={() => show({ kind: 'mesh', group: g.id, survey: s, existing: false })}
							>
								Set one up
							</button>
						{/if}
					</li>
				{/each}
			</ul>

			{#if canChange}
				<div class="mt-5 flex items-center justify-between border-t border-line pt-4 text-sm">
					<span>
						<span class="text-xs font-medium text-muted">Root's password</span><br />
						<span class="text-xs text-muted">
							The same on every member (their "password" ability).
						</span>
					</span>
					<button class={small} onclick={() => show({ kind: 'root', group: g.id })}>
						Set on all
					</button>
				</div>
			{/if}
		{/if}
	</section>
{/each}

{#if open?.kind === 'network'}
	{@const o = open}
	<JobDialog
		title="Wi-Fi network “{o.ssid}” on every member"
		path="/groups/{encodeURIComponent(o.group)}/network"
		action="Change on all"
		body={() => {
			if (key && (!keyOk(key) || key !== key2)) return null;
			if (key && !encryption && openNow(o.group, o.ssid)) return null;
			if (!newSsid && !key && !encryption) return null;
			return {
				ssid: o.ssid,
				new_ssid: newSsid || null,
				key: key || null,
				encryption: encryption || null
			};
		}}
		onclose={close}
		ondone={() => survey(o.group)}
	>
		<p class="text-xs text-muted">
			Empty fields stay as they are. Devices whose network has another name are left alone. Their
			clients reconnect (with the new password, if it changes).
		</p>
		<label class={label}>
			New name <input class={input} bind:value={newSsid} placeholder={o.ssid} maxlength="32" />
		</label>
		<div class="grid gap-3 sm:grid-cols-2">
			<label class={label}>
				New password
				<input class={input} type="password" autocomplete="new-password" bind:value={key} />
			</label>
			<label class={label}>
				Again
				<input class={input} type="password" autocomplete="new-password" bind:value={key2} />
			</label>
		</div>
		{#if key && !keyOk(key)}<p class="text-xs" style:color="var(--warning-text)">
				8 to 63 characters.
			</p>{:else if key && key2 && key !== key2}<p
				class="text-xs"
				style:color="var(--warning-text)"
			>
				The two differ.
			</p>{/if}
		{#if key && !encryption && openNow(o.group, o.ssid)}
			<p class="text-xs" style:color="var(--warning-text)">
				It is open now: choose an encryption for the password to count.
			</p>
		{/if}
		<label class={label}>
			Encryption
			<select class={input} bind:value={encryption}>
				<option value="">as it is</option>
				{#each ENCRYPTIONS as [value, text] (value)}<option {value}>{text}</option>{/each}
			</select>
		</label>
	</JobDialog>
{/if}

{#if open?.kind === 'mesh'}
	{@const o = open}
	{@const s = o.survey}
	{@const meshOf = new Map(
		s.mesh.flatMap((m) => m.on.map((e) => [e.device, { ...e, id: m.mesh_id }]))
	)}
	{@const radioOf = (id: string, section: string | null) =>
		(s.radios[id] ?? []).find((r) => r.section === section)}
	{@const used = [
		...[...meshOf.entries()].map(([id, e]) => radioOf(id, e.radio)),
		...Object.entries(radios)
			.filter(([, r]) => r)
			.map(([id, r]) => radioOf(id, r))
	].filter((r) => r !== undefined)}
	{@const bands = [...new Set(used.map((r) => r.band))]}
	{@const channels = [...new Set(used.map((r) => String(r.channel ?? 'auto')))]}
	{@const needChannel = channels.length > 1 || channels.includes('auto')}
	{@const ch = Number(channel || channels[0])}
	<JobDialog
		title={o.existing ? 'The mesh on every member' : 'A mesh across the group'}
		path="/groups/{encodeURIComponent(o.group)}/mesh"
		action={o.existing ? 'Set on all' : 'Set up'}
		body={() =>
			meshId &&
			keyOk(key) &&
			key === key2 &&
			bands.length <= 1 &&
			(!needChannel || /^\d+$/.test(channel))
				? { mesh_id: meshId, key, radios: { ...radios }, channel: channel || null }
				: null}
		onclose={close}
		ondone={() => survey(o.group)}
	>
		<p class="text-xs text-muted">
			An 802.11s mesh with WPA3 (SAE), bridged into the LAN, so the access points reach each other
			over the air. A device's mesh gets this id and password; a device without one gets it on the
			radio you choose. It goes to every device at once and is kept only if all of them come back
			with it up; otherwise all undo it. (Access points that are also cabled to each other make a
			loop with it.)
		</p>
		<div class="grid gap-3 sm:grid-cols-3">
			<label class={label}>
				Mesh id <input class={input} bind:value={meshId} maxlength="32" required />
			</label>
			<label class={label}>
				Password
				<input
					class={input}
					type="password"
					autocomplete="new-password"
					maxlength="63"
					bind:value={key}
				/>
			</label>
			<label class={label}>
				Again
				<input
					class={input}
					type="password"
					autocomplete="new-password"
					maxlength="63"
					bind:value={key2}
				/>
			</label>
		</div>
		{#if key && !keyOk(key)}<p class="text-xs" style:color="var(--warning-text)">
				8 to 63 characters.
			</p>{:else if key && key2 && key !== key2}<p
				class="text-xs"
				style:color="var(--warning-text)"
			>
				The two differ.
			</p>{/if}
		<fieldset class="flex flex-col gap-2">
			<legend class="text-xs font-medium text-muted">Devices</legend>
			{#each s.members as id (id)}
				{@const has = meshOf.get(id)}
				<div class="flex flex-wrap items-center gap-2 text-sm">
					<span class="w-44 truncate">{name(id)}</span>
					{#if has}
						<span class="text-xs text-muted">
							its mesh “{has.id || 'no id'}” on {has.radio}: set to this
						</span>
					{:else if s.can_mesh[id] === false}
						<span class="text-xs" style:color="var(--warning-text)">
							its Wi-Fi software can't do mesh (install wpad-mesh-mbedtls)
						</span>
					{:else if s.errors[id]}
						<span class="text-xs text-muted">not read</span>
					{:else}
						<select
							class="h-8 rounded-lg border border-line bg-surface px-2 text-xs"
							value={radios[id] ?? ''}
							onchange={(e) => (radios[id] = e.currentTarget.value)}
						>
							<option value="">leave it out</option>
							{#each s.radios[id] ?? [] as r (r.section)}
								<option value={r.section}>
									add on {r.section} ({r.band ?? '?'}, channel {r.channel ?? 'auto'})
								</option>
							{/each}
						</select>
						{#if s.can_mesh[id] === null}<span class="text-xs text-faint">
								needs wpad-mesh or full wpad (it doesn't say)
							</span>{/if}
					{/if}
				</div>
			{/each}
		</fieldset>
		{#if bands.length > 1}
			<p class="text-xs" style:color="var(--warning-text)">
				The mesh radios are on different bands ({bands.join(', ')}); choose radios of one band.
			</p>
		{/if}
		<label class={label}>
			Channel for every mesh radio
			<input
				class={input}
				inputmode="numeric"
				bind:value={channel}
				placeholder={needChannel
					? 'needed: they differ or are on auto'
					: `keep ${channels[0] ?? ''}`}
			/>
		</label>
		{#if bands[0] === '5g' && ch >= 52 && ch <= 144}
			<p class="text-xs" style:color="var(--warning-text)">
				Channel {ch} is a DFS channel: radios listen first (a minute or more) and may have to move, which
				breaks the mesh. 36–48 are safer.
			</p>
		{/if}
	</JobDialog>
{/if}

{#if open?.kind === 'root'}
	{@const o = open}
	<JobDialog
		title="Root's password on every member"
		path="/groups/{encodeURIComponent(o.group)}/root-password"
		action="Set on all"
		body={() => (key.length >= 8 && key.length <= 128 && key === key2 ? { password: key } : null)}
		onclose={close}
	>
		<p class="text-xs text-muted">
			For logging in to LuCI and over SSH as root. Kastellan doesn't keep it. Devices without the
			"password" ability refuse (tick it in their OpenWrt setup).
		</p>
		<div class="grid gap-3 sm:grid-cols-2">
			<label class={label}>
				New password
				<input class={input} type="password" autocomplete="new-password" bind:value={key} />
			</label>
			<label class={label}>
				Again
				<input class={input} type="password" autocomplete="new-password" bind:value={key2} />
			</label>
		</div>
		{#if key && key.length < 8}<p class="text-xs" style:color="var(--warning-text)">
				At least 8 characters.
			</p>{:else if key && key2 && key !== key2}<p
				class="text-xs"
				style:color="var(--warning-text)"
			>
				The two differ.
			</p>{/if}
	</JobDialog>
{/if}
