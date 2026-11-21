<script lang="ts">
	import SetupCard from './SetupCard.svelte';
	import { VM_NAME } from '#lib/actions.js';
	import { untrack } from 'svelte';
	import { api, type ActSetup } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import SetupDialog from './SetupDialog.svelte';

	/** What Kastellan may change on a host, ability by ability: only what is chosen here is
	 * installed there, and the container and VM abilities only act on the chosen ones. Set up in a
	 * dialog: with your own login (asked then, used once, not kept), or the commands to run
	 * yourself, so Kastellan never sees a login at all. */
	let {
		device,
		containers,
		canInstall
	}: { device: string; containers: string[]; canInstall: boolean } = $props();

	let setup = $state<ActSetup | null>(null);
	let chosen = $state<string[]>([]);
	let all = $state(false); // the narrow choice first: only the containers ticked
	let picked = $state<string[]>([]);
	let uneven = $state(false); // the host has different lists per ability
	let error = $state<string | null>(null);
	let opened = $state(false);
	let installing = $state(false);
	let removing = $state(false);

	let vmsAll = $state(false);
	let vmsPicked = $state<string[]>([]);
	// The host's VMs as its collector reports them (none known: none to pick, "all" still works).
	// Only names the actor can take (actions.ts VM_NAME); the others are named, not offered.
	const vmNames = $derived((store.byId.get(device)?.vms ?? []).map((v) => v.name));
	const vms = $derived([...new Set([...vmNames.filter((n) => VM_NAME.test(n)), ...vmsPicked])]);
	const containerNames = $derived([...new Set([...containers, ...picked])]);
	const unusable = $derived(vmNames.filter((n) => !VM_NAME.test(n)));

	const targets = $derived(all ? ['*'] : picked);
	const vmTargets = $derived(vmsAll ? ['*'] : vmsPicked);
	const containerAbilities = $derived(
		chosen.some((a) => a.endsWith('-container') || a === 'container-logs')
	);
	const vmAbilities = $derived(chosen.some((a) => a.startsWith('vm-')));
	const ready = $derived(
		(!containerAbilities || targets.length > 0) && (!vmAbilities || vmTargets.length > 0)
	);

	async function load(first = false) {
		error = null;
		try {
			setup = await api.actScript(device, chosen, targets, vmTargets);
			if (first && setup.allowed) {
				// Start from what the host allows now.
				chosen = Object.keys(setup.allowed);
				const of = (kind: (a: string) => boolean) =>
					Object.entries(setup!.allowed!).filter(([a]) => kind(a));
				const isContainer = (a: string) => a.endsWith('-container') || a === 'container-logs';
				const isVm = (a: string) => a.startsWith('vm-');
				uneven =
					new Set(of(isContainer).map(([, l]) => [...l].sort().join(' '))).size > 1 ||
					new Set(of(isVm).map(([, l]) => [...l].sort().join(' '))).size > 1;
				const names = [...new Set(of(isContainer).flatMap(([, l]) => l))];
				all = names.includes('*');
				picked = all ? [] : names;
				const vmNames = [...new Set(of(isVm).flatMap(([, l]) => l))];
				vmsAll = vmNames.includes('*');
				vmsPicked = vmsAll ? [] : vmNames;
				setup = await api.actScript(device, chosen, targets, vmTargets);
			}
		} catch (e) {
			error = (e as Error).message;
		}
	}

	// A new choice: the commands for it (read only the choice here, not what loading sets).
	$effect(() => {
		void chosen.length;
		void targets.length;
		void vmTargets.length;
		untrack(() => {
			if (opened && setup) load();
		});
	});
	const dev = $derived(store.byId.get(device)); // how it stands (config.yaml, reports)
</script>

<SetupCard
	title="Actor"
	about="What Kastellan may change here, ability by ability: containers, VMs, system updates, a reboot, logs."
	stage={!dev?.act ? 'available' : dev.outdated_scripts.includes('actor') ? 'outdated' : 'set'}
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
			A user <b>kastellan-act</b> that can do nothing by itself. Each ability you tick is one root-owned
			script with one sudo rule; what you don't tick isn't installed, so it can't be run, whatever the
			hub asks. Every action goes to the host's syslog and Kastellan's audit log.
		</p>
		<p class="mt-2 text-xs">
			{#if setup.allowed}
				<span style:color="var(--good-text)">
					The host allows: {Object.keys(setup.allowed).join(', ') || 'nothing yet'}.
				</span>
			{:else if setup.configured}
				<span style:color="var(--warning-text)">In config.yaml, but: {setup.error}</span>
			{:else}<span class="text-muted">Not set up.</span>{/if}
		</p>

		<fieldset class="mt-3 flex flex-col gap-2">
			{#each Object.entries(setup.abilities) as [ability, description] (ability)}
				<label class="choice text-xs">
					<input type="checkbox" value={ability} bind:group={chosen} />
					<span>{description}</span>
				</label>
			{/each}
		</fieldset>
		{#if containerAbilities}
			<fieldset class="mt-4 flex flex-col gap-2 text-xs">
				<legend class="mb-2 text-xs font-medium text-muted">On which containers</legend>
				<label class="choice">
					<input type="radio" bind:group={all} value={false} /> Only these
				</label>
				{#if !all}
					<div class="ml-6.5 grid grid-cols-2 gap-x-6 gap-y-1.5 sm:grid-cols-3">
						{#each containerNames as name (name)}
							<label class="choice font-mono text-xs">
								<input type="checkbox" value={name} bind:group={picked} />
								<span class="truncate">{name}</span>
							</label>
						{/each}
						{#if containers.length === 0}<span class="text-xs text-faint">
								no containers known yet
							</span>{/if}
					</div>
				{/if}
				<label class="choice">
					<input type="radio" bind:group={all} value={true} /> All of them, also ones added later
				</label>
				{#if uneven}
					<p class="mt-1 text-xs" style:color="var(--warning-text)">
						The host has different lists for its abilities; setting it up again makes them this one
						list.
					</p>
				{/if}
			</fieldset>
		{/if}
		{#if vmAbilities}
			<fieldset class="mt-4 flex flex-col gap-2 text-xs">
				<legend class="mb-2 text-xs font-medium text-muted">On which virtual machines</legend>
				<label class="choice">
					<input type="radio" bind:group={vmsAll} value={false} /> Only these
				</label>
				{#if !vmsAll}
					<div class="ml-6.5 grid grid-cols-2 gap-x-6 gap-y-1.5 sm:grid-cols-3">
						{#each vms as name (name)}
							<label class="choice font-mono text-xs">
								<input type="checkbox" value={name} bind:group={vmsPicked} />
								<span class="truncate">{name}</span>
							</label>
						{/each}
						{#if vmNames.length === 0}<span class="text-xs text-faint">
								no VMs known yet (the collector lists them)
							</span>{/if}
					</div>
					{#if unusable.length}<p class="ml-6.5 text-xs text-faint">
							Not offered (Kastellan acts on names of letters, digits and _ . + -, never a number or
							a UUID): {unusable.join(', ')}
						</p>{/if}
				{/if}
				<label class="choice">
					<input type="radio" bind:group={vmsAll} value={true} /> All of them, also ones added later
				</label>
			</fieldset>
		{/if}
		<div class="mt-4 flex justify-end gap-2">
			{#if setup.configured}
				<button
					class="inline-flex h-8 items-center rounded-lg border border-line px-3 text-xs font-medium hover:border-line-strong"
					onclick={() => (removing = true)}
				>
					Remove
				</button>
			{/if}
			<button
				class="inline-flex h-8 items-center rounded-lg border border-line px-3 text-xs font-medium hover:border-line-strong disabled:opacity-50"
				disabled={!ready}
				title={ready ? '' : 'tick at least one container or VM for the abilities chosen'}
				onclick={() => (installing = true)}
			>
				{setup.configured ? 'Set up again, exactly this' : 'Set up exactly this'}
			</button>
		</div>
	{/if}
</SetupCard>

{#if installing && setup}
	<SetupDialog
		title="Set up the actor"
		intro={`Kastellan logs in once with your login and sets the host up for exactly this, as root: ${
			chosen.length ? chosen.join(', ') : 'no abilities (only listing)'
		}${containerAbilities ? ` on ${all ? 'all containers' : picked.join(', ')}` : ''}${
			vmAbilities ? `; VMs: ${vmsAll ? 'all' : vmsPicked.join(', ')}` : ''
		}. Abilities not ticked are removed.`}
		path="/devices/{encodeURIComponent(device)}/act/install"
		body={{ abilities: chosen, containers: targets, vms: vmTargets }}
		script={setup.script}
		port={setup.port}
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

{#if removing && setup}
	<SetupDialog
		title="Remove the actor"
		intro="Kastellan logs in once with your login and takes the actor off this host as root: its user, scripts, lists and sudo rule. Then act: goes from this device in config.yaml."
		path="/devices/{encodeURIComponent(device)}/act/uninstall"
		script={setup.uninstall}
		port={setup.port}
		canRun={canInstall}
		onclose={() => (removing = false)}
		ondone={async (o) => {
			if (o.ok) {
				await store.refresh();
				await load();
			}
		}}
	/>
{/if}
