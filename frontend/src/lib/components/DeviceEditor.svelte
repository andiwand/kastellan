<script lang="ts">
	import { api, type CheckForm, type DeviceForm } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { goto } from '$app/navigation';
	import Plus from '@lucide/svelte/icons/plus';
	import Trash from '@lucide/svelte/icons/trash-2';
	import X from '@lucide/svelte/icons/x';

	/** Add a device, or edit one: what config.yaml says about its name, address, kind, tags and
	 * services. The rest of the device in config.yaml (ssh, docker, ...) stays as it is. Saving
	 * writes config.yaml (comments kept) and the hub picks it up at once. */
	let {
		id = null,
		onclose
	}: {
		id?: string | null; // null: a new device
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
		'other'
	];

	let form = $state<DeviceForm | null>(null);
	let newId = $state('');
	let idTouched = $state(false);
	let tags = $state('');
	let error = $state<string | null>(null);
	let busy = $state(false);
	let confirmDelete = $state(false);
	let dialog: HTMLDialogElement | undefined = $state();

	$effect(() => {
		dialog?.showModal();
	});
	// The page behind stays where it is while the dialog is open.
	$effect(() => {
		const root = document.documentElement;
		const before = root.style.overflow;
		root.style.overflow = 'hidden';
		return () => {
			root.style.overflow = before;
		};
	});

	/** What a service is called when its name is left empty (as config.yaml names it). */
	const defaultName = (c: CheckForm) =>
		c.type === 'ping' ? 'ping' : `${c.type}:${c.target ?? ''}`;

	async function load() {
		error = null;
		try {
			if (id) {
				form = await api.deviceForm(id);
				tags = form.tags.join(', ');
			} else {
				form = {
					name: '',
					host: '',
					kind: 'other',
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
			checks: form.checks.map(named)
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

<dialog
	bind:this={dialog}
	{onclose}
	class="m-auto max-h-[calc(100dvh-2rem)] w-[min(40rem,calc(100vw-2rem))] rounded-2xl border border-line bg-surface p-0 text-text shadow-pop backdrop:bg-black/40"
	aria-labelledby="device-editor-title"
>
	<form onsubmit={save} class="flex max-h-[calc(100dvh-2rem)] flex-col">
		<div class="flex items-center justify-between border-b border-line px-5 py-4">
			<h2 id="device-editor-title" class="text-base font-semibold">
				{id ? `Edit ${form?.name || id}` : 'Add a device'}
			</h2>
			<button type="button" class="text-muted hover:text-text" onclick={() => dialog?.close()}>
				<X class="size-5" /><span class="sr-only">Close</span>
			</button>
		</div>

		{#if form}
			<div class="flex flex-col gap-4 overflow-y-auto overscroll-contain px-5 py-4">
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
							placeholder="192.168.1.10 or nas.lan"
							required
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
					<label class="{label} {id ? 'sm:col-span-2' : ''}">
						Tags
						<input class={input} bind:value={tags} placeholder="docker, ubuntu (comma-separated)" />
					</label>
				</div>
				<label class="choice text-sm">
					<input type="checkbox" bind:checked={form.exposed} />
					<span>
						All of it is reachable from the internet
						<span class="block text-xs text-muted">
							Its own address is public, or everything is forwarded. Known vulnerabilities in what
							it runs count as urgent. Only some services? Tick them below instead.
						</span>
					</span>
				</label>

				<div>
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
													tls: null,
													verify: false
												});
											}}
										>
											<option value="ping">ping</option>
											<option value="tcp">port</option>
											<option value="http">web</option>
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
								{#if check.type !== 'ping'}
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
						<button
							type="button"
							class="{button} text-muted"
							onclick={() => (confirmDelete = true)}
						>
							<Trash class="size-4" /> Remove device
						</button>
					{/if}
				{/if}
			</div>
			<div class="flex gap-2">
				<button type="button" class={button} onclick={() => dialog?.close()}>Cancel</button>
				<button
					class="{button} border-transparent bg-accent text-accent-ink hover:opacity-90"
					disabled={busy || !form}
				>
					{busy ? 'Saving…' : id ? 'Save' : 'Add device'}
				</button>
			</div>
		</div>
	</form>
</dialog>
