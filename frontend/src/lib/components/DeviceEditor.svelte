<script lang="ts">
	import { api, type CheckForm, type DeviceForm } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { goto } from '$app/navigation';
	import Plus from '@lucide/svelte/icons/plus';
	import Trash from '@lucide/svelte/icons/trash-2';

	/** Add a device, or edit one: what config.yaml says about its name, address, kind, tags and
	 * services. The rest of the device in config.yaml (ssh, docker, ...) stays as it is (its
	 * config.yaml tab changes that). Saving writes config.yaml (comments kept) and the hub picks it
	 * up at once; `onclose` follows a save or Cancel. */
	let {
		id = null,
		initial = null,
		onclose
	}: {
		id?: string | null; // null: a new device
		initial?: { name?: string; host?: string } | null; // a new one's start (one found on the network)
		onclose: () => void;
	} = $props();

	const KINDS = [
		'router',
		'access-point',
		'switch',
		'server',
		'pc',
		'laptop',
		'printer',
		'camera',
		'ups',
		'other'
	];

	let form = $state<DeviceForm | null>(null);
	let newId = $state('');
	let idTouched = $state(false);
	let tags = $state('');
	let error = $state<string | null>(null);
	let busy = $state(false);
	let confirmDelete = $state(false);

	// A UPS: which host reads it; without an address of its own, that is all there is to it.
	$effect(() => {
		if (form?.kind === 'ups' && !form.ups) form.ups = { via: '', name: '' };
	});
	const hostless = $derived(form?.kind === 'ups' && !form.host.trim());
	const upses = $derived((store.devices ?? []).filter((d) => d.kind === 'ups' && d.id !== id));

	// Where a virtual machine runs: the other devices (hosts reporting VMs first), their VMs.
	const hosts = $derived(
		(store.devices ?? [])
			.filter((d) => d.id !== id)
			.sort(
				(a, b) => Number(!!b.vms?.length) - Number(!!a.vms?.length) || a.name.localeCompare(b.name)
			)
	);
	const hostVms = $derived(
		(store.devices ?? []).find((d) => d.id === form?.runs_on?.host)?.vms?.map((v) => v.name) ?? []
	);
	// A VM named like this device, not linked yet: offered, never set by itself.
	const simple = (t: string) => t.toLowerCase().replace(/[^a-z0-9]+/g, '-');
	const suggestion = $derived.by(() => {
		if (!form || form.runs_on) return null;
		const names = [id ?? '', form.name].filter(Boolean).map(simple);
		for (const d of store.devices ?? [])
			for (const v of d.vms ?? [])
				if (d.id !== id && !v.device && names.includes(simple(v.name)))
					return { host: d.id, hostName: d.name, vm: v.name };
		return null;
	});

	// The sites devices are at already, to pick from (any new id works too).
	const sites = $derived([...new Set((store.devices ?? []).map((d) => d.site))].sort());

	/** What a service is called when its name is left empty (as config.yaml names it). */
	const defaultName = (c: CheckForm) =>
		c.type === 'ping' || c.type === 'dhcp' ? c.type : `${c.type}:${c.target ?? ''}`;

	async function load() {
		error = null;
		try {
			if (id) {
				form = await api.deviceForm(id);
				tags = form.tags.join(', ');
			} else {
				form = {
					name: initial?.name ?? '',
					host: initial?.host ?? '',
					kind: 'other',
					site: '',
					tags: [],
					always_on: null,
					exposed: false,
					checks: [{ type: 'ping', target: null, name: 'ping' }]
				};
			}
		} catch (e) {
			error = (e as Error).message;
		}
	}
	load();

	const slug = (s: string) =>
		s
			.toLowerCase()
			.normalize('NFKD')
			.replace(/\p{M}/gu, '') // Büro: bu-ro otherwise
			.replace(/[^a-z0-9]+/g, '-')
			.replace(/^-+|-+$/g, '')
			.slice(0, 40);
	$effect(() => {
		if (!id && form && !idTouched) newId = slug(form.name || form.host);
	});

	function addCheck(type: CheckForm['type']) {
		form?.checks.push({ type, target: type === 'http' ? 'https://' : null, name: '' });
	}

	/** Names left empty get what config.yaml would call them. */
	const named = (c: CheckForm): CheckForm => ({
		...c,
		target: c.type === 'tcp' && c.target !== null ? Number(c.target) : c.target,
		server: c.server?.trim() || null,
		name: c.name.trim()
	});

	async function save(e: SubmitEvent) {
		e.preventDefault();
		if (!form) return;
		busy = true;
		error = null;
		const body: DeviceForm = {
			...form,
			tags: tags
				.split(/[,\s]+/)
				.map((t) => t.trim())
				.filter(Boolean),
			// A UPS read through its host has no address: nothing of its own to check.
			checks: hostless ? [] : form.checks.map(named),
			// Not read by a host (a UPS with its own address, a network card): no ups: link.
			ups: form.kind === 'ups' && form.ups?.via ? form.ups : null
		};
		try {
			if (id) await api.changeDevice(id, body);
			else await api.addDevice({ ...body, id: newId });
			await store.refresh();
			onclose();
			if (!id) goto(`/devices/${newId}`);
		} catch (err) {
			error = (err as Error).message;
		} finally {
			busy = false;
		}
	}

	async function remove() {
		if (!id) return;
		busy = true;
		error = null;
		try {
			await api.deleteDevice(id, form?.version);
			await store.refresh();
			onclose();
			goto('/devices');
		} catch (err) {
			error = (err as Error).message;
		} finally {
			busy = false;
		}
	}

	const input =
		'h-9 w-full min-w-0 rounded-lg border border-line bg-surface px-3 text-sm outline-none focus:border-accent';
	const label = 'flex flex-col gap-1 text-xs font-medium text-muted';
	const button =
		'inline-flex h-9 items-center justify-center gap-1.5 rounded-lg border border-line px-3 text-sm font-medium hover:border-line-strong disabled:opacity-50';
</script>

<form onsubmit={save} class="card flex max-w-3xl flex-col">
	{#if form}
		<div class="flex flex-col gap-4 px-5 py-4">
			<div class="grid items-start gap-3 sm:grid-cols-2">
				<label class={label}>
					Name
					<input class={input} bind:value={form.name} placeholder="NAS" />
				</label>
				<label class={label}>
					Address
					<input
						class="{input} font-mono"
						bind:value={form.host}
						placeholder={form.kind === 'ups'
							? 'none: read through its host'
							: '192.168.1.10 or nas.lan'}
						required={form.kind !== 'ups'}
					/>
				</label>
				<label class={label}>
					Kind
					<select class={input} bind:value={form.kind}>
						{#each KINDS as k (k)}<option value={k}>{k}</option>{/each}
					</select>
				</label>
				<label class={label}>
					Always on
					<select
						class={input}
						value={form.always_on === null ? 'auto' : form.always_on ? 'yes' : 'no'}
						onchange={(e) => {
							const v = e.currentTarget.value;
							if (form) form.always_on = v === 'auto' ? null : v === 'yes';
						}}
					>
						<option value="auto">By kind (PCs and laptops may be off)</option>
						<option value="yes">Yes: down is a problem</option>
						<option value="no">No: off is just offline</option>
					</select>
				</label>
				{#if !id}
					<label class={label}>
						Id
						<input
							class="{input} font-mono"
							bind:value={newId}
							oninput={() => (idTouched = true)}
							pattern="[a-z0-9][a-z0-9_\-]*"
							required
						/>
						<span class="font-normal text-faint">Its name in config.yaml; it stays.</span>
					</label>
				{/if}
				<label class={label}>
					Site
					<input
						class="{input} font-mono"
						bind:value={form.site}
						list="known-sites"
						placeholder="home"
						pattern="[a-z0-9][a-z0-9_\-]*"
					/>
					<datalist id="known-sites">
						{#each sites as s (s)}<option value={s}></option>{/each}
					</datalist>
				</label>
				<label class="{label} {id ? '' : 'sm:col-span-2'}">
					Tags
					<input class={input} bind:value={tags} placeholder="docker, ubuntu (comma-separated)" />
				</label>
			</div>
			<div class="flex flex-col gap-2">
				<label class="choice text-sm">
					<input
						type="checkbox"
						checked={!!form.runs_on}
						onchange={(e) => {
							if (form) form.runs_on = e.currentTarget.checked ? { host: '', vm: '' } : null;
						}}
					/>
					<span>
						A virtual machine on another device
						<span class="block text-xs text-muted">
							Its host's state and actions show here; when the host is down, this one waits in its
							alarm.
						</span>
					</span>
				</label>
				{#if form.runs_on}
					<div class="ml-6.5 grid items-start gap-3 sm:grid-cols-2">
						<label class={label}>
							Runs on
							<select class={input} bind:value={form.runs_on.host} required>
								<option value="" disabled>choose its host</option>
								{#each hosts as h (h.id)}
									<option value={h.id}>
										{h.name}{h.vms?.length ? ` (${h.vms.length} VMs)` : ''}
									</option>
								{/each}
							</select>
						</label>
						<label class={label}>
							Its name there
							<input
								class="{input} font-mono"
								bind:value={form.runs_on.vm}
								list="host-vms"
								placeholder="as libvirt names it"
								required
							/>
							<datalist id="host-vms">
								{#each hostVms as v (v)}<option value={v}></option>{/each}
							</datalist>
						</label>
					</div>
				{:else if suggestion}
					{@const s = suggestion}
					<p class="ml-6.5 flex flex-wrap items-center gap-2 text-xs text-muted">
						Is it the virtual machine <b class="font-mono">{s.vm}</b> on {s.hostName}?
						<button
							type="button"
							class="btn btn-sm"
							onclick={() => {
								if (form) form.runs_on = { host: s.host, vm: s.vm };
							}}
						>
							Yes, link it
						</button>
					</p>
				{/if}
			</div>
			<label class="choice text-sm">
				<input type="checkbox" bind:checked={form.exposed} />
				<span>
					All of it is reachable from the internet
					<span class="block text-xs text-muted">
						Its own address is public, or everything is forwarded. Known vulnerabilities in what it
						runs count as urgent. Only some services? Tick them below instead.
					</span>
				</span>
			</label>

			{#if form.kind === 'ups' && form.ups}
				<div class="grid items-start gap-3 sm:grid-cols-2">
					<label class={label}>
						Read by
						<select class={input} bind:value={form.ups.via} required={hostless}>
							<option value="" disabled={hostless}>(none: it has its own address)</option>
							{#each hosts.filter((h) => h.kind !== 'ups') as h (h.id)}
								<option value={h.id}>{h.name}</option>
							{/each}
						</select>
						<span class="font-normal text-faint">
							The host it's plugged into: its collector reads apcupsd or NUT there.
						</span>
					</label>
					<label class={label}>
						Its NUT name
						<input
							class="{input} font-mono"
							bind:value={form.ups.name}
							placeholder="(apcupsd: none)"
						/>
						<span class="font-normal text-faint">Only when the host has several (upsc -l).</span>
					</label>
				</div>
			{/if}
			{#if upses.length}
				<label class="{label} sm:max-w-xs">
					Powered by
					<select
						class={input}
						value={form.powered_by ?? ''}
						onchange={(e) => {
							if (form) form.powered_by = e.currentTarget.value || null;
						}}
					>
						<option value="">no UPS (or not known)</option>
						{#each upses as u (u.id)}<option value={u.id}>{u.name}</option>{/each}
					</select>
				</label>
			{/if}

			<div class:hidden={hostless}>
				<h3 class="text-sm font-semibold">Services</h3>
				<p class="mt-0.5 text-xs text-muted">
					One per way in: an address at home and a public name are two services. A device without
					any isn't checked, only shown.
				</p>
				<ul class="mt-3 flex flex-col gap-2">
					{#each form.checks as check, i (i)}
						<li class="rounded-xl border border-line p-3">
							<div class="flex flex-wrap items-end gap-2">
								<label class="{label} w-24">
									Type
									<select
										class={input}
										value={check.type}
										onchange={(e) => {
											// What belongs to the other type goes (it would be invalid, unseen).
											const type = e.currentTarget.value as CheckForm['type'];
											Object.assign(check, {
												type,
												target: type === 'http' ? 'https://' : null,
												expect: null,
												server: null,
												tls: null,
												verify: false
											});
										}}
									>
										<option value="ping">ping</option>
										<option value="tcp">port</option>
										<option value="http">web</option>
										<option value="dns">dns</option>
										<option value="dhcp">dhcp</option>
									</select>
								</label>
								{#if check.type === 'tcp'}
									<label class="{label} w-24">
										Port
										<input
											class="{input} font-mono"
											type="number"
											min="1"
											max="65535"
											bind:value={check.target}
											required
										/>
									</label>
								{:else if check.type === 'http'}
									<label class="{label} min-w-48 flex-1">
										URL
										<input
											class="{input} font-mono"
											type="url"
											bind:value={check.target}
											placeholder="https://nas.lan:5001"
											required
										/>
									</label>
								{:else if check.type === 'dns'}
									<label class="{label} min-w-48 flex-1">
										Look up
										<input
											class="{input} font-mono"
											bind:value={check.target}
											placeholder="www.example.org"
											required
										/>
									</label>
								{/if}
								<label class="{label} min-w-32 flex-1">
									Name
									<input class={input} bind:value={check.name} placeholder={defaultName(check)} />
								</label>
								<button
									type="button"
									class="{button} px-2 text-muted"
									onclick={() => form?.checks.splice(i, 1)}
									title="Remove this service"
								>
									<Trash class="size-4" /><span class="sr-only">Remove</span>
								</button>
							</div>
							{#if check.type === 'tcp' || check.type === 'http'}
								<label class="choice mt-2 text-xs text-muted">
									<input
										type="checkbox"
										checked={!!check.exposed || form.exposed}
										disabled={form.exposed}
										onchange={(e) => (check.exposed = e.currentTarget.checked)}
									/>
									<span>
										Reachable from the internet{form.exposed
											? ' (the whole device is)'
											: ' (a port forward or a proxy leads here)'}
									</span>
								</label>
							{/if}
							{#if check.type !== 'ping'}
								<label class="mt-2 flex items-center gap-1.5 text-xs text-muted">
									When it is down
									<select
										class="h-7 rounded-md border border-line bg-surface px-1.5 text-xs"
										value={check.alarm ?? 'critical'}
										onchange={(e) => (check.alarm = e.currentTarget.value as CheckForm['alarm'])}
									>
										<option value="critical">an urgent alarm</option>
										<option value="warning">a warning</option>
										<option value="off">no alarm</option>
									</select>
								</label>
							{/if}
							{#if check.type === 'http'}
								<div class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted">
									<label class="flex items-center gap-1.5">
										Status
										<input
											class="h-7 w-20 rounded-md border border-line bg-surface px-2 font-mono text-xs"
											type="number"
											min="100"
											max="599"
											placeholder="< 500"
											bind:value={check.expect}
										/>
									</label>
									<label
										class="flex items-center gap-1.5"
										title="Up only when the page shows this text (in its first 256 KB)"
									>
										Page shows
										<input
											class="h-7 w-44 rounded-md border border-line bg-surface px-2 text-xs"
											maxlength="200"
											placeholder="any page"
											value={check.contains ?? ''}
											oninput={(e) => (check.contains = e.currentTarget.value || null)}
										/>
									</label>
									<label class="flex items-center gap-1.5">
										Certificate alarms
										<select
											class="h-7 rounded-md border border-line bg-surface px-1.5 text-xs"
											value={check.tls == null ? 'auto' : check.tls ? 'on' : 'off'}
											onchange={(e) => {
												const v = e.currentTarget.value;
												check.tls = v === 'auto' ? null : v === 'on';
											}}
										>
											<option value="auto">for public names</option>
											<option value="on">on</option>
											<option value="off">off</option>
										</select>
									</label>
								</div>
							{:else if check.type === 'dns'}
								<div class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted">
									<label class="flex items-center gap-1.5">
										Answers include
										<input
											class="h-7 w-56 rounded-md border border-line bg-surface px-2 font-mono text-xs"
											value={Array.isArray(check.expect) ? check.expect.join(', ') : ''}
											oninput={(e) => {
												const all = e.currentTarget.value.split(/[,\s]+/).filter(Boolean);
												check.expect = all.length ? all : null;
											}}
											placeholder="any address"
										/>
									</label>
									<label class="flex items-center gap-1.5">
										Resolver
										<input
											class="h-7 w-36 rounded-md border border-line bg-surface px-2 font-mono text-xs"
											bind:value={check.server}
											placeholder="the system's"
										/>
									</label>
								</div>
							{/if}
						</li>
					{/each}
				</ul>
				<div class="mt-2 flex flex-wrap gap-2">
					<button type="button" class={button} onclick={() => addCheck('http')}>
						<Plus class="size-4" /> Web
					</button>
					<button type="button" class={button} onclick={() => addCheck('tcp')}>
						<Plus class="size-4" /> Port
					</button>
					<button type="button" class={button} onclick={() => addCheck('dns')}>
						<Plus class="size-4" /> DNS
					</button>
					{#if ['router', 'access-point'].includes(form.kind) && !form.checks.some((c) => c.type === 'dhcp')}
						<button
							type="button"
							class={button}
							title="Asks the network for an address like a new device: up when this device's DHCP server answers; another server answering raises an alarm"
							onclick={() => addCheck('dhcp')}
						>
							<Plus class="size-4" /> DHCP
						</button>
					{/if}
					{#if !form.checks.some((c) => c.type === 'ping')}
						<button type="button" class={button} onclick={() => addCheck('ping')}>
							<Plus class="size-4" /> Ping
						</button>
					{/if}
				</div>
			</div>
		</div>
	{/if}

	{#if error}
		<p
			class="mx-5 mb-3 rounded-lg px-3 py-2 text-sm"
			style="color: var(--critical-text); background: color-mix(in srgb, var(--critical-text) 10%, transparent)"
		>
			{error}
		</p>
	{/if}

	<div class="flex flex-wrap items-center justify-between gap-2 border-t border-line px-5 py-3">
		<div>
			{#if id}
				{#if confirmDelete}
					<span class="mr-2 text-sm">Remove {form?.name || id} from config.yaml?</span>
					<button
						type="button"
						class="{button} text-white"
						style="background: var(--critical-text); border-color: transparent"
						disabled={busy}
						onclick={remove}
					>
						Remove
					</button>
					<button type="button" class={button} onclick={() => (confirmDelete = false)}>
						Keep
					</button>
				{:else}
					<button type="button" class="{button} text-muted" onclick={() => (confirmDelete = true)}>
						<Trash class="size-4" /> Remove device
					</button>
				{/if}
			{/if}
		</div>
		<div class="flex gap-2">
			<button type="button" class={button} onclick={onclose}>Cancel</button>
			<button
				class="{button} border-transparent bg-accent text-accent-ink hover:opacity-90"
				disabled={busy || !form}
			>
				{busy ? 'Saving…' : id ? 'Save' : 'Add device'}
			</button>
		</div>
	</div>
</form>
