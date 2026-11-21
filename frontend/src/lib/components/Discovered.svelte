<script lang="ts">
	import { more, type Discovered } from '#lib/api.js';
	import { store } from '#lib/state.svelte.js';
	import { onMount } from 'svelte';
	import DeviceEditor from './DeviceEditor.svelte';

	/** Devices the routers see that Kastellan doesn't know: add one (its name and address filled
	 * in), or set it aside for good. Nothing is added on its own. */
	let found = $state<Discovered[] | null>(null);
	let open = $state(false);
	let adding = $state<Discovered | null>(null);
	let error = $state<string | null>(null);

	async function load() {
		try {
			found = await more.discovered();
		} catch (e) {
			error = (e as Error).message;
		}
	}
	onMount(load);
	async function ignore(d: Discovered) {
		try {
			await more.ignoreDiscovered(d.mac);
			found = (found ?? []).filter((x) => x.mac !== d.mac);
		} catch (e) {
			error = (e as Error).message;
		}
	}
	const name = (id: string) => store.byId.get(id)?.name ?? id;
	const canEdit = $derived(!!store.me?.user && store.editBlocked === null);
</script>

{#if found?.length}
	<section class="card mt-6 p-4">
		<button
			class="flex w-full items-baseline justify-between gap-2 text-left"
			onclick={() => (open = !open)}
		>
			<h2 class="text-sm font-semibold">Found on the network</h2>
			<span class="text-xs text-muted">
				{found.length} not in Kastellan · {open ? 'hide' : 'show'}
			</span>
		</button>
		{#if open}
			<p class="mt-1 text-xs text-muted">
				Clients the routers report that match no device here. Add the ones worth watching; set the
				others aside (phones, guests).
			</p>
			{#if error}<p class="mt-2 text-xs" style:color="var(--critical-text)">{error}</p>{/if}
			{#if adding}
				<div class="mt-3">
					<DeviceEditor
						initial={{ name: adding.name ?? '', host: adding.address ?? '' }}
						onclose={() => ((adding = null), load())}
					/>
				</div>
			{/if}
			<ul class="mt-2 divide-y divide-line text-sm">
				{#each found as d (d.mac)}
					<li class="flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5">
						<span class="min-w-40 font-medium">{d.name ?? 'unnamed'}</span>
						<span class="font-mono text-xs text-muted">{d.address ?? '–'}</span>
						<span class="font-mono text-xs text-faint">{d.mac}</span>
						<span class="text-xs text-faint">
							{d.wifi ? 'Wi-Fi' : (d.via ?? '')} · {d.routers.map(name).join(', ')}
						</span>
						{#if canEdit}
							<span class="ml-auto flex gap-1.5">
								<button class="btn btn-sm" disabled={!d.address} onclick={() => (adding = d)}>
									Add
								</button>
								<button class="btn btn-sm" title="Not a device to watch" onclick={() => ignore(d)}>
									Set aside
								</button>
							</span>
						{/if}
					</li>
				{/each}
			</ul>
		{/if}
	</section>
{/if}
