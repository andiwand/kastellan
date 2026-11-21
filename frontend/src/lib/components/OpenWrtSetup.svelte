<script lang="ts">
	import SetupCard from './SetupCard.svelte';
	import { untrack } from 'svelte';
	import { api, type OpenWrtSetup } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import SetupDialog from './SetupDialog.svelte';

	/** Kastellan's login on an OpenWrt device: an rpcd login with its own access list, one group
	 * per ability ticked here; reading is always there. Set up in a dialog with root's password
	 * (or the commands to run yourself). */
	let {
		device,
		canInstall,
		services: known = [],
		allowedServices = []
	}: {
		device: string;
		canInstall: boolean;
		services?: string[]; // the device's services (as read)
		allowedServices?: string[]; // the ones Kastellan may act on now
	} = $props();
	let services = $state<string[]>([]);

	let setup = $state<OpenWrtSetup | null>(null);
	let chosen = $state<string[]>([]);
	let error = $state<string | null>(null);
	let opened = $state(false);
	let installing = $state(false);

	async function load(first = false) {
		error = null;
		try {
			if (first) services = [...allowedServices];
			setup = await api.openwrtScript(device, chosen, services);
			if (first && setup.allowed) {
				chosen = setup.allowed;
				setup = await api.openwrtScript(device, chosen, services);
			}
		} catch (e) {
			error = (e as Error).message;
		}
	}

	// A new choice: the commands for it (read only the choice here, not what loading sets).
	$effect(() => {
		void chosen.length;
		void services.length;
		untrack(() => {
			if (opened && setup) load();
		});
	});
	const dev = $derived(store.byId.get(device)); // how it stands (config.yaml, reports)
</script>

<SetupCard
	title="OpenWrt login"
	about="Kastellan's own login on the router: its state, Wi-Fi, services and firmware, ability by ability."
	stage={dev?.openwrt ? 'set' : 'available'}
	onopen={() => {
		if (!opened) {
			opened = true;
			load(true);
		}
	}}
>
	{#if error}<p class="mt-2 text-xs" style:color="var(--critical-text)">{error}</p>{/if}
	{#if !setup && !error}<p class="mt-2 text-xs text-muted">Loading…</p>{/if}
	{#if setup}
		<p class="mt-2 max-w-3xl text-xs leading-relaxed text-muted">
			A login <b>kastellan</b> in the device's own access control (rpcd, as LuCI uses it), with a password
			only Kastellan knows. It may read the device's state, and only what you tick here; the device refuses
			everything else. Kastellan talks to it over HTTPS and pins its certificate.
		</p>
		<p class="mt-2 text-xs">
			{#if setup.allowed}
				<span style:color="var(--good-text)">
					Set up; it may: read{setup.allowed.map((a) => `, ${a}`).join('')}.
				</span>
			{:else if setup.configured}
				<span style:color="var(--warning-text)">
					In config.yaml, but the device doesn't answer.
				</span>
			{:else}<span class="text-muted">Not set up.</span>{/if}
		</p>
		<fieldset class="mt-3 space-y-1.5">
			{#each Object.entries(setup.abilities) as [ability, description] (ability)}
				<label class="choice text-xs">
					<input type="checkbox" value={ability} bind:group={chosen} />
					<span>{description}</span>
				</label>
			{/each}
		</fieldset>
		{#if chosen.includes('services')}
			<fieldset class="mt-3 ml-6">
				<legend class="text-xs text-muted">The services it may act on</legend>
				{#if known.length}
					<div class="mt-1 grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3">
						{#each known as name (name)}
							<label class="choice font-mono text-xs">
								<input type="checkbox" value={name} bind:group={services} />
								{name}
							</label>
						{/each}
					</div>
					<p class="mt-1 text-xs text-faint">
						rpcd and uhttpd (Kastellan's way in), the firewall and the network can only be
						restarted, never stopped.
					</p>
				{:else}
					<p class="mt-1 text-xs text-muted">
						Its services show here once Kastellan reads it (set it up with reading first).
					</p>
				{/if}
			</fieldset>
		{/if}
		<div class="mt-4 flex justify-end">
			<button
				class="inline-flex h-8 items-center rounded-lg border border-line px-3 text-xs font-medium hover:border-line-strong"
				onclick={() => (installing = true)}
			>
				Set up exactly this
			</button>
		</div>
	{/if}
</SetupCard>

{#if installing && setup}
	<SetupDialog
		title="Kastellan's login on this OpenWrt device"
		intro={`Kastellan logs in once as root (SSH) and sets up its own login there: reading${chosen
			.map((a) => `, ${a}`)
			.join(
				''
			)}${chosen.includes('services') ? ` (${services.join(', ') || 'no services'})` : ''}. Root's password is used for this one connection and never stored.`}
		path="/devices/{encodeURIComponent(device)}/openwrt/install"
		body={{ abilities: chosen, services: chosen.includes('services') ? services : [] }}
		script={setup.script}
		port={setup.port}
		needsRoot={false}
		defaultLogin="root"
		canRun={canInstall}
		onclose={() => (installing = false)}
		ondone={async (o) => {
			if (o.ok) {
				await store.refresh();
				await load();
			}
		}}
	/>
{/if}
