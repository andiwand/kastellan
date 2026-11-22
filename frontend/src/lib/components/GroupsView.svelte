<script lang="ts">
	import { api, type GroupSurvey } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import JobDialog from '#lib/components/JobDialog.svelte';
	import GroupDialog from '#lib/components/GroupDialog.svelte';
	import GroupSsh from '#lib/components/GroupSsh.svelte';
	import DeviceList from '#lib/components/DeviceList.svelte';
	import Section from '#lib/components/ui/Section.svelte';
	import Table, { type Column } from '#lib/components/ui/Table.svelte';
	import StateBadge from '#lib/components/ui/StateBadge.svelte';
	import StateDot from '#lib/components/ui/StateDot.svelte';
	import EmptyState from '#lib/components/ui/EmptyState.svelte';
	import { settings, copy } from '#lib/settings.svelte.js';
	import Plus from '@lucide/svelte/icons/plus';
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import Tabs from '#lib/components/Tabs.svelte';
	import MaintenanceButton from '#lib/components/MaintenanceButton.svelte';

	/** The Devices page's Groups tab (?section=groups). Groups of devices (`groups:` in
	 * config.yaml), any kind, each with its members as the devices list. Status: the members, and
	 * where their Wi-Fi is (/wifi shows the networks, mesh and roaming with what is off). Setup:
	 * for OpenWrt members, Wi-Fi networks lined up by name, the mesh, roaming assistance and
	 * root's password, each changed on all at once; for those with `ssh:`, their authorized_keys
	 * and known_hosts (GroupSsh). The devices are the truth: nothing of that is stored, it is read
	 * from them. */
	let groups = $state<
		{ id: string; name: string; devices: string[]; openwrt: string[]; ssh: string[] }[] | null
	>(null);
	let editing = $state<{ id: string | null } | null>(null);
	let deleting = $state<string | null>(null);
	let deleteError = $state<string | null>(null);

	async function remove(gid: string) {
		if (!settings.data) return;
		deleteError = null;
		try {
			const all = copy(settings.data.sections.groups.groups ?? {});
			delete all[gid];
			await settings.save('groups', { groups: all });
			deleting = null;
			await load();
		} catch (e) {
			deleteError = (e as Error).message;
		}
	}

	// Fresh once editing is allowed: on a reload that is known only after the hub answered.
	let asked = false;
	$effect(() => {
		if (!asked && store.editBlocked === null) {
			asked = true;
			untrack(() => settings.load());
		}
	});
	let surveys = $state<Record<string, GroupSurvey | { error: string }>>({});
	let error = $state<string | null>(null);

	type Open =
		| { kind: 'network'; group: string; ssid: string }
		| { kind: 'mesh'; group: string; survey: GroupSurvey; existing: boolean }
		| { kind: 'root'; group: string }
		| { kind: 'roaming'; group: string; survey: GroupSurvey };
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
			surveys = {};
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
	// The members are read (their Wi-Fi, mesh, radios) for Setup only: Status links to /wifi.
	$effect(() => {
		if (!setup || !groups) return;
		for (const g of groups) if (g.openwrt.length && !untrack(() => surveys[g.id])) survey(g.id);
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
	// Status: what the members say (read); Setup: the same with every way to change it.
	const setup = $derived(page.url.searchParams.get('tab') === 'setup');
	function showTab(t: 'status' | 'setup') {
		goto(t === 'setup' ? '?section=groups&tab=setup' : '?section=groups', { replace: true });
	}
	const keyOk = (k: string) => k.length >= 8 && k.length <= 63;
	const input =
		'h-9 w-full rounded-lg border border-line bg-surface px-3 text-sm outline-none focus:border-accent';
	const label = 'flex flex-col gap-1 text-xs font-medium text-muted';
	const small = 'btn btn-sm';
	const ENCRYPTIONS = [
		['sae', 'WPA3'],
		['sae-mixed', 'WPA2/WPA3'],
		['psk2', 'WPA2'],
		['psk-mixed', 'WPA/WPA2'],
		['owe', 'Open, encrypted (OWE)'],
		['none', 'Open']
	];
	const ENCRYPTION_NAME = Object.fromEntries(ENCRYPTIONS);

	type Net = GroupSurvey['networks'][number] & { group: string };
	type Mesh = GroupSurvey['mesh'][number] & { group: string; survey: GroupSurvey };
	const onText = (devices: string[], missing: string[]) =>
		devices.map(name).join(', ') +
		(missing.length ? ` · not on ${missing.map(name).join(', ')}` : '');
	const sameKey = (same: boolean) =>
		same ? { word: 'Same', tone: 'good' as const } : { word: 'Differs', tone: 'warning' as const };
	const networkColumns: Column<Net>[] = $derived([
		{ key: 'ssid', label: 'Network', cell: ssidCell },
		{ key: 'on', label: 'On', grow: true, priority: 2, value: (n) => onText(n.devices, n.missing) },
		{
			key: 'bands',
			label: 'Bands',
			priority: 3,
			value: (n) => [...new Set(n.on.map((e) => e.band).filter(Boolean))].join(' + ')
		},
		{
			key: 'encryption',
			label: 'Encryption',
			priority: 3,
			value: (n) =>
				[...new Set(n.on.map((e) => ENCRYPTION_NAME[e.encryption ?? ''] ?? e.encryption))].join(
					' / '
				)
		},
		{ key: 'key', label: 'Password', cell: netKeyCell, title: 'Compared, never shown' },
		...(canChange
			? [{ key: 'act', label: '', align: 'right', cell: netAction } as Column<Net>]
			: [])
	]);
	const meshColumns: Column<Mesh>[] = $derived([
		{ key: 'id', label: 'Mesh', value: (m) => m.mesh_id || '(no id)' },
		{ key: 'on', label: 'On', grow: true, priority: 2, value: (m) => onText(m.devices, m.missing) },
		{ key: 'key', label: 'Password', cell: meshKeyCell, title: 'Compared, never shown' },
		...(canChange
			? [{ key: 'act', label: '', align: 'right', cell: meshAction } as Column<Mesh>]
			: [])
	]);
</script>

{#snippet ssidCell(n: Net)}
	<span class="block truncate font-medium">{n.ssid}</span>
	<span class="block truncate text-meta text-muted sm:hidden">{onText(n.devices, n.missing)}</span>
{/snippet}
{#snippet netKeyCell(n: Net)}
	<span class="inline-flex flex-wrap items-center gap-1.5">
		<StateBadge state={sameKey(n.same_key)} quiet />
		{#if !n.same_encryption}<StateBadge word="Encryption differs" tone="warning" quiet />{/if}
	</span>
{/snippet}
{#snippet netAction(n: Net)}
	<button class={small} onclick={() => show({ kind: 'network', group: n.group, ssid: n.ssid })}>
		Change on all
	</button>
{/snippet}
{#snippet meshKeyCell(m: Mesh)}
	<StateBadge state={sameKey(m.same_key)} quiet />
{/snippet}
{#snippet meshAction(m: Mesh)}
	<button
		class={small}
		onclick={() => show({ kind: 'mesh', group: m.group, survey: m.survey, existing: true })}
	>
		Set on all
	</button>
{/snippet}

<div class="flex flex-wrap items-end justify-between gap-4">
	<div>
		<p class="max-w-2xl text-sm text-muted">
			Devices that belong together, any kind. For OpenWrt members, a group also keeps Wi-Fi, the
			mesh and root's password the same: read from the devices, changed on all of them with a
			rollback. Passwords are only compared, never shown.
		</p>
	</div>
	{#if setup && canChange && store.editBlocked === null}
		<button class="btn btn-primary" onclick={() => (editing = { id: null })}>
			<Plus class="size-4" /> New group
		</button>
	{/if}
</div>

<div class="mt-5">
	<Tabs
		tabs={[
			{ id: 'status', label: 'Status' },
			{ id: 'setup', label: 'Setup' }
		]}
		current={setup ? 'setup' : 'status'}
		onselect={showTab}
	/>
</div>

{#if error}<div class="mt-4"><EmptyState kind="error" text={error} onretry={load} /></div>{/if}
{#if groups && !groups.length}
	<div class="mt-6">
		<EmptyState
			text="No groups yet{setup
				? ''
				: ' (make one on Setup)'}. A group gathers devices that belong together: the access points, the servers of a rack, the cameras."
		/>
	</div>
{/if}

{#each groups ?? [] as g, i (g.id)}
	{@const s = surveys[g.id]}
	{@const members = g.devices
		.map((id) => store.byId.get(id))
		.filter((d) => d !== undefined)
		.map((device) => ({ device, depth: 0 }))}
	<Section title={g.name} count={g.devices.length} id="group-{g.id}" first={i === 0}>
		{#snippet actions()}
			{#if !setup && store.me?.role === 'admin'}
				<MaintenanceButton kind="groups" id={g.id} devices={g.devices} />
			{/if}
			{#if setup && canChange && store.editBlocked === null}
				<div class="flex items-center gap-2">
					{#if deleting === g.id}
						<span class="text-meta">Delete this group? The devices stay.</span>
						<button
							class="btn btn-sm"
							style:color="var(--critical-text)"
							onclick={() => remove(g.id)}
						>
							Delete
						</button>
						<button class="btn btn-sm" onclick={() => (deleting = null)}>Keep</button>
					{:else}
						<button class="btn btn-sm" onclick={() => (editing = { id: g.id })}>Change</button>
						<button class="btn btn-sm" onclick={() => ((deleting = g.id), (deleteError = null))}>
							Delete
						</button>
					{/if}
				</div>
			{/if}
		{/snippet}
		{#if deleting === g.id && deleteError}
			<div class="mb-3"><EmptyState kind="error" text={deleteError} /></div>
		{/if}
		<DeviceList
			sections={[{ site: g.id, name: '', rows: members }]}
			flat
			compact
			empty="No members known."
		/>

		{#if g.openwrt.length && !setup}
			<p class="mt-3 text-meta text-muted">
				Its Wi-Fi networks, mesh and roaming are on
				<a href="/wifi" class="font-medium text-text hover:underline">Wi-Fi</a>; they are changed on
				all members at once on Setup.
			</p>
		{/if}

		{#if g.openwrt.length && setup}
			<div class="mt-5">
				<div class="mb-2 flex flex-wrap items-baseline justify-between gap-2">
					<h3 class="text-meta font-medium text-muted">
						OpenWrt · {g.openwrt.length === g.devices.length
							? 'all members'
							: g.openwrt.map(name).join(', ')}
					</h3>
					<button
						class="text-meta text-muted underline hover:text-text"
						onclick={() => survey(g.id)}
					>
						read again
					</button>
				</div>
				{#if !s}
					<p class="text-body text-muted">Reading the devices…</p>
				{:else if 'error' in s}
					<EmptyState kind="error" text={s.error} onretry={() => survey(g.id)} />
				{:else}
					{#each Object.entries(s.errors) as [id, why] (id)}
						<p class="mb-2 flex items-baseline gap-1.5 text-meta text-muted">
							<StateDot tone="warning" label="Not read" size="size-1.5" />
							<span>
								{name(id)}: {why}{why.includes('not allowed')
									? ' (tick Wi-Fi in its OpenWrt setup)'
									: ''}
							</span>
						</p>
					{/each}

					<h4 class="mt-3 mb-2 text-meta font-medium text-muted">Wi-Fi networks</h4>
					<Table
						columns={networkColumns}
						rows={s.networks.map((n) => ({ ...n, group: g.id }))}
						key={(n) => n.ssid}
						label="Wi-Fi networks of {g.name}"
						empty="None read."
					/>

					<h4 class="mt-5 mb-2 text-meta font-medium text-muted">Mesh</h4>
					<Table
						columns={meshColumns}
						rows={s.mesh.map((m) => ({ ...m, group: g.id, survey: s }))}
						key={(m) => m.mesh_id}
						label="Mesh of {g.name}"
					>
						{#snippet empty()}
							<span class="inline-flex flex-wrap items-center gap-3">
								No mesh.
								{#if canChange}
									<button
										class={small}
										onclick={() => show({ kind: 'mesh', group: g.id, survey: s, existing: false })}
									>
										Set one up
									</button>
								{/if}
							</span>
						{/snippet}
					</Table>

					{#if canChange}
						<div class="card mt-5 divide-y divide-line">
							{#if s.roaming && Object.keys(s.roaming.changes).length}
								<div class="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
									<span>
										<span class="text-body font-medium">Roaming</span>
										<span class="block text-meta text-muted">
											802.11r/k/v on the shared networks, one access point at a time. What it finds
											is on <a href="/wifi" class="underline hover:text-text">Wi-Fi</a>.
										</span>
									</span>
									<button
										class={small}
										onclick={() => show({ kind: 'roaming', group: g.id, survey: s })}
									>
										Roaming assistance…
									</button>
								</div>
							{/if}
							<div class="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
								<span>
									<span class="text-body font-medium">Root's password</span>
									<span class="block text-meta text-muted">
										The same on every member (their "password" ability).
									</span>
								</span>
								<button class={small} onclick={() => show({ kind: 'root', group: g.id })}>
									Set on all
								</button>
							</div>
						</div>
					{/if}
				{/if}
			</div>
		{/if}
		{#if setup && g.ssh.length && store.me?.role === 'admin' && !store.health?.demo}
			<GroupSsh group={g.id} {canChange} />
		{/if}
	</Section>
{/each}

{#if editing}
	<GroupDialog
		id={editing.id}
		onclose={() => {
			editing = null;
			load();
		}}
	/>
{/if}

{#if open?.kind === 'roaming'}
	{@const o = open}
	<JobDialog
		title="Roaming assistance"
		path="/groups/{encodeURIComponent(o.group)}/roaming"
		action="Set it up"
		body={() => ({})}
		onclose={close}
		ondone={() => survey(o.group)}
	>
		<p class="text-xs text-muted">
			Enable 802.11r/k/v on shared personal networks with matching passwords and encryption. Changes
			run one access point at a time, with rollback if it stops responding. Clients may reconnect.
		</p>
		<ul class="mt-2 text-xs">
			{#each Object.entries(o.survey.roaming?.changes ?? {}) as [id, sections] (id)}
				<li>{name(id)}: {Object.keys(sections).join(', ')}</li>
			{/each}
		</ul>
		<p class="mt-2 text-xs text-muted">
			usteer itself is installed on the devices (opkg or apk: usteer); Kastellan doesn't install
			packages.
		</p>
	</JobDialog>
{/if}

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
		{#if key && !keyOk(key)}<p class="text-xs text-muted">
				<StateDot tone="warning" label="Check this" size="size-1.5" />
				8 to 63 characters.
			</p>{:else if key && key2 && key !== key2}<p class="text-xs text-muted">
				<StateDot tone="warning" label="Check this" size="size-1.5" />
				The two differ.
			</p>{/if}
		{#if key && !encryption && openNow(o.group, o.ssid)}
			<p class="text-xs text-muted">
				<StateDot tone="warning" label="Check this" size="size-1.5" />
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
		{#if key && !keyOk(key)}<p class="text-xs text-muted">
				<StateDot tone="warning" label="Check this" size="size-1.5" />
				8 to 63 characters.
			</p>{:else if key && key2 && key !== key2}<p class="text-xs text-muted">
				<StateDot tone="warning" label="Check this" size="size-1.5" />
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
						<span class="text-xs text-muted">
							<StateDot tone="warning" label="Can't" size="size-1.5" />
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
			<p class="text-xs text-muted">
				<StateDot tone="warning" label="Check this" size="size-1.5" />
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
			<p class="text-xs text-muted">
				<StateDot tone="warning" label="Check this" size="size-1.5" />
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
		{#if key && key.length < 8}<p class="text-xs text-muted">
				<StateDot tone="warning" label="Check this" size="size-1.5" />
				At least 8 characters.
			</p>{:else if key && key2 && key !== key2}<p class="text-xs text-muted">
				<StateDot tone="warning" label="Check this" size="size-1.5" />
				The two differ.
			</p>{/if}
	</JobDialog>
{/if}
