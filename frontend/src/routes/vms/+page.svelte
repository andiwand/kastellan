<script lang="ts">
	import { store } from '#lib/state.svelte.js';
	import { ago } from '#lib/format.js';
	import VmsTable from '#lib/components/VmsTable.svelte';
	import ReadAgain from '#lib/components/ReadAgain.svelte';
	import PageHeader from '#lib/components/ui/PageHeader.svelte';
	import Section from '#lib/components/ui/Section.svelte';
	import EmptyState from '#lib/components/ui/EmptyState.svelte';

	/** Every virtual machine on every host (libvirt/KVM, through the host's collector), grouped
	 * by host; started, shut down, restarted or forced off where the host's actor allows it. */
	const admin = $derived(store.me?.role === 'admin' && !!store.me.user);
	const hosts = $derived((store.devices ?? []).filter((d) => d.vms?.length));
</script>

<svelte:head><title>Virtual machines · Kastellan</title></svelte:head>

<PageHeader
	title="Virtual machines"
	summary="The virtual machines on every host (libvirt, KVM), as each host's collector reports them."
/>

{#if store.devices === null}
	<div class="card h-48 animate-pulse"></div>
{:else if hosts.length === 0}
	<EmptyState
		text="No virtual machines known yet. Kastellan sees a host's VMs through its collector (set it up on the host's page); the host needs libvirt (virsh)."
	/>
{:else}
	{#each hosts as d, i (d.id)}
		<Section
			title={d.name}
			href="/devices/{d.id}"
			count={d.vms?.length}
			first={i === 0}
			id="host-{d.id}"
		>
			{#snippet meta()}
				{#if d.host_checked}<span>
						from its collector, {ago(d.host_checked, store.now)} · <ReadAgain device={d.id} />
					</span>{/if}
			{/snippet}
			<VmsTable device={d.id} vms={d.vms ?? []} canAct={!!d.act && admin} />
		</Section>
	{/each}
{/if}
