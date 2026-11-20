<script lang="ts">
	import { api, type ActSetup } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import SetupDialog from './SetupDialog.svelte';

	/** What Kastellan may change on a host, ability by ability: only what is chosen here is
	 * installed there, and the container abilities only act on the chosen containers. Set up in a
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

	const targets = $derived(all ? ['*'] : picked);
	const containerAbilities = $derived(chosen.some((a) => a.endsWith('-container')));
	const ready = $derived(!containerAbilities || targets.length > 0);

	async function load(first = false) {
		error = null;
		try {
			setup = await api.actScript(device, chosen, targets);
			if (first && setup.allowed) {
				// Start from what the host allows now.
				chosen = Object.keys(setup.allowed);
				const lists = Object.entries(setup.allowed)
					.filter(([a]) => a.endsWith('-container'))
					.map(([, l]) => [...l].sort().join(' '));
				uneven = new Set(lists).size > 1;
				const names = [...new Set(Object.values(setup.allowed).flat())];
				all = names.includes('*');
				picked = all ? [] : names;
				setup = await api.actScript(device, chosen, targets);
			}
		} catch (e) {
			error = (e as Error).message;
		}
	}

	// The commands follow the choice.
	$effect(() => {
		void chosen.length;
		void targets.length;
		if (opened && setup) load();
	});
</script>

<details
	class="card mt-4 p-4"
	ontoggle={(e) => {
		if ((e.currentTarget as HTMLDetailsElement).open && !opened) {
			opened = true;
			load(true);
		}
	}}
>
	<summary class="cursor-pointer text-sm font-semibold">
		Actor: what Kastellan may change here
	</summary>
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
				<label class="choice text-sm">
					<input type="checkbox" value={ability} bind:group={chosen} />
					<span>{description}</span>
				</label>
			{/each}
		</fieldset>
		{#if containerAbilities}
			<fieldset class="mt-4 flex flex-col gap-2 text-sm">
				<legend class="mb-2 text-xs font-medium text-muted">On which containers</legend>
				<label class="choice">
					<input type="radio" bind:group={all} value={false} /> Only these
				</label>
				{#if !all}
					<div class="ml-6.5 grid grid-cols-2 gap-x-6 gap-y-1.5 sm:grid-cols-3">
						{#each containers as name (name)}
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
		<div class="mt-4 flex justify-end">
			<button
				class="inline-flex h-8 items-center rounded-lg border border-line px-3 text-xs font-medium hover:border-line-strong disabled:opacity-50"
				disabled={!ready}
				title={ready ? '' : 'tick at least one container'}
				onclick={() => (installing = true)}
			>
				Set up exactly this
			</button>
		</div>
	{/if}
</details>

{#if installing && setup}
	<SetupDialog
		title="Set up the actor"
		intro={`Kastellan logs in once with your login and sets the host up for exactly this, as root: ${
			chosen.length ? chosen.join(', ') : 'no abilities (only listing)'
		}${containerAbilities ? ` on ${all ? 'all containers' : picked.join(', ')}` : ''}. Abilities not ticked are removed.`}
		path="/devices/{encodeURIComponent(device)}/act/install"
		body={{ abilities: chosen, containers: targets }}
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
